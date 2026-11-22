"""Mini App API for auto-apply: is this offer supported, start it, and the account e-mail / password for new accounts.

The password is write-only: it goes to the macOS Keychain and is never sent back (the page only learns it is set).
"""

from __future__ import annotations

import re
import threading
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from summer_hunter.apply import ats, workday
from summer_hunter.apply import service as auto
from summer_hunter.database.repository import Repository
from summer_hunter.utils.settings import Settings

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,24}$", re.I)
LABEL = {"ready": "Prête : valide l'envoi dans le chat", "needs": "Réponses attendues dans le chat",
         "verify_email": "Confirme le mail Workday", "submitting": "Envoi en cours", "submitted": "Envoyée",
         "unconfirmed": "Envoi à vérifier", "cancelled": "Annulée", "account_exists": "Compte existant : mot de passe",
         "auth_failed": "Mot de passe refusé", "invalid": "Bloquée : voir le chat", "error": "Erreur : voir le chat",
         "captcha": "Captcha : à finir à la main", "blocked": "Champ refusé : voir le chat"}


def _header_email(settings: Settings) -> str:
    from summer_hunter.web.cv import load_content

    return str((load_content(settings.config_dir).get("header") or {}).get("email") or "")


def routes(settings: Settings, sessions: sessionmaker[Session], telegram=None) -> list[Route]:  # noqa: ANN001
    def info(opp_id: int) -> dict[str, Any] | None:
        with sessions() as session:
            opp = Repository(session).opportunity(opp_id)
            if opp is None:
                return None
            target = auto.target_for(opp, session)
            jid = any(ats.gh_jid(u) for u in (opp.apply_url, opp.official_url))
        kind = target.kind if target else ("greenhouse" if jid else None)
        state = auto.load_state(settings.data_dir, opp_id) or {}
        status = state.get("status")
        return {"supported": bool(kind) and bool(auto.config(settings.config_dir).get("enabled")),
                "platform": ats.NAMES.get(kind or "", ""), "account": kind == "workday",
                "status": status, "status_label": LABEL.get(status or "", ""),
                "can_start": status not in ("submitting", "submitted", "unconfirmed") and telegram is not None}

    async def get_offer(request: Request) -> Response:
        data = info(int(request.path_params["opp_id"]))
        return JSONResponse(data) if data else JSONResponse({"error": "Offre introuvable."}, status_code=404)

    async def start(request: Request) -> Response:
        opp_id = int(request.path_params["opp_id"])
        data = info(opp_id)
        if not data or not data["supported"]:
            return JSONResponse({"error": "Plateforme non gérée pour cette offre."}, status_code=409)
        if not data["can_start"]:
            return JSONResponse({"error": "Candidature déjà envoyée ou en cours."}, status_code=409)
        threading.Thread(target=auto.prepare, args=(settings, sessions, telegram, opp_id, True), daemon=True,
                         name="auto-apply-web").start()
        return JSONResponse({"ok": True}, status_code=202)

    async def get_account(request: Request) -> Response:
        email = auto.account_email(settings.data_dir, _header_email(settings))
        return JSONResponse({"email": email, "password_set": workday.keychain_get(workday.DEFAULT_ACCOUNT) is not None})

    async def set_account(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        email = str(data.get("email") or "").strip()
        password = str(data.get("password") or "")
        if not EMAIL.match(email):
            return JSONResponse({"error": "Adresse e-mail invalide."}, status_code=400)
        if password:
            if problem := workday.password_problem(password):
                return JSONResponse({"error": f"Mot de passe : il faut {problem}."}, status_code=400)
            try:
                workday.keychain_set(workday.DEFAULT_ACCOUNT, password)
            except workday.KeychainError:
                return JSONResponse({"error": "Trousseau du Mac indisponible : rien enregistré."}, status_code=500)
        auto.set_account_email(settings.data_dir, email)
        return JSONResponse({"ok": True, "email": email,
                             "password_set": workday.keychain_get(workday.DEFAULT_ACCOUNT) is not None})

    return [
        Route("/api/apply/account", get_account),
        Route("/api/apply/account", set_account, methods=["POST"]),
        Route("/api/apply/{opp_id:int}", get_offer),
        Route("/api/apply/{opp_id:int}", start, methods=["POST"]),
    ]
