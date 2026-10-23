"""CRM commands and buttons for the existing Telegram bot (same bot, same authorized chat)."""

from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from summer_hunter.crm import followups, metrics, sync
from summer_hunter.crm import statuses as st
from summer_hunter.crm.excel_import import ImportReport, WorkbookError
from summer_hunter.crm.service import CRM
from summer_hunter.crm.validation import (
    ValidationError,
    app_code,
    contact_code,
    parse_app_id,
    parse_contact_id,
    parse_date,
    parse_datetime,
)
from summer_hunter.database.models import CRMApplication
from summer_hunter.database.repository import Repository, as_utc
from summer_hunter.engine.feedback import recompute
from summer_hunter.integrations.telegram_client import TelegramError
from summer_hunter.notifications.formatter import safe_url

if TYPE_CHECKING:
    from summer_hunter.bot.handlers import Bot

log = logging.getLogger(__name__)

COMMANDS = [
    ("crm", "Application CRM summary"),
    ("applications", "Active applications"),
    ("interviews", "Upcoming interviews"),
    ("followups", "Pending follow-ups"),
    ("deadlines", "Upcoming application deadlines"),
    ("offers", "Offers received"),
    ("export", "Send the Excel CRM workbook"),
    ("sync", "Import your edits from the Excel workbook"),
    ("company", "Applications for a company: /company <name>"),
]
HELP = (
    "\n\n<b>CRM</b>\n" + "\n".join(f"/{c}: {d}" for c, d in COMMANDS) +
    "\n/applied: submitted applications"
    "\n/setstatus APP-3 INTERVIEW · /interview APP-3 2026-11-05 14:00 [final] · /deadline APP-3 2026-11-15"
    "\n/next APP-3 2026-10-30 Relancer Jane · /note APP-3 texte · /followed APP-3"
    "\n/contact APP-3 Nom | Poste | https://linkedin.com/in/… | Recruiter · /contactstatus C-1 MESSAGE_SENT"
    "\nTu peux aussi m'envoyer le fichier Excel modifié : je l'importe."
)
# Quick status buttons per current status (pre-application uses the confirmation flow for APPLIED).
NEXT_STEPS = {
    "DISCOVERED": ["SHORTLISTED", "TO_APPLY", "WITHDRAWN"],
    "SHORTLISTED": ["TO_APPLY", "PREPARING", "WITHDRAWN"],
    "TO_APPLY": ["PREPARING", "WITHDRAWN", "CLOSED"],
    "PREPARING": ["WITHDRAWN", "CLOSED"],
    "APPLIED": ["ONLINE_ASSESSMENT", "INTERVIEW", "REJECTED", "WITHDRAWN"],
    "ONLINE_ASSESSMENT": ["INTERVIEW", "REJECTED", "WITHDRAWN"],
    "INTERVIEW": ["FINAL_INTERVIEW", "OFFER_RECEIVED", "REJECTED", "WITHDRAWN"],
    "FINAL_INTERVIEW": ["OFFER_RECEIVED", "REJECTED", "WITHDRAWN"],
    "OFFER_RECEIVED": ["OFFER_ACCEPTED", "WITHDRAWN"],
}
MAX_LINES = 30


def _when(dt: datetime | None, tz: str) -> str:
    return as_utc(dt).astimezone(ZoneInfo(tz)).strftime("%d/%m %H:%M") if dt else "—"


def line(app: CRMApplication, tz: str) -> str:
    o = app.opportunity
    extra = []
    if app.date_applied:
        extra.append(f"postulé le {app.date_applied:%d/%m}")
    if app.interview_at:
        extra.append(f"entretien {_when(app.interview_at, tz)}")
    if app.deadline and app.status in st.PRE_APPLICATION:
        extra.append(f"limite {app.deadline:%d/%m}")
    if o.career_category:
        extra.append(o.career_category)
    return (f"<b>{app_code(app.id)}</b> {escape(o.company_name[:30])} · {escape(o.title[:60])} · "
            f"<i>{st.LABEL_FR.get(app.status, app.status)}</i>" + (f" · {escape(', '.join(extra))}" if extra else ""))


