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
from sqlalchemy import Date, DateTime, Integer, delete, select
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
        pk = list(model.__table__.primary_key.columns)
        if len(pk) != 1 or not isinstance(pk[0].type, Integer):       # natural keys (e.g. source_health) have no sequence
            continue
        name, col = model.__tablename__, pk[0].name
        session.execute(text(f"SELECT setval(pg_get_serial_sequence('{name}', '{col}'), "
                             f"COALESCE((SELECT MAX({col}) FROM {name}), 1), (SELECT MAX({col}) FROM {name}) IS NOT NULL)"))


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


PROTOCOL = 2
INFORMATIONAL = {"generate_document"}          # queued by older hosted versions; nothing to replay


class SyncError(RuntimeError):
    pass


def _resolve(session: Session, origin: str, gen: int, entity: str, hosted_id: int | None) -> int | None:
    """An id seen online: either a row from the last snapshot (same id on the Mac) or a row created online in this
    snapshot generation, whose creation was replayed first and mapped to the Mac's own id."""
    if hosted_id is None:
        return None
    row = session.get(m.SyncIdMap, (origin, gen, entity, hosted_id))
    return row.local_id if row else hosted_id


def _map(session: Session, origin: str, gen: int, entity: str, hosted_id: int, local_id: int) -> None:
    session.merge(m.SyncIdMap(origin=origin, generation=gen, entity=entity, hosted_id=hosted_id, local_id=local_id))


def _need(obj, what: str, ident) -> Any:  # noqa: ANN001
    if obj is None:
        raise SyncError(f"{what} {ident} not found on the Mac")
    return obj


def apply_change(session: Session, settings: Settings, net_cfg: dict[str, Any], origin: str, change: dict[str, Any]) -> list[dict]:
    """Replay one online edit through the same commands as the API. Returns the conflicts it detected."""
    from summer_hunter.crm.service import CRM
    from summer_hunter.os import commands as cmd

    kind, p = change["kind"], dict(change["payload"])
    gen = int(p.pop("_gen", 0))
    ctx = cmd.Ctx(session, settings, net_cfg, "sync")
    contact = lambda key="contact_id": _need(session.get(m.CRMContact, _resolve(session, origin, gen, "contact", p.get(key))),  # noqa: E731
                                             "contact", p.get(key))
    message = lambda: _need(session.get(m.OutreachMessage, _resolve(session, origin, gen, "message", p.get("message_id"))),  # noqa: E731
                            "message", p.get("message_id"))
    opportunity = lambda: _need(session.get(m.Opportunity, p.get("opportunity_id")), "opportunity", p.get("opportunity_id"))  # noqa: E731

    def application():  # noqa: ANN202
        return _need(CRM(session).application_for(p["opportunity_id"]), "application for opportunity", p["opportunity_id"])

    if kind == "track":
        cmd.track(ctx, opportunity())
    elif kind == "dismiss":
        cmd.dismiss(ctx, opportunity(), p.get("reason"))
    elif kind == "status":
        cmd.set_status(ctx, application(), p["status"], p.get("confirm_submission", False), p.get("applied_on"), p.get("base_status"))
    elif kind == "interview":
        cmd.interview(ctx, application(), p["at"], p.get("final", False))
    elif kind == "patch":
        cmd.patch(ctx, application(), p.get("fields", {}), p.get("base", {}))
    elif kind == "add_contact":
        c, _ = cmd.add_contact(ctx, p)
        _map(session, origin, gen, "contact", p["created_id"], c.id)
    elif kind == "confirm_contact":
        cmd.confirm_contact(ctx, contact())
    elif kind == "dismiss_contact":
        cmd.dismiss_contact(ctx, contact())
    elif kind == "message":
        c = contact()
        msg = m.OutreachMessage(contact_id=c.id, opportunity_id=p.get("opportunity_id"), kind=p["kind"], variant=p.get("variant", "default"),
                                text=p["text"], generator=p.get("generator") or "template", evidence_refs=p.get("evidence_refs"),
                                validation=p.get("validation"), personalization=p.get("personalization"))
        cmd.save_message(ctx, msg)
        _map(session, origin, gen, "message", p["created_id"], msg.id)
    elif kind == "message_sent":
        cmd.mark_sent(ctx, message(), datetime.fromisoformat(p["at"]) if p.get("at") else None)
    elif kind == "message_dismiss":
        cmd.dismiss_message(ctx, message())
    elif kind == "interaction":
        cmd.interaction(ctx, contact(), p["kind"], p.get("opportunity_id"), p.get("note"), p.get("follow_up_on"))
    elif kind == "action_done":
        cmd.action_done(ctx, p["key"], change.get("created"))
    elif kind == "mode":
        cmd.set_mode(ctx, p["mode"])
    elif kind not in INFORMATIONAL:
        raise SyncError(f"unknown change kind {kind!r}: update the Mac before syncing")
    session.flush()
    return ctx.conflicts


