"""Mini App "CV" tab API: AI CV Builder over HTTP (Telegram initData auth on /api/, signed links on /files/)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from summer_hunter.cv_builder import edits, service
from summer_hunter.cv_builder.profile import INFERRED, KIND_FR, REJECTED, USER_CONFIRMED, ProfileStore
from summer_hunter.cv_builder.versions import VersionStore
from summer_hunter.utils.settings import Settings
from summer_hunter.web import auth

log = logging.getLogger(__name__)
MAX_TEXT = 20000
EXAMPLES = ["en français", "en anglais", "1 page", "2 pages", "mets en avant Nexya", "retire Amundi",
            "pour un poste de Sales Engineer", "version générique"]


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def routes(settings: Settings, sessions: sessionmaker[Session], telegram=None) -> list[Route]:  # noqa: ANN001, C901
    token = settings.telegram_bot_token or "dev"
    root = (settings.data_dir / "cv_builder").resolve()

    def with_links(data: dict[str, Any]) -> dict[str, Any]:
        files = {}
        if data.get("has_pdf"):
            files["pdf"] = auth.sign_link(f"/files/cvb/{data['id']}/pdf", token)
        if data.get("has_docx"):
            files["docx"] = auth.sign_link(f"/files/cvb/{data['id']}/docx", token)
        return data | {"files": files}

    def version_json(vid: int) -> dict[str, Any] | None:
        with sessions() as session:
            row = VersionStore(session, settings.data_dir, settings.config_dir).get(vid)
            return with_links(service.payload(row)) if row else None

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def vid_of(request: Request) -> int:
        return int(request.path_params["vid"])

    async def create(request: Request) -> Response:
        data = await body(request)
        text = str(data.get("text") or "").strip()
        if not text:
            return _err("Décris le CV voulu ou colle une offre.")
        if len(text) > MAX_TEXT:
            return _err("Texte trop long (20 000 caractères maximum).")
        overrides = {"language": data.get("language") if data.get("language") in {"en", "fr"} else None,
                     "pages": int(data["pages"]) if str(data.get("pages")) in {"1", "2"} else None}
        try:
            built = await asyncio.to_thread(service.generate, settings, sessions, text, overrides)
        except Exception as exc:  # noqa: BLE001
            log.error("miniapp cv build failed", extra={"error": type(exc).__name__})
            return _err("Génération impossible (erreur interne).", 500)
        log.info("miniapp cv built", extra={"cv": built.tag, "mode": built.req.mode})
        return JSONResponse(version_json(built.version_id))

    async def edit(request: Request) -> Response:
        data = await body(request)
        text = str(data.get("text") or "").strip()[:300]
        e = edits.parse(text)
        proposed = []
        if e.facts:
            with sessions() as session:
                store = ProfileStore(session)
                for fact in e.facts:
                    p = store.propose(fact, source="Mini App")
                    proposed.append({"id": p.fact.id, "kind": KIND_FR.get(p.fact.kind, p.fact.kind), "text": p.fact.text,
                                     "status": p.fact.status})
                session.commit()
        if not e.overrides:
            if proposed:
                return JSONResponse({"facts": proposed})
            return JSONResponse({"error": "Modification non comprise.", "examples": EXAMPLES}, status_code=422)
        try:
            built = await asyncio.to_thread(service.derive, settings, sessions, vid_of(request), text, e.overrides)
        except Exception as exc:  # noqa: BLE001
            log.error("miniapp cv edit failed", extra={"error": type(exc).__name__})
            return _err("Modification impossible (erreur interne).", 500)
        if built is None:
            return _err("Version introuvable.", 404)
        return JSONResponse({"version": version_json(built.version_id), "understood": e.understood, "facts": proposed})

    async def regenerate(request: Request) -> Response:
        try:
            built = await asyncio.to_thread(service.derive, settings, sessions, vid_of(request),
                                            "régénération (profil à jour)", {})
        except Exception as exc:  # noqa: BLE001
            log.error("miniapp cv regenerate failed", extra={"error": type(exc).__name__})
            return _err("Régénération impossible (erreur interne).", 500)
        if built is None:
            return _err("Version introuvable.", 404)
        return JSONResponse(version_json(built.version_id))

    async def get_version(request: Request) -> Response:
        data = version_json(vid_of(request))
        return JSONResponse(data) if data else _err("Version introuvable.", 404)

    async def save(request: Request) -> Response:
        with sessions() as session:
            vs = VersionStore(session, settings.data_dir, settings.config_dir)
            row = vs.get(vid_of(request))
            if row is None:
                return _err("Version introuvable.", 404)
            vs.set_saved(row.id, not row.saved)
            session.commit()
            return JSONResponse({"saved": bool(row.saved)})

    async def send(request: Request) -> Response:
        if telegram is None:
            return _err("Telegram non configuré.", 503)
        with sessions() as session:
            row = VersionStore(session, settings.data_dir, settings.config_dir).get(vid_of(request))
            files = VersionStore.files(row) if row else []
        if not files:
            return _err("Fichiers introuvables.", 404)
        for f in files:
            await asyncio.to_thread(telegram.send_document, settings.telegram_chat_id, f, f.stem)
        return JSONResponse({"ok": True, "sent": len(files)})

    async def history(request: Request) -> Response:
        with sessions() as session:
            vs = VersionStore(session, settings.data_dir, settings.config_dir)
            out = []
            for latest in vs.history(20):
                out.append(with_links(service.payload(latest)) | {
                    "versions": [{"id": v.id, "version": v.version, "change": v.change, "saved": bool(v.saved),
                                  "language": v.language, "pages": v.pages,
                                  "created": v.created_at.isoformat() if v.created_at else None}
                                 for v in vs.lineage(latest.lineage_id)]})
        return JSONResponse({"items": out})

    async def profile(request: Request) -> Response:
        with sessions() as session:
            store = ProfileStore(session)
            if store.seed():
                session.commit()

            def fact(f) -> dict[str, Any]:  # noqa: ANN001
                return {"id": f.id, "kind": KIND_FR.get(f.kind, f.kind), "text": f.text, "note": f.note, "status": f.status}
            return JSONResponse({"pending": [fact(f) for f in store.facts(INFERRED)],
                                 "confirmed": [fact(f) for f in store.facts(USER_CONFIRMED)]})

    async def profile_add(request: Request) -> Response:
        text = str((await body(request)).get("text") or "").strip()
        if not text:
            return _err("Écris le fait à ajouter.")
        with sessions() as session:
            p = ProfileStore(session).propose(text, source="Mini App")
            session.commit()
            return JSONResponse({"id": p.fact.id, "created": p.created, "status": p.fact.status,
                                 "kind": KIND_FR.get(p.fact.kind, p.fact.kind), "text": p.fact.text})

    async def profile_decide(request: Request) -> Response:
        decision = (await body(request)).get("decision")
        status = {"ok": USER_CONFIRMED, "no": REJECTED, "undo": INFERRED}.get(decision)
        if status is None:
            return _err("Décision inconnue.")
        with sessions() as session:
            f = ProfileStore(session).decide(int(request.path_params["fid"]), status)
            session.commit()
        if f is None:
            return _err("Fait introuvable.", 404)
        log.info("miniapp profile fact decided", extra={"fact_id": f.id, "status": status})
        return JSONResponse({"id": f.id, "status": f.status})

    async def download(request: Request) -> Response:
        if not auth.verify_link(request.url.path, request.query_params.get("exp", ""), request.query_params.get("sig", ""), token):
            return _err("Lien expiré.", 403)
        kind = request.path_params["kind"]
        if kind not in {"pdf", "docx"}:
            return _err("Introuvable.", 404)
        with sessions() as session:
            row = VersionStore(session, settings.data_dir, settings.config_dir).get(vid_of(request))
            raw = (row.pdf_path if kind == "pdf" else row.docx_path) if row else None
        if not raw:
            return _err("Introuvable.", 404)
        target = Path(raw).resolve()
        if root not in target.parents or not target.exists():
            return _err("Introuvable.", 404)
        media = ("application/pdf" if kind == "pdf"
                 else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        return FileResponse(target, media_type=media, filename=target.name)

    return [
        Route("/api/cv", create, methods=["POST"]),
        Route("/api/cv/history", history),
        Route("/api/cv/profile", profile),
        Route("/api/cv/profile", profile_add, methods=["POST"]),
        Route("/api/cv/profile/{fid:int}", profile_decide, methods=["POST"]),
        Route("/api/cv/{vid:int}", get_version),
        Route("/api/cv/{vid:int}/edit", edit, methods=["POST"]),
        Route("/api/cv/{vid:int}/regenerate", regenerate, methods=["POST"]),
        Route("/api/cv/{vid:int}/save", save, methods=["POST"]),
        Route("/api/cv/{vid:int}/send", send, methods=["POST"]),
        Route("/files/cvb/{vid:int}/{kind:str}", download),
    ]
