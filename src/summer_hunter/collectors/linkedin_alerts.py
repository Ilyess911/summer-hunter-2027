"""LinkedIn job-alert emails, parsed locally.

Two authorized inputs, both optional:
- a folder of exported .eml files (e.g. saved from Mail.app or Gmail "Download message");
- an IMAP mailbox (read-only, app password), limited to messages from LinkedIn job alerts.

Nothing is fetched from linkedin.com: only the job id, title, company and location printed
in the email are kept. The canonical public URL https://www.linkedin.com/jobs/view/<id>/ is
stored for the user to open.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.policy import default as default_policy
from email.utils import parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup

from summer_hunter.collectors.base import CollectContext, Collector
from summer_hunter.models.opportunity import RawOpportunity

log = logging.getLogger(__name__)

_JOB_ID = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d{6,})")
_GENERIC = re.compile(r"^(view job|apply|see all jobs|see more jobs|easy apply|view all|unsubscribe|\s*)$", re.I)
SENDERS = ("jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com", "jobs-listings@linkedin.com")


def _canonical(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def _split_company_location(lines: list[str]) -> tuple[str | None, str | None]:
    for line in lines:
        if "·" in line:
            parts = [p.strip() for p in line.split("·") if p.strip()]
            if len(parts) >= 2:
                return parts[0], parts[1]
    clean = [l for l in lines if l and not _GENERIC.match(l) and not re.search(r"\b(ago|applicants?|promoted|actively recruiting|easy apply)\b", l, re.I)]
    company = clean[0] if clean else None
    location = clean[1] if len(clean) > 1 else None
    return company, location


def parse_alert_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        m = _JOB_ID.search(a["href"])
        if not m:
            continue
        text = a.get_text(" ", strip=True)
        job_id = m.group(1)
        entry = jobs.setdefault(job_id, {"job_id": job_id, "title": None, "company": None, "location": None})
        if text and not _GENERIC.match(text) and (entry["title"] is None or len(text) > len(entry["title"])):
            container = a
            for _ in range(4):
                if container.parent is None:
                    break
                container = container.parent
                strings = [s.strip() for s in container.stripped_strings]
                if len(strings) >= 3:
                    break
            strings = [s.strip() for s in container.stripped_strings]
            try:
                idx = strings.index(text)
            except ValueError:
                idx = next((i for i, s in enumerate(strings) if text.startswith(s) or s.startswith(text)), -1)
            after = strings[idx + 1: idx + 4] if idx >= 0 else []
            company, location = _split_company_location(after)
            entry.update(title=text, company=company, location=location)
    return [j for j in jobs.values() if j["title"] and j["company"]]


def parse_alert_text(text: str) -> list[dict]:
    lines = [l.strip() for l in (text or "").splitlines()]
    jobs = []
    for i, line in enumerate(lines):
        m = _JOB_ID.search(line)
        if not m:
            continue
        prev = [l for l in lines[max(0, i - 6): i] if l and not _GENERIC.match(l.rstrip(":")) and "http" not in l]
        if len(prev) >= 2 and "·" in prev[-1]:
            title = prev[-2]
            company, location = _split_company_location(prev[-1:])
        elif len(prev) >= 3:
            title, company, location = prev[-3], prev[-2], prev[-1]
        elif len(prev) == 2:
            title, company, location = prev[-2], prev[-1], None
        else:
            continue
        jobs.append({"job_id": m.group(1), "title": title, "company": company, "location": location})
    return [j for j in jobs if j["title"] and j["company"]]


def parse_message(msg: Message) -> tuple[list[dict], datetime | None]:
    received = None
    if msg.get("Date"):
        try:
            received = parsedate_to_datetime(msg["Date"])
        except (TypeError, ValueError):
            received = None
    html_part = text_part = None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype == "text/html" and html_part is None:
            html_part = part.get_content()
        elif ctype == "text/plain" and text_part is None:
            text_part = part.get_content()
    jobs = parse_alert_html(html_part) if html_part else []
    if not jobs and text_part:
        jobs = parse_alert_text(text_part)
    return jobs, received


def _is_linkedin_alert(msg: Message) -> bool:
    sender = (msg.get("From") or "").lower()
    return any(s in sender for s in SENDERS) or "linkedin" in sender and "job" in (msg.get("Subject") or "").lower()


class LinkedInAlertsCollector(Collector):
    name = "linkedin_alerts"

    def __init__(self, eml_dir: Path | None = None, imap: dict | None = None, lookback_days: int = 3) -> None:
        self.eml_dir = eml_dir
        self.imap = imap
        self.lookback_days = lookback_days

    def _messages_from_dir(self) -> list[Message]:
        if not self.eml_dir or not self.eml_dir.exists():
            return []
        out = []
        for path in sorted(self.eml_dir.glob("*.eml")):
            with path.open("rb") as handle:
                out.append(email.message_from_binary_file(handle, policy=default_policy))
        return out

    def _messages_from_imap(self) -> list[Message]:
        if not self.imap:
            return []
        since = (datetime.now() - timedelta(days=self.lookback_days)).strftime("%d-%b-%Y")
        out = []
        conn = imaplib.IMAP4_SSL(self.imap["host"], timeout=30)
        try:
            conn.login(self.imap["user"], self.imap["password"])
            conn.select(self.imap.get("folder", "INBOX"), readonly=True)
            ids: set[bytes] = set()
            for sender in SENDERS:
                typ, data = conn.search(None, "FROM", f'"{sender}"', "SINCE", since)
                if typ == "OK" and data and data[0]:
                    ids.update(data[0].split())
            for msg_id in sorted(ids)[-100:]:
                typ, data = conn.fetch(msg_id, "(BODY.PEEK[])")
                if typ == "OK" and data and isinstance(data[0], tuple):
                    out.append(email.message_from_bytes(data[0][1], policy=default_policy))
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - logout failure is irrelevant
                pass
        return out

    async def collect(self, ctx: CollectContext):
        if not self.eml_dir and not self.imap:
            return self.result(status="disabled", detail="Set LINKEDIN_ALERTS_DIR or IMAP_* to enable LinkedIn alert ingestion.")
        errors: list[str] = []
        messages: list[Message] = []
        try:
            messages += self._messages_from_dir()
        except OSError as exc:
            errors.append(f"eml dir: {exc}")
        try:
            messages += self._messages_from_imap()
        except (imaplib.IMAP4.error, OSError) as exc:
            errors.append(f"imap: {type(exc).__name__}")
        records: dict[str, RawOpportunity] = {}
        for msg in messages:
            if not _is_linkedin_alert(msg):
                continue
            jobs, received = parse_message(msg)
            for job in jobs:
                if not ctx.title_rules.worth_collecting(job["title"]):
                    continue
                records[job["job_id"]] = RawOpportunity(
                    source=self.name, source_id=job["job_id"], url=_canonical(job["job_id"]), apply_url=_canonical(job["job_id"]),
                    title=job["title"], company=job["company"], location_text=job["location"],
                    description_text="", posted_at=received.astimezone(timezone.utc) if received else None,
                    extra={"origin": "linkedin_job_alert_email"},
                )
        status = "failed" if errors and not records else ("partial" if errors else "ok")
        return self.result(records=list(records.values()), errors=errors, scanned=len(messages), status=status)
