"""Night window runner: repeated collection cycles 00:00-06:00 Europe/Paris, then the 06:00 digest.

launchd starts `summer-hunter night` at 00:05 and 06:00 local time. If the Mac was asleep,
launchd runs the missed job at wake; the runner then sees it is outside the window and does a
single catch-up cycle plus the digest. A file lock prevents concurrent runs. All window maths is
done in Europe/Paris with zoneinfo, so DST transitions are handled by the tz database.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


class AlreadyRunning(Exception):
    pass


@contextmanager
def single_instance(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise AlreadyRunning(str(lock_path)) from None
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _hm(value: str) -> dtime:
    h, m = value.split(":")
    return dtime(int(h), int(m))


@dataclass
class Window:
    tz: ZoneInfo
    start: dtime
    end: dtime

    @classmethod
    def from_settings(cls, settings) -> "Window":  # noqa: ANN001
        return cls(ZoneInfo(settings.timezone), _hm(settings.night_start), _hm(settings.night_end))

    def bounds(self, day: date) -> tuple[datetime, datetime]:
        """Aware start/end datetimes of the window that starts on `day` (end may be next day)."""
        start = datetime.combine(day, self.start, self.tz)
        end_day = day if self.end > self.start else day + timedelta(days=1)
        return start, datetime.combine(end_day, self.end, self.tz)

    def current(self, now: datetime) -> tuple[datetime, datetime] | None:
        local = now.astimezone(self.tz)
        for day in (local.date(), local.date() - timedelta(days=1)):
            start, end = self.bounds(day)
            if start <= local < end:
                return start, end
        return None

    def contains(self, now: datetime) -> bool:
        return self.current(now) is not None


class StopFlag:
    def __init__(self) -> None:
        self.stopped = False

    def install(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handler)

    def _handler(self, signum, _frame) -> None:  # noqa: ANN001
        log.info("stop signal received", extra={"signal": signum})
        self.stopped = True

    def __call__(self) -> bool:
        return self.stopped


def _sleep_until(target: datetime, stop: StopFlag, now_fn) -> None:  # noqa: ANN001
    while not stop():
        remaining = (target - now_fn()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30))


def run_night(hunter, notifier, settings, stop: StopFlag | None = None, now_fn=None, max_cycles: int | None = None) -> dict:  # noqa: ANN001
    """Main entry for the scheduled job. Returns a summary dict."""
    stop = stop or StopFlag()
    window = Window.from_settings(settings)
    now_fn = now_fn or (lambda: datetime.now(window.tz))
    interval = timedelta(minutes=settings.cycle_interval_minutes)
    summary = {"cycles": 0, "mode": None, "digest": None, "errors": []}
    current = window.current(now_fn())
    if current:
        summary["mode"] = "night_window"
        _, end = current
        safety_margin = timedelta(minutes=10)
        while not stop() and now_fn() < end - safety_margin:
            started = now_fn()
            stats = asyncio.run(hunter.run_cycle(trigger="nightly", should_stop=stop))
            summary["cycles"] += 1
            summary["errors"] += stats.errors[:5]
            if max_cycles and summary["cycles"] >= max_cycles:
                break
            next_start = started + interval
            if next_start >= end - safety_margin:
                break
            _sleep_until(next_start, stop, now_fn)
        digest_at = datetime.combine(end.date(), _hm(settings.digest_time), window.tz)
        if not stop() and now_fn() < digest_at:
            _sleep_until(digest_at, stop, now_fn)
    else:
        summary["mode"] = "catch_up"
        last_cycle_recent = _recent_cycle(hunter, hours=8)
        if not last_cycle_recent:
            stats = asyncio.run(hunter.run_cycle(trigger="catch_up", should_stop=stop))
            summary["cycles"] += 1
            summary["errors"] += stats.errors[:5]
    if not stop() and not notifier.digest_sent_today():
        report = notifier.morning_digest()
        summary["digest"] = {"sent": report.sent, "dry_run": report.dry_run, "errors": report.errors}
    return summary


def _recent_cycle(hunter, hours: float) -> bool:  # noqa: ANN001
    from summer_hunter.database.repository import Repository, as_utc
    from summer_hunter.pipeline import since_hours

    with hunter.session_factory() as session:
        run = Repository(session).last_run()
        return bool(run and as_utc(run.started_at) >= since_hours(hours))
