"""V3 Telegram commands and buttons: compensation, global priority, Africa, networking, signals, stats, modes."""

from __future__ import annotations

import logging
from html import escape
from typing import TYPE_CHECKING

from summer_hunter.crm.service import CRM
from summer_hunter.database.repository import Repository
from summer_hunter.engine import analytics, modes, signals
from summer_hunter.engine.feedback import recompute
from summer_hunter.engine.geo import country_name
from summer_hunter.engine.regions import REGIONS
from summer_hunter.engine.strategist import LABEL_FR, balanced
from summer_hunter.integrations.telegram_client import TelegramError
from summer_hunter.notifications.formatter import safe_url

if TYPE_CHECKING:
    from summer_hunter.bot.handlers import Bot

log = logging.getLogger(__name__)

COMMANDS = [
    ("priority", "Highest global application priority"),
    ("paid", "Confirmed paid internships"),
    ("highest_paid", "Ranked by monthly pay (EUR)"),
    ("best_value", "Ranked by estimated monthly balance"),
    ("africa", "African opportunities"),
    ("compensation_unknown", "Strong matches whose pay needs checking"),
    ("networking", "Opportunities with a contact to reach"),
    ("early_signals", "Early recruitment signals (not vacancies)"),
    ("hunter_stats", "Discovery performance metrics"),
    ("mode", "Recruitment mode: /mode exploration | conversion"),
]
UNKNOWN_PAY = {None, "COMPENSATION_UNKNOWN", "PAID_AMOUNT_UNKNOWN", "MIXED_OR_CONDITIONAL"}
MAX = 15


def pay_label(o) -> str:  # noqa: ANN001
    if o.monthly_min_eur is not None:
        same = o.monthly_max_eur is None or o.monthly_max_eur - o.monthly_min_eur < max(1.0, 0.01 * o.monthly_min_eur)
        rng = (f"{o.monthly_min_eur:,.0f}" if same
               else f"{o.monthly_min_eur:,.0f}-{o.monthly_max_eur:,.0f}")
        basis = {"gross": " b", "net": " n"}.get(o.salary_basis or "", " ?")
        return f"{rng} €/mois{basis}"
    return {"UNPAID_CONFIRMED": "non payé", "PAID_AMOUNT_UNKNOWN": "payé, montant ?", "MIXED_OR_CONDITIONAL": "conditionnel"}.get(
        o.compensation_status, "salaire ?")


def line(o) -> str:  # noqa: ANN001
    gp = "–" if o.global_priority is None else f"{o.global_priority:.0f}" + ("*" if o.global_provisional else "")
    action = LABEL_FR.get(o.next_action or "", o.next_action or "?")
    return (f"#{o.id} <b>{escape(o.title[:60])}</b> @ {escape(o.company_name[:30])}, {escape(country_name(o.country_code))} · "
            f"prio {gp} · {escape(pay_label(o))} · {escape(action)}" + (f" · {o.strategy_category.lower()}" if o.strategy_category else ""))


