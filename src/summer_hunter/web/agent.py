"""Agent API: lets an external mail agent (Instinct) read active applications and report recruiter replies.

Exposed through the same tunnel as the Mini App, under /agent/v1/, outside the Telegram auth.
Disabled unless SH_AGENT_TOKEN is set (Bearer token, 32+ characters, compared in constant time).

The agent proposes, the rules decide. A reported status is applied only when it passes the same
checks as the built-in mailbox reader (crm.mail_status): known application already submitted,
forward-only move, offers and final statuses untouched, mail received on or after the application
date and its last status change, the company named in the sender, subject or quoted evidence, and
one change per message id. Every applied change is announced on Telegram.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from html import escape

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from summer_hunter.crm import mail_status as ms
from summer_hunter.crm import statuses as st
from summer_hunter.crm.service import CRM
from summer_hunter.crm.validation import ValidationError, app_code, parse_app_id
from summer_hunter.database.models import CRMEvent

log = logging.getLogger(__name__)

SOURCE = "agent"
ALLOWED = {ms.REJECTED, ms.CLOSED, ms.INTERVIEW, ms.ASSESSMENT}
MAX_BODY = 32_000
RATE_PER_MIN = 60           # authenticated calls
FAIL_PER_MIN = 20           # failed authentications (never counted against the valid client)
CLOCK_SKEW = timedelta(minutes=10)
_WRITE = threading.Lock()   # one status report at a time: check and update see the same state
RULES = ("Report only replies about an application listed here, with its id. Allowed statuses: REJECTED, CLOSED, "
         "INTERVIEW, ONLINE_ASSESSMENT. Send the mail's Message-ID, its received time (ISO 8601 with timezone), "
         "sender, subject and the exact sentence that shows the outcome. Do not report acknowledgements or "
         "conditional sentences ('if you are not selected...'). When unsure which application a mail is about, do not report it.")


class _Rate:
    def __init__(self, per_min: int) -> None:
        self.per_min = per_min
        self.hits: list[float] = []
        self.lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self.lock:
            self.hits = [t for t in self.hits if now - t < 60]
            if len(self.hits) >= self.per_min:
                return False
            self.hits.append(now)
            return True


def clean(text: str, limit: int) -> str:
    """Untrusted text for a Telegram notice: no control or bidi characters, one line, no slash commands."""
    text = "".join(c for c in (text or "") if unicodedata.category(c) not in ("Cc", "Cf"))
    text = " ".join(text.split()).replace("/", "∕")
    return escape(text[:limit])


def _token() -> str:
    return (os.environ.get("SH_AGENT_TOKEN") or "").strip()


def _authorized(request: Request) -> bool:
    token = _token()
    if len(token) < 32:
        return False
    scheme, _, given = request.headers.get("authorization", "").strip().partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(given.strip().encode(), token.encode())


def routes(settings, sessions: sessionmaker[Session], telegram=None) -> list[Route]:  # noqa: ANN001
    rate, failures = _Rate(RATE_PER_MIN), _Rate(FAIL_PER_MIN)

    def guard(request: Request) -> Response | None:
        if len(_token()) < 32:
            return JSONResponse({"error": "agent API disabled"}, status_code=404)
        if not _authorized(request):
            sent = request.headers.get("authorization", "").strip()
            log.warning("agent auth refused", extra={"path": request.url.path, "scheme_ok": sent.lower().startswith("bearer "),
                                                     "token_len": len(sent.partition(" ")[2].strip()), "expected_len": len(_token())})
            if not failures.allow():
                return JSONResponse({"error": "rate limited"}, status_code=429)
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not rate.allow():
            return JSONResponse({"error": "rate limited"}, status_code=429)
        return None

    def reply(payload: dict, status_code: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status_code, headers={"Cache-Control": "no-store"})

    def notify(text: str) -> None:
        if telegram is None or not settings.telegram_chat_id:
            return
        try:
            telegram.send_message(settings.telegram_chat_id, text)
        except Exception:  # noqa: BLE001 - a failed notice never fails the update
            log.warning("agent notice not sent")

    async def applications(request: Request) -> Response:
        if (denied := guard(request)) is not None:
            return denied
        with sessions() as session:
            crm = CRM(session, settings.data_dir, settings.timezone)
            rows = [{"id": app_code(a.id), "company": a.opportunity.company_name, "title": a.opportunity.title,
                     "status": a.status, "applied_on": a.date_applied.isoformat()}
                    for a in crm.applications()
                    if a.date_applied and a.status in st.POST_APPLICATION and a.status not in st.OFFERS | st.TERMINAL]
        return reply({"applications": rows, "allowed_statuses": sorted(ALLOWED), "rules": RULES})

    def apply_report(data: dict) -> tuple[int, dict, str | None]:
        missing = [k for k in ("application", "status", "message_id", "received_at", "subject", "sender", "evidence")
                   if not isinstance(data.get(k), str) or not data[k].strip()]
        if missing:
            return 422, {"error": f"missing fields: {', '.join(missing)}"}, None
        status = data["status"].strip().upper()
        if status not in ALLOWED:
            return 422, {"error": f"status must be one of {sorted(ALLOWED)}"}, None
        try:
            received = datetime.fromisoformat(data["received_at"].strip().replace("Z", "+00:00"))
        except ValueError:
            return 422, {"error": "received_at must be ISO 8601"}, None
        if received.tzinfo is None:
            return 422, {"error": "received_at needs a timezone"}, None
        try:
            app_id = parse_app_id(data["application"])
        except ValidationError as exc:
            return 422, {"error": str(exc)}, None
        received = received.astimezone(timezone.utc)
        if received > datetime.now(timezone.utc) + CLOCK_SKEW:
            return 422, {"error": "received_at is in the future"}, None
        evidence = data["evidence"][:2000]
        item = ms.MailItem(data["message_id"].strip()[:500], data["sender"][:300], data["subject"][:500], evidence, received)
        seen = ms.classify(item.subject, evidence)
        if seen != status:
            log.info("agent evidence not recognised", extra={"wanted": status, "seen": seen, "subject": item.subject[:200],
                                                             "evidence": evidence[:600]})
            return 409, {"changed": False, "reason": "evidence does not clearly state this outcome"}, (
                f"🤖 Instinct propose {ms.KIND_FR[status]} pour {escape(data['application'][:12])}, non appliqué "
                f"(phrase non reconnue). Objet : « {clean(item.subject, 90)} »\nExtrait : « {clean(evidence, 200)} »\n"
                f"Si c'est juste : /setstatus {clean(data['application'], 12)} {status}")
        with _WRITE, sessions() as session:
            crm = CRM(session, settings.data_dir, settings.timezone)
            app = crm.application(app_id)
            if app is None or app.date_applied is None:
                return 404, {"error": "unknown or not submitted application"}, None
            label = crm.label(app)
            key = ms.dedupe_key(item)
            if session.execute(select(CRMEvent.id).where(CRMEvent.dedupe_key == key)).first():
                return 200, {"changed": False, "reason": "message already applied"}, None
            apps = [a for a in crm.applications() if a.date_applied is not None]
            found, _, why = ms.match(apps, item)
            if found is None or found.id != app.id:
                why = why or "the mail points to another application"
                return 409, {"changed": False, "reason": why}, (
                    f"🤖 Instinct propose {ms.KIND_FR[status]} pour {escape(label)}, non appliqué ({escape(why)}). "
                    f"Objet du mail : « {clean(item.subject, 90)} ». Si c'est juste : /setstatus {app_code(app.id)} {status}")
            if not ms._forward(app, status):
                return 200, {"changed": False, "reason": f"not a forward move from {app.status}"}, None
            last = ms._last_status_change(crm, app)
            if ms._local_day(received, crm.tz) < app.date_applied or (last and received < last):
                return 200, {"changed": False, "reason": "mail older than the application or its current status"}, None
            try:
                changed = crm.set_status(app, status, SOURCE, notes=f"Instinct : {item.subject[:180]}", dedupe_key=key)
                session.commit()
            except IntegrityError:
                session.rollback()
                return 200, {"changed": False, "reason": "message already applied"}, None
        text = (f"🤖 Instinct · {ms.KIND_FR[status]} : {escape(label)}\nObjet : « {clean(item.subject, 120)} »\n"
                f"Extrait : « {clean(evidence, 240)} »") if changed else None
        return 200, {"changed": changed, "application": app_code(app_id), "status": status}, text

    async def report(request: Request) -> Response:
        if (denied := guard(request)) is not None:
            return denied
        try:
            if int(request.headers.get("content-length") or 0) > MAX_BODY:
                return reply({"error": "body too large"}, 413)
        except ValueError:
            return reply({"error": "bad content-length"}, 400)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                return reply({"error": "body too large"}, 413)
        try:
            data = json.loads(body)
        except ValueError:
            return reply({"error": "invalid JSON"}, 400)
        if not isinstance(data, dict):
            return reply({"error": "expected a JSON object"}, 400)
        code, payload, notice = await asyncio.to_thread(apply_report, data)
        if notice:
            await asyncio.to_thread(notify, notice)
        log.info("agent report", extra={"status": code, "changed": payload.get("changed"), "reason": payload.get("reason"),
                                        "application": str(data.get("application"))[:12]})
        return reply(payload, code)

    return [Route("/agent/v1/applications", applications), Route("/agent/v1/status", report, methods=["POST"])]
