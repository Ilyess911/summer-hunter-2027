"""Mac <-> hosted synchronization. The Mac is the source of truth.

1. pull: the Mac fetches edits made online (sync_changes), applies them through the same services
   the local app uses, and acknowledges them;
2. push: the Mac uploads a snapshot of the read models (tables below) in chunks; the hosted side
   replaces each table's rows. The hosted app keeps working on the last snapshot when the Mac is off.
Transport: HTTPS + Bearer SH_SYNC_TOKEN. Nothing is ever pushed from the hosted side to anything else.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import Date, DateTime, delete, select
from sqlalchemy.orm import Session, sessionmaker

from summer_hunter.database import models as m
from summer_hunter.utils.settings import Settings

log = logging.getLogger(__name__)
TABLES = [m.Company, m.Opportunity, m.SourceRecord, m.EligibilityEvaluation, m.FitEvaluation, m.Contact, m.CRMApplication,
          m.CRMContact, m.CRMEvent, m.CRMReminder, m.ContactEvidence, m.ContactOpportunity, m.OutreachMessage, m.Interaction,
          m.RecruitmentSignal, m.CollectionRun, m.SourceHealth, m.FxRate, m.UserFeedback, m.LinkedInPost, m.LLMUsage]
BY_NAME = {t.__tablename__: t for t in TABLES}
CHUNK = 400


def _plain(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (datetime, date)) else v


def _rows(session: Session, model) -> list[dict[str, Any]]:  # noqa: ANN001
    cols = [c.name for c in model.__table__.columns]
    stmt = select(model)
    if model is m.FitEvaluation or model is m.EligibilityEvaluation:     # latest evaluation per opportunity only
        from sqlalchemy import func

        latest = select(func.max(model.id)).group_by(model.opportunity_id)
        stmt = stmt.where(model.id.in_(latest))
    if model is m.CollectionRun:
        stmt = stmt.order_by(model.id.desc()).limit(50)
    return [{c: _plain(getattr(r, c)) for c in cols} for r in session.execute(stmt).scalars()]


def snapshot(session: Session) -> dict[str, list[dict[str, Any]]]:
    return {t.__tablename__: _rows(session, t) for t in TABLES}


def begin_snapshot(session: Session) -> None:
    """Empty the synchronized tables, children first (foreign keys). The outbox (sync_changes) is never touched."""
    for model in reversed(TABLES):
        session.execute(delete(model))
    session.flush()


def finish_snapshot(session: Session) -> None:
    """Postgres: move id sequences past the imported ids so rows created online do not collide."""
    if session.get_bind().dialect.name != "postgresql":
        return
    from sqlalchemy import text

    for model in TABLES:
        name = model.__tablename__
        session.execute(text(f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), COALESCE((SELECT MAX(id) FROM {name}), 1))"))


def apply_snapshot_part(session: Session, table: str, rows: list[dict[str, Any]], replace: bool = False) -> int:
    model = BY_NAME.get(table)
    if model is None:
        raise ValueError(f"table {table!r} is not synchronized")
    cols = {c.name: c for c in model.__table__.columns}
    if replace:
        session.execute(delete(model))
    for row in rows:
        clean = {}
        for k, v in row.items():
            col = cols.get(k)
            if col is None:
                continue
            if v is not None and isinstance(col.type, DateTime):
                v = datetime.fromisoformat(v)
            elif v is not None and isinstance(col.type, Date):
                v = date.fromisoformat(v)
            clean[k] = v
        session.add(model(**clean))
    session.flush()
    return len(rows)


def apply_change(session: Session, settings: Settings, change: dict[str, Any]) -> None:
    """Replay one online edit locally, through the same services (validation included)."""
    from summer_hunter.crm.service import CRM
    from summer_hunter.crm.validation import parse_date, parse_datetime
    from summer_hunter.database.repository import Repository
    from summer_hunter.os import networking as net

    kind, p = change["kind"], change["payload"]
    crm = CRM(session, settings.data_dir, settings.timezone)
    if kind == "track":
        crm.track(session.get(m.Opportunity, p["opportunity_id"]), "sync")
    elif kind == "status":
        a = crm.application(p["application_id"])
        if p["status"] == "APPLIED" or (p["status"] in {"ONLINE_ASSESSMENT", "INTERVIEW", "FINAL_INTERVIEW", "OFFER_RECEIVED"}
                                        and a.date_applied is None):
            if p.get("confirm_submission"):
                crm.confirm_applied(a.opportunity, "sync", parse_date(p["applied_on"]) if p.get("applied_on") else None)
        if p["status"] != a.status:
            crm.set_status(a, p["status"], "sync")
    elif kind == "interview":
        a = crm.application(p["application_id"])
        crm.record_interview(a, parse_datetime(p["at"], settings.timezone), "sync", final=p.get("final", False))
    elif kind == "patch":
        a = crm.application(p["application_id"])
        fields = {k: (parse_date(v) if k in {"next_action_date", "deadline"} and v else v) for k, v in p.items()
                  if k in {"notes", "next_action", "next_action_date", "deadline"}}
        crm.update_fields(a, fields, "sync")
    elif kind == "add_contact":
        net.add_user_contact(session, p["company"], p["name"], p.get("position"), p.get("linkedin_url"), p.get("category", "other"),
                             p.get("opportunity_id"), p.get("note"))
    elif kind in {"confirm_contact", "dismiss_contact"}:
        c = session.get(m.CRMContact, p["contact_id"])
        if c and kind == "confirm_contact":
            net.confirm_contact(session, c)
        elif c:
            c.dismissed = True
    elif kind == "message_sent":
        c = session.get(m.CRMContact, p["contact_id"])
        msg = m.OutreachMessage(contact_id=c.id, opportunity_id=p.get("opportunity_id"), kind=p["kind"], text=p["text"],
                                generator="hosted", status="sent", sent_at=datetime.fromisoformat(change["created"]))
        session.add(msg)
        session.flush()
        net.record_interaction(session, c, "message_sent", p.get("opportunity_id"), note=p["kind"], message_id=msg.id)
    elif kind == "interaction":
        c = session.get(m.CRMContact, p["contact_id"])
        net.record_interaction(session, c, p["kind"], p.get("opportunity_id"), p.get("note"),
                               follow_up_on=parse_date(p["follow_up_on"]) if p.get("follow_up_on") else None)
    elif kind == "action_done":
        Repository(session).set_kv(f"action_done:{p['key'][:180]}", change["created"])
    elif kind == "mode":
        from summer_hunter.engine import modes

        modes.set_mode(Repository(session), p["mode"])
    elif kind == "dismiss":
        o = session.get(m.Opportunity, p["opportunity_id"])
        Repository(session).record_feedback(o, "dismiss", source="sync", data_dir=settings.data_dir, timezone=settings.timezone)
    # "message" drafts and "generate_document" requests are informational: drafts are regenerated locally on demand.
    session.flush()


def run(settings: Settings, sessions: sessionmaker[Session], url: str, token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """pull -> apply -> ack -> push. Each pulled change is applied in its own savepoint; failures are reported, not hidden."""
    own = client is None
    client = client or httpx.Client(timeout=60)
    headers = {"Authorization": f"Bearer {token}"}
    base = url.rstrip("/")
    out: dict[str, Any] = {"applied": 0, "failed": [], "pushed": {}}
    try:
        changes = client.get(f"{base}/api/sync/changes", headers=headers).raise_for_status().json()
        done = []
        with sessions() as s:
            for ch in changes:
                try:
                    with s.begin_nested():
                        apply_change(s, settings, ch)
                    done.append(ch["id"])
                except Exception as exc:  # noqa: BLE001 - one bad change must not block the others
                    out["failed"].append({"id": ch["id"], "kind": ch["kind"], "error": f"{type(exc).__name__}: {exc}"[:200]})
            s.commit()
        if done:
            client.post(f"{base}/api/sync/ack", headers=headers, json=done).raise_for_status()
        out["applied"] = len(done)
        with sessions() as s:
            snap = snapshot(s)
        client.post(f"{base}/api/sync/begin", headers=headers).raise_for_status()
        for table, rows in snap.items():
            for part, start in enumerate(range(0, max(len(rows), 1), CHUNK)):
                chunk = rows[start:start + CHUNK]
                body = json.loads(json.dumps(chunk, default=str))
                client.post(f"{base}/api/sync/push/{table}", params={"part": part}, headers=headers, json=body).raise_for_status()
            out["pushed"][table] = len(rows)
        client.post(f"{base}/api/sync/done", headers=headers).raise_for_status()
    finally:
        if own:
            client.close()
    log.info("os sync done", extra={"applied": out["applied"], "failed": len(out["failed"])})
    return out