class V3Bot:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.settings = bot.settings

    def _send_list(self, title: str, rows: list, note: str = "", empty: str = "Rien pour l'instant.") -> None:
        body = "\n".join(line(o) for o in rows[:MAX]) or empty
        more = f"\n… +{len(rows) - MAX}" if len(rows) > MAX else ""
        self.bot.send(f"<b>{escape(title)}</b>\n{body}{more}" + (f"\n<i>{escape(note)}</i>" if note else ""))

    def _pool(self, repo: Repository) -> list:
        return repo.top_global(500)

    # Commands ------------------------------------------------------------------------------------------
    def handle_command(self, cmd: str, args: str) -> bool:
        handler = getattr(self, f"cmd_{cmd}", None)
        if handler is None:
            return False
        with self.bot.session_factory() as session:
            handler(Repository(session), args.strip())
            session.commit()
        return True

    def cmd_priority(self, repo: Repository, args: str) -> None:
        alloc = (self.settings.scoring.get("strategist") or {}).get("allocation", {"AMBITIOUS": .25, "TARGETED": .5, "SAFETY": .25})
        top = balanced(self._pool(repo), 10, alloc, lambda o: o.strategy_category, lambda o: o.global_priority)
        mix = {c: sum(o.strategy_category == c for o in top) for c in alloc}
        self._send_list(f"Priorité globale · mode {modes.get(repo, self.settings.scoring)}", top,
                        "* = provisoire (salaire inconnu). Mélange " + ", ".join(f"{k.lower()} {v}" for k, v in mix.items())
                        + ". Ce sont des priorités, pas des probabilités d'embauche.")

    def cmd_paid(self, repo: Repository, args: str) -> None:
        rows = [o for o in self._pool(repo) if o.compensation_status == "PAID_CONFIRMED"]
        self._send_list("Stages payés (montant confirmé par l'offre)", sorted(rows, key=lambda o: -o.global_priority))

    def cmd_highest_paid(self, repo: Repository, args: str) -> None:
        rows = sorted((o for o in self._pool(repo) if o.monthly_min_eur is not None), key=lambda o: -o.monthly_min_eur)
        self._send_list("Mieux payés (minimum de la fourchette)", rows,
                        "Montants annoncés convertis en euros, non corrigés du coût de la vie. Brut / net comme indiqué "
                        "par l'offre (b = brut, n = net, ? = non précisé) : ne compare pas un net à un brut.")

    def cmd_best_value(self, repo: Repository, args: str) -> None:
        rows = sorted((o for o in self._pool(repo) if o.monthly_balance_eur is not None), key=lambda o: -o.monthly_balance_eur)
        if rows:
            self._send_list("Meilleur solde mensuel estimé (salaire − coût de la vie)", rows,
                            "Estimations, pas des dépenses garanties.")
        else:
            self.bot.send("<b>Meilleur solde mensuel estimé</b>\nAucun coût de la vie sourcé n'est configuré "
                          "(config/compensation.yaml, section living_costs) : le solde ne peut pas être estimé honnêtement. "
                          "En attendant : /highest_paid.")

    def cmd_africa(self, repo: Repository, args: str) -> None:
        rows = [o for o in self._pool(repo) if o.country_code in REGIONS["africa"]]
        self._send_list("Afrique", sorted(rows, key=lambda o: -o.global_priority), empty="Aucune offre africaine pertinente en base.")

    def cmd_compensation_unknown(self, repo: Repository, args: str) -> None:
        rows = [o for o in self._pool(repo) if o.compensation_status in UNKNOWN_PAY and (o.recruitability_score or 0) >= 65]
        self._send_list("Bons profils, salaire à vérifier", sorted(rows, key=lambda o: -o.global_priority),
                        "Salaire non indiqué ≠ non payé. Demande-le au recruteur ou cherche sur le site officiel.")

    def cmd_networking(self, repo: Repository, args: str) -> None:
        rows = [o for o in self._pool(repo) if (o.networking_score or 0) >= 75]
        lines = []
        for o in sorted(rows, key=lambda o: -o.global_priority)[:MAX]:
            fit = repo.latest_fit(o.id)
            reco = ((fit.strategy or {}).get("networking") or {}) if fit else {}
            who = (reco.get("contact") or {}).get("name") or "?"
            lines.append(line(o) + f"\n   👤 {escape(who)} : {escape(reco.get('why', ''))}")
        self.bot.send("<b>Contacts à approcher</b>\n" + ("\n".join(lines) or "Aucun contact vérifié pour l'instant.")
                      + "\n<i>Brouillon de message : bouton « Contact recruiter » sur la carte. Rien n'est envoyé seul.</i>")

    def cmd_early_signals(self, repo: Repository, args: str) -> None:
        rows = signals.recent(repo, 30)
        icon = {signals.CONFIRMED: "✅", signals.SIGNAL: "📣", signals.WATCH: "👀"}
        lines = [f"{icon.get(s.level, '•')} {escape(s.level)} · {escape(s.company or '?')} : {escape(s.summary[:120])}"
                 + (f" (<a href=\"{escape(url, quote=True)}\">source</a>)" if (url := safe_url(s.source_url)) else "")
                 for s in rows[:MAX]]
        self.bot.send("<b>Signaux de recrutement (30 j)</b>\n" + ("\n".join(lines) or "Aucun signal.")
                      + "\n<i>Un signal n'est pas une offre : il le devient quand une annonce officielle apparaît.</i>")

    def cmd_hunter_stats(self, repo: Repository, args: str) -> None:
        p = analytics.compute(repo.s, REGIONS)
        failing = [s["source"] for s in p.sources if s["failures"] >= 2]
        self.bot.send("\n".join([
            "<b>Performance du chasseur</b>",
            f"Découvertes : {p.discovered} · pertinentes : {p.relevant} · inéligibles : {p.ineligible} · doublons fusionnés : {p.duplicates_merged}",
            f"Payées confirmées : {p.paid_confirmed} · salaire inconnu : {p.compensation_unknown}",
            f"Taux payé : {p.paid_rate.label()} · couverture salaire : {p.compensation_coverage.label()}",
            f"Precision@10 : {p.precision_at_10.label()} · actionnabilité : {p.actionability.label()}",
            f"Contacts : {p.contacts} · candidatures : {p.applications_created} suivies, {p.applications_submitted} envoyées, "
            f"{p.interviews} entretiens, {p.offers} offres",
            "Par région : " + ", ".join(f"{k} {v}" for k, v in p.by_region.items()),
            f"Sources en échec : {', '.join(failing[:6]) or 'aucune'} · LLM : {p.llm_calls} appels, {p.llm_cost_usd:.2f} $",
            "<i>Petits échantillons : taux indicatifs. Note les recommandations (bouton ⭐) pour mesurer la précision.</i>",
        ]))

    def cmd_mode(self, repo: Repository, args: str) -> None:
        if not args:
            self.bot.send(f"Mode actuel : <b>{modes.get(repo, self.settings.scoring)}</b>. /mode exploration ou /mode conversion.")
            return
        try:
            mode = modes.set_mode(repo, args)
        except ValueError as exc:
            self.bot.send(escape(str(exc)))
            return
        repo.s.commit()
        self.bot.send(f"Mode <b>{mode}</b> activé. Recalcul complet des priorités et recommandations en cours (1 à 2 min)…")
        rescore_in_background(self.bot, mode)

    # Buttons -------------------------------------------------------------------------------------------
    def handle_opportunity_callback(self, cq: dict, action: str, parts: list[str], opp, repo: Repository) -> bool:  # noqa: ANN001
        if action == "vc":
            self.bot.telegram.answer_callback(cq["id"])
            self.bot.send(compensation_text(opp, repo))
        elif action == "ve":
            self.bot.telegram.answer_callback(cq["id"])
            self.bot.send(eligibility_text(opp, repo))
        elif action == "rt":
            self.bot.telegram.answer_callback(cq["id"])
            ratings = {k: v for k, v in (self.settings.scoring.get("ratings") or {}).items() if isinstance(v, dict)}
            keys = [{"text": v.get("label", k), "callback_data": f"rr:{opp.id}:{k}"} for k, v in ratings.items()]
            self.bot.send(f"Note la recommandation #{opp.id} (sert à affiner le classement) :",
                          [keys[i:i + 2] for i in range(0, len(keys), 2)])
        elif action == "rr" and len(parts) == 3 and parts[2] in (self.settings.scoring.get("ratings") or {}):
            repo.record_feedback(opp, "rating", parts[2])
            recompute(repo, self.settings.scoring)
            self.bot.telegram.answer_callback(cq["id"], "Merci, note enregistrée")
        elif action == "ga":
            self.bot.telegram.answer_callback(cq["id"], "Préparation du dossier…")
            self.generate_application(opp, repo)
        else:
            return False
        return True

    def generate_application(self, opp, repo: Repository) -> None:  # noqa: ANN001
        from summer_hunter.web.cv import build_cv
        from summer_hunter.web.letter import start_letter_job
        from summer_hunter.web.pack import build_pack

        app = CRM(repo.s).application_for(opp.id)
        if app and app.date_applied:
            self.bot.send(f"Candidature déjà envoyée le {app.date_applied:%d/%m} : la version envoyée reste figée, "
                          "je génère une nouvelle version à part.")
        try:
            cv = build_cv(self.settings.config_dir, self.settings.data_dir / "cv" / str(opp.id), opp.id, opp.company_name,
                          opp.title, opp.description_text or "", opp.job_family)
            for f in [p for p in (cv.pdf_path, cv.tex_path) if p]:
                self.bot.telegram.send_document(self.bot.chat_id, f, f.stem)
        except (FileNotFoundError, TelegramError) as exc:
            self.bot.send(f"CV impossible : {escape(str(exc))}")
        fit = repo.latest_fit(opp.id)
        strat = (fit.strategy if fit else None) or {}
        pack = build_pack(self.settings.config_dir, self.settings.profile_raw, opp, strat, strat.get("networking"),
                          fit.recruitability if fit else None)
        lines = [f"<b>Dossier #{opp.id} · {escape(opp.company_name)}</b>",
                 "<b>Message LinkedIn (brouillon, à envoyer toi-même)</b>"
                 + (f" → {escape(pack['outreach_target'])}" if pack["outreach_target"] else ""),
                 f"<pre>{escape(pack['outreach'])}</pre>", "<b>3 points pour l'entretien (tirés de ton CV)</b>",
                 *[f"• {escape(t)}" for t in pack["talking_points"]]]
        if pack["gaps"]:
            lines += ["<b>Écarts à préparer</b>", *[f"• {escape(g)}" for g in pack["gaps"][:5]]]
        if not pack["outreach_target"] and pack["searches"]:
            lines.append("<b>Trouver un contact</b> : " + " · ".join(f"<a href=\"{escape(s['url'], quote=True)}\">{escape(s['label'])}</a>"
                                                                     for s in pack["searches"]))
        lines.append("<i>Lettre en cours (2-3 min). Rien n'est envoyé à l'entreprise.</i>")
        self.bot.send("\n".join(lines))
        start_letter_job(self.settings, self.bot.session_factory, self.bot.telegram, opp.id)