def record_text(crm: CRM, app: CRMApplication) -> str:
    o, tz = app.opportunity, crm.tz
    rows = [
        f"<b>{app_code(app.id)} · {escape(o.company_name)}</b>",
        escape(o.title),
        f"Statut : <b>{st.LABEL_FR.get(app.status, app.status)}</b> ({app.status})",
        f"Candidature : {app.date_applied:%d/%m/%Y}" if app.date_applied else "Candidature : pas encore envoyée",
        f"Date limite : {app.deadline:%d/%m/%Y} ({app.deadline_source})" if app.deadline else "Date limite : inconnue",
        f"Prochaine action : {escape(app.next_action or '—')}" + (f" ({app.next_action_date:%d/%m})" if app.next_action_date else ""),
        f"Entretien : {_when(app.interview_at, tz)}",
        f"Recruteur : {escape(app.recruiter_name or 'aucun contact vérifié')}",
        f"Documents : CV v{app.cv_version or 0}, lettre v{app.letter_version or 0}"
        + (f" · version envoyée figée ({escape(Path(app.submitted_folder).name)})" if app.submitted_folder else ""),
        f"Fit {o.fit_score} · recrutabilité {o.recruitability_score if o.recruitability_score is not None else '?'} · "
        f"éligibilité {escape(o.eligibility_status or '?')}" + (f" · {escape(o.career_category)}" if o.career_category else ""),
    ]
    if app.notes:
        rows.append(f"Notes : {escape(app.notes[:400])}")
    return "\n".join(rows)


def record_buttons(app: CRMApplication, webapp_url: str | None = None) -> list[list[dict[str, str]]]:
    steps = NEXT_STEPS.get(app.status, [])
    rows: list[list[dict[str, str]]] = []
    if app.status in st.PRE_APPLICATION:
        rows.append([{"text": "✅ Postulé", "callback_data": f"ap:{app.opportunity_id}"}])
    quick = [{"text": st.LABEL_FR[s], "callback_data": f"cs:{app.id}:{s}"} for s in steps]
    rows += [quick[i:i + 3] for i in range(0, len(quick), 3)]
    if app.status == "APPLIED":
        rows.append([{"text": "📨 Relance faite", "callback_data": f"cf:{app.id}"}])
    links = []
    url = safe_url(app.opportunity.apply_url) or safe_url(app.opportunity.official_url)
    if url:
        links.append({"text": "🌐 Offre", "url": url})
    if safe_url(webapp_url):
        links.append({"text": "📱 Fiche", "web_app": {"url": f"{webapp_url}/#/o/{app.opportunity_id}"}})
    if links:
        rows.append(links)
    return rows


