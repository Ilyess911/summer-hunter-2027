"""CRM part of the daily Telegram briefing: real counts and the actions that need you today."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select

from summer_hunter.crm import followups
from summer_hunter.crm import statuses as st
from summer_hunter.crm.service import CRM
from summer_hunter.crm.validation import app_code
from summer_hunter.database.models import CRMReminder
from summer_hunter.database.repository import as_utc

MAX_ACTIONS = 12


@dataclass
class CRMSection:
    lines: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    buttons: list[list[dict[str, str]]] = field(default_factory=list)
    reminders: list[followups.Reminder] = field(default_factory=list)


def _letter_ready(crm: CRM, today: date) -> list[followups.Reminder]:
    out = []
    for app in crm.applications({"TO_APPLY", "PREPARING"}):
        if app.letter_version and app.letter_hash:
            key = f"letter_ready:{app.id}:{app.letter_hash[:16]}"
            if not crm.s.execute(select(CRMReminder.id).where(CRMReminder.dedupe_key == key)).first():
                out.append(followups.Reminder("letter_ready", key, f"{crm.label(app)} : lettre v{app.letter_version} prête à relire",
                                              today, application=app))
    return out


def section(crm: CRM, today: date | None = None) -> CRMSection:
    today = today or crm.today()
    out = CRMSection()
    apps = crm.applications()
    active = [a for a in apps if a.status not in st.TERMINAL]
    if not apps:
        out.lines = ["Aucune candidature suivie pour l'instant (bouton Track sur une offre)."]
        return out
    awaiting = sum(a.status == "APPLIED" for a in apps)
    soon = [a for a in apps if a.interview_at and today <= crm.local_date(a.interview_at) <= today + timedelta(days=14)
            and a.status not in st.TERMINAL - {"OFFER_ACCEPTED"}]
    to_prepare = sum(a.status in {"TO_APPLY", "PREPARING"} for a in apps)
    offers = sum(a.status == "OFFER_RECEIVED" for a in apps)
    out.lines = [f"• {len(active)} candidature(s) active(s)", f"• {awaiting} en attente de réponse",
                 f"• {len(soon)} entretien(s) dans les 14 jours", f"• {to_prepare} à préparer"]
    if offers:
        out.lines.append(f"• {offers} offre(s) reçue(s) à décider")

    reminders = followups.pending(crm, today) + _letter_ready(crm, today)
    actions: list[tuple[str, int | None]] = []
    for a in sorted(soon, key=lambda x: as_utc(x.interview_at)):
        actions.append((f"{crm.label(a)} : entretien le {as_utc(a.interview_at).astimezone(ZoneInfo(crm.tz)).strftime('%d/%m %H:%M')}", a.id))
    for a in active:
        if a.next_action_date and a.next_action_date < today:
            actions.append((f"{crm.label(a)} : action en retard ({a.next_action or 'sans libellé'}, prévue le "
                            f"{a.next_action_date:%d/%m})", a.id))
    room = max(0, MAX_ACTIONS - len(actions))
    out.reminders = reminders[:room]          # only what is actually shown counts as delivered
    actions += [(r.text, r.application.id if r.application else None) for r in out.reminders]
    out.actions = [escape(text) for text, _ in actions[:MAX_ACTIONS]]
    seen: list[int] = []
    for _, app_id in actions:
        if app_id and app_id not in seen:
            seen.append(app_id)
    out.buttons = [[{"text": f"📂 {app_code(i)}", "callback_data": f"ca:{i}"} for i in seen[k:k + 3]]
                   for k in range(0, min(len(seen), 6), 3)]
    return out