def compensation_text(opp, repo: Repository) -> str:  # noqa: ANN001
    fit = repo.latest_fit(opp.id)
    comp = (fit.compensation if fit else None) or {}
    pay = comp.get("pay") or {}
    lines = [f"<b>Rémunération #{opp.id}</b> · {escape(opp.company_name)}", f"Statut : <b>{escape(comp.get('status', '?'))}</b> · "
             f"confiance {escape(comp.get('confidence', '?'))}"]
    if pay:
        lines.append(f"Annoncé : {pay['amount_min']:,.2f}" + (f"-{pay['amount_max']:,.2f}" if pay['amount_max'] != pay['amount_min'] else "")
                     + f" {escape(pay.get('currency') or '?')} / {escape(pay.get('period') or 'période ?')} ({escape(pay.get('basis'))})")
        lines.append(f"Texte d'origine : « {escape(pay.get('wording', ''))} »")
    if comp.get("monthly_min_eur") is not None:
        lines.append(f"Mensuel : {comp['monthly_min_eur']:,.0f} €" + (" (estimation)" if comp.get("monthly_is_estimate") else "")
                     + (f" · taux {comp['fx_rate']} ({escape(comp.get('fx_source') or '')}, {comp.get('fx_date') or 's.d.'})"
                        if comp.get("fx_rate") else ""))
    if comp.get("living_cost_eur") is not None:
        lines.append(f"Coût de la vie estimé : {comp['living_cost_eur']:,.0f} €/mois ({escape(comp.get('living_cost_source') or '')})")
    if comp.get("monthly_balance_eur") is not None:
        lines.append(f"Solde mensuel estimé : {comp['monthly_balance_eur']:+,.0f} €")
    if comp.get("benefits"):
        lines.append("Avantages : " + escape(", ".join(k.replace("_", " ") for k in comp["benefits"])))
    lines.append(f"Score d'attractivité : {comp.get('score') if comp.get('score') is not None else 'indisponible'}")
    lines += [f"<i>{escape(n)}</i>" for n in (comp.get("notes") or [])[:4]]
    return "\n".join(lines)


