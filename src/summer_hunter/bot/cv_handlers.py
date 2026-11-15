"""AI CV Builder on Telegram.

    /cv_profile               overview: verified facts by kind, confirmed additions, facts awaiting confirmation
    /cv_profile all           every verified claim from the approved CV and profile
    /cv_profile add <texte>   propose a new fact ("J'ai obtenu une certification IBM"); stored only after Confirmer
    /cv <texte>               lot 3: generate the tailored CV (PDF + DOCX) with an optimization report
    /cv_history [n]           lot 4: generated CVs (latest version of each); n = versions of CV-000n
    /cv_analyze <texte>       lot 2: how a request or posting is understood (role, company, country, language, requirements)
    /cv (alone)               lot 5: asks for the request; the next message (long postings split by Telegram included) is it
    /cv_settings              lot 5: default language, pages, style, formats, personalization
    /cv_upload                lot 5: read an existing CV (PDF/DOCX) and propose its new lines as facts to confirm

Conversation (lot 5): after a CV, a short message such as "en anglais, une page" or "mets en avant Nexya"
(or a reply to the CV's message) creates the next version. "J'ai obtenu…" proposes a profile fact instead.

Buttons pf:<id>:ok|no|undo (profile facts); cvh/cvf (history); on a CV: cvr regenerate, cve edit,
cvs style, cvl language, cvp PDF, cvd DOCX, cvk save; cvo settings.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from pathlib import Path
from html import escape
from typing import TYPE_CHECKING

from summer_hunter.cv_builder.profile import (INFERRED, KIND_FR, MISSING, REJECTED, USER_CONFIRMED, ProfileStore)
from summer_hunter.cv_builder.versions import label
from summer_hunter.engine.geo import country_name
from summer_hunter.engine import knowledge

if TYPE_CHECKING:
    from summer_hunter.bot.handlers import Bot

log = logging.getLogger(__name__)

COMMANDS = [
    ("cv_profile", "Profil CV permanent : voir, confirmer, ajouter un fait"),
    ("cv", "Créer un CV adapté (PDF + DOCX) : /cv <offre ou demande>"),
    ("cv_history", "CV générés et leurs versions"),
    ("cv_analyze", "Analyse d'une demande de CV ou d'une offre (sans générer)"),
    ("cv_settings", "Réglages CV : langue, pages, style, formats"),
    ("cv_upload", "Importer un CV existant pour enrichir ton profil"),
]
HELP = ("\n\n<b>Profil CV</b> : /cv_profile montre ce qui est vérifié et ce qui attend ta confirmation. "
        "/cv_profile add &lt;texte&gt; propose un nouveau fait ; rien n'est enregistré sans « Confirmer ». "
        "/cv_analyze &lt;offre ou demande&gt; montre comment je comprends la demande. "
        "/cv &lt;offre ou demande&gt; génère le CV en PDF et DOCX ; ensuite, écris simplement la modification "
        "(« en anglais, une page », « mets en avant Nexya », « retire Amundi ») pour une nouvelle version. "
        "/cv_history, /cv_settings, /cv_upload.")
AWAIT_TTL_S = 15 * 60          # how long /cv, ✏️ Modifier or /cv_upload wait for the next message
ACTIVE_TTL_S = 2 * 3600        # how long a short message may still edit the last CV
EDIT_EXAMPLES = ("Exemples : « en anglais, une page », « mets davantage en avant mon projet de robotique », "
                 "« retire Amundi », « style ATS », « pour un poste de Sales Engineer ».")
MAX_PENDING_BUTTONS = 12
ADD_WORDS = {"add", "ajouter", "ajoute", "+"}


def version_buttons(vid: int, saved: bool = False) -> list[list[dict[str, str]]]:
    return [[{"text": "🔁 Regénérer", "callback_data": f"cvr:{vid}"}, {"text": "✏️ Modifier", "callback_data": f"cve:{vid}"}],
            [{"text": "🎨 Style", "callback_data": f"cvs:{vid}"}, {"text": "🌐 Langue", "callback_data": f"cvl:{vid}"}],
            [{"text": "📄 PDF", "callback_data": f"cvp:{vid}"}, {"text": "📝 DOCX", "callback_data": f"cvd:{vid}"},
             {"text": "⭐ Sauvegardée" if saved else "⭐ Sauvegarder", "callback_data": f"cvk:{vid}"}],
            [{"text": "✉️ Mail de candidature", "callback_data": f"cvm:{vid}"}]]


def fact_buttons(fact_id: int) -> list[list[dict[str, str]]]:
    return [[{"text": "✅ Confirmer", "callback_data": f"pf:{fact_id}:ok"},
             {"text": "❌ Faux / retirer", "callback_data": f"pf:{fact_id}:no"}]]


class CVBot:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._seeded = False
        self._seed_lock = threading.Lock()
        self._lock = threading.Lock()
        self._await: tuple[str, int | None, float] | None = None     # (request | edit | upload, version id, since)
        self.buffer_wait_s = 4.0
        self._buf: list[str] = []
        self._buf_timer: threading.Timer | None = None
        self._buf_lock = threading.Lock()

    def _store(self, session) -> ProfileStore:  # noqa: ANN001
        store = ProfileStore(session)
        if not self._seeded:
            with self._seed_lock:               # the polling thread and a build thread may both get here first
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
        if cmd == "cv_history":
            self.history(args)
            return True
        if cmd == "cv_settings":
            self.show_settings()
            return True
        if cmd == "cv_upload":
            self._await = ("upload", None, time.time())
            self.bot.send("📎 Envoie ton CV en PDF ou DOCX (10 Mo max). Je lis ses lignes et je te propose celles "
                          "que ton profil n'a pas encore ; rien n'est ajouté sans ta confirmation.")
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
            self._await = ("request", None, time.time())
            self.bot.send("Envoie-moi une offre d'emploi, un métier, une entreprise ou simplement le type de CV que tu "
                          "souhaites créer.")
            return
        self._await = None
        self.bot.send("⏳ Analyse en cours…")
        if background:
            self._collect(text, new_batch=True)   # Telegram splits a long posting: the next parts join this one
        else:
            self._generate(text)

    def _collect(self, text: str, new_batch: bool = False) -> None:
        previous = ""
        with self._buf_lock:
            if new_batch and self._buf:           # an explicit /cv never merges into the previous request
                if self._buf_timer:
                    self._buf_timer.cancel()
                previous, self._buf = "\n".join(self._buf), []
            self._buf.append(text)
            if self._buf_timer:
                self._buf_timer.cancel()
            self._buf_timer = threading.Timer(self.buffer_wait_s, self._flush)
            self._buf_timer.daemon = True
            self._buf_timer.start()
        if previous.strip():
            threading.Thread(target=self._generate, args=(previous,), daemon=True, name="cv-builder").start()

    def _flush(self) -> None:
        with self._buf_lock:
            text, self._buf, self._buf_timer = "\n".join(self._buf), [], None
        if text.strip():
            self._generate(text)

    # Conversation ---------------------------------------------------------------------------------

    def handle_text(self, text: str, reply_to: int | None = None) -> bool:
        """Free text that belongs to the CV builder. False leaves it to the rest of the bot."""
        from summer_hunter.collectors.link_ingest import first_url
        from summer_hunter.database.repository import Repository

        text = text.strip()
        if not text or text.startswith("/"):
            return False
        now = time.time()
        if reply_to:
            with self.bot.session_factory() as session:
                vid = Repository(session).get_kv(f"cv_msg:{reply_to}")
            if vid:
                self.edit(int(vid), text)
                return True
        with self._buf_lock:
            collecting = bool(self._buf)
        if collecting:
            self._collect(text)
            return True
        waiting = self._await if self._await and now - self._await[2] < AWAIT_TTL_S else None
        if waiting:
            kind, vid, _ = waiting
            if kind == "request":
                self._await = None
                self.bot.send("⏳ Analyse en cours…")
                self._collect(text)
                return True
            if kind == "edit" and vid:
                self._await = None
                self.edit(vid, text)
                return True
            if kind == "upload":
                self.bot.send("J'attends un fichier PDF ou DOCX (ton CV), pas du texte. /cv_upload pour recommencer.")
                self._await = None
                return True
        with self.bot._paste_lock:
            if self.bot._paste_parts:         # the next part of a posting being pasted, not an edit
                return False
        active = self._active()
        if active and len(text) < 400 and not first_url(text):
            from summer_hunter.cv_builder import edits

            e = edits.parse(text)
            if not e.empty:
                self.edit(active, text, e)
                return True
        return False

    def _active(self) -> int | None:
        from summer_hunter.database.repository import Repository

        with self.bot.session_factory() as session:
            raw = Repository(session).get_kv("cv_active") or ""
        vid, _, since = raw.partition(":")
        if vid.isdigit() and since.replace(".", "").isdigit() and time.time() - float(since) < ACTIVE_TTL_S:
            return int(vid)
        return None

    def edit(self, vid: int, text: str, e=None, background: bool = True) -> None:  # noqa: ANN001
        from summer_hunter.cv_builder import edits

        e = e or edits.parse(text)
        if e.facts:
            with self.bot.session_factory() as session:
                store = self._store(session)
                for fact in e.facts:
                    self._propose(store, fact)
                session.commit()
            self.bot.send("Une fois le fait confirmé, appuie sur 🔁 Regénérer sous le CV pour l'intégrer.")
        if not e.overrides:
            if not e.facts:
                self.bot.send("Je n'ai pas compris la modification. " + EDIT_EXAMPLES)
            return
        self.bot.send("✏️ Nouvelle version : " + escape("; ".join(e.understood)) + "…")
        args = (vid, text[:300], dict(e.overrides))
        if background:
            threading.Thread(target=self._derive, args=args, daemon=True, name="cv-edit").start()
        else:
            self._derive(*args)

    def _derive(self, vid: int, change: str, overrides: dict) -> None:  # noqa: ANN001
        from summer_hunter.cv_builder import service

        with self._lock:
            try:
                built = service.derive(self.bot.settings, self.bot.session_factory, vid, change, overrides)
            except Exception as exc:  # noqa: BLE001
                log.error("cv edit failed", extra=safe_error(exc))
                self.bot.send("❌ Modification impossible (erreur interne, voir les logs).")
                return
            if built is None:
                self.bot.send("Version introuvable.")
                return
            log.info("cv edited", extra={"cv": built.tag, "fields": sorted(overrides)})
            self.deliver(built.req, built.res, built.tag, built.version_id)

    def versions(self, session):  # noqa: ANN001, ANN201
        from summer_hunter.cv_builder.versions import VersionStore

        return VersionStore(session, self.bot.settings.data_dir, self.bot.settings.config_dir)

    def _generate(self, text: str) -> None:
        from summer_hunter.cv_builder import service

        with self._lock:              # one build at a time in this process; service.build_lock covers the Mini App
            try:
                with self.bot.session_factory() as session:
                    self._store(session)
                built = service.generate(self.bot.settings, self.bot.session_factory, text)
            except Exception as exc:  # noqa: BLE001 - a failed build must answer, never crash the bot
                log.error("cv build failed", extra=safe_error(exc))
                self.bot.send("❌ Génération impossible (erreur interne, voir les logs).")
                return
            req, res = built.req, built.res
            log.info("cv built", extra={"cv": built.tag, "mode": req.mode, "family": req.family, "pages": res.pages,
                                        "ok": res.ok})
            self.deliver(req, res, built.tag, built.version_id)   # under the lock: an older build never becomes active

    def deliver(self, req, res, tag: str, vid: int | None = None) -> None:  # noqa: ANN001
        from summer_hunter.cv_builder import prefs
        from summer_hunter.database.repository import Repository

        with self.bot.session_factory() as session:
            wanted = prefs.formats(prefs.load(Repository(session)))
        ids = [self._send_id(summary_card(req, res, tag))]
        self._send_files([p for p, fmt in ((res.pdf_path, "pdf"), (res.docx_path, "docx")) if p and fmt in wanted])
        ids.append(self._send_id(optimization_report(req, res), version_buttons(vid) if vid else None))
        if vid:
            with self.bot.session_factory() as session:
                repo = Repository(session)
                for mid in ids:
                    if mid:
                        repo.set_kv(f"cv_msg:{mid}", str(vid))      # a reply to these messages edits this version
                repo.set_kv("cv_active", f"{vid}:{time.time():.0f}")
                session.commit()

    def _send_id(self, text: str, buttons=None) -> int | None:  # noqa: ANN001
        try:
            return self.bot.telegram.send_message(self.bot.chat_id, text, buttons)
        except Exception as exc:  # noqa: BLE001
            log.warning("send failed", extra={"error": str(exc)})
            return None

    def _send_files(self, paths) -> None:  # noqa: ANN001
        for path in paths:
            try:
                self.bot.telegram.send_document(self.bot.chat_id, path, path.stem)
            except Exception as exc:  # noqa: BLE001
                log.warning("sendDocument failed", extra={"error": str(exc)})

    def history(self, args: str) -> None:
        arg = args.strip().upper().removeprefix("CV-").lstrip("0")
        with self.bot.session_factory() as session:
            vs = self.versions(session)
            if arg.isdigit():
                self._lineage(vs, int(arg))
                return
            rows = vs.history(10)
        if not rows:
            self.bot.send("Aucun CV généré pour l'instant. Essaie : /cv stage Tech Sales à Dublin")
            return
        lines = ["🗂 <b>CV générés</b> (dernière version de chacun)"]
        lines += [f"<b>{label(r)}</b> · {escape(r.target_role or '?')}" + (f" @ {escape(r.company)}" if r.company else "")
                  + (f" · {escape(country_name(r.country_code))}" if r.country_code else "")
                  + f" · {r.language} · {r.created_at:%d/%m %H:%M}" + (" ⭐" if r.saved else "") for r in rows]
        buttons = [[{"text": f"📂 CV-{r.lineage_id:04d}", "callback_data": f"cvh:{r.lineage_id}"} for r in rows[i:i + 3]]
                   for i in range(0, len(rows), 3)]
        self.bot.send("\n".join(lines), buttons)

    def _lineage(self, vs, lineage_id: int) -> None:  # noqa: ANN001
        rows = vs.lineage(lineage_id)
        if not rows:
            self.bot.send(f"CV-{lineage_id:04d} introuvable.")
            return
        lines = [f"🗂 <b>CV-{lineage_id:04d}</b> · {escape(rows[0].target_role or '?')}"]
        for r in rows:
            lines.append(f"v{r.version} · {r.created_at:%d/%m %H:%M} · {r.language} · {r.pages or '?'} p."
                         + (f" · <i>{escape(r.change)}</i>" if r.change else " · version initiale") + (" ⭐" if r.saved else ""))
        buttons = [[{"text": f"📄 v{r.version}", "callback_data": f"cvf:{r.id}"} for r in rows[i:i + 4]]
                   for i in range(0, len(rows), 4)]
        self.bot.send("\n".join(lines), buttons)

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

    # Settings ------------------------------------------------------------------------------------

    def show_settings(self) -> None:
        from summer_hunter.cv_builder import prefs
        from summer_hunter.database.repository import Repository

        with self.bot.session_factory() as session:
            current = prefs.load(Repository(session))
        lines = ["⚙️ <b>Réglages CV</b> (une demande explicite passe toujours avant)"]
        lines += [f"{prefs.LABEL_FR[k]} : <b>{escape(prefs.VALUE_FR.get(v, v))}</b>" for k, v in current.items()]
        lines.append("<i>Styles Modern Professional et Consulting / Business : pas encore disponibles.</i>")
        buttons = [[{"text": f"↻ {prefs.LABEL_FR[k]}", "callback_data": f"cvo:0:{k}"}] for k in current]
        self.bot.send("\n".join(lines), buttons)

    # Upload --------------------------------------------------------------------------------------

    def receive_document(self, document: dict) -> bool:
        """A CV sent as PDF or DOCX: read it and propose its new lines. False for other files."""
        name = str(document.get("file_name") or "")
        suffix = Path(name).suffix.lower()
        if suffix not in {".pdf", ".docx"}:
            return False
        waiting = self._await and self._await[0] == "upload" and time.time() - self._await[2] < AWAIT_TTL_S
        if not waiting:      # a PDF can be a job description: never read into the profile without /cv_upload
            self.bot.send("Pour importer ton CV dans ton profil, lance d'abord /cv_upload puis renvoie le fichier.")
            return True
        self._await = None
        if int(document.get("file_size") or 0) > 10_000_000:
            self.bot.send("Fichier trop gros (10 Mo maximum).")
            return True
        from summer_hunter.cv_builder.upload import extract_text, novel_lines

        tmp = self.bot.settings.data_dir / "cv_builder" / "uploads" / f"{time.time_ns()}{suffix}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.bot.telegram.download_file(document["file_id"], tmp)
            text = extract_text(tmp)
        except Exception as exc:  # noqa: BLE001
            log.warning("cv upload failed", extra={"error": type(exc).__name__})
            self.bot.send("Lecture du CV impossible (fichier illisible ou protégé).")
            return True
        finally:
            tmp.unlink(missing_ok=True)       # personal data: the copy never outlives the reading
        with self.bot.session_factory() as session:
            store = self._store(session)
            known = _all_strings(self._cv()) + [f.text for f in store.facts()]
            lines = novel_lines(text, known)
            created = []
            for line in lines:
                proposal = store.propose(line, source="CV importé")
                if proposal.created:
                    created.append(proposal.fact)
            session.commit()
        log.info("cv upload read", extra={"proposed": len(created)})
        if not created:
            self.bot.send("📄 CV lu : rien de nouveau par rapport à ton profil.")
            return True
        self.bot.send(f"📄 CV lu : {len(created)} ligne(s) absentes de ton profil. Confirme seulement ce qui est exact ; "
                      "le reste ne sera jamais affiché sur un CV.")
        for f in created:
            self.bot.send(f"#{f.id} <b>{escape(KIND_FR.get(f.kind, f.kind))}</b> : {escape(f.text)}", fact_buttons(f.id))
        return True

    def _version_callback(self, cq: dict, action: str, ident: int) -> bool:
        self.bot.telegram.answer_callback(cq["id"])
        with self.bot.session_factory() as session:
            vs = self.versions(session)
            if action == "cvh":
                self._lineage(vs, ident)
                return True
            row = vs.get(ident)
            files = vs.files(row) if row else []
            tag = label(row) if row else ""
        if not row:
            self.bot.send("Version introuvable.")
        elif not files:
            self.bot.send(f"{tag} : fichiers absents du disque.")
        else:
            self.bot.send(f"📄 {tag}")
            self._send_files(files)
        return True

    def _cv_button(self, cq: dict, action: str, vid: int, arg: str | None) -> bool:
        from summer_hunter.cv_builder.document import STYLES
        from summer_hunter.cv_builder.edits import Edit

        tg = self.bot.telegram
        with self.bot.session_factory() as session:
            vs = self.versions(session)
            row = vs.get(vid)
            if row is None:
                tg.answer_callback(cq["id"], "Version introuvable")
                return True
            tag, language, saved = label(row), row.language, row.saved
            if action == "cvk":
                vs.set_saved(vid, not saved)
                session.commit()
            files = {"cvp": row.pdf_path, "cvd": row.docx_path}
        if action == "cvk":
            tg.answer_callback(cq["id"], "Version sauvegardée ⭐" if not saved else "Sauvegarde retirée")
            mid = (cq.get("message") or {}).get("message_id")
            if mid:
                try:
                    tg.edit_buttons(self.bot.chat_id, mid, version_buttons(vid, not saved))
                except Exception:  # noqa: BLE001
                    pass
            return True
        tg.answer_callback(cq["id"])
        if action != "cve":
            self._await = None
        if action in files:
            path = Path(files[action]) if files[action] else None
            if path and path.exists():
                self._send_files([path])
            else:
                self.bot.send(f"{tag} : fichier absent du disque.")
        elif action == "cve":
            self._await = ("edit", vid, time.time())
            self.bot.send(f"✏️ Écris la modification de {tag}. " + EDIT_EXAMPLES)
        elif action == "cvr":
            self._regenerate(vid)
        elif action == "cvl":
            other = "fr" if language == "en" else "en"
            self.edit(vid, f"langue : {other}", Edit({"language": other}, [], [f"langue : {'français' if other == 'fr' else 'anglais'}"]))
        elif action == "cvs":
            if arg in STYLES:
                self.edit(vid, f"style : {arg}", Edit({"style": arg}, [], [f"style : {STYLES[arg]}"]))
            else:
                buttons = [[{"text": name, "callback_data": f"cvs:{vid}:{sid}"} for sid, name in STYLES.items()]]
                self.bot.send("🎨 Styles disponibles. Modern Professional et Consulting / Business arrivent plus tard.", buttons)
        return True

    def _email(self, cq: dict, vid: int, language: str | None) -> bool:
        """Draft the cover email in the chat, ready to copy. Never sent to anyone."""
        from summer_hunter.cv_builder.service import email_for

        mail = email_for(self.bot.settings, self.bot.session_factory, vid, language)
        if mail is None:
            self.bot.telegram.answer_callback(cq["id"], "Version introuvable")
            return True
        self.bot.telegram.answer_callback(cq["id"], "Mail prêt, à copier")
        other = "en" if mail.language == "fr" else "fr"
        buttons = [[{"text": "📋 Copier l'objet", "copy_text": {"text": mail.subject[:256]}}],
                   [{"text": "🇬🇧 Version anglaise" if other == "en" else "🇫🇷 Version française",
                     "callback_data": f"cvm:{vid}:{other}"}]]
        self.bot.send("✉️ <b>Mail de candidature</b> · rien n'est envoyé : touche le bloc pour copier le texte.\n\n"
                      f"<b>Objet</b> : <code>{escape(mail.subject)}</code>\n\n<pre>{escape(mail.body)}</pre>", buttons)
        log.info("cv email drafted", extra={"language": mail.language})
        return True

    def _regenerate(self, vid: int, background: bool = True) -> None:
        """Same request, current profile (e.g. after confirming a fact): a new version, nothing else changes."""
        self.bot.send("🔁 Régénération avec ton profil à jour…")
        args = (vid, "régénération (profil à jour)", {})
        if background:
            threading.Thread(target=self._derive, args=args, daemon=True, name="cv-regen").start()
        else:
            self._derive(*args)

    # Callbacks ------------------------------------------------------------------------------------

    def handle_callback(self, cq: dict, action: str, parts: list[str]) -> bool:
        if action in {"cvh", "cvf"}:
            return self._version_callback(cq, action, int(parts[1]))
        if action == "cvm":
            return self._email(cq, int(parts[1]), parts[2] if len(parts) > 2 else None)
        if action in {"cvr", "cve", "cvs", "cvl", "cvp", "cvd", "cvk"}:
            return self._cv_button(cq, action, int(parts[1]), parts[2] if len(parts) > 2 else None)
        if action == "cvo" and len(parts) == 3:
            from summer_hunter.cv_builder import prefs
            from summer_hunter.database.repository import Repository

            with self.bot.session_factory() as session:
                try:
                    prefs.cycle(Repository(session), parts[2])
                except KeyError:
                    self.bot.telegram.answer_callback(cq["id"], "Réglage inconnu")
                    return True
                session.commit()
            self.bot.telegram.answer_callback(cq["id"], "Réglage modifié")
            self.show_settings()
            return True
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


def summary_card(req, res, tag: str = "") -> str:  # noqa: ANN001
    head = ("✅ <b>CV GÉNÉRÉ</b>" if res.ok else "⚠️ <b>CV GÉNÉRÉ, À VÉRIFIER</b>") + (f" · {escape(tag)}" if tag else "")
    return "\n".join([head, "",
                      f"Poste : {escape(req.target_role or 'non précisé')}",
                      f"Entreprise : {escape(req.company or 'non précisée')}",
                      f"Lieu : {escape(req.location if req.country_code else 'non précisé')}",
                      f"Langue : {'anglais' if req.language == 'en' else 'français'}",
                      f"Format : {res.pages or '?'} page{'s' if (res.pages or 1) > 1 else ''}, style ATS Classic",
                      f"Fichiers : {escape(res.stem)}.pdf / .docx"])


def optimization_report(req, res) -> str:  # noqa: ANN001
    from summer_hunter.cv_builder.service import report_items

    checks = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in res.checks]
    lines = ["📊 <b>Rapport d'optimisation</b>"]
    questions = []
    for label_, text in report_items(req, res.cv, checks, res.notes):
        if label_ == "Question":
            questions.append(text)
        elif label_ == "Note":
            lines.append("• " + escape(text))
        else:
            lines.append(f"• {escape(label_)} : {escape(text)}")
    if questions:
        lines.append("\n<b>Questions</b>")
        lines += [f"{i}. {escape(q)}" for i, q in enumerate(questions, 1)]
    return "\n".join(lines)

def _all_strings(node) -> list[str]:  # noqa: ANN001
    out: list[str] = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            out += _all_strings(v)
    elif isinstance(node, list):
        for v in node:
            out += _all_strings(v)
    return out


def safe_error(exc: BaseException) -> dict[str, str]:
    """Exception type and code location only: messages can quote CV or profile text (YAML, SQL parameters)."""
    import traceback

    frames = traceback.extract_tb(exc.__traceback__)[-3:]
    return {"error": type(exc).__name__,
            "where": " < ".join(f"{Path(f.filename).name}:{f.lineno}" for f in reversed(frames))}
