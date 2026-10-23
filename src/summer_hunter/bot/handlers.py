"""Telegram bot: commands and inline-button callbacks. Only the configured chat is served."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from summer_hunter.bot.crm_handlers import COMMANDS as CRM_COMMANDS
from summer_hunter.bot.crm_handlers import HELP as CRM_HELP
from summer_hunter.bot.crm_handlers import CRMBot
from summer_hunter.database.models import utcnow
from summer_hunter.database.repository import Repository, as_utc
from summer_hunter.engine.ai_analysis import Analyzer, draft_networking_message
from summer_hunter.engine.feedback import DISMISS_REASONS, recompute
from summer_hunter.engine.geo import country_name
from summer_hunter.integrations.anthropic_client import LLMProvider, NullProvider
from summer_hunter.integrations.telegram_client import TelegramClient, TelegramError
from summer_hunter.models.candidate import CandidateProfile
from summer_hunter.notifications import formatter
from summer_hunter.notifications.notifier import DeliveryReport, Notifier
from summer_hunter.utils.settings import Settings

log = logging.getLogger(__name__)

COMMANDS = [
    ("start", "Welcome and quick status"),
    ("help", "List commands"),
    ("top10", "Ten best open opportunities"),
    ("today", "Opportunities discovered in the last 24 h"),
    ("stats", "Counts, sources and AI spend"),
    ("saved", "Saved opportunities"),
    ("applied", "Opportunities marked as applied"),
    ("rejected", "Recently rejected and why"),
    ("status", "Last runs and source health"),
    ("settings", "Current non-secret configuration"),
    ("industry", "Industry & robotics track: best open opportunities"),
    ("maintenance", "Maintenance & reliability internships"),
    ("robotics", "Robotics & RaaS internships"),
    ("industrial_projects", "Industrial project management internships"),
    ("high_recruitability", "Best match with your verified experience"),
]
TRACK_FILTERS = {   # command -> (title, predicate on an opportunity)
    "industry": ("Industry & robotics track", lambda o: o.career_track == "industry_robotics"),
    "maintenance": ("Maintenance & reliability", lambda o: o.job_family == "maintenance_reliability"),
    "robotics": ("Robotics & RaaS", lambda o: o.job_family in {"robotics_raas", "robotics_specialized"}),
    "industrial_projects": ("Industrial project management", lambda o: o.job_family == "industrial_project_management"),
}
HIGH_RECRUITABILITY = 75

COMMANDS += CRM_COMMANDS

HELP = "\n".join(f"/{c}: {d}" for c, d in COMMANDS) + (
    "\n\nEnvoie un lien d'offre (ou colle son texte) : je l'évalue, puis je génère ton CV et ta lettre."
    "\n\nButtons: Save, Track (creates the CRM record), Applied (asks for confirmation), Dismiss (asks why, to learn), "
    "Explain Score, Networking msg (a draft for you to send yourself; nothing is ever sent to recruiters automatically)."
) + CRM_HELP


def _line(o) -> str:  # noqa: ANN001
    score = f"{o.priority_score:.0f}" if o.priority_score is not None else "-"
    return (f"#{o.id} <b>{escape(o.title[:70])}</b> @ {escape(o.company_name[:40])}, {escape(country_name(o.country_code))} · "
            f"prio {score} · fit {o.fit_score} · {formatter.DECISION_LABEL.get(o.eligibility_status or '', '?')}")


class Bot:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session], telegram: TelegramClient,
                 provider: LLMProvider | None = None) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.telegram = telegram
        self.provider = provider or NullProvider()
        self.chat_id = str(settings.telegram_chat_id)
        self.profile = CandidateProfile.from_yaml(settings.profile_raw)
        self.crm = CRMBot(self)

    # Entry points ----------------------------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            cq = update["callback_query"]
            chat = str(((cq.get("message") or {}).get("chat") or {}).get("id"))
            if chat != self.chat_id:
                log.warning("callback from unauthorized chat ignored")
                return
            self.handle_callback(cq)
            return
        msg = update.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id"))
        if chat != self.chat_id:
            log.warning("message from unauthorized chat ignored")
            return
        if msg.get("document"):
            self.crm.receive_document(msg["document"])
            return
        text = (msg.get("text") or "").strip()
        reply_to = (msg.get("reply_to_message") or {}).get("message_id")
        if reply_to and text and not text.startswith("/"):
            with self.session_factory() as session:
                opp_id = Repository(session).get_kv(f"lm_msg:{reply_to}")
            if opp_id:
                from summer_hunter.web.letter import start_letter_job

                self.send("Révision en cours (2 à 3 min)…")
                start_letter_job(self.settings, self.session_factory, self.telegram, int(opp_id), revision=text)
                return
        if reply_to and text and not text.startswith("/"):
            with self.session_factory() as session:
                pasted_for = Repository(session).get_kv(f"paste_msg:{reply_to}")
            if pasted_for is not None:
                from summer_hunter.bot import links

                self.send("📥 Texte reçu, analyse de l'offre…")
                links.start(self.settings, self.session_factory, self.telegram, pasted_for or None, text)
                return
        if text.startswith("/"):
            head, _, args = text.partition(" ")
            self.handle_command(head[1:].split("@")[0].lower(), args)
            return
        from summer_hunter.collectors.link_ingest import first_url

        url = first_url(text)
        if url:
            from summer_hunter.bot import links

            self.send("🔎 Lecture de l'offre… (carte, puis CV, puis lettre)")
            links.start(self.settings, self.session_factory, self.telegram, url)
        elif len(text) > 400:
            from summer_hunter.bot import links

            self.send("📥 Texte d'offre reçu, analyse…")
            links.start(self.settings, self.session_factory, self.telegram, None, text)

    def poll_forever(self, stop_after_s: float | None = None) -> None:
        started = time.monotonic()
        with self.session_factory() as session:
            offset = Repository(session).get_kv("telegram_offset")
        offset_int = int(offset) if offset else None
        try:
            self.telegram.set_commands(COMMANDS)
        except TelegramError as exc:
            log.warning("setMyCommands failed", extra={"error": str(exc)})
        backoff = 1
        while stop_after_s is None or time.monotonic() - started < stop_after_s:
            try:
                updates = self.telegram.get_updates(offset_int, timeout=30)
                backoff = 1
            except TelegramError as exc:
                log.warning("getUpdates failed", extra={"error": str(exc)})
                time.sleep(min(backoff, 60))
                backoff *= 2
                continue
            for upd in updates:
                offset_int = int(upd["update_id"]) + 1
                try:
                    self.handle_update(upd)
                except Exception:  # noqa: BLE001 - one bad update must not kill the bot
                    log.exception("update handling failed")
                with self.session_factory() as session:
                    Repository(session).set_kv("telegram_offset", str(offset_int))
                    session.commit()

    # Commands ------------------------------------------------------------------------------------

    def send(self, text: str, buttons=None) -> None:  # noqa: ANN001
        try:
            self.telegram.send_message(self.chat_id, text, buttons)
        except TelegramError as exc:
            log.warning("send failed", extra={"error": str(exc)})

    def handle_command(self, cmd: str, args: str = "") -> None:
        if self.crm.handle_command(cmd, args):
            return
        with self.session_factory() as session:
            repo = Repository(session)
            if cmd in {"start", "help"}:
                intro = "<b>Summer Hunter 2027</b>\nInternship intelligence for April–August 2027.\n\n" if cmd == "start" else ""
                self.send(intro + HELP)
            elif cmd == "top10":
                rows = repo.top(10)
                self.send("<b>Top 10 open opportunities</b>\n" + ("\n".join(_line(o) for o in rows) or "Nothing scored yet."))
                for o in rows[:3]:
                    Notifier(self.settings, self.session_factory, self.telegram).send_card(repo, o, DeliveryReport())
                session.commit()
            elif cmd == "today":
                rows = repo.discovered_since(utcnow() - timedelta(hours=24))
                rows = [o for o in rows if o.eligibility_status != "REJECTED"][:15]
                self.send("<b>Discovered in the last 24 h (not rejected)</b>\n" + ("\n".join(_line(o) for o in rows) or "None."))
            elif cmd == "saved":
                rows = repo.by_user_status("saved")
                self.send("<b>Saved</b>\n" + ("\n".join(_line(o) for o in rows) or "None yet."))
            elif cmd == "rejected":
                rows = repo.by_eligibility("REJECTED", limit=10)
                lines = []
                for o in rows:
                    ev = repo.latest_eligibility(o.id)
                    why = "; ".join(c["explanation"] for c in (ev.criteria if ev else []) if c["status"] == "FAIL")[:160]
                    lines.append(f"#{o.id} {escape(o.title[:60])} @ {escape(o.company_name[:30])}: <i>{escape(why)}</i>")
                self.send("<b>Recently rejected</b>\n" + ("\n".join(lines) or "None."))
            elif cmd == "stats":
                c = repo.counts()
                spend = repo.usage_summary(7)
                lines = [f"Opportunities: {c['total']} (eligible {c.get('ELIGIBLE', 0)}, verify {c.get('NEEDS_VERIFICATION', 0)}, "
                         f"rejected {c.get('REJECTED', 0)})",
                         f"Saved {c['saved']} · Applied {c['applied']} · Dismissed {c['dismissed']}",
                         f"Source records {c['source_records']} · LinkedIn posts {c['linkedin_posts']}",
                         "AI spend (7 days): " + (", ".join(f"{d} ${v:.3f} ({n} calls)" for d, n, v in spend) or "none")]
                self.send("<b>Stats</b>\n" + "\n".join(escape(l) for l in lines))
            elif cmd == "status":
                run = repo.last_run(successful_only=False)
                tz = ZoneInfo(self.settings.timezone)
                run_line = (f"Last run #{run.id} ({run.trigger}) {run.status}, started "
                            f"{as_utc(run.started_at).astimezone(tz):%d %b %H:%M}") if run else "No run yet."
                health = [f"{'✅' if (h.consecutive_failures or 0) == 0 else '⚠️'} {h.source}: {h.last_status}, {h.last_item_count} items"
                          + (f", {h.consecutive_failures} failures" if h.consecutive_failures else "") for h in repo.health()]
                self.send("<b>Status</b>\n" + escape(run_line) + "\n" + escape("\n".join(health[:30]) or "No source health yet."))
            elif cmd in TRACK_FILTERS or cmd == "high_recruitability":
                pool = repo.top(500)
                if cmd == "high_recruitability":
                    title = f"Recruitability ≥ {HIGH_RECRUITABILITY} (match with verified experience, not a probability)"
                    rows = sorted((o for o in pool if (o.recruitability_score or 0) >= HIGH_RECRUITABILITY),
                                  key=lambda o: -(o.recruitability_score or 0))
                else:
                    title, keep = TRACK_FILTERS[cmd]
                    rows = [o for o in pool if keep(o)]
                lines = [_line(o) + f" · recruit {o.recruitability_score if o.recruitability_score is not None else '-'}"
                         for o in rows[:20]]
                self.send(f"<b>{escape(title)}</b>\n" + ("\n".join(lines) or "Nothing open right now.")
                          + (f"\n… +{len(rows) - 20}" if len(rows) > 20 else ""))
            elif cmd == "settings":
                summary = self.settings.public_summary()
                prefs = repo.preference_adjustments()
                lines = [f"{k}: {v}" for k, v in summary.items()]
                lines.append("learned preferences: " + (", ".join(f"{p.dimension}={p.key} {p.delta:+g}" for p in prefs) or "none"))
                lines.append("Edit config/*.yaml to change; `summer-hunter preferences reset` clears learning.")
                self.send("<b>Settings</b>\n" + escape("\n".join(lines)))
            else:
                self.send("Unknown command.\n\n" + HELP)

    # Callbacks -------------------------------------------------------------------------------------

    def handle_callback(self, cq: dict) -> None:
        data = cq.get("data") or ""
        parts = data.split(":")
        if len(parts) < 2 or not parts[1].isdigit():
            self.telegram.answer_callback(cq["id"], "Unknown action")
            return
        action, opp_id = parts[0], int(parts[1])
        if self.crm.handle_callback(cq, action, parts):
            return
        message = cq.get("message") or {}
        with self.session_factory() as session:
            repo = Repository(session)
            opp = repo.opportunity(opp_id)
            if opp is None:
                self.telegram.answer_callback(cq["id"], "Opportunity not found")
                return
            if action == "sv":
                repo.record_feedback(opp, "save")
                recompute(repo, self.settings.scoring)
                self.telegram.answer_callback(cq["id"], "Saved")
            elif self.crm.handle_opportunity_callback(cq, action, opp, repo):
                pass
            elif action == "dm":
                repo.record_feedback(opp, "dismiss")
                recompute(repo, self.settings.scoring)
                self.telegram.answer_callback(cq["id"], "Dismissed")
                self.send(f"Why dismiss #{opp.id} ({escape(opp.title[:60])})? (optional, used to learn)", formatter.dismiss_reason_buttons(opp.id))
            elif action == "rs" and len(parts) == 3 and parts[2] in DISMISS_REASONS:
                repo.record_feedback(opp, "reason", parts[2])
                rows = recompute(repo, self.settings.scoring)
                self.telegram.answer_callback(cq["id"], "Thanks, preference updated")
                if message.get("message_id"):
                    try:
                        self.telegram.edit_buttons(self.chat_id, message["message_id"], None)
                    except TelegramError:
                        pass
                log.info("preferences recomputed", extra={"rows": len(rows)})
            elif action == "ex":
                ev, fit = repo.latest_eligibility(opp.id), repo.latest_fit(opp.id)
                self.telegram.answer_callback(cq["id"])
                if ev and fit:
                    self.send(formatter.explain(opp, ev.criteria, fit.report, fit.networking, fit.priority, ev.sponsorship,
                                                fit.recruitability))
            elif action == "cv":
                self.telegram.answer_callback(cq["id"], "CV en préparation…")
                from summer_hunter.web.cv import build_cv

                try:
                    result = build_cv(self.settings.config_dir, self.settings.data_dir / "cv" / str(opp.id), opp.id,
                                      opp.company_name, opp.title, opp.description_text or "", opp.job_family)
                except FileNotFoundError as exc:
                    self.send(f"CV impossible : {escape(str(exc))}")
                else:
                    for f in [p for p in (result.pdf_path, result.tex_path) if p]:
                        try:
                            self.telegram.send_document(self.chat_id, f, f.stem)
                        except TelegramError as exc:
                            log.warning("sendDocument failed", extra={"error": str(exc)})
                    if not result.pdf_path:
                        self.send(f"PDF non compilé, le .tex est joint. {escape(result.log[:300])}")
            elif action == "lm":
                from summer_hunter.web.letter import start_letter_job

                self.telegram.answer_callback(cq["id"], "Lettre en cours…")
                self.send(f"✉️ Lettre pour #{opp.id} en cours de rédaction (modèle local, 2 à 3 min).")
                start_letter_job(self.settings, self.session_factory, self.telegram, opp.id)
            elif action == "lr" and len(parts) == 3:
                from summer_hunter.web.letter import REVISIONS, start_letter_job

                if parts[2] in REVISIONS:
                    self.telegram.answer_callback(cq["id"], "Révision en cours…")
                    self.send("Révision en cours (2 à 3 min)…")
                    start_letter_job(self.settings, self.session_factory, self.telegram, opp.id, revision=REVISIONS[parts[2]])
            elif action == "nm":
                self.telegram.answer_callback(cq["id"], "Drafting…")
                contacts = repo.contacts(opp.id)
                best = max(contacts, key=lambda c: c.confidence, default=None)
                post = repo.linkedin_post(opp.linkedin_post_url) if opp.linkedin_post_url else None
                analyzer = Analyzer(self.provider, repo, self.profile, self.settings.llm, self.settings.timezone) \
                    if not isinstance(self.provider, NullProvider) else None
                text, method = draft_networking_message(self.provider, analyzer, best.name if best else None,
                                                        best.role if best else None, opp.company_name, opp.title,
                                                        post.text if post else None, self.profile)
                target = (f"To: {escape(best.name)} ({escape(best.relationship_type.replace('_', ' '))})" if best
                          else "No verified contact: adapt before sending to the recruiting team.")
                self.send(f"<b>Draft networking message #{opp.id}</b> ({escape(method)})\n{target}\n\n<pre>{escape(text)}</pre>\n"
                          "<i>Not sent. Copy, review and send it yourself.</i>")
            else:
                self.telegram.answer_callback(cq["id"], "Unknown action")
            session.commit()
