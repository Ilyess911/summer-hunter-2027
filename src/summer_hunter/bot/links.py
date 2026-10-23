"""'Send a link, get everything': read the posting, evaluate it, then CV and cover letter."""

from __future__ import annotations

import asyncio
import logging
import threading
from html import escape

from summer_hunter.collectors.link_ingest import NeedsText, fetch_posting, from_pasted_text, is_linkedin
from summer_hunter.database.repository import Repository
from summer_hunter.integrations.anthropic_client import LLMError, NullProvider
from summer_hunter.integrations.telegram_client import TelegramError
from summer_hunter.notifications import formatter
from summer_hunter.pipeline import CycleStats, Hunter

log = logging.getLogger(__name__)


def _local_model(settings):  # noqa: ANN001, ANN202
    from summer_hunter.web.letter import build_provider

    try:
        return build_provider(settings.llm)
    except LLMError:
        return None


def ask_for_text(settings, sessions, telegram, url: str | None, reason: str) -> None:  # noqa: ANN001
    note = ("LinkedIn ne peut pas être lu automatiquement." if url and is_linkedin(url) else f"Lecture impossible : {escape(reason)}.")
    msg_id = telegram.send_message(
        settings.telegram_chat_id,
        f"{note}\n<b>Réponds à ce message en collant le texte de l'offre.</b>\n"
        "Astuce : commence par une ligne <code>Titre | Entreprise | Lieu</code> pour un résultat exact.")
    with sessions() as session:
        Repository(session).set_kv(f"paste_msg:{msg_id}", url or "")
        session.commit()


def process(settings, sessions, telegram, url: str | None, text: str | None = None, auto_documents: bool = True) -> None:  # noqa: ANN001
    chat = settings.telegram_chat_id
    provider = _local_model(settings)
    hunter = Hunter(settings, sessions, provider=NullProvider())
    try:
        if text:
            raw = from_pasted_text(url, text, provider)
        else:
            async def read():
                async with hunter._http() as http:
                    return await fetch_posting(http, url, provider)
            raw = asyncio.run(read())
    except NeedsText as exc:
        ask_for_text(settings, sessions, telegram, url, str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("link ingestion failed")
        ask_for_text(settings, sessions, telegram, url, type(exc).__name__)
        return

    stats = CycleStats()
    with sessions() as session:
        repo = Repository(session)
        opp = hunter.ingest(repo, raw, stats)
        hunter.evaluate(repo, opp, None, stats)
        session.commit()
        ev, fit = repo.latest_eligibility(opp.id), repo.latest_fit(opp.id)
        card_text, buttons = formatter.card(opp, ev.criteria, fit.report, fit.networking, fit.priority,
                                            repo.contacts(opp.id), repo.get_kv("webapp_url"), fit.recruitability,
                                            fit.compensation, fit.strategy)
        opp_id, decision = opp.id, opp.eligibility_status
        fails = [c["explanation"] for c in ev.criteria if c["status"] == "FAIL"]
        merged = "déjà connue (fusionnée)" if stats.merged_duplicates else "ajoutée"
    telegram.send_message(chat, f"🔗 Offre {merged} : #{opp_id}\n\n" + card_text, buttons)

    if decision == "REJECTED":
        telegram.send_message(chat, "⛔ Rejetée par tes critères : " + escape("; ".join(fails[:2])) +
                              "\nTu peux quand même générer les documents :",
                              [[{"text": "📄 CV quand même", "callback_data": f"cv:{opp_id}"},
                                {"text": "✉️ Lettre quand même", "callback_data": f"lm:{opp_id}"}]])
        return
    if not auto_documents:
        return
    from summer_hunter.crm.service import CRM

    with sessions() as session:
        tracked = CRM(session).application_for(opp_id)
        submitted_on = tracked.date_applied if tracked else None
    if submitted_on:
        telegram.send_message(chat, f"📎 Candidature déjà envoyée le {submitted_on:%d/%m} : je ne régénère pas tes documents "
                                    "(la version envoyée est conservée). Utilise les boutons CV / Lettre si tu veux "
                                    "vraiment une nouvelle version.")
        return
    from summer_hunter.web.cv import build_cv
    from summer_hunter.web.letter import run_letter_job

    with sessions() as session:
        o = Repository(session).opportunity(opp_id)
        cv = build_cv(settings.config_dir, settings.data_dir / "cv" / str(opp_id), opp_id, o.company_name, o.title,
                      o.description_text or "", o.job_family)
    for f in [p for p in (cv.pdf_path, cv.tex_path) if p]:
        try:
            telegram.send_document(chat, f, f.stem)
        except TelegramError as exc:
            log.warning("CV send failed", extra={"error": str(exc)})
    telegram.send_message(chat, "✉️ Lettre de motivation en cours (modèle local, 2 à 3 min)…")
    run_letter_job(settings, sessions, telegram, opp_id)


def start(settings, sessions, telegram, url: str | None, text: str | None = None) -> None:  # noqa: ANN001
    threading.Thread(target=process, args=(settings, sessions, telegram, url, text), daemon=True).start()
