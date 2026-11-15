"""AI CV Builder on Telegram, lot 1: the permanent profile (/cv_profile).

    /cv_profile               overview: verified facts by kind, confirmed additions, facts awaiting confirmation
    /cv_profile all           every verified claim from the approved CV and profile
    /cv_profile add <texte>   propose a new fact ("J'ai obtenu une certification IBM"); stored only after Confirmer

Buttons pf:<id>:ok (confirm), pf:<id>:no (reject), pf:<id>:undo (back to "to confirm").
"""

from __future__ import annotations

import logging
from collections import Counter
from html import escape
from typing import TYPE_CHECKING

from summer_hunter.cv_builder.profile import (INFERRED, KIND_FR, MISSING, REJECTED, USER_CONFIRMED, ProfileStore)
from summer_hunter.engine import knowledge

if TYPE_CHECKING:
    from summer_hunter.bot.handlers import Bot

log = logging.getLogger(__name__)

COMMANDS = [
    ("cv_profile", "Profil CV permanent : voir, confirmer, ajouter un fait"),
]
HELP = ("\n\n<b>Profil CV</b> : /cv_profile montre ce qui est vérifié et ce qui attend ta confirmation. "
        "/cv_profile add &lt;texte&gt; propose un nouveau fait ; rien n'est enregistré sans « Confirmer ».")
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
