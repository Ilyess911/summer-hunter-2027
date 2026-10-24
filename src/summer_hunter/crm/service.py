"""CRM business logic. Every change goes through here so the activity log stays complete."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from summer_hunter.crm import statuses as st
from summer_hunter.crm.deadlines import explicit_deadline
from summer_hunter.crm.validation import (
    MAX_NOTE,
    ValidationError,
    app_code,
    check_url,
    clean_text,
    normalize_name,
)
from summer_hunter.database.models import (
    Contact,
    CRMApplication,
    CRMContact,
    CRMEvent,
    Opportunity,
    utcnow,
)

log = logging.getLogger(__name__)

SOURCES = {"telegram", "excel", "miniapp", "cli", "system", "migration", "web", "sync"}
EVENT_FOR_STATUS = {
    "APPLIED": "application_submitted", "INTERVIEW": "interview_scheduled", "FINAL_INTERVIEW": "interview_scheduled",
    "OFFER_RECEIVED": "offer_received", "OFFER_ACCEPTED": "offer_accepted", "REJECTED": "rejection_received",
    "WITHDRAWN": "application_withdrawn",
}
# Fields a user may change after creation (Telegram, CLI, Excel), with their kind for validation.
EDITABLE = {"next_action": "text", "next_action_date": "date", "notes": "note", "date_applied": "date",
            "deadline": "date", "interview_at": "datetime", "recruiter_name": "text", "recruiter_linkedin": "url",
            "compensation": "text", "internship_start": "date", "internship_end": "date"}


@dataclass
class Change:
    field: str
    old: Any
    new: Any


def _fmt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class CRM:
    def __init__(self, session: Session, data_dir: Path | None = None, timezone: str = "Europe/Paris") -> None:
        self.s = session
        self.data_dir = data_dir
        self.tz = timezone

    def today(self) -> date:
        return datetime.now(ZoneInfo(self.tz)).date()

    def local_date(self, dt: datetime) -> date:
        """Calendar day of a stored (UTC) instant in the user's timezone."""
        from summer_hunter.database.repository import as_utc

        return as_utc(dt).astimezone(ZoneInfo(self.tz)).date()

    # Queries ----------------------------------------------------------------------------------
    def application(self, app_id: int) -> CRMApplication | None:
        return self.s.get(CRMApplication, app_id)

    def application_for(self, opp_id: int) -> CRMApplication | None:
        return self.s.execute(select(CRMApplication).where(CRMApplication.opportunity_id == opp_id)).scalar_one_or_none()

    def applications(self, statuses: set[str] | None = None) -> list[CRMApplication]:
        stmt = select(CRMApplication).order_by(CRMApplication.id)
        if statuses:
            stmt = stmt.where(CRMApplication.status.in_(sorted(statuses)))
        return list(self.s.execute(stmt).scalars())

    def by_company(self, text: str) -> list[CRMApplication]:
        needle = f"%{text.strip().lower()}%"
        return list(self.s.execute(select(CRMApplication).join(Opportunity)
                                   .where(func.lower(Opportunity.company_name).like(needle))
                                   .order_by(CRMApplication.id)).scalars())

    def contact(self, contact_id: int) -> CRMContact | None:
        return self.s.get(CRMContact, contact_id)

    def contacts(self) -> list[CRMContact]:
        return list(self.s.execute(select(CRMContact).order_by(CRMContact.id)).scalars())

    def events(self, application_id: int | None = None) -> list[CRMEvent]:
        stmt = select(CRMEvent).order_by(CRMEvent.created_at, CRMEvent.id)
        if application_id is not None:
            stmt = stmt.where(CRMEvent.application_id == application_id)
        return list(self.s.execute(stmt).scalars())

    # Activity log -----------------------------------------------------------------------------
    def log_event(self, event_type: str, source: str, app: CRMApplication | None = None, contact: CRMContact | None = None,
                  previous: Any = None, new: Any = None, notes: str | None = None, dedupe_key: str | None = None) -> CRMEvent | None:
        if source not in SOURCES:
            raise ValueError(f"unknown event source {source!r}")
        if dedupe_key and self.s.execute(select(CRMEvent.id).where(CRMEvent.dedupe_key == dedupe_key)).first():
            return None
        company = app.opportunity.company_name if app else (contact.company if contact else None)
        event = CRMEvent(application_id=app.id if app else None, contact_id=contact.id if contact else None,
                         company=company, event_type=event_type, previous_value=_fmt(previous), new_value=_fmt(new),
                         source=source, notes=notes, dedupe_key=dedupe_key)
        self.s.add(event)
        self.s.flush()
        log.info("crm event", extra={"event": event_type, "application_id": event.application_id, "source": source})
        return event

    def _touch(self, obj: CRMApplication | CRMContact) -> None:
        obj.version = (obj.version or 0) + 1
        obj.updated_at = utcnow()

    # Applications -----------------------------------------------------------------------------
    def track(self, opp: Opportunity, source: str, status: str = "TO_APPLY") -> tuple[CRMApplication, bool]:
        """Create the application record for `opp` (idempotent: a second call returns the existing one)."""
        existing = self.application_for(opp.id)
        if existing:
            return existing, False
        if status not in st.PRE_APPLICATION:
            raise ValidationError(f"a new application starts before submission, not at {status}")
        deadline = explicit_deadline(opp.description_text)
        app = CRMApplication(opportunity_id=opp.id, status=status, furthest_status=status,
                             deadline=deadline, deadline_source="posting" if deadline else None)
        self.s.add(app)
        self.s.flush()
        app.opportunity = opp
        if opp.user_status == "new":
            opp.user_status = "saved"
        self.log_event("application_created", source, app, new=status,
                       notes=f"from opportunity #{opp.id}" + (f"; deadline {deadline} stated in the posting" if deadline else ""))
        self.import_public_contacts(app, source)
        self.sync_documents(app)
        return app, True

    def set_status(self, app: CRMApplication, new: str, source: str, force: bool = False, notes: str | None = None,
                   dedupe_key: str | None = None) -> bool:
        new = (new or "").strip().upper()
        st.check_transition(app.status, new, app.date_applied is not None, force=force)
        if new == app.status:
            return False
        old = app.status
        app.status = new
        app.furthest_status = st.deeper(app.furthest_status or old, new)
        self._sync_opportunity(app)
        self._touch(app)
        self.log_event("status_changed", source, app, previous=old, new=new, notes=notes, dedupe_key=dedupe_key)
        if new in EVENT_FOR_STATUS:
            self.log_event(EVENT_FOR_STATUS[new], source, app, previous=old, new=new,
                           dedupe_key=f"{dedupe_key}:detail" if dedupe_key else None)
        return True

    def confirm_applied(self, opp: Opportunity, source: str, applied_on: date | None = None) -> CRMApplication:
        """You confirmed the application was submitted: record the date, move to APPLIED, freeze the documents."""
        app, _ = self.track(opp, source)
        applied_on = applied_on or self.today()
        if applied_on > self.today():
            raise ValidationError("the application date cannot be in the future")
        if app.date_applied is None:
            self.update_fields(app, {"date_applied": applied_on}, source)
        if app.status in st.PRE_APPLICATION:
            self.set_status(app, "APPLIED", source)
        self.freeze_documents(app)
        return app

    def record_interview(self, app: CRMApplication, when: datetime, source: str, final: bool = False) -> None:
        self.update_fields(app, {"interview_at": when}, source)
        target = "FINAL_INTERVIEW" if final else "INTERVIEW"
        if st.RANK.get(app.status, -1) < st.RANK[target] and app.status not in st.TERMINAL:
            self.set_status(app, target, source)
        else:
            self.log_event("interview_scheduled", source, app, new=when)

    def follow_up_done(self, app: CRMApplication, source: str, notes: str | None = None) -> None:
        self.log_event("follow_up_completed", source, app, new=self.today(), notes=notes)
        self._touch(app)

    def update_fields(self, app: CRMApplication, changes: dict[str, Any], source: str,
                      dedupe_prefix: str | None = None) -> list[Change]:
        """Apply already-parsed values; validate kinds and log one event per changed field."""
        applied: list[Change] = []
        for name, value in changes.items():
            kind = EDITABLE.get(name)
            if kind is None:
                raise ValidationError(f"{name} is read-only")
            value = self._check(name, kind, value)
            old = getattr(app, name)
            if _same(old, value):
                continue
            if name == "date_applied" and value is None and app.status in st.POST_APPLICATION:
                raise ValidationError("date applied cannot be cleared on a submitted application")
            if name == "date_applied" and value and value > self.today():
                raise ValidationError("date applied cannot be in the future")
            setattr(app, name, value)
            if name == "deadline":
                app.deadline_source = "manual" if value else None
            applied.append(Change(name, old, value))
            self.log_event("field_updated", source, app, previous=old, new=value, notes=name,
                           dedupe_key=f"{dedupe_prefix}:{name}" if dedupe_prefix else None)
        if applied:
            if any(c.field == "date_applied" for c in applied):
                self._sync_opportunity(app)
            self._touch(app)
        return applied

    def _check(self, name: str, kind: str, value: Any) -> Any:
        if kind == "text":
            return clean_text(value, name)
        if kind == "note":
            return clean_text(value, name, MAX_NOTE)
        if kind == "url":
            return check_url(value, name)
        if kind == "date" and value is not None and not isinstance(value, date):
            raise ValidationError(f"{name} must be a date")
        if kind == "datetime" and value is not None and not isinstance(value, datetime):
            raise ValidationError(f"{name} must be a date and time")
        return value

    def _sync_opportunity(self, app: CRMApplication) -> None:
        """Keep the discovery lists consistent: anything submitted leaves the to-do lists."""
        opp = app.opportunity
        if app.date_applied is not None or app.status in st.POST_APPLICATION:
            opp.user_status = "applied"
        elif app.status in {"WITHDRAWN", "CLOSED", "REJECTED"}:
            opp.user_status = "dismissed"
        elif opp.user_status in {"new", "dismissed"}:
            opp.user_status = "saved"

    # Contacts ---------------------------------------------------------------------------------
    def import_public_contacts(self, app: CRMApplication, source: str) -> list[CRMContact]:
        """Copy the contacts the networking engine found with public evidence; nothing else."""
        added = []
        found = self.s.execute(select(Contact).where(Contact.opportunity_id == app.opportunity_id)).scalars()
        for c in found:
            evidence = c.source_post_url or c.profile_url
            if not evidence or c.confidence < 0.6:
                continue
            ctype = st.RELATIONSHIP_TO_TYPE.get(c.relationship_type, "Other")
            contact = self.add_contact(app.opportunity.company_name, c.name, source, position=c.role,
                                       linkedin_url=c.profile_url, application=app, contact_type=ctype,
                                       origin=evidence)
            if contact:
                added.append(contact)
        return added

    def add_contact(self, company: str, name: str, source: str, position: str | None = None,
                    linkedin_url: str | None = None, application: CRMApplication | None = None,
                    contact_type: str = "Other", origin: str = "manual", notes: str | None = None) -> CRMContact | None:
        name = clean_text(name, "contact name", 160) or ""
        if not name:
            raise ValidationError("a contact needs a name")
        if contact_type not in st.CONTACT_TYPES:
            raise ValidationError(f"contact type must be one of: {', '.join(st.CONTACT_TYPES)}")
        company = clean_text(company, "company", 200) or ""
        norm = normalize_name(name)
        if self.s.execute(select(CRMContact.id).where(CRMContact.company == company, CRMContact.normalized_name == norm)).first():
            return None
        try:
            url = check_url(linkedin_url, "LinkedIn profile")
        except ValidationError:
            if origin == "manual":
                raise
            url = None
        contact = CRMContact(company=company, name=name, normalized_name=norm, position=clean_text(position, "position", 200),
                             linkedin_url=url, application_id=application.id if application else None,
                             contact_type=contact_type, interaction_status="IDENTIFIED", source=origin[:300],
                             notes=clean_text(notes, "notes", MAX_NOTE))
        self.s.add(contact)
        self.s.flush()
        self.log_event("contact_added", source, application, contact, new=f"{name} ({contact_type})",
                       notes="public evidence: " + origin if origin != "manual" else "entered manually")
        if application and contact_type == "Recruiter" and not application.recruiter_name:
            application.recruiter_name, application.recruiter_linkedin = name, url
        return contact

    def set_contact_status(self, contact: CRMContact, new: str, source: str, dedupe_key: str | None = None) -> bool:
        new = (new or "").strip().upper()
        if new not in st.INTERACTION_STATUSES:
            raise ValidationError(f"interaction status must be one of: {', '.join(st.INTERACTION_STATUSES)}")
        if new == contact.interaction_status:
            return False
        old = contact.interaction_status
        contact.interaction_status = new
        if new in {"MESSAGE_SENT", "CONNECTED", "REPLIED", "CALL_SCHEDULED"}:
            contact.last_interaction_date = self.today()
            contact.first_contact_date = contact.first_contact_date or self.today()
        self._touch(contact)
        app = self.application(contact.application_id) if contact.application_id else None
        event = "recruiter_contacted" if new == "MESSAGE_SENT" else "contact_status_changed"
        self.log_event(event, source, app, contact, previous=old, new=new, dedupe_key=dedupe_key)
        return True

    def update_contact_fields(self, contact: CRMContact, changes: dict[str, Any], source: str,
                              dedupe_prefix: str | None = None) -> list[Change]:
        allowed = {"next_followup_date": "date", "next_action": "text", "notes": "note"}
        applied = []
        for name, value in changes.items():
            if name not in allowed:
                raise ValidationError(f"{name} is read-only")
            value = self._check(name, allowed[name], value)
            old = getattr(contact, name)
            if _same(old, value):
                continue
            setattr(contact, name, value)
            applied.append(Change(name, old, value))
            app = self.application(contact.application_id) if contact.application_id else None
            self.log_event("contact_updated", source, app, contact, previous=old, new=value, notes=name,
                           dedupe_key=f"{dedupe_prefix}:{name}" if dedupe_prefix else None)
        if applied:
            self._touch(contact)
        return applied

    # Documents ----------------------------------------------------------------------------------
    def sync_documents(self, app: CRMApplication) -> None:
        if self.data_dir is None:
            return
        from summer_hunter.crm.documents import freeze_documents, sync_documents

        if app.date_applied and not app.submitted_folder:   # submitted before the CRM tracked documents
            freeze_documents(self, app, "Frozen when the CRM first saw this application: files may postdate the "
                                        "actual submission.")
        else:
            sync_documents(self, app)

    def freeze_documents(self, app: CRMApplication, note: str = "") -> None:
        if self.data_dir is not None:
            from summer_hunter.crm.documents import freeze_documents

            freeze_documents(self, app, note)

    def label(self, app: CRMApplication) -> str:
        return f"{app_code(app.id)} {app.opportunity.company_name} · {app.opportunity.title}"


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        a = a if a.tzinfo else a.replace(tzinfo=b.tzinfo)
        return abs((a - b).total_seconds()) < 60
    return (a or None) == (b or None)
