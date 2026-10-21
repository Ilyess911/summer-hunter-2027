"""run_night with a simulated clock: window loop, digest at 06:00, catch-up after a missed night."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from summer_hunter.pipeline import CycleStats
from summer_hunter.scheduler import nightly_runner
from summer_hunter.scheduler.nightly_runner import StopFlag, run_night

TZ = ZoneInfo("Europe/Paris")


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class FakeHunter:
    def __init__(self, clock: Clock, minutes_per_cycle: int = 20) -> None:
        self.clock, self.cycles, self.triggers, self.minutes = clock, 0, [], minutes_per_cycle

    async def run_cycle(self, trigger: str = "manual", should_stop=lambda: False, **_):
        self.cycles += 1
        self.triggers.append(trigger)
        self.clock.now += timedelta(minutes=self.minutes)
        return CycleStats()


class FakeNotifier:
    def __init__(self, clock: Clock) -> None:
        self.clock, self.sent_at, self._sent = clock, None, False

    def digest_sent_today(self) -> bool:
        return self._sent

    def morning_digest(self):
        self.sent_at, self._sent = self.clock.now, True

        class R:
            sent, dry_run, errors = 2, False, []
        return R()


def patch_sleep(monkeypatch, clock: Clock):
    monkeypatch.setattr(nightly_runner.time, "sleep", lambda s: setattr(clock, "now", clock.now + timedelta(seconds=s)))


def test_night_window_runs_cycles_then_digest_at_six(monkeypatch, settings):
    clock = Clock(datetime(2026, 10, 22, 0, 5, tzinfo=TZ))
    patch_sleep(monkeypatch, clock)
    hunter, notifier = FakeHunter(clock), FakeNotifier(clock)
    summary = run_night(hunter, notifier, settings, StopFlag(), now_fn=clock)
    assert summary["mode"] == "night_window"
    assert hunter.cycles == 4                    # 00:05, 01:35, 03:05, 04:35 with a 90-minute interval
    assert set(hunter.triggers) == {"nightly"}
    assert notifier.sent_at.astimezone(TZ).strftime("%H:%M") == "06:00"


def test_missed_night_catches_up_once_and_sends_digest(monkeypatch, settings):
    clock = Clock(datetime(2026, 10, 22, 8, 40, tzinfo=TZ))   # Mac woke at 08:40
    patch_sleep(monkeypatch, clock)
    monkeypatch.setattr(nightly_runner, "_recent_cycle", lambda hunter, hours: False)
    hunter, notifier = FakeHunter(clock), FakeNotifier(clock)
    summary = run_night(hunter, notifier, settings, StopFlag(), now_fn=clock)
    assert summary["mode"] == "catch_up" and hunter.triggers == ["catch_up"]
    assert notifier.sent_at is not None


def test_stop_signal_ends_gracefully(monkeypatch, settings):
    clock = Clock(datetime(2026, 10, 22, 0, 5, tzinfo=TZ))
    patch_sleep(monkeypatch, clock)
    stop = StopFlag()
    hunter = FakeHunter(clock)
    original = hunter.run_cycle

    async def cycle_then_stop(**kw):
        result = await original(**kw)
        stop.stopped = True
        return result

    hunter.run_cycle = cycle_then_stop
    notifier = FakeNotifier(clock)
    run_night(hunter, notifier, settings, stop, now_fn=clock)
    assert hunter.cycles == 1 and notifier.sent_at is None
