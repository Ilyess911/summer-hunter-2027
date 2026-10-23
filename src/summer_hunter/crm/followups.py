"""Follow-up and deadline reminders. Suggestions only: nothing is ever sent to a recruiter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select

from summer_hunter.crm import statuses as st
from summer_hunter.crm.service import CRM
from summer_hunter.crm.validation import contact_code
from summer_hunter.database.models import CRMApplication, CRMContact, CRMReminder

APPLICATION_FOLLOWUP_DAYS = 10
CONTACT_FOLLOWUP_DAYS = 5
DEADLINE_THRESHOLDS = (7, 3, 1)


@dataclass
class Reminder:
    kind: str                      # followup_application | followup_contact | deadline
    key: str
    text: str
    due: date
    application: CRMApplication | None = None
    contact: CRMContact | None = None
    covers: list[str] = field(default_factory=list)   # extra keys silenced with this one


def add_business_days(start: date, days: int) -> date:
    current, left = start, days
    while left > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            left -= 1
    return current


def _application_anchor(crm: CRM, app: CRMApplication) -> date | None:
    """The last moment something happened on a submitted application: submission, status change or follow-up."""
    if app.date_applied is None:
        return None
    anchor = app.date_applied
    for ev in crm.events(app.id):
        if ev.event_type == "follow_up_completed" or (ev.event_type == "status_changed" and ev.new_value != "APPLIED"):
            anchor = max(anchor, ev.created_at.date())
    return anchor


def application_followups(crm: CRM, today: date) -> list[Reminder]:
    out = []
    for app in crm.applications({"APPLIED"}):
        anchor = _application_anchor(crm, app)
        if anchor is None:
            continue
        due = add_business_days(anchor, APPLICATION_FOLLOWUP_DAYS)
        if today < due:
            continue
        who = (f"via {app.recruiter_name}" if app.recruiter_name
               else "aucun contact connu : relance par le portail, ou ignore si l'entreprise ne le permet pas")
        out.append(Reminder("followup_application", f"followup_application:{app.id}:{anchor.isoformat()}",
                            f"{crm.label(app)} : sans réponse depuis {APPLICATION_FOLLOWUP_DAYS} jours ouvrés, relance conseillée ({who})",
                            due, application=app))
    return out


def contact_followups(crm: CRM, today: date) -> list[Reminder]:
    out = []
    for c in crm.contacts():
        app = crm.application(c.application_id) if c.application_id else None
        if app and app.status in st.TERMINAL:
            continue
        if c.interaction_status == "MESSAGE_SENT" and c.last_interaction_date:
            due = add_business_days(c.last_interaction_date, CONTACT_FOLLOWUP_DAYS)
            if today >= due:
                out.append(Reminder("followup_contact", f"followup_contact:{c.id}:{c.last_interaction_date.isoformat()}",
                                    f"{contact_code(c.id)} {c.name} ({c.company}) : pas de réponse à ton message, relance possible",
                                    due, application=app, contact=c))
        elif c.interaction_status == "FOLLOW_UP_REQUIRED" or (c.next_followup_date and c.next_followup_date <= today
                                                               and c.interaction_status != "CLOSED"):
            anchor = (c.next_followup_date or c.last_interaction_date or today).isoformat()
            out.append(Reminder("followup_contact", f"followup_contact:{c.id}:{anchor}:{c.interaction_status}",
                                f"{contact_code(c.id)} {c.name} ({c.company}) : relance prévue", c.next_followup_date or today,
                                application=app, contact=c))
    return out


def deadline_reminders(crm: CRM, today: date) -> list[Reminder]:
    """7, 3 and 1 day(s) before a confirmed future deadline; only the tightest threshold crossed is sent."""
    out = []
    for app in crm.applications(st.PRE_APPLICATION):
        if app.deadline is None or app.deadline <= today:
            continue
        left = (app.deadline - today).days
        crossed = [t for t in DEADLINE_THRESHOLDS if left <= t]
        if not crossed:
            continue
        keys = [f"deadline:{app.id}:{app.deadline.isoformat()}:{t}" for t in sorted(crossed)]
        out.append(Reminder("deadline", keys[0], f"{crm.label(app)} : date limite le {app.deadline:%d/%m} (J-{left})",
                            app.deadline, application=app, covers=keys[1:]))
    return out


def all_suggestions(crm: CRM, today: date | None = None) -> list[Reminder]:
    today = today or crm.today()
    return deadline_reminders(crm, today) + application_followups(crm, today) + contact_followups(crm, today)


def _sent(crm: CRM, key: str) -> bool:
    return crm.s.execute(select(CRMReminder.id).where(CRMReminder.dedupe_key == key)).first() is not None


def pending(crm: CRM, today: date | None = None) -> list[Reminder]:
    """Suggestions not delivered yet (a reminder already sent, or silenced by a tighter one, stays quiet)."""
    return [r for r in all_suggestions(crm, today) if not _sent(crm, r.key)]


def mark_sent(crm: CRM, reminders: list[Reminder]) -> None:
    for r in reminders:
        for key in [r.key, *r.covers]:
            if not _sent(crm, key):
                crm.s.add(CRMReminder(dedupe_key=key, kind=r.kind, application_id=r.application.id if r.application else None,
                                      contact_id=r.contact.id if r.contact else None, due_date=r.due))
    crm.s.flush()