def _check_url(url: str) -> str:
    """The token and the whole CRM travel to this URL: HTTPS only (plain HTTP allowed for loopback tests)."""
    parsed = httpx.URL(url)
    if parsed.scheme != "https" and parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SyncError("SH_OS_HOSTED_URL must use https://")
    return url.rstrip("/")


def _pull(settings: Settings, sessions: sessionmaker[Session], client: httpx.Client, base: str, headers: dict, out: dict) -> bool:
    """Replay pending online edits in order; stop at the first failure (later edits may depend on it)."""
    from summer_hunter.utils.settings import load_yaml

    data = client.get(f"{base}/api/sync/changes", headers=headers).raise_for_status().json()
    if not isinstance(data, dict) or data.get("protocol") != PROTOCOL:
        raise SyncError("the hosted app runs another sync protocol: deploy the same version on both sides")
    origin, done = data["origin"], []
    net_cfg = load_yaml(settings.config_dir / "networking.yaml")
    with sessions() as s:
        for ch in data["changes"]:
            if s.get(m.SyncApplied, (origin, ch["id"])):          # replayed before, the acknowledgement was lost
                done.append(ch["id"])
                continue
            try:
                with s.begin_nested():
                    out["conflicts"] += apply_change(s, settings, net_cfg, origin, ch)
                    s.add(m.SyncApplied(origin=origin, change_id=ch["id"]))
                done.append(ch["id"])
                out["applied"] += 1
            except Exception as exc:  # noqa: BLE001 - reported, and the queue stops here
                out["failed"].append({"id": ch["id"], "kind": ch["kind"], "error": f"{type(exc).__name__}: {exc}"[:200]})
                break
        s.commit()
    if done:
        client.post(f"{base}/api/sync/ack", headers=headers, json=done).raise_for_status()
    return not out["failed"]


def _push(sessions: sessionmaker[Session], client: httpx.Client, base: str, headers: dict, out: dict) -> bool:
    """Upload a snapshot to staging, then ask for the atomic swap. False when an online edit arrived meanwhile."""
    with sessions() as s:
        snap = snapshot(s)
    run_id = client.post(f"{base}/api/sync/begin", headers=headers).raise_for_status().json()["run_id"]
    for table, rows in snap.items():
        for part, start in enumerate(range(0, max(len(rows), 1), CHUNK)):
            body = json.loads(json.dumps(rows[start:start + CHUNK], default=str))
            client.post(f"{base}/api/sync/push/{table}", params={"run_id": run_id, "part": part}, headers=headers,
                        json=body).raise_for_status()
        out["pushed"][table] = len(rows)
    r = client.post(f"{base}/api/sync/done", params={"run_id": run_id}, headers=headers)
    if r.status_code == 409:
        return False
    r.raise_for_status()
    return True


def _remember_conflicts(sessions: sessionmaker[Session], conflicts: list[dict]) -> None:
    from summer_hunter.database.repository import Repository

    if not conflicts:
        return
    with sessions() as s:
        repo = Repository(s)
        try:
            kept = json.loads(repo.get_kv("sync_conflicts") or "[]")
        except ValueError:
            kept = []
        stamp = datetime.now().isoformat(timespec="seconds")
        repo.set_kv("sync_conflicts", json.dumps((kept + [{**c, "at": stamp} for c in conflicts])[-50:], default=str))
        s.commit()


def run(settings: Settings, sessions: sessionmaker[Session], url: str, token: str, client: httpx.Client | None = None,
        attempts: int = 3) -> dict[str, Any]:
    """pull -> replay -> ack -> push to staging -> atomic swap. When online edits arrive during the upload the swap is
    refused and the cycle starts again, so the hosted copy never drops an edit. Failures stop the push and are reported."""
    base = _check_url(url)
    own = client is None
    client = client or httpx.Client(timeout=60)
    headers = {"Authorization": f"Bearer {token}"}
    out: dict[str, Any] = {"applied": 0, "failed": [], "conflicts": [], "pushed": {}, "swapped": False}
    try:
        for _ in range(attempts):
            if not _pull(settings, sessions, client, base, headers, out):
                break
            if _push(sessions, client, base, headers, out):
                out["swapped"] = True
                break
        else:
            out["failed"].append({"id": None, "kind": "swap", "error": f"online edits kept arriving during {attempts} uploads; "
                                                                        "the hosted copy keeps its previous snapshot"})
    finally:
        if own:
            client.close()
        _remember_conflicts(sessions, out["conflicts"])
    log.info("os sync done", extra={"applied": out["applied"], "failed": len(out["failed"]), "swapped": out["swapped"]})
    return out
