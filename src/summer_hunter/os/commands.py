"""Write commands shared by the API and by the replay of online edits on the Mac.

Both paths call the same function, so an edit made in the hosted app has exactly the same durable effects
(feedback, events, interactions, networking refresh) once the Mac replays it. Each command returns the
payload to queue in hosted mode: stable references (opportunity ids come from the Mac), the id of any row
created online, and the values the edit was based on, so the replay can detect a newer change on the Mac.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from summer_hunter.crm import statuses as st
from summer_hunter.crm.service import CRM
from summer_hunter.crm.validation import ValidationError, parse_date, parse_datetime
from summer_hunter.database.models import CRMApplication, CRMContact, Opportunity, OutreachMessage, utcnow
from summer_hunter.database.repository import Repository
from summer_hunter.os import networking as net
from summer_hunter.utils.settings import Settings

PATCH_FIELDS = ("notes", "next_action", "next_action_date", "deadline")
DATE_FIELDS = {"next_action_date", "deadline"}


class NeedsConfirmation(Exception):
    """A submission is never inferred: moving past APPLIED needs an explicit confirmation."""


@dataclass
class Ctx:
    session: Session
    settings: Settings
    net_cfg: dict[str, Any]
    source: str                                  # "web" (API) or "sync" (replay on the Mac)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def crm(self) -> CRM:
        return CRM(self.session, self.settings.data_dir, self.settings.timezone)

    @property
    def repo(self) -> Repository:
        return Repository(self.session)


def _text(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


# Opportunities and applications ---------------------------------------------------------------------------
def action_done(ctx: Ctx, key: str, at: str | None = None) -> dict[str, Any]:
    ctx.repo.set_kv(f"action_done:{key[:180]}", at or utcnow().isoformat())
    return {"key": key}


def track(ctx: Ctx, opp: Opportunity) -> tuple[CRMApplication, bool, dict[str, Any]]:
    app, created = ctx.crm.track(opp, ctx.source)
    return app, created, {"opportunity_id": opp.id}


def dismiss(ctx: Ctx, opp: Opportunity, reason: str | None) -> dict[str, Any]:
    from summer_hunter.engine.feedback import DISMISS_REASONS, recompute

    ctx.repo.record_feedback(opp, "dismiss", source=ctx.source, data_dir=ctx.settings.data_dir, timezone=ctx.settings.timezone)
    if reason in DISMISS_REASONS:
        ctx.repo.record_feedback(opp, "reason", reason)
    recompute(ctx.repo, ctx.settings.scoring)
    return {"opportunity_id": opp.id, "reason": reason}


def set_status(ctx: Ctx, app: CRMApplication, target: str, confirm_submission: bool, applied_on: str | None,
               base_status: str | None = None) -> dict[str, Any]:
    """base_status: the status the online edit started from (replay only); a different local status is a conflict."""
    target = target.strip().upper()
    base = app.status
    if base_status is not None and base_status != app.status and target != app.status:
        ctx.conflicts.append({"entity": "application", "opportunity_id": app.opportunity_id, "field": "status",
                              "online": target, "online_base": base_status, "local": app.status})
        return {}
    if target == "APPLIED" or (target in st.POST_APPLICATION and app.date_applied is None):
        if not confirm_submission:
            raise NeedsConfirmation("confirme que la candidature a bien été envoyée")
        day = parse_date(applied_on, "applied on") if applied_on else None
        ctx.crm.confirm_applied(app.opportunity, ctx.source, day)
        ctx.repo.record_feedback(app.opportunity, "applied", source=ctx.source, data_dir=ctx.settings.data_dir,
                                 timezone=ctx.settings.timezone)
    if target != app.status:
        ctx.crm.set_status(app, target, ctx.source)
    net.recompute(ctx.session, ctx.net_cfg, app.opportunity_id)
    return {"opportunity_id": app.opportunity_id, "status": target, "applied_on": applied_on,
            "confirm_submission": confirm_submission, "base_status": base}


def interview(ctx: Ctx, app: CRMApplication, at: str, final: bool) -> dict[str, Any]:
    ctx.crm.record_interview(app, parse_datetime(at, ctx.settings.timezone, "interview"), ctx.source, final=final)
    net.recompute(ctx.session, ctx.net_cfg, app.opportunity_id)
    return {"opportunity_id": app.opportunity_id, "at": at, "final": final}


def patch(ctx: Ctx, app: CRMApplication, sent: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """base: field values the online edit started from (replay only). A field changed on the Mac since then is
    kept and reported as a conflict instead of being overwritten by an older online edit."""
    current = {k: _text(getattr(app, k)) for k in PATCH_FIELDS}
    changes: dict[str, Any] = {}
    for k in PATCH_FIELDS:
        if k not in sent:
            continue
        new = sent[k] or None
        if base is not None and k in base and current[k] != base[k] and current[k] != _text(new):
            ctx.conflicts.append({"entity": "application", "opportunity_id": app.opportunity_id, "field": k,
                                  "online": new, "online_base": base[k], "local": current[k]})
            continue
        changes[k] = parse_date(new, k) if k in DATE_FIELDS and new else new
    if changes:
        ctx.crm.update_fields(app, changes, ctx.source)
    return {"opportunity_id": app.opportunity_id, "fields": {k: sent[k] for k in PATCH_FIELDS if k in sent},
            "base": {k: current[k] for k in PATCH_FIELDS if k in sent}}


# Contacts and outreach ------------------------------------------------------------------------------------------
def add_contact(ctx: Ctx, fields: dict[str, Any]) -> tuple[CRMContact, dict[str, Any]]:
    c = net.add_user_contact(ctx.session, fields["company"], fields["name"], fields.get("position"), fields.get("linkedin_url"),
                             fields.get("category", "other"), fields.get("opportunity_id"), fields.get("note"))
    if fields.get("opportunity_id"):
        net.recompute(ctx.session, ctx.net_cfg, fields["opportunity_id"])
    keep = ("company", "name", "position", "linkedin_url", "category", "opportunity_id", "note")
    return c, {**{k: fields.get(k) for k in keep}, "created_id": c.id}


def confirm_contact(ctx: Ctx, c: CRMContact) -> dict[str, Any]:
    net.confirm_contact(ctx.session, c)
    net.recompute(ctx.session, ctx.net_cfg)
    return {"contact_id": c.id}


def dismiss_contact(ctx: Ctx, c: CRMContact) -> dict[str, Any]:
    c.dismissed = True
    net.recompute(ctx.session, ctx.net_cfg)
    return {"contact_id": c.id}


def save_message(ctx: Ctx, m: OutreachMessage) -> dict[str, Any]:
    """A draft (generated or a variant). Queued with its full text so the Mac keeps the exact wording."""
    ctx.session.add(m)
    ctx.session.flush()
    return {"created_id": m.id, "contact_id": m.contact_id, "opportunity_id": m.opportunity_id, "kind": m.kind,
            "variant": m.variant, "text": m.text, "generator": m.generator, "evidence_refs": m.evidence_refs,
            "validation": m.validation, "personalization": m.personalization}


def mark_sent(ctx: Ctx, m: OutreachMessage, at: datetime | None = None) -> dict[str, Any]:
    if m.status != "sent":
        c = ctx.session.get(CRMContact, m.contact_id)
        m.status, m.sent_at = "sent", at or utcnow()
        net.record_interaction(ctx.session, c, "message_sent", m.opportunity_id, note=f"{m.kind} ({m.variant})", message_id=m.id)
        net.recompute(ctx.session, ctx.net_cfg)
    return {"message_id": m.id, "at": m.sent_at.isoformat() if m.sent_at else None}


def dismiss_message(ctx: Ctx, m: OutreachMessage) -> dict[str, Any]:
    if m.status != "sent":
        m.status = "dismissed"
    return {"message_id": m.id}


def interaction(ctx: Ctx, c: CRMContact, kind: str, opportunity_id: int | None, note: str | None,
                follow_up_on: str | None) -> tuple[Any, dict[str, Any]]:
    when = parse_date(follow_up_on, "follow-up date") if follow_up_on else None
    if kind == "follow_up_scheduled" and when is None:
        raise ValidationError("une relance prévue a besoin d’une date")
    i = net.record_interaction(ctx.session, c, kind, opportunity_id, note, follow_up_on=when)
    net.recompute(ctx.session, ctx.net_cfg)
    return i, {"contact_id": c.id, "kind": kind, "opportunity_id": opportunity_id, "note": note, "follow_up_on": follow_up_on}


def set_mode(ctx: Ctx, mode: str) -> dict[str, Any]:
    from summer_hunter.engine import modes

    modes.set_mode(ctx.repo, mode)
    return {"mode": mode}