class CRMBot:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.settings = bot.settings

    def crm(self, session) -> CRM:  # noqa: ANN001
        return CRM(session, self.settings.data_dir, self.settings.timezone)

    def _list(self, title: str, rows: list[str], empty: str) -> None:
        body = "\n".join(rows[:MAX_LINES]) + (f"\n… +{len(rows) - MAX_LINES}" if len(rows) > MAX_LINES else "")
        self.bot.send(f"<b>{title}</b>\n" + (body or empty))

    # Commands ------------------------------------------------------------------------------
    def handle_command(self, cmd: str, args: str) -> bool:
        handler = getattr(self, f"cmd_{cmd}", None)
        if handler is None:
            return False
        try:
            handler(args.strip())
        except (ValidationError, st.TransitionError) as exc:
            self.bot.send(f"⚠️ {escape(str(exc))}")
        return True

    def cmd_crm(self, args: str) -> None:
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            m = metrics.compute(crm)

            def pct(v: float | None) -> str:
                return "n/a" if v is None else f"{v:.0%}"

            self.bot.send("\n".join([
                "<b>CRM candidatures</b>",
                f"Offres découvertes : {m.discovered} · présélectionnées : {m.shortlisted}",
                f"Suivies : {m.tracked} · envoyées : {m.submitted} · en attente : {m.awaiting}",
                f"Entretiens prévus : {m.interviews_scheduled} · offres : {m.offers}",
                f"Refus : {m.rejections} · retirées : {m.withdrawn} · relances à faire : {m.followups}",
                f"Taux de réponse {pct(m.response_rate)} · entretien {pct(m.interview_rate)} · "
                f"entretien→offre {pct(m.interview_to_offer)} · succès {pct(m.success_rate)}",
            ]))

    def cmd_applications(self, args: str) -> None:
        """Active applications, optionally filtered by career category (/applications maintenance)."""
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            apps = crm.applications(st.ACTIVE)
            title = "Candidatures actives"
            if args:
                needle = args.lower()
                apps = [a for a in apps if needle in (a.opportunity.career_category or "").lower()
                        or needle in (a.opportunity.job_family or "").replace("_", " ")]
                title += f" · {escape(args[:40])}"
            self._list(title, [line(a, crm.tz) for a in apps], "Aucune. Bouton Track sur une offre pour commencer.")

    def cmd_applied(self, args: str) -> None:
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            apps = sorted((a for a in crm.applications() if a.date_applied), key=lambda a: a.date_applied, reverse=True)
            self._list("Candidatures envoyées", [line(a, crm.tz) for a in apps], "Aucune pour l'instant.")

    def cmd_interviews(self, args: str) -> None:
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            today = crm.today()
            apps = sorted((a for a in crm.applications() if a.interview_at and crm.local_date(a.interview_at) >= today),
                          key=lambda a: as_utc(a.interview_at))
            self._list("Entretiens à venir", [line(a, crm.tz) for a in apps], "Aucun entretien enregistré.")

    def cmd_followups(self, args: str) -> None:
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            items = [r for r in followups.all_suggestions(crm) if r.kind != "deadline"]
            self._list("Relances suggérées (rien n'est envoyé automatiquement)", [f"• {escape(r.text)}" for r in items],
                       "Aucune relance à faire.")

    def cmd_deadlines(self, args: str) -> None:
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            today = crm.today()
            apps = sorted((a for a in crm.applications(st.PRE_APPLICATION) if a.deadline and a.deadline >= today),
                          key=lambda a: a.deadline)
            unknown = sum(1 for a in crm.applications(st.PRE_APPLICATION) if not a.deadline)
            rows = [f"{line(a, crm.tz)} (J-{(a.deadline - today).days})" for a in apps]
            if unknown:
                rows.append(f"<i>{unknown} candidature(s) sans date limite connue (aucune n'est inventée ; /deadline pour en fixer une).</i>")
            self._list("Dates limites", rows, "Aucune date limite connue.")

    def cmd_offers(self, args: str) -> None:
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            self._list("Offres reçues", [line(a, crm.tz) for a in crm.applications(st.OFFERS)], "Pas encore d'offre.")

    def cmd_company(self, args: str) -> None:
        if not args:
            self.bot.send("Usage : /company &lt;nom&gt;")
            return
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            self._list(f"Candidatures · {escape(args[:40])}", [line(a, crm.tz) for a in crm.by_company(args)], "Aucune.")

    def cmd_export(self, args: str) -> None:
        pending = sync.local_edits_pending(self.settings, self.bot.session_factory)
        if pending and pending.applied:
            self.bot.send("⚠️ Le classeur local contient des modifications non importées : je ne l'écrase pas. "
                          "Lance /sync d'abord.")
            return
        path = sync.export(self.settings, self.bot.session_factory)
        if pending and (pending.conflicts or pending.errors):
            self.bot.send("Le classeur local avait des modifications en conflit ou invalides : il a été régénéré, "
                          "l'ancienne version est gardée dans data/crm/backups.")
        try:
            self.bot.telegram.send_document(self.bot.chat_id, path, "CRM Summer Hunter 2027 (source : SQLite)")
        except TelegramError as exc:
            self.bot.send(f"Envoi impossible : {escape(str(exc))}")

    def cmd_sync(self, args: str) -> None:
        path = sync.workbook_path(self.settings)
        if not path.exists():
            self.bot.send("Aucun classeur local. /export pour le créer, ou envoie-moi ton fichier Excel modifié.")
            return
        self.run_import(path)

    def cmd_setstatus(self, args: str) -> None:
        ref, _, status = args.partition(" ")
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            app = self._app(crm, ref)
            if status.strip().upper() == "APPLIED" and app.status in st.PRE_APPLICATION:
                self.bot.send("Pour confirmer l'envoi, utilise le bouton ✅ Postulé :", [[{"text": "✅ Postulé",
                              "callback_data": f"ap:{app.opportunity_id}"}]])
                return
            crm.set_status(app, status, "telegram")
            self._after_change(crm, app)
            session.commit()

    def cmd_interview(self, args: str) -> None:
        parts = args.split()
        if len(parts) < 2:
            raise ValidationError("usage : /interview APP-3 2026-11-05 14:00 [final]")
        final = parts[-1].lower() == "final"
        when_text = " ".join(parts[1:-1] if final else parts[1:])
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            app = self._app(crm, parts[0])
            when = parse_datetime(when_text, crm.tz, "interview date")
            crm.record_interview(app, when, "telegram", final=final)
            self._after_change(crm, app)
            session.commit()

    def cmd_deadline(self, args: str) -> None:
        ref, _, value = args.partition(" ")
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            app = self._app(crm, ref)
            crm.update_fields(app, {"deadline": parse_date(value, "deadline")}, "telegram")
            self._after_change(crm, app)
            session.commit()

    def cmd_note(self, args: str) -> None:
        ref, _, text = args.partition(" ")
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            app = self._app(crm, ref)
            note = text.strip()
            merged = f"{app.notes}\n{crm.today():%Y-%m-%d} {note}" if app.notes else f"{crm.today():%Y-%m-%d} {note}"
            crm.update_fields(app, {"notes": merged}, "telegram")
            self._after_change(crm, app)
            session.commit()

    def cmd_next(self, args: str) -> None:
        parts = args.split(maxsplit=2)
        if len(parts) < 3:
            raise ValidationError("usage : /next APP-3 2026-10-30 Relancer Jane")
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            app = self._app(crm, parts[0])
            crm.update_fields(app, {"next_action_date": parse_date(parts[1], "next action date"), "next_action": parts[2]},
                              "telegram")
            self._after_change(crm, app)
            session.commit()

    def cmd_followed(self, args: str) -> None:
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            app = self._app(crm, args.split()[0] if args else "")
            crm.follow_up_done(app, "telegram")
            session.commit()
            self.bot.send(f"📨 Relance notée pour {app_code(app.id)}. Prochaine suggestion dans 10 jours ouvrés sans réponse.")

    def cmd_contact(self, args: str) -> None:
        ref, _, rest = args.partition(" ")
        fields = [f.strip() for f in rest.split("|")]
        if not fields or not fields[0]:
            raise ValidationError("usage : /contact APP-3 Nom | Poste | https://linkedin.com/in/… | Recruiter")
        name, position, url, ctype = (fields + [None] * 4)[:4]
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            app = self._app(crm, ref)
            contact = crm.add_contact(app.opportunity.company_name, name, "telegram", position=position or None,
                                      linkedin_url=url or None, application=app, contact_type=ctype or "Other")
            session.commit()
            self.bot.send(f"👤 Contact {contact_code(contact.id)} ajouté à {app_code(app.id)}." if contact
                          else "Ce contact existe déjà pour cette entreprise.")

    def cmd_contactstatus(self, args: str) -> None:
        ref, _, status = args.partition(" ")
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            contact = crm.contact(parse_contact_id(ref))
            if contact is None:
                raise ValidationError(f"contact {ref} introuvable")
            crm.set_contact_status(contact, status, "telegram")
            session.commit()
            self.bot.send(f"👤 {contact_code(contact.id)} {escape(contact.name)} : {contact.interaction_status}")

    def _app(self, crm: CRM, ref: str) -> CRMApplication:
        app = crm.application(parse_app_id(ref))
        if app is None:
            raise ValidationError(f"candidature {ref} introuvable")
        return app

    def _after_change(self, crm: CRM, app: CRMApplication) -> None:
        crm.s.flush()
        self.bot.send("✅ Mis à jour.\n\n" + record_text(crm, app), record_buttons(app, self._webapp(crm)))

    def _webapp(self, crm: CRM) -> str | None:
        return Repository(crm.s).get_kv("webapp_url")

    # Excel import -----------------------------------------------------------------------------
    def run_import(self, path: Path, prefer_excel: bool = False) -> ImportReport | None:
        try:
            report = sync.import_file(self.settings, self.bot.session_factory, path, prefer_excel=prefer_excel)
        except WorkbookError as exc:
            self.bot.send(f"⛔ Import refusé : {escape(str(exc))}")
            return None
        lines = [f"<b>Import Excel</b> : {escape(report.summary())}"]
        lines += [f"✅ {escape(a)}" for a in report.applied[:15]]
        lines += [f"⚠️ {escape(e)}" for e in report.errors[:10]]
        lines += [f"· {escape(i)}" for i in report.ignored[:5]]
        buttons = None
        if report.conflicts:
            lines.append("\n<b>Conflits</b> (la base a changé depuis l'export de ce fichier) :")
            lines += [f"• {c.record} {c.field} : Excel « {escape(str(c.workbook))} » ≠ base « {escape(str(c.database))} »"
                      for c in report.conflicts[:10]]
            lines.append("Rien n'a été appliqué pour ces lignes. Choisis :")
            with self.bot.session_factory() as session:
                Repository(session).set_kv("crm_pending_import", str(path))
                session.commit()
            buttons = [[{"text": "Appliquer Excel", "callback_data": "ci:1"},
                        {"text": "Garder la base", "callback_data": "ci:0"}]]
        if report.backup:
            lines.append(f"<i>Sauvegarde : {escape(report.backup.name)}</i>")
        self.bot.send("\n".join(lines), buttons)
        return report

    def receive_document(self, document: dict) -> None:
        name = str(document.get("file_name") or "")
        if not name.lower().endswith(".xlsx"):
            self.bot.send("Seuls les classeurs .xlsx du CRM sont importés.")
            return
        if int(document.get("file_size") or 0) > 10_000_000:
            self.bot.send("Fichier trop gros (10 Mo maximum).")
            return
        inbox = sync.crm_dir(self.settings) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / f"{datetime.now():%Y%m%d-%H%M%S}.xlsx"
        try:
            self.bot.telegram.download_file(document["file_id"], dest)
        except (TelegramError, KeyError) as exc:
            self.bot.send(f"Téléchargement impossible : {escape(str(exc))}")
            return
        self.bot.send("📥 Classeur reçu, import…")
        self.run_import(dest)

    # Callbacks ---------------------------------------------------------------------------------
    def handle_callback(self, cq: dict, action: str, parts: list[str]) -> bool:
        """CRM callbacks that do not start from an opportunity id. Returns False when not ours."""
        if action == "ci":
            with self.bot.session_factory() as session:
                repo = Repository(session)
                pending = repo.get_kv("crm_pending_import")
                if pending:
                    repo.set_kv("crm_pending_import", "")
                session.commit()
            self.bot.telegram.answer_callback(cq["id"])
            if not pending:
                self.bot.send("Aucun import en attente.")
            elif parts[1] == "1":
                self.run_import(Path(pending), prefer_excel=True)
            else:
                path = sync.export(self.settings, self.bot.session_factory)
                self.bot.send("Base conservée, classeur régénéré (l'ancien est gardé dans data/crm/backups).")
                try:
                    self.bot.telegram.send_document(self.bot.chat_id, path, "CRM à jour")
                except TelegramError as exc:
                    log.warning("crm workbook send failed", extra={"error": str(exc)})
            return True
        if action not in {"ca", "cs", "cf"}:
            return False
        with self.bot.session_factory() as session:
            crm = self.crm(session)
            app = crm.application(int(parts[1]))
            if app is None:
                self.bot.telegram.answer_callback(cq["id"], "Candidature introuvable")
                return True
            try:
                if action == "cs" and len(parts) == 3:
                    if parts[2] == "APPLIED":
                        raise st.TransitionError("utilise ✅ Postulé pour confirmer l'envoi")
                    crm.set_status(app, parts[2], "telegram")
                    self.bot.telegram.answer_callback(cq["id"], st.LABEL_FR.get(parts[2], parts[2]))
                elif action == "cf":
                    crm.follow_up_done(app, "telegram")
                    self.bot.telegram.answer_callback(cq["id"], "Relance notée")
                else:
                    self.bot.telegram.answer_callback(cq["id"])
            except (ValidationError, st.TransitionError) as exc:
                self.bot.telegram.answer_callback(cq["id"], str(exc)[:190])
                return True
            session.commit()
            self.bot.send(record_text(crm, app), record_buttons(app, self._webapp(crm)))
        return True

    def handle_opportunity_callback(self, cq: dict, action: str, opp, repo: Repository) -> bool:  # noqa: ANN001
        """Track / Mark applied (with confirmation) on an opportunity card."""
        crm = self.crm(repo.s)
        if action == "tr":
            app, created = crm.track(opp, "telegram")
            self.bot.telegram.answer_callback(cq["id"], "Candidature suivie" if created else "Déjà suivie")
            self.bot.send(("📌 Suivi créé.\n\n" if created else "") + record_text(crm, app), record_buttons(app, self._webapp(crm)))
            return True
        if action == "ap":
            existing = crm.application_for(opp.id)
            if existing and existing.date_applied:
                self.bot.telegram.answer_callback(cq["id"], f"Déjà postulé le {existing.date_applied:%d/%m}")
                return True
            self.bot.telegram.answer_callback(cq["id"])
            self.bot.send(f"Confirmer que ta candidature pour <b>{escape(opp.title[:80])}</b> @ {escape(opp.company_name[:40])} "
                          f"est envoyée (date : aujourd'hui) ?",
                          [[{"text": "✅ Oui, envoyée", "callback_data": f"apc:{opp.id}"},
                            {"text": "Annuler", "callback_data": f"apx:{opp.id}"}]])
            return True
        if action == "apc":
            repo.record_feedback(opp, "applied", source="telegram", data_dir=self.settings.data_dir,
                                 timezone=self.settings.timezone)
            recompute(repo, self.settings.scoring)
            app = crm.application_for(opp.id)
            self.bot.telegram.answer_callback(cq["id"], "Candidature enregistrée")
            self._clear_buttons(cq)
            self.bot.send("✅ Candidature enregistrée.\n\n" + record_text(crm, app), record_buttons(app, self._webapp(crm)))
            return True
        if action == "apx":
            self.bot.telegram.answer_callback(cq["id"], "Annulé")
            self._clear_buttons(cq)
            return True
        return False

    def _clear_buttons(self, cq: dict) -> None:
        message = cq.get("message") or {}
        if message.get("message_id"):
            try:
                self.bot.telegram.edit_buttons(self.bot.chat_id, message["message_id"], None)
            except TelegramError:
                pass
