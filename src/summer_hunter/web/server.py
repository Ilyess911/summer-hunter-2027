"""Telegram Mini App backend (Starlette). Listens on 127.0.0.1 only; exposed through a tunnel."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from summer_hunter.database.models import Opportunity
from summer_hunter.database.repository import Repository, as_utc
from summer_hunter.engine.feedback import DISMISS_REASONS, recompute
from summer_hunter.engine.geo import country_name
from summer_hunter.utils.settings import Settings
from summer_hunter.web import auth
from summer_hunter.web.cv import build_cv

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
VIEWS = {"top", "new", "saved", "applied", "rejected", "verify"}


REGIONS = {
    "europe": {"GB", "IE", "CH", "DE", "NL", "SE", "DK", "NO", "FI", "BE", "LU", "ES", "IT", "PT", "AT", "PL", "CZ", "HU",
               "RO", "GR", "EE", "LT", "LV", "BG", "HR", "SK", "SI", "MT", "CY", "IS", "MC"},
    "asia": {"SG", "HK", "TW", "JP", "KR", "CN", "AU", "NZ", "VN", "ID", "TH", "MY", "PH"},
    "middle_east": {"AE", "QA", "SA", "KW", "BH", "OM", "JO", "LB", "MA", "TN", "DZ"},
    "americas": {"US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "CR"},
}


def region_of(code: str | None) -> str:
    return next((r for r, codes in REGIONS.items() if code in codes), "other")


def summary(o: Opportunity) -> dict[str, Any]:
    return {"id": o.id, "title": o.title, "company": o.company_name, "country": country_name(o.country_code),
            "city": o.city, "region": region_of(o.country_code),
            "country_code": o.country_code, "location": o.location_text, "decision": o.eligibility_status,
            "fit": o.fit_score, "networking": o.networking_score, "priority": o.priority_score, "tier": o.priority_tier,
            "family": o.job_family, "user_status": o.user_status, "status": o.status,
            "discovered": as_utc(o.first_discovered_at).date().isoformat() if o.first_discovered_at else None}


class TelegramAuth(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:  # noqa: ANN001
        super().__init__(app)
        self.settings = settings
        self.dev = os.environ.get("SH_WEB_DEV") == "1"

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        if request.url.path.startswith("/api/") and not self.dev:
            try:
                auth.verify_init_data(request.headers.get("x-telegram-init-data", ""), self.settings.telegram_bot_token or "",
                                      self.settings.telegram_chat_id or "")
            except auth.AuthError as exc:
                log.warning("web auth refused", extra={"path": request.url.path, "reason": str(exc)})
                return JSONResponse({"error": "unauthorized", "detail": str(exc)}, status_code=401)
        response = await call_next(request)
        log.info("web request", extra={"path": request.url.path, "status": response.status_code})
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def create_app(settings: Settings, sessions: sessionmaker[Session], telegram=None) -> Starlette:  # noqa: ANN001
    cv_dir = settings.data_dir / "cv"

    def opp_or_404(repo: Repository, request: Request) -> Opportunity | None:
        try:
            return repo.opportunity(int(request.path_params["opp_id"]))
        except (ValueError, KeyError):
            return None

    def asset_version() -> str:
        """Changes whenever a static file changes, so Telegram's webview never mixes old and new assets."""
        stamp = "".join(f"{f.name}{f.stat().st_mtime_ns}" for f in sorted(STATIC.iterdir()))
        return hashlib.sha256(stamp.encode()).hexdigest()[:10]

    async def index(request: Request) -> Response:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        v = asset_version()
        html = html.replace("/static/style.css", f"/static/style.css?v={v}").replace("/static/app.js", f"/static/app.js?v={v}")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    async def list_opps(request: Request) -> Response:
        view = request.query_params.get("view", "top")
        view = view if view in VIEWS else "top"
        q = (request.query_params.get("q") or "").lower().strip()
        with sessions() as session:
            repo = Repository(session)
            if view == "top":
                rows = [o for o in repo.top(300) if o.notify or (o.family_tier or 0) > 0]
            elif view == "new":
                rows = [o for o in repo.top(300) if o.user_status == "new" and (o.family_tier or 0) > 0]
            elif view == "verify":
                rows = [o for o in repo.top(300) if o.eligibility_status in ("ELIGIBLE", "NEEDS_VERIFICATION") and (o.family_tier or 0) > 0]
            elif view == "rejected":
                rows = [o for o in repo.by_eligibility("REJECTED", limit=300) if (o.family_tier or 0) > 0 and o.status == "active"]
            else:
                rows = repo.by_user_status({"saved": "saved", "applied": "applied"}[view], limit=300)
            if q:
                rows = [o for o in rows if q in f"{o.title} {o.company_name} {o.location_text}".lower()]
            region = request.query_params.get("region")
            if region in REGIONS:
                rows = [o for o in rows if region_of(o.country_code) == region]
            active = [o for o in repo.top(500) if (o.family_tier or 0) > 0]
            stats = {"target": len(active), "new": sum(o.user_status == "new" and bool(o.notify) for o in active),
                     "saved": sum(o.user_status == "saved" for o in active),
                     "applied": sum(o.user_status == "applied" for o in active)}
        return JSONResponse({"items": [summary(o) for o in rows], "stats": stats})

    async def detail(request: Request) -> Response:
        with sessions() as session:
            repo = Repository(session)
            opp = opp_or_404(repo, request)
            if not opp:
                return JSONResponse({"error": "not found"}, status_code=404)
            ev, fit = repo.latest_eligibility(opp.id), repo.latest_fit(opp.id)
            post = repo.linkedin_post(opp.linkedin_post_url) if opp.linkedin_post_url else None
            data = summary(opp) | {
                "apply_url": opp.apply_url or opp.official_url, "official_url": opp.official_url,
                "linkedin_post_url": opp.linkedin_post_url,
                "description": (opp.description_text or "")[:6000],
                "criteria": ev.criteria if ev else [], "sponsorship": ev.sponsorship if ev else None,
                "fit_report": fit.report if fit else None, "networking_report": fit.networking if fit else None,
                "priority_report": fit.priority if fit else None,
                "contacts": [{"name": c.name, "role": c.role, "relationship": c.relationship_type, "confidence": c.confidence,
                              "url": c.profile_url or c.source_post_url, "evidence": c.evidence} for c in repo.contacts(opp.id)],
                "sources": [{"source": s.source, "url": s.url} for s in opp.sources],
                "post": {"author": post.author_name, "text": post.text[:1500], "class": post.classification} if post else None,
                "dismiss_reasons": {k: v[2] for k, v in DISMISS_REASONS.items()},
            }
        return JSONResponse(data)

    async def action(request: Request) -> Response:
        body = await request.json()
        act, reason = body.get("action"), body.get("reason")
        if act not in {"save", "dismiss", "applied", "reason", "reset"}:
            return JSONResponse({"error": "bad action"}, status_code=400)
        if act == "reason" and reason not in DISMISS_REASONS:
            return JSONResponse({"error": "bad reason"}, status_code=400)
        with sessions() as session:
            repo = Repository(session)
            opp = opp_or_404(repo, request)
            if not opp:
                return JSONResponse({"error": "not found"}, status_code=404)
            if act == "reset":
                opp.user_status = "new"
            else:
                repo.record_feedback(opp, act, reason)
                recompute(repo, settings.scoring)
            session.commit()
            return JSONResponse({"ok": True, "user_status": opp.user_status})

    async def make_cv(request: Request) -> Response:
        with sessions() as session:
            repo = Repository(session)
            opp = opp_or_404(repo, request)
            if not opp:
                return JSONResponse({"error": "not found"}, status_code=404)
            args = (settings.config_dir, cv_dir / str(opp.id), opp.id, opp.company_name, opp.title,
                    opp.description_text or "", opp.job_family)
        try:
            result = await asyncio.to_thread(build_cv, *args)
        except FileNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        token = settings.telegram_bot_token or "dev"
        files = {"tex": auth.sign_link(f"/files/cv/{opp.id}/{result.tex_path.name}", token)}
        if result.pdf_path:
            files["pdf"] = auth.sign_link(f"/files/cv/{opp.id}/{result.pdf_path.name}", token)
        return JSONResponse({"files": files, "compiled": bool(result.pdf_path), "log": result.log[:800]})

    async def send_cv(request: Request) -> Response:
        if telegram is None:
            return JSONResponse({"error": "Telegram not configured"}, status_code=503)
        opp_id = request.path_params["opp_id"]
        folder = cv_dir / str(int(opp_id))
        files = sorted(folder.glob("CV_*.pdf")) + sorted(folder.glob("CV_*.tex")) if folder.exists() else []
        if not files:
            return JSONResponse({"error": "generate the CV first"}, status_code=400)
        for f in files:
            await asyncio.to_thread(telegram.send_document, settings.telegram_chat_id, f, f"CV #{opp_id}: {f.suffix[1:].upper()}")
        return JSONResponse({"ok": True, "sent": [f.name for f in files]})

    async def download(request: Request) -> Response:
        path = request.url.path
        if not auth.verify_link(path, request.query_params.get("exp", ""), request.query_params.get("sig", ""),
                                settings.telegram_bot_token or "dev"):
            return JSONResponse({"error": "link expired"}, status_code=403)
        name = request.path_params["name"]
        target = (cv_dir / str(int(request.path_params["opp_id"])) / name).resolve()
        if cv_dir.resolve() not in target.parents or not target.exists() or target.suffix not in {".pdf", ".tex"}:
            return JSONResponse({"error": "not found"}, status_code=404)
        media = "application/pdf" if target.suffix == ".pdf" else "application/x-tex"
        return FileResponse(target, media_type=media, filename=target.name)

    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True})

    routes = [
        Route("/", index),
        Route("/healthz", health),
        Route("/api/opportunities", list_opps),
        Route("/api/opportunities/{opp_id:int}", detail),
        Route("/api/opportunities/{opp_id:int}/action", action, methods=["POST"]),
        Route("/api/opportunities/{opp_id:int}/cv", make_cv, methods=["POST"]),
        Route("/api/opportunities/{opp_id:int}/cv/send", send_cv, methods=["POST"]),
        Route("/files/cv/{opp_id:int}/{name:str}", download),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    return Starlette(routes=routes, middleware=[Middleware(TelegramAuth, settings=settings)])