def eligibility_text(opp, repo: Repository) -> str:  # noqa: ANN001
    ev = repo.latest_eligibility(opp.id)
    crit = ev.criteria if ev else []
    open_items = [c for c in crit if c["status"] in {"UNKNOWN", "FAIL"} and c.get("material", True)]
    lines = [f"<b>À vérifier avant de postuler #{opp.id}</b> ({escape(opp.eligibility_status or '?')})"]
    lines += [f"{'❌' if c['status'] == 'FAIL' else '❔'} {escape(c['criterion'].replace('_', ' '))} : {escape(c['explanation'][:200])}"
              for c in open_items] or ["Aucun point ouvert."]
    url = safe_url(opp.apply_url) or safe_url(opp.official_url)
    if url:
        lines.append(f"Source officielle : <a href=\"{escape(url, quote=True)}\">offre</a>")
    return "\n".join(lines)


def rescore_in_background(bot, mode: str) -> None:  # noqa: ANN001
    """Re-run the full evaluation (same code path as the nightly run) so scores, weights and actions stay consistent."""
    import threading

    def run() -> None:
        from summer_hunter.pipeline import CycleStats, Hunter

        hunter = Hunter(bot.settings, bot.session_factory, provider=bot.provider)
        stats = CycleStats()
        with bot.session_factory() as session:
            hunter.evaluate_pending(Repository(session), stats, force=True)
            session.commit()
        bot.send(f"Mode <b>{mode}</b> : {stats.evaluated} offres réévaluées. /priority pour le nouveau classement.")

    threading.Thread(target=run, daemon=True).start()
