"""Digest and opportunity-card delivery with notification de-duplication."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from summer_hunter.database.models import utcnow
from summer_hunter.database.repository import Repository, as_utc
from summer_hunter.integrations.telegram_client import TelegramClient, TelegramError
from summer_hunter.models.opportunity import ACTIONABLE_POST_CLASSES
from summer_hunter.notifications import formatter
from summer_hunter.utils.settings import Settings

log = logging.getLogger(__name__)


@dataclass
class DeliveryReport:
    messages: list[str] = field(default_factory=list)
    sent: int = 0
    skipped_duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


class Notifier:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session], telegram: TelegramClient | None) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.telegram = telegram
        self.send_interval_s = 1.1

    def _send(self, repo: Repository, report: DeliveryReport, key: str, kind: str, opp_id: int | None, text: str,
              buttons=None) -> bool:
        if repo.notification_sent(key):
            report.skipped_duplicates += 1
            return False
        report.messages.append(text)
        if report.dry_run or self.telegram is None:
            return False
        try:
            message_id = self.telegram.send_message(self.settings.telegram_chat_id, text, buttons)
        except TelegramError as exc:
            report.errors.append(str(exc))
            repo.record_notification(key, kind, opp_id, None, "failed")
            return False
        repo.record_notification(key, kind, opp_id, message_id, "sent")
        report.sent += 1
        return True

    def send_card(self, repo: Repository, opp, report: DeliveryReport) -> bool:  # noqa: ANN001
        ev, fit = repo.latest_eligibility(opp.id), repo.latest_fit(opp.id)
        if ev is None or fit is None:
            return False
        text, buttons = formatter.card(opp, ev.criteria, fit.report, fit.networking, fit.priority, repo.contacts(opp.id))
        return self._send(repo, report, f"card:{opp.id}", "card", opp.id, text, buttons)

    def morning_digest(self, dry_run: bool = False, force: bool = False) -> DeliveryReport:
        report = DeliveryReport(dry_run=dry_run or self.telegram is None)
        tz = ZoneInfo(self.settings.timezone)
        today = datetime.now(tz).date().isoformat()
        rules = self.settings.scoring["priority"]
        with self.session_factory() as session:
            repo = Repository(session)
            last = repo.get_kv("last_digest_at")
            since = as_utc(datetime.fromisoformat(last)) if last else utcnow() - timedelta(hours=24)
            new = repo.discovered_since(since)
            stats = {
                "discovered": len(new),
                "rejected": sum(o.eligibility_status == "REJECTED" for o in new),
                "needs_verification": sum(o.eligibility_status == "NEEDS_VERIFICATION" for o in new),
                "eligible": sum(o.eligibility_status == "ELIGIBLE" for o in new),
                "shortlisted": sum(bool(o.notify) for o in new),
            }
            # Cards: everything shortlisted and not yet carded (new tonight or re-scored above threshold).
            candidates = [o for o in repo.top(limit=60) if o.notify and o.user_status == "new"]
            best = [(o, repo.latest_fit(o.id).report) for o in candidates if repo.latest_fit(o.id)]
            leads = [p for p in repo.unnotified_post_leads([c.value for c in ACTIONABLE_POST_CLASSES]) if p.opportunity_id is None]
            issues = [f"{h.source}: {h.consecutive_failures} consecutive failures ({h.last_error})"
                      for h in repo.health() if (h.consecutive_failures or 0) >= 2]
            window = f"since {as_utc(since).astimezone(tz):%a %d %b %H:%M} (Europe/Paris)"
            text = formatter.digest(stats, best, leads, issues, window)
            digest_key = f"digest:{today}" if not force else f"digest:{today}:{utcnow().isoformat()}"
            self._send(repo, report, digest_key, "digest", None, text)
            cap = int(rules.get("max_cards_per_digest") or 0)
            for opp, _ in (best[:cap] if cap > 0 else best):
                if self.send_card(repo, opp, report):
                    time.sleep(self.send_interval_s)   # stay under Telegram's ~1 msg/s per chat
            if not report.dry_run:
                for post in leads[:5]:
                    post.notified = True
                repo.set_kv("last_digest_at", utcnow().isoformat())
                repo.set_kv("last_digest_date", today)
                session.commit()
            else:
                session.rollback()
        return report

    def digest_sent_today(self) -> bool:
        tz = ZoneInfo(self.settings.timezone)
        with self.session_factory() as session:
            return Repository(session).get_kv("last_digest_date") == datetime.now(tz).date().isoformat()
