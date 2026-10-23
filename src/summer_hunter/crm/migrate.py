"""One-time import of the applications recorded before the CRM existed (legacy `applications` table)."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from summer_hunter.crm.service import CRM
from summer_hunter.database.models import Application, KeyValue, Opportunity
from summer_hunter.database.repository import as_utc

FLAG = "crm_legacy_migrated"


def migrate_legacy_applications(session: Session, timezone: str = "Europe/Paris") -> int:
    """Each legacy row was an explicit "Applied" click: it becomes an APPLIED record with its real date.

    Idempotent and cheap, so it runs at every start: a process still running the previous code can
    write a legacy row after the first migration, and that row must not be lost.
    """
    crm = CRM(session, None, timezone)
    moved = 0
    for legacy in session.execute(select(Application).order_by(Application.id)).scalars():
        opp = session.get(Opportunity, legacy.opportunity_id)
        if opp is None or crm.application_for(opp.id):
            continue
        app, _ = crm.track(opp, "migration")
        applied_on = as_utc(legacy.applied_at).astimezone(ZoneInfo(timezone)).date() if legacy.applied_at else crm.today()
        crm.update_fields(app, {"date_applied": applied_on, "notes": legacy.notes}, "migration")
        crm.set_status(app, "APPLIED", "migration", notes="recorded by the Applied button before the CRM existed")
        moved += 1
    if moved:
        row = session.get(KeyValue, FLAG)
        if row is None:
            session.add(KeyValue(key=FLAG, value=str(moved)))
        else:
            row.value = str(int(row.value) + moved)
    session.flush()
    return moved

