"""Summer Hunter OS API (FastAPI). One process serves the web app in local mode; the same app runs on
Vercel in hosted mode against Postgres, fed by the Mac through /api/sync.

Every route except /api/health, /api/auth/login and /api/sync/* (token) requires the session cookie;
every state-changing route also requires the X-SH-Request header (CSRF guard).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from summer_hunter.crm import statuses as st
from summer_hunter.crm.service import CRM
from summer_hunter.crm.validation import ValidationError
from summer_hunter.database.models import (
    ContactEvidence,
    ContactOpportunity,
    CRMContact,
    Interaction,
    Opportunity,
    OutreachMessage,
    SyncChange,
    utcnow,
)
from summer_hunter.database.repository import Repository
from summer_hunter.engine.regions import REGIONS
from summer_hunter.os import commands as cmd
from summer_hunter.os import messages as msg
from summer_hunter.os import networking as net
from summer_hunter.os import security, views
from summer_hunter.utils.settings import Settings, load_yaml

log = logging.getLogger(__name__)
WRITE_LOCK = 7_243_001
GEN_KEY, ORIGIN_KEY, RUN_KEY = "os_snapshot_gen", "os_origin", "os_sync_run"
SYNC_PROTOCOL = 2


# Request schemas ---------------------------------------------------------------------------------------
class Login(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class StatusChange(BaseModel):
    status: str = Field(max_length=30)
    confirm_submission: bool = False
    applied_on: str | None = Field(default=None, max_length=10)


class InterviewIn(BaseModel):
    at: str = Field(max_length=20)
    final: bool = False


class AppPatch(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)
    next_action: str | None = Field(default=None, max_length=300)
    next_action_date: str | None = Field(default=None, max_length=10)
    deadline: str | None = Field(default=None, max_length=10)


class ContactIn(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=160)
    position: str | None = Field(default=None, max_length=200)
    linkedin_url: str | None = Field(default=None, max_length=500)
    category: str = Field(default="other", max_length=30)
    opportunity_id: int | None = None
    note: str | None = Field(default=None, max_length=600)


class MessageIn(BaseModel):
    opportunity_id: int | None = None
    kind: str | None = Field(default=None, max_length=30)
    variant: str = Field(default="default", max_length=20)


class VariantIn(BaseModel):
    variant: str = Field(max_length=20)


class InteractionIn(BaseModel):
    kind: Literal["reply", "call", "meeting", "note", "follow_up_scheduled"]
    opportunity_id: int | None = None
    note: str | None = Field(default=None, max_length=2000)
    follow_up_on: str | None = Field(default=None, max_length=10)


class DismissIn(BaseModel):
    reason: str | None = Field(default=None, max_length=20)


class PreferencesIn(BaseModel):
    mode: Literal["exploration", "conversion"] | None = None


def create_app(settings: Settings, sessions: sessionmaker[Session], hosted: bool | None = None) -> FastAPI:
    hosted = (os.environ.get("SH_OS_MODE") == "hosted") if hosted is None else hosted
    app = FastAPI(title="Summer Hunter OS", docs_url=None, redoc_url=None, openapi_url=None)
    net_cfg = load_yaml(settings.config_dir / "networking.yaml") if (settings.config_dir / "networking.yaml").exists() else {}

    def db(request: Request):
        with sessions() as s:
            if hosted and request.method not in {"GET", "HEAD", "OPTIONS"} and s.get_bind().dialect.name == "postgresql":
                # One writer at a time online: edits, login attempts and the snapshot swap never interleave.
                s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": WRITE_LOCK})
            yield s

    def ctx(s: Session) -> cmd.Ctx:
        return cmd.Ctx(s, settings, net_cfg, "web")

    def authed(request: Request) -> None:
        if not security.valid_session(request.cookies.get(security.COOKIE)):
            raise HTTPException(401, "not signed in")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get(security.CSRF_HEADER) != "1":
            raise HTTPException(403, "missing X-SH-Request header")

    def local_only(feature: str) -> None:
        if hosted:
            raise HTTPException(409, f"{feature} runs on the Mac only (local mode)")

    def record(s: Session, kind: str, payload: dict[str, Any]) -> None:
        """In hosted mode, every edit is also queued for the Mac, which stays the source of truth. The snapshot
        generation tells the replay which ids were created online since the last snapshot."""
        if hosted:
            gen = int(Repository(s).get_kv(GEN_KEY) or 0)
            s.add(SyncChange(kind=kind, payload={**payload, "_gen": gen}))

    @app.middleware("http")
    async def headers(request: Request, call_next):  # noqa: ANN001
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValidationError)
    async def validation(_: Request, exc: ValidationError):  # noqa: ANN202
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(st.TransitionError)
    async def transition(_: Request, exc: st.TransitionError):  # noqa: ANN202
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # Auth ------------------------------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "mode": "hosted" if hosted else "local"}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response, s: Session = Depends(db)) -> dict[str, Any]:
        client = request.client.host if request.client else "unknown"
        if hosted:  # behind Vercel's edge, which sets X-Forwarded-For; the global cap covers a spoofed value
            client = request.headers.get("x-forwarded-for", client).split(",")[0].strip() or client
        allowed = security.allow_attempt_persistent(s, client)
        if not allowed:
            raise HTTPException(429, "too many attempts, try again in 15 minutes")
        if not security.verify_password(body.password, os.environ.get("SH_OS_PASSWORD_HASH")):
            raise HTTPException(401, "wrong password")
        response.set_cookie(security.COOKIE, security.make_session(), max_age=security.SESSION_SECONDS, httponly=True,
                            samesite="lax", secure=hosted, path="/")
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, Any]:
        if request.headers.get(security.CSRF_HEADER) != "1":
            raise HTTPException(403, "missing X-SH-Request header")
        response.delete_cookie(security.COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me", dependencies=[Depends(authed)])
    def me() -> dict[str, Any]:
        return {"ok": True}

    # Meta, command center, search ---------------------------------------------------------------------
    @app.get("/api/meta", dependencies=[Depends(authed)])
    def meta(s: Session = Depends(db)) -> dict[str, Any]:
        repo = Repository(s)
        return {"mode": "hosted" if hosted else "local", "last_sync": repo.get_kv("os_last_sync"), "last_run": views.last_run(s),
                "sync_conflicts": _conflict_count(repo), "features": {"documents": not hosted, "ai_messages": not hosted, "contact_search": bool(settings.brave_api_key)},
                "recruitment_mode": repo.get_kv("recruitment_mode") or "conversion"}

    @app.get("/api/command-center", dependencies=[Depends(authed)])
    def command_center(s: Session = Depends(db)) -> dict[str, Any]:
        return views.command_center(s)

    @app.post("/api/actions/{key:path}/done", dependencies=[Depends(authed)])
    def action_done(key: str, s: Session = Depends(db)) -> dict[str, Any]:
        record(s, "action_done", cmd.action_done(ctx(s), key))
        s.commit()
        return {"ok": True}

    @app.get("/api/search", dependencies=[Depends(authed)])
    def search(q: str = Query("", max_length=100), s: Session = Depends(db)) -> dict[str, Any]:
        return views.search(s, q)

    @app.get("/api/activity", dependencies=[Depends(authed)])
    def activity(s: Session = Depends(db)) -> list[dict[str, Any]]:
        return views.activity(s)

    # Opportunities -------------------------------------------------------------------------------------
    @app.get("/api/opportunities", dependencies=[Depends(authed)])
    def opportunities(q: str = Query("", max_length=100), region: str | None = None, country: str | None = Query(None, max_length=2),
                      category: str | None = None, track: str | None = None, paid: bool | None = None,
                      min_recruit: int | None = Query(None, ge=0, le=100), eligibility: str | None = None, status: str | None = None,
                      deadline_days: int | None = Query(None, ge=0, le=365), min_monthly: float | None = Query(None, ge=0),
                      sort: str = "priority", order: Literal["asc", "desc"] = "desc", page: int = Query(1, ge=1),
                      page_size: int = Query(50, ge=1, le=200), s: Session = Depends(db)) -> dict[str, Any]:
        return views.list_opportunities(s, q, region, country, category, track, paid, min_recruit, eligibility, status,
                                        deadline_days, min_monthly, sort, order, page, page_size)

    @app.get("/api/opportunities/{opp_id}", dependencies=[Depends(authed)])
    def opportunity(opp_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        data = views.opportunity_detail(s, opp_id)
        if data is None:
            raise HTTPException(404, "opportunity not found")
        return data

    def _opp(s: Session, opp_id: int) -> Opportunity:
        o = s.get(Opportunity, opp_id)
        if o is None:
            raise HTTPException(404, "opportunity not found")
        return o

    @app.post("/api/opportunities/{opp_id}/track", dependencies=[Depends(authed)])
    def track(opp_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        app_, created, payload = cmd.track(ctx(s), _opp(s, opp_id))
        record(s, "track", payload)
        s.commit()
        return {"application_id": app_.id, "created": created}

    @app.post("/api/opportunities/{opp_id}/dismiss", dependencies=[Depends(authed)])
    def dismiss(opp_id: int, body: DismissIn, s: Session = Depends(db)) -> dict[str, Any]:
        record(s, "dismiss", cmd.dismiss(ctx(s), _opp(s, opp_id), body.reason))
        s.commit()
        return {"ok": True}

    @app.post("/api/opportunities/{opp_id}/contacts/search", dependencies=[Depends(authed)])
    def contact_search(opp_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        """Public profile search (Brave API). Results stay UNVERIFIED until you confirm them."""
        from summer_hunter.os.discovery import people_search_links, search_contacts

        o = _opp(s, opp_id)
        links = people_search_links(o)
        if hosted:
            return {"status": "disabled", "added": 0, "links": links,
                    "detail": "Profile search runs on the Mac. Use the LinkedIn searches below, then add the people you find."}
        if not settings.brave_api_key:
            return {"status": "disabled", "added": 0, "links": links,
                    "detail": "No BRAVE_SEARCH_API_KEY: use the LinkedIn searches below, then add the people you find."}
        added = search_contacts(s, o, settings.brave_api_key)
        net.recompute(s, net_cfg, o.id)
        s.commit()
        return {"status": "ok", "added": added, "links": links}

    # Applications ----------------------------------------------------------------------------------------
    @app.get("/api/pipeline", dependencies=[Depends(authed)])
    def pipeline(s: Session = Depends(db)) -> dict[str, Any]:
        return views.pipeline(s)

    @app.get("/api/applications/{app_id}", dependencies=[Depends(authed)])
    def application(app_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        data = views.application_detail(s, app_id)
        if data is None:
            raise HTTPException(404, "application not found")
        return data

    def _app(crm: CRM, app_id: int):  # noqa: ANN202
        a = crm.application(app_id)
        if a is None:
            raise HTTPException(404, "application not found")
        return a

    @app.post("/api/applications/{app_id}/status", dependencies=[Depends(authed)])
    def change_status(app_id: int, body: StatusChange, s: Session = Depends(db)) -> dict[str, Any]:
        a = _app(CRM(s), app_id)
        try:
            payload = cmd.set_status(ctx(s), a, body.status, body.confirm_submission, body.applied_on)
        except cmd.NeedsConfirmation as exc:
            raise HTTPException(409, str(exc)) from None
        record(s, "status", payload)
        s.commit()
        return views.application_row(a)

    @app.post("/api/applications/{app_id}/interview", dependencies=[Depends(authed)])
    def interview(app_id: int, body: InterviewIn, s: Session = Depends(db)) -> dict[str, Any]:
        a = _app(CRM(s), app_id)
        record(s, "interview", cmd.interview(ctx(s), a, body.at, body.final))
        s.commit()
        return views.application_row(a)

    @app.patch("/api/applications/{app_id}", dependencies=[Depends(authed)])
    def patch_app(app_id: int, body: AppPatch, s: Session = Depends(db)) -> dict[str, Any]:
        a = _app(CRM(s), app_id)
        record(s, "patch", cmd.patch(ctx(s), a, body.model_dump(exclude_unset=True)))
        s.commit()
        return views.application_row(a)

    # Documents (local mode: LaTeX and the local model run on the Mac) ------------------------------------------
    @app.post("/api/opportunities/{opp_id}/documents/{kind}", dependencies=[Depends(authed)])
    def generate_document(opp_id: int, kind: Literal["cv", "letter"], s: Session = Depends(db)) -> dict[str, Any]:
        o = _opp(s, opp_id)
        if hosted:
            s.commit()
            local_only("Document generation")
        if kind == "cv":
            from summer_hunter.web.cv import build_cv

            result = build_cv(settings.config_dir, settings.data_dir / "cv" / str(o.id), o.id, o.company_name, o.title,
                              o.description_text or "", o.job_family)
            crm = CRM(s, settings.data_dir, settings.timezone)
            a = crm.application_for(o.id)
            if a:
                crm.sync_documents(a)
            s.commit()
            return {"status": "ready", "compiled": bool(result.pdf_path), "log": result.log[-400:]}
        Repository(s).set_kv(f"letter_job:{o.id}", "running")
        s.commit()
        threading.Thread(target=_letter_job, args=(o.id,), daemon=True).start()
        return {"status": "running", "detail": "Cover letter generation started (local model, 2 to 3 minutes)."}

    def _letter_job(opp_id: int) -> None:
        from summer_hunter.web.letter import build_provider, clean_role, generate_letter, recipient_for  # noqa: F401

        state = "failed"
        try:
            with sessions() as s:
                o = s.get(Opportunity, opp_id)
                args = dict(company=o.company_name, title=o.title, location=o.location_text, description=o.description_text or "",
                            recipient=recipient_for(Repository(s).contacts(o.id)), job_family=o.job_family)
            generate_letter(settings.config_dir, settings.data_dir / "applications", build_provider(settings.llm), settings.llm,
                            opp_id, **args)
            state = "ready"
        except Exception:  # noqa: BLE001 - reported to the UI through the job state
            log.exception("letter job failed")
        with sessions() as s:
            Repository(s).set_kv(f"letter_job:{opp_id}", state)
            a = CRM(s, settings.data_dir, settings.timezone).application_for(opp_id)
            if a:
                CRM(s, settings.data_dir, settings.timezone).sync_documents(a)
            s.commit()

    @app.get("/api/opportunities/{opp_id}/documents", dependencies=[Depends(authed)])
    def documents(opp_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        from summer_hunter.crm.documents import application_folder

        o = _opp(s, opp_id)
        cv_dir = settings.data_dir / "cv" / str(o.id)
        folder = application_folder(settings.data_dir, o.company_name, o.title)
        files = []
        for kind, base in (("cv", cv_dir), ("letter", folder)):
            for f in sorted(base.glob("*.pdf")) if base.exists() else []:
                if kind == "letter" and not (f.name.startswith("LM ") or f.name == "cover_letter.pdf"):
                    continue
                if kind == "cv" and base == folder:
                    continue
                files.append({"kind": kind, "name": f.name, "size": f.stat().st_size,
                              "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()})
        return {"files": files, "letter_job": Repository(s).get_kv(f"letter_job:{o.id}")}

    @app.get("/api/opportunities/{opp_id}/documents/file/{name}", dependencies=[Depends(authed)])
    def document_file(opp_id: int, name: str, s: Session = Depends(db)) -> FileResponse:
        from summer_hunter.crm.documents import application_folder

        o = _opp(s, opp_id)
        roots = [(settings.data_dir / "cv" / str(o.id)).resolve(), application_folder(settings.data_dir, o.company_name, o.title).resolve()]
        for root in roots:
            target = (root / name).resolve()
            if target.parent == root and target.exists() and target.suffix in {".pdf", ".tex"}:
                return FileResponse(target, media_type="application/pdf" if target.suffix == ".pdf" else "application/x-tex",
                                    filename=target.name)
        raise HTTPException(404, "file not found")

    # Networking and outreach ------------------------------------------------------------------------------------
    @app.get("/api/networking", dependencies=[Depends(authed)])
    def networking(s: Session = Depends(db)) -> list[dict[str, Any]]:
        return views.networking_queue(s, 100)

    @app.post("/api/networking/refresh", dependencies=[Depends(authed)])
    def networking_refresh(s: Session = Depends(db)) -> dict[str, Any]:
        local_only("Contact discovery")
        out = net.import_public_contacts(s)
        out["scored"] = net.recompute(s, net_cfg)
        s.commit()
        return out

    @app.get("/api/contacts", dependencies=[Depends(authed)])
    def contacts(q: str = Query("", max_length=100), category: str | None = None, verification: str | None = None,
                 s: Session = Depends(db)) -> list[dict[str, Any]]:
        return views.list_contacts(s, q, category, verification)

    @app.post("/api/contacts", dependencies=[Depends(authed)])
    def add_contact(body: ContactIn, s: Session = Depends(db)) -> dict[str, Any]:
        c, payload = cmd.add_contact(ctx(s), body.model_dump())
        record(s, "add_contact", payload)
        s.commit()
        return views.contact_row(c)

    def _contact(s: Session, contact_id: int) -> CRMContact:
        c = s.get(CRMContact, contact_id)
        if c is None:
            raise HTTPException(404, "contact not found")
        return c

    @app.get("/api/contacts/{contact_id}", dependencies=[Depends(authed)])
    def contact(contact_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        data = views.contact_detail(s, contact_id)
        if data is None:
            raise HTTPException(404, "contact not found")
        return data

    @app.post("/api/contacts/{contact_id}/confirm", dependencies=[Depends(authed)])
    def confirm(contact_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        c = _contact(s, contact_id)
        record(s, "confirm_contact", cmd.confirm_contact(ctx(s), c))
        s.commit()
        return views.contact_row(c)

    @app.post("/api/contacts/{contact_id}/dismiss", dependencies=[Depends(authed)])
    def dismiss_contact(contact_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        record(s, "dismiss_contact", cmd.dismiss_contact(ctx(s), _contact(s, contact_id)))
        s.commit()
        return {"ok": True}

    def _context(s: Session, c: CRMContact, opp_id: int | None):  # noqa: ANN202
        o = s.get(Opportunity, opp_id) if opp_id else None
        a = CRM(s).application_for(o.id) if o else None
        ev = list(s.execute(select(ContactEvidence).where(ContactEvidence.contact_id == c.id)).scalars())
        inter = [i for i in s.execute(select(Interaction).where(Interaction.contact_id == c.id)).scalars()
                 if opp_id is None or i.opportunity_id in (None, opp_id)]
        link = s.execute(select(ContactOpportunity).where(ContactOpportunity.contact_id == c.id,
                                                          ContactOpportunity.opportunity_id == opp_id)).scalar_one_or_none() if o else None
        return o, a, ev, inter, link

    @app.post("/api/contacts/{contact_id}/messages", dependencies=[Depends(authed)])
    def generate_message(contact_id: int, body: MessageIn, s: Session = Depends(db)) -> dict[str, Any]:
        c = _contact(s, contact_id)
        o, a, ev, inter, link = _context(s, c, body.opportunity_id)
        suggested = _kind_for(link)
        if suggested == "after_applying" and not (a and a.date_applied):
            suggested = "connection_request"        # never claim an application that is not recorded
        kind = body.kind or msg.suggest_kind(suggested, c, a, inter)
        try:
            d = msg.compose(kind, c, o, a, ev, inter, net_cfg, body.variant)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        m = OutreachMessage(contact_id=c.id, opportunity_id=o.id if o else None, kind=d.kind, variant=d.variant, text=d.text,
                            generator="template", evidence_refs=d.evidence_refs, validation=d.validation,
                            personalization=d.personalization)
        record(s, "message", cmd.save_message(ctx(s), m))
        s.commit()
        return views.message_row(m)

    def _kind_for(link: ContactOpportunity | None) -> str | None:
        if link is None or not link.timing_action:
            return None
        return {"FOLLOW_UP": "follow_up", "CONTACT_AFTER_APPLICATION": "after_applying"}.get(link.timing_action)

    def _message(s: Session, message_id: int) -> OutreachMessage:
        m = s.get(OutreachMessage, message_id)
        if m is None:
            raise HTTPException(404, "message not found")
        return m

    @app.post("/api/messages/{message_id}/variant", dependencies=[Depends(authed)])
    def variant(message_id: int, body: VariantIn, s: Session = Depends(db)) -> dict[str, Any]:
        base = _message(s, message_id)
        c = _contact(s, base.contact_id)
        o, a, ev, inter, _ = _context(s, c, base.opportunity_id)
        try:
            d = msg.compose(base.kind, c, o, a, ev, inter, net_cfg, body.variant)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        m = OutreachMessage(contact_id=c.id, opportunity_id=base.opportunity_id, kind=d.kind, variant=d.variant, text=d.text,
                            generator="template", evidence_refs=d.evidence_refs, validation=d.validation,
                            personalization=d.personalization)
        record(s, "message", cmd.save_message(ctx(s), m))
        s.commit()
        return views.message_row(m)

    @app.post("/api/messages/{message_id}/ai", dependencies=[Depends(authed)])
    def ai_rewrite(message_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        local_only("AI rewriting")
        from summer_hunter.os.ai import rewrite

        base = _message(s, message_id)
        c = _contact(s, base.contact_id)
        o, a, ev, inter, _ = _context(s, c, base.opportunity_id)
        result = rewrite(settings, base, c, o, a, ev, inter, net_cfg)
        if result is None:
            raise HTTPException(422, "the local model's version failed the checks; the validated draft is kept")
        s.add(result)
        s.commit()
        return views.message_row(result)

    @app.post("/api/messages/{message_id}/sent", dependencies=[Depends(authed)])
    def mark_sent(message_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        m = _message(s, message_id)
        if m.status == "sent":
            return views.message_row(m)
        _contact(s, m.contact_id)
        record(s, "message_sent", cmd.mark_sent(ctx(s), m))
        s.commit()
        return views.message_row(m)

    @app.post("/api/messages/{message_id}/dismiss", dependencies=[Depends(authed)])
    def dismiss_message(message_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        m = _message(s, message_id)
        record(s, "message_dismiss", cmd.dismiss_message(ctx(s), m))
        s.commit()
        return views.message_row(m)

    @app.post("/api/contacts/{contact_id}/interactions", dependencies=[Depends(authed)])
    def interaction(contact_id: int, body: InteractionIn, s: Session = Depends(db)) -> dict[str, Any]:
        c = _contact(s, contact_id)
        i, payload = cmd.interaction(ctx(s), c, body.kind, body.opportunity_id, body.note, body.follow_up_on)
        record(s, "interaction", payload)
        s.commit()
        return {"id": i.id, "kind": i.kind}

    # Analytics and performance ------------------------------------------------------------------------------------
    @app.get("/api/analytics", dependencies=[Depends(authed)])
    def analytics(s: Session = Depends(db)) -> dict[str, Any]:
        from summer_hunter.os.reports import analytics_report

        return analytics_report(s)

    @app.get("/api/performance", dependencies=[Depends(authed)])
    def performance(s: Session = Depends(db)) -> dict[str, Any]:
        from summer_hunter.os.reports import performance_report

        return performance_report(s, Repository(s).get_kv("os_last_sync"))

    @app.get("/api/export/crm", dependencies=[Depends(authed)])
    def export_crm() -> FileResponse:
        local_only("The Excel export")
        from summer_hunter.crm import sync as crm_sync

        path = crm_sync.export(settings, sessions)
        return FileResponse(path, filename=path.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/api/config", dependencies=[Depends(authed)])
    def config() -> dict[str, Any]:
        return {"global_priority": settings.scoring.get("global_priority"), "contact_score": net_cfg.get("contact_score"),
                "timing": net_cfg.get("timing"), "compensation_preferences": settings.compensation.get("preferences"),
                "regions": sorted(REGIONS)}

    @app.post("/api/config/preferences", dependencies=[Depends(authed)])
    def preferences(body: PreferencesIn, s: Session = Depends(db)) -> dict[str, Any]:
        if body.mode:
            record(s, "mode", cmd.set_mode(ctx(s), body.mode))
        s.commit()
        return {"ok": True}

    # Sync (hosted side only; the Mac authenticates with SH_SYNC_TOKEN) -----------------------------------------
    if hosted:
        _sync_routes(app, db)
    return app


def _conflict_count(repo: Repository) -> int:
    import json

    try:
        return len(json.loads(repo.get_kv("sync_conflicts") or "[]"))
    except ValueError:
        return 0


def _sync_routes(app: FastAPI, db) -> None:  # noqa: ANN001
    """Pull of online edits, then an atomic snapshot swap: chunks go to a staging table and /done replaces the
    synchronized tables in one transaction, only when every online edit has been acknowledged by the Mac."""
    import json
    import uuid

    from sqlalchemy import delete

    from summer_hunter.database.models import SyncStaging
    from summer_hunter.os import sync as sync_mod

    def sync_auth(request: Request) -> None:
        if not security.sync_token_ok(request.headers.get("authorization", "").removeprefix("Bearer ")):
            raise HTTPException(401, "sync token required")

    def origin(s: Session) -> str:
        repo = Repository(s)
        if s.get_bind().dialect.name == "postgresql":           # created once, under the same lock as every write
            s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": WRITE_LOCK})
        value = repo.get_kv(ORIGIN_KEY)
        if not value:
            value = uuid.uuid4().hex
            repo.set_kv(ORIGIN_KEY, value)
            s.commit()
        return value

    @app.get("/api/sync/changes", dependencies=[Depends(sync_auth)])
    def sync_changes(s: Session = Depends(db)) -> dict[str, Any]:
        rows = s.execute(select(SyncChange).where(SyncChange.applied_at.is_(None)).order_by(SyncChange.id)).scalars()
        changes = [{"id": r.id, "kind": r.kind, "payload": r.payload, "created": r.created_at.isoformat()} for r in rows]
        return {"protocol": SYNC_PROTOCOL, "origin": origin(s), "changes": changes}

    @app.post("/api/sync/ack", dependencies=[Depends(sync_auth)])
    def sync_ack(ids: list[int] = Body(...), s: Session = Depends(db)) -> dict[str, Any]:
        for r in s.execute(select(SyncChange).where(SyncChange.id.in_(ids))).scalars():
            r.applied_at = utcnow()
        s.commit()
        return {"acked": len(ids)}

    @app.post("/api/sync/begin", dependencies=[Depends(sync_auth)])
    def sync_begin(s: Session = Depends(db)) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        s.execute(delete(SyncStaging))                       # a newer run supersedes an unfinished one
        Repository(s).set_kv(RUN_KEY, run_id)
        s.commit()
        return {"run_id": run_id, "protocol": SYNC_PROTOCOL}

    @app.post("/api/sync/push/{table}", dependencies=[Depends(sync_auth)])
    def sync_push(table: str, run_id: str = Query(max_length=40), part: int = 0, rows: list[dict[str, Any]] = Body(...),
                  s: Session = Depends(db)) -> dict[str, Any]:
        if table not in sync_mod.BY_NAME:
            raise HTTPException(422, f"table {table!r} is not synchronized")
        if Repository(s).get_kv(RUN_KEY) != run_id:
            raise HTTPException(409, "sync run superseded")
        s.add(SyncStaging(run_id=run_id, table_name=table, rows=json.dumps(rows)))
        s.commit()
        return {"table": table, "part": part, "rows": len(rows)}

    @app.post("/api/sync/done", dependencies=[Depends(sync_auth)])
    def sync_done(run_id: str = Query(max_length=40), s: Session = Depends(db)) -> dict[str, Any]:
        repo = Repository(s)
        if repo.get_kv(RUN_KEY) != run_id:
            raise HTTPException(409, "sync run superseded")
        pending = s.execute(select(SyncChange.id).where(SyncChange.applied_at.is_(None)).limit(1)).first()
        if pending:                                          # an online edit arrived after the pull: pull again first
            raise HTTPException(409, "pending online edits")
        staged: dict[str, list[dict[str, Any]]] = {}
        for part in s.execute(select(SyncStaging).where(SyncStaging.run_id == run_id).order_by(SyncStaging.id)).scalars():
            staged.setdefault(part.table_name, []).extend(json.loads(part.rows))
        sync_mod.begin_snapshot(s)
        counts = {name: sync_mod.apply_snapshot_part(s, name, staged.get(name, [])) for name in sync_mod.BY_NAME}
        sync_mod.finish_snapshot(s)
        s.execute(delete(SyncStaging))
        repo.set_kv(GEN_KEY, str(int(repo.get_kv(GEN_KEY) or 0) + 1))
        repo.set_kv("os_last_sync", utcnow().isoformat())
        s.commit()
        return {"ok": True, "rows": counts}
