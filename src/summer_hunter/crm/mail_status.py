"""Application statuses read from the candidate's mailbox (read-only IMAP, app password).

Recruiter replies (rejection, interview invitation, online test, position closed) are matched
to a CRM application and the status is moved forward automatically. A wrong status is worse
than a missed one, so the rules are conservative:

- only the new part of a message is read (quoted history and footers are cut), and sentences
  that are conditional ("if you are not selected…") never count;
- a message whose text reads as two different outcomes changes nothing;
- the company must appear in the sender or the subject; a mention in the body alone is
  reported, never applied; when several applications share the company, the subject or the
  opening of the message must name the job;
- only submitted applications change, statuses only move forward, final statuses and an
  offer received are never touched, and the message must have reached the mailbox (IMAP
  INTERNALDATE) on or after the application date and the last status change;
- the mailbox is opened read-only, bodies are fetched with BODY.PEEK (nothing is marked read,
  moved or deleted), and the scan checkpoint only advances when every message was read;
- a message is applied once: its id is checked as a CRM event dedupe key before any change.
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import Message
from email.policy import default as default_policy
from email.utils import parseaddr
from html import escape
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from sqlalchemy import func, select

from summer_hunter.crm import statuses as st
from summer_hunter.crm.service import CRM
from summer_hunter.crm.validation import app_code
from summer_hunter.database.models import CRMApplication, CRMEvent
from summer_hunter.database.repository import Repository, as_utc

log = logging.getLogger(__name__)

SOURCE = "email"
LAST_SCAN_KEY = "mail_status:last_scan"
NOTIFIED_KEY = "mail_status:notified"
FIRST_SCAN_DAYS = 90
MAX_BODIES = 800          # bodies fetched per scan; more means an incomplete scan (checkpoint not advanced)
SCAN_BUDGET_S = 240       # overall time budget for one scan

REJECTED, CLOSED, INTERVIEW, ASSESSMENT = "REJECTED", "CLOSED", "INTERVIEW", "ONLINE_ASSESSMENT"

_PATTERNS: dict[str, list[str]] = {
    CLOSED: [
        r"\b(?:position|role|requisition|job|vacancy|opening|internship) (?:has been|was|is now|have been) (?:cancell?ed|closed|withdrawn|put on hold)\b",
        r"\bno longer (?:accepting applications|recruiting for|hiring for)\b",
        r"\b(?:poste|offre) (?:a été|est) (?:annulée?|fermée?|clôturée?)\b",
    ],
    REJECTED: [
        r"\bnot (?:to )?(?:be )?(?:mov(?:e|ing)|go(?:ing)?) forward (?:with|for|on) your (?:application|candidacy|profile)",
        r"\b(?:decided|chosen|elected|decision) (?:not to|to not) (?:move forward|proceed|progress|pursue)",
        r"\b(?:ne vous a|ne vous avons|nous ne vous avons) pas (?:sélectionné|retenu)e?s?\b",
        r"\bn[’']avez pas été (?:sélectionnée?|retenue?)\b",
        r"\b(?:move|moving|proceed|proceeding|progress|go) forward with (?:other|another|different) (?:candidates?|applicants?)",
        r"\b(?:decided|chosen|elected) to (?:pursue|proceed with|progress) (?:other|another|different) (?:candidates?|applicants?)",
        r"\bwill not be (?:moving forward|proceeding) with your (?:application|candidacy)",
        r"\bwill not be (?:progressing|taking forward) your (?:application|candidacy)",
        r"\b(?:we|will) (?:will )?not be moving forward(?= *(?:[.,;]|at this (?:time|stage|point)|$))",
        r"\byou (?:were|have) not (?:been )?(?:selected|successful|shortlisted)\b",
        r"\byour (?:application|candidacy) (?:was|has) not (?:been )?(?:successful|selected|shortlisted)",
        r"\bunable to (?:offer you|progress your|move forward with your)",
        r"\bregret to (?:inform|let you know|advise) you\b",
        r"\bne pas donner (?:une )?suite (?:à|a) votre candidature",
        r"\bne (?:pouvons|pourrons) (?:malheureusement )?pas donner (?:une )?suite",
        r"\bvotre candidature n[’']a pas été (?:retenue|sélectionnée)",
        r"\bavons (?:le regret de vous informer|décidé de ne pas donner suite)",
        r"\borienté(?:s)? notre choix vers\b",
        r"\bleider (?:mitteilen|absagen)\b",
    ],
    INTERVIEW: [
        r"\b(?:like|pleased|happy|delighted) to invite you (?:to|for) (?:an? |a first |a short )?(?:interview|call|conversation|video call|phone screen)",
        r"\b(?:like|want) to schedule (?:an? |your )?(?:interview|call|phone screen|video call)",
        r"\binterview invitation\b",
        r"\byour (?:upcoming )?interview (?:with us )?(?:is|on|at) ",
        r"\b(?:souhaitons|aimerions) (?:vous proposer|vous convier à|organiser) (?:un |votre )?(?:entretien|échange)",
        r"\binvitation à (?:un )?entretien\b",
        r"\beinladung zum (?:vorstellungs)?gespräch\b",
    ],
    ASSESSMENT: [
        r"\binvit(?:e|ing|ation) (?:you )?to (?:complete|take) (?:an? |the |our )?(?:online )?(?:assessment|test|coding challenge)",
        r"\bplease complete (?:the|an|your|our) (?:online )?(?:assessment|test|coding challenge)",
        r"\bonline assessment invitation\b",
        r"\b(?:vous invitons à|merci de) (?:passer|compléter|réaliser) (?:un |le |notre )?test en ligne",
    ],
}
_RX = {kind: [re.compile(p, re.I) for p in pats] for kind, pats in _PATTERNS.items()}
# pending or not-yet-decided wording vetoes the sentence ("not selected for now, your application is still under review")
_PENDING = re.compile(r"\b(?:still (?:under )?(?:review|consideration)|not (?:yet )?made a decision|have not made|no decision|"
                      r"pour le moment|pour l[’']instant|reste à l[’']étude|toujours à l[’']étude|en cours d[’']examen)\b", re.I)
_CONDITIONAL = re.compile(r"\b(?:if|should|unless|in case|whether|si|au cas où|sollten|falls)\b", re.I)
_QUOTE_START = re.compile(r"^(?:on .{4,120} wrote:|le .{4,120} a écrit\s*:|-{2,} ?original message|de ?: |from: |envoyé ?: |sent: )",
                          re.I | re.M)
_FOOTER = re.compile(r"^(?:unsubscribe|this (?:e-?mail|message) (?:is|was|and any)|confidential|privacy|©|copyright)", re.I | re.M)

_ATS = re.compile(r"workday|greenhouse|lever\.co|ashby|smartrecruiters|icims|successfactors|taleo|eightfold|phenom|avature|"
                  r"jobvite|recruitee|teamtailor|personio|workable|bamboohr|myworkday|hirevue|hackerrank|codility", re.I)
_SUBJECT_HINT = re.compile(r"applica|candida|interview|entretien|position|role|internship|stage|intern\b|opportunit|"
                           r"your interest|update|next step|assessment|bewerbung|recruit", re.I)
_LEGAL = re.compile(r"\b(?:inc|llc|ltd|limited|plc|gmbh|ag|sa|sas|sarl|bv|nv|corp|corporation|company|co|group|holdings?)\b\.?", re.I)
_TITLE_NOISE = {"intern", "internship", "interns", "stage", "stagiaire", "summer", "2026", "2027", "2028", "the", "and", "for",
                "with", "of", "in", "to", "a", "an", "m", "f", "d", "w", "x", "h", "i", "ii", "iii", "co", "op", "program",
                "programme", "graduate", "student", "students", "working", "werkstudent", "trainee", "apprentice", "alternance"}


def current_part(text: str) -> str:
    """The new part of a message: quoted history, '>' lines and footers removed."""
    text = "\n".join(line for line in (text or "").splitlines() if not line.lstrip().startswith(">"))
    for rx in (_QUOTE_START, _FOOTER):
        m = rx.search(text)
        if m and m.start() > 40:
            text = text[:m.start()]
    return text[:8000]


def _sentences(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", s) for s in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if s.strip()]


def classify(subject: str, text: str) -> str | None:
    """Kind of recruiter reply; None for acknowledgements, anything conditional, or mixed signals."""
    sentences = [s for s in _sentences(f"{subject}.\n\n{current_part(text)}") if not _CONDITIONAL.search(s) and not _PENDING.search(s)]
    kinds = {kind for kind, rxs in _RX.items() for s in sentences if any(rx.search(s) for rx in rxs)}
    if kinds == {CLOSED, REJECTED}:
        return CLOSED           # "we regret to inform you the position has been cancelled"
    return kinds.pop() if len(kinds) == 1 else None


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def company_keys(name: str) -> set[str]:
    """Spellings a company goes by in mail: name without legal suffix; acronym for 3+ word names."""
    base = re.sub(r"[^a-z0-9& ]+", " ", _LEGAL.sub(" ", _fold(name)))
    base = re.sub(r"\s+", " ", base).strip()
    keys = {base} if len(base) >= 2 else set()
    words = [w for w in base.replace("&", " ").split() if w]
    if len(words) >= 3:
        keys.add("".join(w[0] for w in words))
    return keys


def _mentions(keys: set[str], haystack: str) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", haystack) for k in keys)


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", _fold(title.split(" - ")[0] if " - " in title else title))
    return {w for w in words if w not in _TITLE_NOISE and len(w) > 1}


def _title_score(title: str, haystack: str) -> float:
    tokens = _title_tokens(title)
    if not tokens:
        return 0.0
    present = {t for t in tokens if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", haystack)}
    return len(present) / len(tokens)


@dataclass
class MailItem:
    message_id: str
    sender: str
    subject: str
    text: str
    received: datetime | None = None      # IMAP INTERNALDATE (when it reached the mailbox), aware


@dataclass
class Outcome:
    item: MailItem
    kind: str
    app_id: int | None = None
    label: str = ""
    changed: bool = False
    candidates: list[tuple[int, str]] = field(default_factory=list)
    reason: str = ""


@dataclass
class MailReport:
    scanned: int = 0
    complete: bool = True
    changed: list[Outcome] = field(default_factory=list)
    unmatched: list[Outcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def match(apps: list[CRMApplication], item: MailItem) -> tuple[CRMApplication | None, list[CRMApplication], str]:
    """(the single application this mail is about or None, applications it may concern, why not matched)."""
    name, addr = parseaddr(item.sender)
    domain = addr.rpartition("@")[2]
    head = _fold(f"{name} {domain.replace('.', ' ')} {item.subject}")
    opening = _fold(current_part(item.text)[:1500])
    strong = [a for a in apps if _mentions(company_keys(a.opportunity.company_name), head)]
    if not strong:
        weak = [a for a in apps if _mentions(company_keys(a.opportunity.company_name), opening)]
        return None, weak, "entreprise citée seulement dans le texte"
    if len({_fold(a.opportunity.company_name) for a in strong}) != 1:
        return None, strong, "plusieurs entreprises citées"
    if len(strong) == 1:
        return strong[0], strong, ""
    where = f"{head}\n{opening}"
    scored = sorted(((_title_score(a.opportunity.title, where), a) for a in strong), key=lambda x: x[0], reverse=True)
    if scored[0][0] >= 0.75 and scored[0][0] - scored[1][0] >= 0.25:
        return scored[0][1], strong, ""
    return None, strong, "plusieurs candidatures dans cette entreprise"


def _local_day(dt: datetime, tz: str) -> date:
    return dt.astimezone(ZoneInfo(tz)).date()


def _last_status_change(crm: CRM, app: CRMApplication) -> datetime | None:
    """Last move past APPLIED (marking it applied is often clicked days after sending, so it does not count)."""
    value = crm.s.execute(select(func.max(CRMEvent.created_at)).where(
        CRMEvent.application_id == app.id, CRMEvent.event_type == "status_changed",
        CRMEvent.new_value.not_in(st.PRE_APPLICATION | {"APPLIED"}))).scalar()
    return as_utc(value) if value else None


def _forward(app: CRMApplication, kind: str) -> bool:
    if app.status in st.TERMINAL or app.status not in st.POST_APPLICATION or app.status in st.OFFERS:
        return False
    if kind in (REJECTED, CLOSED):
        return True
    return st.RANK.get(app.status, 0) < st.RANK[kind]


def dedupe_key(item: MailItem) -> str:
    """One key per Message-ID, shared by the mailbox reader and the agent API, so a mail changes a status once."""
    mid = item.message_id.strip().strip("<>").strip()
    return f"mail:{hashlib.sha1(mid.encode()).hexdigest()[:20]}"


def apply(crm: CRM, items: list[MailItem], dry_run: bool = False) -> MailReport:
    report = MailReport(scanned=len(items))
    apps = [a for a in crm.applications() if a.date_applied is not None]
    for item in items:
        kind = classify(item.subject, item.text)
        if kind is None:
            continue
        app, hits, why = match(apps, item)
        if not hits:
            continue   # about none of your applications
        if app is None:
            report.unmatched.append(Outcome(item, kind, candidates=[(a.id, crm.label(a)) for a in hits], reason=why))
            continue
        if not _forward(app, kind):
            continue
        if item.received is None:
            report.unmatched.append(Outcome(item, kind, candidates=[(app.id, crm.label(app))], reason="date de réception inconnue"))
            continue
        last = _last_status_change(crm, app)
        if _local_day(item.received, crm.tz) < app.date_applied or (last and item.received < last):
            continue   # older than the application or than its current status: about an earlier step
        key = dedupe_key(item)
        if crm.s.execute(select(CRMEvent.id).where(CRMEvent.dedupe_key == key)).first():
            continue   # this message was already applied (and possibly corrected by hand since)
        out = Outcome(item, kind, app.id, crm.label(app))
        out.changed = True if dry_run else crm.set_status(app, kind, SOURCE, notes=f"Mail : {item.subject[:180]}",
                                                         dedupe_key=key)
        if out.changed:
            report.changed.append(out)
    return report


# Mailbox -----------------------------------------------------------------------------------------

def _body_text(msg: Message) -> str:
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            continue
        if not isinstance(content, str):
            continue
        if part.get_content_type() == "text/plain":
            plain.append(content)
        elif part.get_content_type() == "text/html":
            html.append(content)
    if plain:
        return "\n".join(plain)
    out = []
    for h in html:
        soup = BeautifulSoup(h, "html.parser")
        for q in soup.select("blockquote, .gmail_quote, .gmail_extra"):
            q.decompose()
        out.append(soup.get_text(" "))
    return "\n".join(out)


def to_item(msg: Message, received: datetime | None = None) -> MailItem:
    return MailItem(message_id=(msg.get("Message-ID") or "").strip() or hashlib.sha1(bytes(msg)).hexdigest(),
                    sender=str(msg.get("From") or ""), subject=str(msg.get("Subject") or ""),
                    text=_body_text(msg), received=received)


def _worth_body(sender: str, subject: str, keys: set[str], own: str) -> bool:
    addr = parseaddr(sender)[1].lower()
    if "linkedin.com" in addr or (own and addr == own.lower()):
        return False    # LinkedIn alerts, and your own sent mail (All Mail includes it)
    head = _fold(f"{sender} {subject}")
    return bool(_ATS.search(sender) or _SUBJECT_HINT.search(subject) or _mentions(keys, head))


class ScanIncomplete(Exception):
    pass


def _ok(typ: str, what: str) -> None:
    if typ != "OK":
        raise ScanIncomplete(f"IMAP {what} failed")


def _folder(conn: imaplib.IMAP4, configured: str | None) -> str:
    """Gmail: the special-use \\All mailbox (catches mail filtered out of the inbox). Otherwise the configured one."""
    if configured and configured.upper() != "INBOX":
        return configured
    typ, rows = conn.list()
    if typ == "OK":
        for row in rows or []:
            line = row.decode(errors="replace") if isinstance(row, bytes) else str(row)
            if "\\All" in line:
                m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
                if m:
                    return m.group(1)
    return configured or "INBOX"


def fetch(imap: dict, since: date, company_names: list[str], deadline: float | None = None) -> list[MailItem]:
    """Read-only scan: headers of every message since `since`, then bodies of plausible recruiter replies."""
    deadline = deadline or time.monotonic() + SCAN_BUDGET_S
    keys: set[str] = set()
    for name in company_names:
        keys |= company_keys(name)
    conn = imaplib.IMAP4_SSL(imap["host"], timeout=30)
    items: list[MailItem] = []
    try:
        conn.login(imap["user"], imap["password"])
        folder = _folder(conn, imap.get("folder"))
        typ, _ = conn.select(f'"{folder}"' if " " in folder and not folder.startswith('"') else folder, readonly=True)
        _ok(typ, f"select {folder}")
        typ, data = conn.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
        _ok(typ, "search")
        uids = data[0].split() if data and data[0] else []
        wanted: list[bytes] = []
        for chunk in range(0, len(uids), 300):
            if time.monotonic() > deadline:
                raise ScanIncomplete("time budget exceeded while reading headers")
            batch = uids[chunk:chunk + 300]
            typ, data = conn.uid("FETCH", b",".join(batch), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            _ok(typ, "header fetch")
            got = 0
            for row in data or []:
                if not isinstance(row, tuple):
                    continue
                uid = re.search(rb"UID (\d+)", row[0])
                if not uid:
                    continue
                got += 1
                head = email.message_from_bytes(row[1], policy=default_policy)
                if _worth_body(str(head.get("From") or ""), str(head.get("Subject") or ""), keys, imap["user"]):
                    wanted.append(uid.group(1))
            if got < len(batch):
                raise ScanIncomplete(f"header fetch returned {got} of {len(batch)} messages")
        if len(wanted) > MAX_BODIES:
            raise ScanIncomplete(f"{len(wanted)} candidate messages (> {MAX_BODIES}); scan a shorter period with --days")
        for uid in wanted:
            if time.monotonic() > deadline:
                raise ScanIncomplete("time budget exceeded while reading messages")
            typ, data = conn.uid("FETCH", uid, "(INTERNALDATE BODY.PEEK[])")
            _ok(typ, "body fetch")
            row = next((r for r in data or [] if isinstance(r, tuple)), None)
            if row is None:
                continue   # expunged between search and fetch
            tt = imaplib.Internaldate2tuple(row[0])
            received = datetime.fromtimestamp(time.mktime(tt), timezone.utc) if tt else None
            items.append(to_item(email.message_from_bytes(row[1], policy=default_policy), received))
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001 - logout failure is irrelevant
            pass
    return items


def imap_config(settings) -> dict | None:  # noqa: ANN001
    if not (settings.imap_host and settings.imap_user and settings.imap_password):
        return None
    return {"host": settings.imap_host, "user": settings.imap_user, "password": settings.imap_password,
            "folder": settings.imap_folder}


def sync_mailbox(settings, session_factory, dry_run: bool = False, days: int | None = None) -> MailReport | None:  # noqa: ANN001
    """Scan the mailbox and update statuses. None when IMAP is not configured."""
    imap = imap_config(settings)
    if imap is None:
        return None
    with session_factory() as session:
        repo = Repository(session)
        crm = CRM(session, settings.data_dir, settings.timezone)
        apps = [a for a in crm.applications() if a.date_applied is not None]
        last = repo.get_kv(LAST_SCAN_KEY)
        if days is not None:
            since = crm.today() - timedelta(days=days)
        elif last:
            since = date.fromisoformat(last) - timedelta(days=2)   # overlap: SINCE is by day; dedupe makes it safe
        else:
            first = min((a.date_applied for a in apps), default=crm.today())
            since = max(first - timedelta(days=1), crm.today() - timedelta(days=FIRST_SCAN_DAYS))
        started = crm.today()
        report = MailReport()
        try:
            items = fetch(imap, since, [a.opportunity.company_name for a in apps])
        except (imaplib.IMAP4.error, OSError, ScanIncomplete) as exc:
            report.complete = False
            report.errors.append(f"{type(exc).__name__}: {exc}"[:300])
            return report
        report = apply(crm, items, dry_run=dry_run)
        if not dry_run:
            if days is None:
                repo.set_kv(LAST_SCAN_KEY, started.isoformat())
            session.commit()
        return report


KIND_FR = {REJECTED: "❌ Refus", CLOSED: "🚫 Poste fermé", INTERVIEW: "🗓 Entretien", ASSESSMENT: "📝 Test en ligne"}


def report_text(report: MailReport, already_notified: set[str] | None = None) -> str | None:
    """Telegram recap; None when there is nothing new to say."""
    already_notified = already_notified or set()
    lines = [f"{KIND_FR[o.kind]} : {escape(o.label)}" for o in report.changed]
    for o in (o for o in report.unmatched if o.item.message_id not in already_notified):
        codes = ", ".join(app_code(i) for i, _ in o.candidates[:4])
        lines.append(f"❓ {KIND_FR[o.kind]} ? « {escape(o.item.subject[:90])} » ({escape(o.reason)} : {codes}). "
                     f"Si c'est le bon dossier : /setstatus APP-… {o.kind}")
    if report.errors:
        lines.append("⚠️ Lecture de la boîte incomplète : " + escape(report.errors[0]))
    if not lines:
        return None
    return "<b>📬 Boîte mail</b>\n" + "\n".join(lines)


_scan_lock = threading.Lock()


def run_and_report(settings, session_factory, send, quiet: bool = True) -> MailReport | None:  # noqa: ANN001
    """Scan, then send one Telegram recap. Unmatched mails are reported once. Never two scans at once."""
    if not _scan_lock.acquire(blocking=False):
        if not quiet:
            send("📬 Lecture de la boîte déjà en cours.")
        return None
    try:
        report = sync_mailbox(settings, session_factory)
        if report is None:
            if not quiet:
                send("📬 Boîte mail non connectée : remplis IMAP_HOST, IMAP_USER et IMAP_PASSWORD dans .env.")
            return None
        with session_factory() as session:
            repo = Repository(session)
            seen = set(filter(None, (repo.get_kv(NOTIFIED_KEY) or "").split("\n")))
            today = date.today().isoformat()
            if report.errors and quiet and repo.get_kv("mail_status:error_day") == today:
                report.errors = []              # automatic scans report a failing mailbox once a day
            elif report.errors:
                repo.set_kv("mail_status:error_day", today)
            text = report_text(report, seen)
            repo.set_kv(NOTIFIED_KEY, "\n".join(list(seen | {o.item.message_id for o in report.unmatched})[-500:]))
            session.commit()
        if text:
            send(text)
        elif not quiet:
            send(f"📬 Boîte mail lue ({report.scanned} mails de recrutement) : aucun nouveau statut.")
        return report
    finally:
        _scan_lock.release()


def run_in_background(settings, session_factory, send, quiet: bool = True) -> None:  # noqa: ANN001
    """Same as run_and_report on a daemon thread, so Telegram polling never waits on IMAP."""
    def work() -> None:
        try:
            run_and_report(settings, session_factory, send, quiet)
        except Exception:  # noqa: BLE001 - the mailbox must never take the bot down
            log.exception("mailbox scan failed")
    threading.Thread(target=work, name="mail-scan", daemon=True).start()
