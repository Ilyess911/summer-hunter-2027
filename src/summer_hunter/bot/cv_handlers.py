"""AI CV Builder on Telegram, lot 1: the permanent profile (/cv_profile).

    /cv_profile               overview: verified facts by kind, confirmed additions, facts awaiting confirmation
    /cv_profile all           every verified claim from the approved CV and profile
    /cv_profile add <texte>   propose a new fact ("J'ai obtenu une certification IBM"); stored only after Confirmer
    /cv <texte>               lot 3: generate the tailored CV (PDF + DOCX) with an optimization report
    /cv_analyze <texte>       lot 2: how a request or posting is understood (role, company, country, language, requirements)

Buttons pf:<id>:ok (confirm), pf:<id>:no (reject), pf:<id>:undo (back to "to confirm").
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING

from summer_hunter.cv_builder.profile import (INFERRED, KIND_FR, MISSING, REJECTED, USER_CONFIRMED, ProfileStore)
from summer_hunter.engine import knowledge

if TYPE_CHECKING:
    from summer_hunter.bot.handlers import Bot

log = logging.getLogger(__name__)

COMMANDS = [
    ("cv_profile", "Profil CV permanent : voir, confirmer, ajouter un fait"),
    ("cv", "Créer un CV adapté (PDF + DOCX) : /cv <offre ou demande>"),
    ("cv_analyze", "Analyse d'une demande de CV ou d'une offre (sans générer)"),
]
HELP = ("\n\n<b>Profil CV</b> : /cv_profile montre ce qui est vérifié et ce qui attend ta confirmation. "
        "/cv_profile add &lt;texte&gt; propose un nouveau fait ; rien n'est enregistré sans « Confirmer ». "
        "/cv_analyze &lt;offre ou demande&gt; montre comment je comprends la demande. "
        "/cv &lt;offre ou demande&gt; génère le CV en PDF et DOCX.")
MAX_PENDING_BUTTONS = 12
ADD_WORDS = {"add", "ajouter", "ajoute", "+"}


def fact_buttons(fact_id: int) -> list[list[dict[str, str]]]:
    return [[{"text": "✅ Confirmer", "callback_data": f"pf:{fact_id}:ok"},
             {"text": "❌ Faux / retirer", "callback_data": f"pf:{fact_id}:no"}]]


class CVBot:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._seeded = False

    def _store(self, session) -> ProfileStore:  # noqa: ANN001
        store = ProfileStore(session)
        if not self._seeded:
            if store.seed():
                session.commit()
            self._seeded = True
        return store

    def _cv(self) -> dict:
        from summer_hunter.web.cv import load_content

        try:
            return load_content(self.bot.settings.config_dir)
        except FileNotFoundError:
            return {}

    # Commands -------------------------------------------------------------------------------------

    def handle_command(self, cmd: str, args: str) -> bool:
        if cmd == "cv_analyze":
            self.analyze(args)
            return True
        if cmd == "cv":
            self.generate(args)
            return True
        if cmd != "cv_profile":
            return False
        head, _, rest = args.strip().partition(" ")
        with self.bot.session_factory() as session:
            store = self._store(session)
            if head.lower() in ADD_WORDS:
                self._propose(store, rest)
            elif head.lower() in {"all", "tout"}:
                self._all_verified(store)
            else:
                self._overview(store)
            session.commit()
        return True

    def _overview(self, store: ProfileStore) -> None:
        claims = store.claims(self.bot.settings.profile_raw, self._cv())
        verified = [c for c in claims if c.status == knowledge.VERIFIED]
        by_kind = Counter(c.kind for c in verified)
        lines = ["<b>Profil CV permanent</b>",
                 "✅ <b>Vérifié</b> (CV approuvé + profil) : " + ", ".join(f"{n} {k}" for k, n in sorted(by_kind.items())),
                 "<i>/cv_profile all pour le détail.</i>"]
        confirmed = store.facts(USER_CONFIRMED)
        if confirmed:
            lines.append("\n☑️ <b>Confirmé par toi</b>")
            lines += [f"• {escape(KIND_FR.get(f.kind, f.kind))} : {escape(f.text)}" for f in confirmed]
        pending = store.facts(INFERRED)
        if pending:
            lines.append(f"\n❔ <b>À confirmer</b> ({len(pending)}), jamais présenté comme un fait tant que non confirmé")
            lines += [f"#{f.id} {escape(KIND_FR.get(f.kind, f.kind))} : {escape(f.text)}"
                      + (f" <i>({escape(f.note)})</i>" if f.note else "") for f in pending]
        missing = store.facts(MISSING)
        if missing:
            lines.append("\n⛔ <b>Manquant</b>")
            lines += [f"• {escape(f.text)}" for f in missing]
        unknown = [c for c in claims if c.status == knowledge.UNKNOWN and not (c.ref or "").startswith("fact:")]
        if unknown:
            lines.append("⛔ Inconnu dans le profil : " + ", ".join(escape(c.text) for c in unknown))
        lines.append("\nAjouter : /cv_profile add &lt;texte&gt;")
        self.bot.send("\n".join(lines))
        for f in pending[:MAX_PENDING_BUTTONS]:
            self.bot.send(f"#{f.id} {escape(f.text)}", fact_buttons(f.id))
        if len(pending) > MAX_PENDING_BUTTONS:
            self.bot.send(f"… +{len(pending) - MAX_PENDING_BUTTONS} autres : relance /cv_profile après avoir tranché ceux-ci.")

    def _all_verified(self, store: ProfileStore) -> None:
        claims = [c for c in store.claims(self.bot.settings.profile_raw, self._cv()) if c.status in knowledge.USABLE]
        lines = ["<b>Faits utilisables dans un CV</b>"]
        for kind in sorted({c.kind for c in claims}):
            lines.append(f"\n<b>{escape(KIND_FR.get(kind, kind))}</b>")
            lines += [f"• {escape(c.text[:180])}" for c in claims if c.kind == kind]
        self.bot.send("\n".join(lines))

    def _propose(self, store: ProfileStore, text: str) -> None:
        if not text.strip():
            self.bot.send("Écris le fait après la commande, par exemple :\n/cv_profile add J'ai obtenu une certification IBM")
            return
        proposal = store.propose(text)
        f = proposal.fact
        if not proposal.created:
            state = {USER_CONFIRMED: "déjà confirmé", INFERRED: "déjà en attente de confirmation",
                     REJECTED: "déjà marqué faux", MISSING: "déjà noté manquant"}.get(f.status, f.status)
            self.bot.send(f"#{f.id} {escape(f.text)} : {state}.",
                          fact_buttons(f.id) if f.status in {INFERRED, REJECTED} else None)
            return
        log.info("profile fact proposed", extra={"fact_id": f.id, "kind": f.kind})
        self.bot.send(f"Souhaites-tu ajouter ce fait à ton profil professionnel ?\n\n"
                      f"<b>{escape(KIND_FR.get(f.kind, f.kind))}</b> : {escape(f.text)}\n\n"
                      "<i>Tant qu'il n'est pas confirmé, il ne sera jamais affiché comme un fait sur un CV.</i>",
                      fact_buttons(f.id))

    def generate(self, text: str, background: bool = True) -> None:
        if not text.strip():
            self.bot.send("Envoie-moi une offre d'emploi, un métier, une entreprise ou simplement le type de CV voulu, "
                          "après la commande. Exemple :\n/cv stage AI Business Development à Singapour")
            return
        self.bot.send("⏳ Analyse en cours…")
        if background:
            threading.Thread(target=self._generate, args=(text,), daemon=True, name="cv-builder").start()
        else:
            self._generate(text)

    def _generate(self, text: str) -> None:
        from summer_hunter.cv_builder.document import build
        from summer_hunter.cv_builder.intent import analyze, context_from

        try:
            with self.bot.session_factory() as session:
                store = self._store(session)
                req = analyze(text, context_from(self.bot.settings, session))
                confirmed = store.confirmed()
            out_dir = self.bot.settings.data_dir / "cv_builder" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            res = build(self.bot.settings.config_dir, out_dir, req, confirmed)
        except Exception:  # noqa: BLE001 - a failed build must answer, never crash the bot
            log.exception("cv build failed")
            self.bot.send("❌ Génération impossible (erreur interne, voir les logs).")
            return
        log.info("cv built", extra={"mode": req.mode, "family": req.family, "pages": res.pages, "ok": res.ok})
        self.bot.send(summary_card(req, res))
        for path in (res.pdf_path, res.docx_path):
            if path:
                try:
                    self.bot.telegram.send_document(self.bot.chat_id, path, path.stem)
                except Exception as exc:  # noqa: BLE001
                    log.warning("sendDocument failed", extra={"error": str(exc)})
        self.bot.send(optimization_report(req, res))

    def analyze(self, text: str) -> None:
        from summer_hunter.cv_builder.intent import analyze, context_from

        if not text.strip():
            self.bot.send("Colle une offre ou décris le CV voulu après la commande, par exemple :\n"
                          "/cv_analyze CV pour un stage Robotics Engineer chez une startup à Singapour")
            return
        with self.bot.session_factory() as session:
            self._store(session)
            req = analyze(text, context_from(self.bot.settings, session))
        log.info("cv request analyzed", extra={"mode": req.mode, "family": req.family, "track": req.track})
        self.bot.send(describe(req))

    # Callbacks ------------------------------------------------------------------------------------

    def handle_callback(self, cq: dict, action: str, parts: list[str]) -> bool:
        if action != "pf" or len(parts) != 3 or parts[2] not in {"ok", "no", "undo"}:
            return False
        status = {"ok": USER_CONFIRMED, "no": REJECTED, "undo": INFERRED}[parts[2]]
        with self.bot.session_factory() as session:
            fact = ProfileStore(session).decide(int(parts[1]), status)
            session.commit()
        tg = self.bot.telegram
        if fact is None:
            tg.answer_callback(cq["id"], "Fait introuvable")
            return True
        log.info("profile fact decided", extra={"fact_id": fact.id, "status": status})
        tg.answer_callback(cq["id"], {"ok": "Ajouté au profil", "no": "Retiré", "undo": "Remis à confirmer"}[parts[2]])
        mid = (cq.get("message") or {}).get("message_id")
        if mid:
            undo = [[{"text": "↩️ Annuler", "callback_data": f"pf:{fact.id}:undo"}]] if parts[2] != "undo" else fact_buttons(fact.id)
            try:
                tg.edit_buttons(self.bot.chat_id, mid, undo)
            except Exception:  # noqa: BLE001 - a stale message must not fail the decision
                pass
        label = {USER_CONFIRMED: "☑️ confirmé", REJECTED: "❌ retiré", INFERRED: "❔ remis à confirmer"}[status]
        self.bot.send(f"#{fact.id} {escape(fact.text)} : {label}.")
        return True


MODE_FR = {"offer": "offre fournie", "spontaneous": "candidature spontanée (aucune offre)", "target": "profil cible (aucune offre)"}
ROLE_SOURCE_FR = {"offer": "titre de l'offre", "stated": "indiqué", "inferred": "déduit, à préciser si besoin"}
TRACK_FR = {"business": "Business & vente", "product": "Produit, data & tech", "industry": "Industrie & robotique"}
LEVEL_FR = {"mandatory": "obligatoire", "preferred": "souhaité", "exposure": "mentionné"}
COVERAGE = [("covered", "✅ Couvert"), ("partial", "🟡 Partiel"), ("to_confirm", "❔ À confirmer dans ton profil"),
            ("missing", "⛔ Non couvert")]


def describe(req) -> str:  # noqa: ANN001 - CVRequest
    lines = ["🔎 <b>Analyse de la demande</b>",
             f"Poste : <b>{escape(req.target_role or 'non précisé')}</b> <i>({ROLE_SOURCE_FR.get(req.role_source, '')})</i>",
             f"Type : {MODE_FR.get(req.mode, req.mode)}",
             f"Entreprise : {escape(req.company or 'non précisée')}",
             f"Lieu : {escape(req.location if req.country_code else 'non précisé')}",
             f"Langue du CV : {'anglais' if req.language == 'en' else 'français'} <i>({escape(req.language_reason)})</i>",
             f"Format : {req.pages} page{'s' if req.pages > 1 else ''}" + (f", style {escape(req.style)}" if req.style else ""),
             f"Piste CV : {TRACK_FR.get(req.track, req.track)} <i>(famille {escape(req.family)})</i>"]
    if req.sector:
        lines.append("Secteur : " + escape(", ".join(req.sector)))
    if req.emphasis:
        lines.append("À mettre en avant : " + escape(" ; ".join(req.emphasis)))
    if req.mode == "offer":
        if req.requirements:
            lines.append("\n<b>Exigences de l'offre</b>")
            for cov, label in COVERAGE:
                items = req.by_coverage(cov)
                if items:
                    lines.append(f"{label} : " + escape(", ".join(f"{r.name} ({LEVEL_FR[r.level]})" for r in items)))
        else:
            lines.append("\nAucune compétence technique du lexique repérée dans l'offre.")
    elif req.expected_skills:
        lines.append("\n<b>Généralement attendu pour ce poste</b> <i>(pas une exigence d'entreprise)</i> : "
                     + escape(", ".join(req.expected_skills)))
    if req.keywords:
        lines.append("Mots-clés ATS : " + escape(", ".join(req.keywords)))
    if req.questions:
        lines.append("\n<b>Questions</b>")
        lines += [f"{i}. {escape(q)}" for i, q in enumerate(req.questions, 1)]
    return "\n".join(lines)


def summary_card(req, res) -> str:  # noqa: ANN001
    head = "✅ <b>CV GÉNÉRÉ</b>" if res.ok else "⚠️ <b>CV GÉNÉRÉ, À VÉRIFIER</b>"
    return "\n".join([head, "",
                      f"Poste : {escape(req.target_role or 'non précisé')}",
                      f"Entreprise : {escape(req.company or 'non précisée')}",
                      f"Lieu : {escape(req.location if req.country_code else 'non précisé')}",
                      f"Langue : {'anglais' if req.language == 'en' else 'français'}",
                      f"Format : {res.pages or '?'} page{'s' if (res.pages or 1) > 1 else ''}, style ATS Classic",
                      f"Fichiers : {escape(res.stem)}.pdf / .docx"])


def optimization_report(req, res) -> str:  # noqa: ANN001
    from summer_hunter.cv_builder.document import _norm, blocks

    shown = _norm(" ".join(t for _, t in blocks(res.cv)))
    lines = ["📊 <b>Rapport d'optimisation</b>"]
    strengths = [e.get("org", "").split(" -- ")[0] for e in res.cv.get("experiences", [])[:2]]
    strengths += [p.get("name", "").split(" -- ")[0] for p in res.cv.get("projects", [])[:2]]
    lines.append("• Mis en avant : " + escape(", ".join(s for s in strengths if s)))
    kw = [k for k in req.keywords if _norm(k) and _norm(k) in shown]
    if kw:
        lines.append("• Mots-clés présents : " + escape(", ".join(kw[:10])))
    if req.mode == "offer":
        missing = [r for r in req.requirements if r.coverage == "missing" and r.level == "mandatory"]
        partial = [r for r in req.requirements if r.coverage in {"partial", "to_confirm"}]
        if missing:
            lines.append("• Exigences non couvertes (jamais ajoutées) : " + escape(", ".join(r.name for r in missing)))
        if partial:
            lines.append("• Partielles ou à confirmer : " + escape(", ".join(r.name for r in partial)))
    gaps = res.cv.get("gaps") or []
    if gaps:
        lines.append("• L'offre cite, ton CV ne montre pas : " + escape(", ".join(gaps)))
    for n in res.notes:
        lines.append("• " + escape(n))
    failed = [c for c in res.checks if not c.ok]
    lines.append("• Contrôles : " + ("tous OK (dates, sections, faits vérifiés, pages, PDF = DOCX)" if not failed
                                    else escape("; ".join(f"{c.name} : {c.detail}" for c in failed))))
    if req.questions:
        lines.append("\n<b>Questions</b>")
        lines += [f"{i}. {escape(q)}" for i, q in enumerate(req.questions, 1)]
    return "\n".join(lines)
