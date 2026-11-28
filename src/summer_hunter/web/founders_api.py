"""Mini App API for the daily founders list (crm/founders.py): read, mark contacted / not for me / undo, run today's search."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from summer_hunter.crm import founders
from summer_hunter.database.models import CRMContact, LLMUsage
from summer_hunter.database.repository import Repository
from summer_hunter.engine.geo import country_name
from summer_hunter.utils.settings import Settings

log = logging.getLogger(__name__)

CONTACTED = {"MESSAGE_SENT", "CONNECTED", "REPLIED", "CALL_SCHEDULED", "FOLLOW_UP_REQUIRED"}
FILTERS = {"todo", "sent", "all"}


def item(row: CRMContact, today_ids: set[int]) -> dict[str, Any]:
    n = founders.notes_of(row)
    country = n.get("country") or ""
    return {
        "id": row.id, "name": row.name, "company": row.company, "city": n.get("city") or "",
        "city_verified": bool(n.get("city_verified")), "country_code": country,
        "country": country_name(country) if country else "", "sector": n.get("sector") or "",
        "team_size": "" if n.get("team_size") in (None, "", "unknown") else n["team_size"],
        "what": n.get("what") or "", "why": n.get("why") or "", "message": n.get("message") or "",
        "source": row.source if (row.source or "").startswith("https://") else "",
        "website": n.get("website") if str(n.get("website") or "").startswith("https://") else "",
        "linkedin": n.get("linkedin_search") or "", "status": row.interaction_status, "dismissed": bool(row.dismissed),
        "contacted": row.interaction_status in CONTACTED, "today": row.id in today_ids,
        "added": row.created_at.isoformat() if row.created_at else None,
    }


def routes(settings: Settings, sessions: sessionmaker[Session], telegram=None) -> list[Route]:  # noqa: ANN001
    running = threading.Event()

    def today_state(session: Session) -> dict[str, Any]:
        cfg = founders.config(settings.config_dir)
        day = founders.today()
        raw = Repository(session).get_kv(founders.KV_DAY + day.isoformat())
        cap = float(cfg.get("daily_budget_usd", 0.30))
        spent = founders.spent(session, day)
        last = session.scalar(select(LLMUsage.cost_usd).where(LLMUsage.purpose == founders.PURPOSE, LLMUsage.outcome == "ok")
                              .order_by(LLMUsage.id.desc()).limit(1)) or 0.0
        from summer_hunter.utils.budget import month_left

        room = min(cap - spent, month_left(session, day, settings.llm))
        fits = room >= max(float(last), founders.MIN_RUN_ESTIMATE_USD)
        return {"date": day.isoformat(), "done": raw is not None, "ids": json.loads(raw or "[]"),
                "areas": [a["name"] for a in founders.areas_for(day, cfg)], "spent": round(spent, 2), "cap": cap,
                "enabled": bool(cfg.get("enabled")), "running": running.is_set(),
                "can_run": bool(cfg.get("enabled")) and raw is None and fits and not running.is_set()
                and bool(settings.anthropic_api_key)}

    async def listing(request: Request) -> Response:
        flt = request.query_params.get("filter", "todo")
        flt = flt if flt in FILTERS else "todo"
        with sessions() as session:
            state = today_state(session)
            ids = set(state["ids"])
            rows = session.execute(select(CRMContact).where(CRMContact.category == founders.CATEGORY)
                                   .order_by(CRMContact.created_at.desc(), CRMContact.id.desc()).limit(300)).scalars().all()
            out = [item(r, ids) for r in rows]
        groups = {"todo": [x for x in out if not x["dismissed"] and not x["contacted"]],
                  "sent": [x for x in out if x["contacted"]], "all": out}
        counts = {k: len(v) for k, v in groups.items()}
        out = groups[flt]
        return JSONResponse({"today": state, "items": out, "filter": flt, "counts": counts})

    async def mark(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            data = {}
        action = (data or {}).get("action") if isinstance(data, dict) else None
        cid = int(request.path_params["cid"])
        with sessions() as session:
            row = session.get(CRMContact, cid)
            if row is None or row.category != founders.CATEGORY:
                return JSONResponse({"error": "Contact introuvable."}, status_code=404)
            if action == "undo":
                row.dismissed, row.interaction_status = False, "TO_CONTACT"
                row.first_contact_date = row.last_interaction_date = None
                row.next_action = "Envoyer le message LinkedIn"
                note = "Remis à contacter"
            else:
                note = founders.mark(session, cid, str(action))
                if note is None:
                    return JSONResponse({"error": "Action inconnue."}, status_code=400)
            session.commit()
            ids = set(today_state(session)["ids"])
            return JSONResponse({"ok": True, "note": note, "item": item(row, ids)})

    def work() -> None:
        try:
            res = founders.run(settings, sessions, telegram)
            log.info("founders run from Mini App: %s", res.status)
        except Exception as exc:  # noqa: BLE001
            log.warning("founders run failed: %s", type(exc).__name__)
        finally:
            running.clear()

    async def run(request: Request) -> Response:
        with sessions() as session:
            state = today_state(session)
        if not state["can_run"]:
            why = ("recherche déjà en cours" if state["running"] else "liste du jour déjà faite" if state["done"]
                   else "désactivé" if not state["enabled"] else "plafond du jour atteint")
            return JSONResponse({"error": f"Impossible : {why}."}, status_code=409)
        running.set()
        await asyncio.to_thread(lambda: threading.Thread(target=work, name="founders-web", daemon=True).start())
        return JSONResponse({"ok": True, "status": "started"}, status_code=202)

    return [
        Route("/api/founders", listing),
        Route("/api/founders/run", run, methods=["POST"]),
        Route("/api/founders/{cid:int}", mark, methods=["POST"]),
    ]
