"""Auto-apply flow shared by the bot: prepare (fill + screenshot), answer the open questions, submit on the tap.

State lives in data/apply/<opp_id>/state.json. One browser at a time (process lock). A submission is recorded as
"submitting" before the click, so a second tap or a restart never sends the same application twice.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from summer_hunter.apply import answers as ans
from summer_hunter.apply import ats, browser
from summer_hunter.database.repository import Repository
from summer_hunter.utils.settings import load_yaml

log = logging.getLogger(__name__)

class _ApplyLock:
    """One form at a time, across threads AND processes (bot, CLI): a thread lock plus an flock on data/apply/.lock."""

    def __init__(self) -> None:
        self._t = threading.Lock()
        self._path: Path | None = None
        self._fh = None

    def at(self, data_dir: Path) -> _ApplyLock:
        self._path = Path(data_dir) / "apply" / ".lock"
        return self

    def acquire(self, blocking: bool = True) -> bool:
        import fcntl

        if not self._t.acquire(blocking=blocking):
            return False
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fh = self._path.open("w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            fh.close()
            self._t.release()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        import fcntl

        fh, self._fh = self._fh, None
        if fh is not None:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
        self._t.release()

    def __enter__(self) -> _ApplyLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


_LOCK = _ApplyLock()
ICON = {ans.PROFILE: "👤", ans.RULE: "📏", ans.MODEL: "🤖", ans.USER: "✍️", ans.FILE: "📎", ans.ASK: "⚠️"}
RETRYABLE = {"ready", "captcha", "invalid", "blocked", "error", "needs", "verify_email", "account_exists", "auth_failed",
             "code"}       # code: the site wanted an e-mailed code that never came: the form was not sent    # never: submitting, submitted, unconfirmed


def config(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "apply.yaml"
    cfg = load_yaml(path) if path.exists() else {}
    cv = config_dir / "cv_content.yaml"
    if cv.exists() and "work_history" not in cfg:     # the CV's own jobs, for forms that ask them one by one
        try:
            cfg["work_history"] = work_history(load_yaml(cv))
        except Exception:  # noqa: BLE001
            log.exception("cv_content unreadable for work history")
    return cfg


_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _month_year(text: str) -> str | None:
    m = re.search(r"([A-Za-z]{3})[a-z]*\.? (\d{4})", text or "")
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    return f"{_MONTHS.index(m.group(1).lower()) + 1:02d}/{m.group(2)}"


def work_history(cv: dict[str, Any]) -> list[dict[str, Any]]:
    """cv_content experiences as form entries: title, company, location, MM/YYYY dates, a short description."""
    out = []
    for e in cv.get("experiences") or []:
        start, _, end = str(e.get("dates") or "").partition("--")
        current = bool(re.search(r"present|today|now", end, re.I))
        bullets = [b["text"] if isinstance(b, dict) else str(b) for b in e.get("bullets") or []]
        out.append({"title": re.sub(r"\s+--\s+", " - ", str(e.get("role") or "")).strip(),
                    "company": str(e.get("org") or "").split(" -- ")[0].strip(),
                    "location": str(e.get("location") or ""),
                    "start": _month_year(start), "end": None if current else _month_year(end), "current": current,
                    "description": "\n".join(f"- {b}" for b in bullets[:3])})
    return [w for w in out if w["title"] and w["company"] and w["start"]]


def target_for(opp, session=None, look_up: bool = False) -> ats.Target | None:  # noqa: ANN001
    urls = [getattr(opp, "apply_url", None), getattr(opp, "official_url", None)]
    for u in urls:
        if t := ats.detect(u):
            return t
    jid_url = next((u for u in urls if ats.gh_jid(u)), None)
    if not jid_url:
        return None
    board = None                       # company careers page backed by Greenhouse: which board?
    if session is not None:
        from sqlalchemy import select

        from summer_hunter.database.models import SourceRecord

        src = session.scalar(select(SourceRecord.source).where(SourceRecord.opportunity_id == opp.id,
                                                               SourceRecord.source.like("greenhouse:%")).limit(1))
        board = src.split(":", 1)[1] if src else None
    if board is None and look_up:
        board = ats.board_from_page(jid_url)
    return ats.detect(jid_url, board) if board else None


def supported(opp, config_dir: Path) -> bool:  # noqa: ANN001
    return bool(config(config_dir).get("enabled")) and (target_for(opp) is not None or
                                                         any(ats.gh_jid(u) for u in (opp.apply_url, opp.official_url)))


# State --------------------------------------------------------------------------------------------

def _dir(data_dir: Path, opp_id: int) -> Path:
    return data_dir / "apply" / str(int(opp_id))


def load_state(data_dir: Path, opp_id: int) -> dict[str, Any] | None:
    p = _dir(data_dir, opp_id) / "state.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(data_dir: Path, opp_id: int, state: dict[str, Any]) -> None:
    d = _dir(data_dir, opp_id)
    d.mkdir(parents=True, exist_ok=True)
    state["updated"] = datetime.now(timezone.utc).isoformat()
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / "state.json")


def _answers(state: dict[str, Any]) -> list[ans.Answer]:
    return [ans.from_dict(a) for a in state.get("answers", [])]


# Documents ----------------------------------------------------------------------------------------

def documents(settings, opp) -> dict[str, str | None]:  # noqa: ANN001
    """The CV and letter already made for this offer (the CV is built when missing; the letter is not)."""
    from summer_hunter.web.cv import build_cv, clean_role
    from summer_hunter.web.letter import _safe_dir

    cv_dir = settings.data_dir / "cv" / str(opp.id)
    pdfs = sorted(cv_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime) if cv_dir.exists() else []
    cv = str(pdfs[-1]) if pdfs else None
    if cv is None:
        built = build_cv(settings.config_dir, cv_dir, opp.id, opp.company_name, opp.title, opp.description_text or "",
                         opp.job_family)
        cv = str(built.pdf_path) if built.pdf_path else None
    lm_dir = settings.data_dir / "applications" / _safe_dir(opp.company_name) / _safe_dir(clean_role(opp.title))
    letters = sorted(lm_dir.glob("LM *.pdf"), key=lambda p: p.stat().st_mtime) if lm_dir.exists() else []
    return {"resume": cv, "cover_letter": str(letters[-1]) if letters else None}


def snapshot(data_dir: Path, opp_id: int, docs: dict[str, str | None]) -> tuple[dict[str, str | None], dict[str, str]]:
    """Copy the documents shown in the preview, so the files sent are exactly those, whatever happens later."""
    import hashlib
    import shutil

    d = _dir(data_dir, opp_id) / "files"
    d.mkdir(parents=True, exist_ok=True)
    out: dict[str, str | None] = {}
    hashes: dict[str, str] = {}
    for role, path in docs.items():
        if not path or not Path(path).is_file():
            out[role] = None
            continue
        dest = d / Path(path).name
        shutil.copyfile(path, dest)
        out[role] = str(dest)
        hashes[str(dest)] = hashlib.sha256(dest.read_bytes()).hexdigest()
    return out, hashes


def _hash_ok(state: dict[str, Any]) -> bool:
    """Every attached file must be the snapshot taken at preview time, unchanged."""
    import hashlib

    manifest = state.get("hashes") or {}
    files = [str(a.get("value")) for a in state.get("answers", []) if a.get("kind") == "file" and a.get("value")]
    if any(f not in manifest for f in files):
        return False
    for path, digest in manifest.items():
        p = Path(path)
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            return False
    return True


# Prepare ------------------------------------------------------------------------------------------

def prepare(settings, sessions, telegram, opp_id: int, explicit: bool = True) -> None:  # noqa: ANN001
    chat = settings.telegram_chat_id
    cfg = config(settings.config_dir)
    if not cfg.get("enabled"):
        telegram.send_message(chat, "Candidature auto désactivée (config/apply.yaml).")
        return
    from summer_hunter.crm.service import CRM

    with sessions() as session:
        opp = Repository(session).opportunity(opp_id)
        if opp is None:
            return
        target = target_for(opp, session, look_up=True)
        app = CRM(session).application_for(opp_id)
        applied_on = app.date_applied if app else None
        info = {"title": opp.title, "company": opp.company_name, "posting": opp.description_text or ""}
        docs = documents(settings, opp) if target else {}
    if target is None:
        telegram.send_message(chat, f"🤖 #{opp_id} : plateforme non gérée (Greenhouse, Lever, Ashby et Workday "
                                    "seulement). Postule via le bouton Apply.")
        return
    if target.kind == "workday" and not explicit:      # Workday saves drafts at the company: only on a tap
        telegram.send_message(chat, f"🤖 #{opp_id} est sur Workday : préparer la candidature crée ton compte chez "
                                    f"{escape(info['company'])} et y enregistre un brouillon (rien n'est envoyé avant ton ✅).",
                              [[{"text": "🤖 Créer le compte et préparer", "callback_data": f"aa:{opp_id}:prep"}]])
        return
    if not _LOCK.at(settings.data_dir).acquire(blocking=False):
        telegram.send_message(chat, "🤖 Un formulaire est déjà en cours de remplissage, réessaie dans une minute.")
        return
    try:
        old = load_state(settings.data_dir, opp_id) or {}
        if applied_on or old.get("status") in ("submitted", "submitting", "unconfirmed"):
            telegram.send_message(chat, f"🤖 #{opp_id} : candidature déjà envoyée ou en cours, je ne remplis pas une "
                                        "deuxième fois.")
            return
        if target.kind == "workday":
            docs, hashes = snapshot(settings.data_dir, opp_id, {"resume": docs.get("resume")})
            state = {"opp_id": opp_id, "kind": "workday", "status": "needs", "target": target.__dict__,
                     "known": dict(old.get("known") or {}), "hashes": hashes, "resume": docs.get("resume"),
                     "title": info["title"], "company": info["company"], "posting": info["posting"][:6000],
                     "rev": int(old.get("rev") or 0), "created": datetime.now(timezone.utc).isoformat()}
            save_state(settings.data_dir, opp_id, state)
            telegram.send_message(chat, f"🤖 Workday de <b>{escape(info['company'])}</b> : connexion ou création du compte, "
                                        "puis remplissage étape par étape (2 à 4 min)…")
            _workday_pass(settings, sessions, telegram, state, cfg)
            return
        telegram.send_message(chat, f"🤖 Je remplis le formulaire {ats.NAMES[target.kind]} de "
                                    f"<b>{escape(info['company'])}</b> (environ 1 min)…")
        try:
            form = ats.fetch_form(target)
        except ats.FormError as exc:
            telegram.send_message(chat, f"🤖 Formulaire illisible : {escape(str(exc))}. Postule via le bouton Apply.")
            return
        docs, hashes = snapshot(settings.data_dir, opp_id, docs)
        answers = _plan(settings, cfg, form, docs, info)
        state = {"opp_id": opp_id, "status": "ready", "form": form.to_dict(), "answers": [a.to_dict() for a in answers],
                 "hashes": hashes,
                 "title": info["title"], "company": info["company"],
                 "created": datetime.now(timezone.utc).isoformat()}
        save_state(settings.data_dir, opp_id, state)
        _preview(settings, sessions, telegram, state, cfg)
    except Exception as exc:  # noqa: BLE001 - always tell the user
        log.exception("auto-apply prepare failed")
        telegram.send_message(chat, f"🤖 Remplissage impossible : {type(exc).__name__}.")
    finally:
        _LOCK.release()


def _plan(settings, cfg: dict[str, Any], form: ats.Form, docs: dict[str, str | None],  # noqa: ANN001
          info: dict[str, str]) -> list[ans.Answer]:
    from summer_hunter.web.cv import load_content
    from summer_hunter.web.letter import build_provider, experience_facts, facts_block, verified_skills

    content = load_content(settings.config_dir)
    profile = load_yaml(settings.config_dir / "candidate_profile.yaml")
    citizenship = list((profile.get("immigration") or {}).get("citizenship") or [])
    answers = ans.build(form, content.get("header") or {}, cfg, citizenship, docs)
    applicant = cfg.get("applicant") or {}
    for a in answers:
        if a.kind == "location":
            a.extra["search"] = applicant.get("location_search") or ""
        if a.role == "edu_school":
            a.extra["fallback"] = applicant.get("school_fallback") or ""
    try:
        facts = facts_block(content, experience_facts(content, verified_skills(settings.config_dir)))
        ans.fill_with_model(build_provider(settings.llm), answers, facts, info["title"], info["company"], info["posting"])
    except Exception as exc:  # noqa: BLE001 - the model is optional: the user answers instead
        log.warning("auto-apply: model step skipped", extra={"error": str(exc)[:200]})
    return answers


def _preview(settings, sessions, telegram, state: dict[str, Any], cfg: dict[str, Any]) -> None:  # noqa: ANN001
    chat, opp_id = settings.telegram_chat_id, int(state["opp_id"])
    form = ats.Form.from_dict(state["form"])
    answers = _answers(state)
    shot = _dir(settings.data_dir, opp_id) / "preview.png"
    out = browser.fill(form.target.form_url, form.target.kind, answers, shot, submit=False,
                       headless=bool(cfg.get("headless", True)), timeout_s=int(cfg.get("timeout_s", 45)))
    state["rev"] = int(state.get("rev") or 0) + 1      # older preview buttons stop working
    state["problems"], state["failed"], state["notes"] = out.problems, out.failed, out.notes
    state["blocked_requests"] = out.blocked_requests
    save_state(settings.data_dir, opp_id, state)
    if out.status == "error" or out.screenshot is None:
        telegram.send_message(chat, f"🤖 Le navigateur n'a pas pu ouvrir le formulaire ({escape(out.detail)}).")
        return
    telegram.send_photo(chat, out.screenshot, f"Aperçu #{opp_id} : formulaire rempli, rien d'envoyé (CV et lettre "
                                              "joints seulement au moment de l'envoi).")
    text, buttons = render(state)
    msg_id = telegram.send_message(chat, text, buttons)
    with sessions() as session:
        Repository(session).set_kv(f"aa_msg:{msg_id}", f"{opp_id}:{int(state.get('rev') or 0)}")
        session.commit()


def render(state: dict[str, Any]) -> tuple[str, list[list[dict[str, str]]]]:
    opp_id = int(state["opp_id"])
    answers = _answers(state)
    kind = ats.NAMES.get(state["form"]["target"]["kind"], "")
    lines = [f"🤖 <b>{escape(state['title'][:80])}</b> · {escape(state['company'][:50])} ({kind})", ""]
    for n, a in enumerate(answers, 1):
        if a.missing and not a.required:
            continue
        if a.kind == "file":
            val = Path(str(a.value)).name if a.value else "—"
        elif isinstance(a.value, bool):
            val = "Oui" if a.value else "Non"
        elif isinstance(a.value, list):
            val = ", ".join(a.value)
        else:
            val = str(a.value or "")
        label = escape(a.label[:70])
        if a.required and a.missing:
            hint = f" ({escape(a.note)})" if a.note else ""
            opts = f"\n     choix : {escape(' | '.join(a.options[:8]))}" if a.options and len(a.options) <= 8 else ""
            if a.kind == "boolean":
                opts = "\n     choix : Oui | Non"
            lines.append(f"⚠️ <b>{n}. {label}</b> → à toi{hint}{opts}")
        else:
            note = f" <i>({escape(a.note)})</i>" if a.source == ans.MODEL else ""
            limit = 700 if a.source == ans.MODEL else 160           # what the model wrote is shown in full
            lines.append(f"{ICON.get(a.source, '•')} {n}. {label} → {escape(val[:limit])}{note}")
    if state.get("problems"):
        lines += ["", "⚠️ Champs que la page a refusés : " + escape(" ; ".join(state["problems"][:4]))]
    if state.get("notes"):
        lines += ["ℹ️ " + escape(" ; ".join(state["notes"][:3]))]
    failed = set(state.get("failed") or [])
    block = ans.blocking(answers) + [a for a in answers if a.required and a.key in failed and not a.missing]
    lines.append("")
    if block:
        lines.append(f"{len(block)} champ(s) obligatoire(s) vide(s) ou refusé(s) par la page. Réponds à ce message, "
                     "une ligne par champ : <code>numéro: réponse</code>.")
    else:
        lines.append("Tout est rempli. Relis, corrige si besoin en répondant <code>numéro: réponse</code>, puis envoie.")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "…"
    buttons = [[{"text": "❌ Annuler", "callback_data": f"aa:{opp_id}:no"}]]
    if not block:
        buttons.insert(0, [{"text": "✅ Envoyer la candidature",
                            "callback_data": f"aa:{opp_id}:go:{int(state.get('rev') or 0)}"}])
    return text, buttons


# Replies, submit, cancel --------------------------------------------------------------------------

_LINE = re.compile(r"^\s*(\d{1,3})\s*[:=.)-]\s*(.+?)\s*$")


def on_reply(settings, sessions, telegram, opp_id: int, text: str, tag: int | None = None) -> None:  # noqa: ANN001
    chat = settings.telegram_chat_id
    with _LOCK.at(settings.data_dir):     # load, change and save under the same lock as submit: no stale overwrite
        state = load_state(settings.data_dir, opp_id)
        if not state or state.get("status") not in RETRYABLE:
            telegram.send_message(chat, "🤖 Ce formulaire n'est plus modifiable (envoyé, annulé ou en cours).")
            return
        current = int(state.get("qrev" if state.get("kind") == "workday" else "rev") or 0)
        if tag is None or tag != current:       # numbers refer to the questions of THAT message only
            telegram.send_message(chat, "🤖 Ce message n'est plus le dernier : réponds au message le plus récent.")
            return
        workday_state = state.get("kind") == "workday"
        answers = [ans.from_dict(q) for q in state.get("questions", [])] if workday_state else _answers(state)
        done, bad = [], []
        for line in text.splitlines():
            m = _LINE.match(line)
            if not m:
                continue
            n = int(m.group(1))
            if not 1 <= n <= len(answers):
                bad.append(f"{n} : numéro inconnu")
                continue
            a = answers[n - 1]
            if ans.set_value(a, m.group(2)):
                done.append(str(n))
            else:
                hint = f" (choix : {' | '.join(a.options[:8])})" if a.options and len(a.options) <= 8 else ""
                bad.append(f"{n} : réponse non reconnue{hint}")
        if not done:
            telegram.send_message(chat, "🤖 Rien compris. Format : <code>3: Oui</code>, une ligne par champ."
                                        + ("\n" + escape("\n".join(bad)) if bad else ""))
            return
        if workday_state:
            for n in done:
                a = answers[int(n) - 1]
                state.setdefault("known", {})[a.key] = a.value
                remember(settings.data_dir, a)
            save_state(settings.data_dir, opp_id, state)
            if bad:
                telegram.send_message(chat, "⚠️ " + escape(" ; ".join(bad)))
            telegram.send_message(chat, f"🤖 Noté ({', '.join(done)}). Je reprends le formulaire Workday…")
            _workday_pass(settings, sessions, telegram, state, config(settings.config_dir))
            return
        state["answers"] = [a.to_dict() for a in answers]
        state["status"] = "ready"
        save_state(settings.data_dir, opp_id, state)
        if bad:
            telegram.send_message(chat, "⚠️ " + escape(" ; ".join(bad)))
        telegram.send_message(chat, f"🤖 Noté ({', '.join(done)}). Nouvel aperçu en cours…")
        _preview(settings, sessions, telegram, state, config(settings.config_dir))


CODE_RE = re.compile(r"^\s*([A-Za-z0-9]{6,10})\s*$")
CODE_WAIT_S = 600


class CodeSource:
    """The security code a site e-mails at submit time: given beforehand, or typed in the chat while the form waits."""

    def __init__(self, settings, sessions, telegram, opp_id: int, first: str | None = None) -> None:  # noqa: ANN001
        self.settings, self.sessions, self.telegram, self.opp_id, self._first = settings, sessions, telegram, opp_id, first
        self.path = _dir(settings.data_dir, opp_id) / "code.txt"

    def first(self) -> str | None:
        code, self._first = self._first, None
        return code

    def ask(self, attempt: int) -> None:
        self.path.unlink(missing_ok=True)
        with self.sessions() as session:
            Repository(session).set_kv("aa_code_wait", str(self.opp_id))
            session.commit()
        why = "Code refusé ou expiré : un nouveau code vient d'être envoyé" if attempt else "Le site a envoyé un code"
        self.telegram.send_message(self.settings.telegram_chat_id,
                                   f"📧 {why} à ton adresse mail. <b>Envoie-le ici</b> (juste le code), j'attends 10 min "
                                   "avec le formulaire ouvert.")

    def wait(self) -> str | None:
        deadline = time.monotonic() + CODE_WAIT_S
        try:
            while time.monotonic() < deadline:
                if self.path.exists():
                    code = self.path.read_text(encoding="utf-8").strip()
                    self.path.unlink(missing_ok=True)
                    if CODE_RE.match(code):
                        return code
                time.sleep(2)
            return None
        finally:
            with self.sessions() as session:
                Repository(session).set_kv("aa_code_wait", "")
                session.commit()


def take_code(settings, sessions, text: str) -> int | None:  # noqa: ANN001
    """A chat message that is a code while a form waits for one: hand it over (returns the offer id)."""
    m = CODE_RE.match(text or "")
    if not m:
        return None
    with sessions() as session:
        waiting = Repository(session).get_kv("aa_code_wait") or ""
    if not waiting.isdigit():
        return None
    p = _dir(settings.data_dir, int(waiting)) / "code.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(m.group(1), encoding="utf-8")
    return int(waiting)


def submit(settings, sessions, telegram, opp_id: int, rev: int | None = None, code: str | None = None) -> None:  # noqa: ANN001
    chat = settings.telegram_chat_id
    cfg = config(settings.config_dir)
    if not _LOCK.at(settings.data_dir).acquire(blocking=False):
        telegram.send_message(chat, "🤖 Un formulaire est déjà en cours, réessaie dans une minute.")
        return
    try:
        state = load_state(settings.data_dir, opp_id)
        if not state or state.get("status") not in RETRYABLE:
            status = (state or {}).get("status", "absent")
            telegram.send_message(chat, f"🤖 Envoi refusé : état « {escape(status)} » (déjà envoyée, annulée ou en cours).")
            return
        if rev is None or rev != int(state.get("rev") or 0):
            telegram.send_message(chat, "🤖 Ce bouton correspond à un ancien aperçu : utilise celui du dernier message.")
            return
        if state.get("kind") == "workday":
            if state.get("status") != "ready" or not _hash_ok(state):
                telegram.send_message(chat, "🤖 Envoi refusé : le formulaire Workday n'est pas arrivé à la page de "
                                            "relecture, ou le CV a changé. Relance la préparation.")
                return
            telegram.send_message(chat, f"🤖 Envoi Workday à <b>{escape(state['company'])}</b> (2 à 3 min)…")
            _workday_pass(settings, sessions, telegram, state, cfg, submit=True)     # sets "submitting" itself
            return
        answers = _answers(state)
        if block := ans.blocking(answers):
            telegram.send_message(chat, f"🤖 Il manque encore {len(block)} réponse(s) obligatoire(s).")
            return
        missing = [a.label for a in answers if a.kind == "file" and a.value and not Path(str(a.value)).exists()]
        if missing or not _hash_ok(state):
            telegram.send_message(chat, "🤖 CV ou lettre absents ou modifiés depuis l'aperçu. Relance « Postuler pour moi ».")
            return
        telegram.send_message(chat, f"🤖 Envoi à <b>{escape(state['company'])}</b> en cours (environ 1 min)…")
        state["status"] = "submitting"                 # set right before the browser: from here, never resend blindly
        save_state(settings.data_dir, opp_id, state)
        form = ats.Form.from_dict(state["form"])
        shot = _dir(settings.data_dir, opp_id) / "result.png"
        out = browser.fill(form.target.form_url, form.target.kind, answers, shot, submit=True,
                           headless=bool(cfg.get("headless", True)), timeout_s=int(cfg.get("timeout_s", 45)),
                           codes=CodeSource(settings, sessions, telegram, opp_id, code))
        status = out.status
        if out.clicked and status not in ("submitted", "captcha", "code"):
            status = "unconfirmed"            # the click may have gone through: only the user can say
        state["status"], state["result"] = status, {"detail": out.detail, "problems": out.problems,
                                                    "clicked": out.clicked}
        out.status = status
        save_state(settings.data_dir, opp_id, state)
        _report(settings, sessions, telegram, state, out, form.target.form_url)
    except Exception as exc:  # noqa: BLE001
        log.exception("auto-apply submit failed")
        state = load_state(settings.data_dir, opp_id) or {}
        if state.get("status") == "submitting":
            state["status"] = "unconfirmed"      # the click may have happened: never resend blindly
            save_state(settings.data_dir, opp_id, state)
        telegram.send_message(chat, f"🤖 Erreur pendant l'envoi ({type(exc).__name__}). Vérifie sur le site avant de "
                                    "réessayer.")
    finally:
        _LOCK.release()


def _report(settings, sessions, telegram, state: dict[str, Any], out: browser.Outcome, url: str) -> None:  # noqa: ANN001
    chat, opp_id = settings.telegram_chat_id, int(state["opp_id"])
    if out.screenshot and out.screenshot.exists():
        telegram.send_photo(chat, out.screenshot, f"Page après l'envoi #{opp_id}")
    if out.status == "submitted":
        _mark_applied(sessions, opp_id)
        telegram.send_message(chat, f"✅ Candidature envoyée à <b>{escape(state['company'])}</b>. "
                                    "Notée « postulée » dans le CRM, documents figés.")
        return
    link = [[{"text": "Ouvrir le formulaire", "url": url}]]
    if out.status == "code":
        telegram.send_message(chat, "📧 Pas envoyée : le site attend le code reçu par mail et il n'est pas arrivé à "
                                    "temps. Renvoie avec le bouton, puis envoie le nouveau code ici.",
                              [[{"text": "✅ Renvoyer", "callback_data": f"aa:{opp_id}:go:{int(state.get('rev') or 0)}"}]])
        return
    if out.status == "captcha":
        telegram.send_message(chat, "🧩 Le site demande un captcha : je ne le contourne pas. Rien n'a été envoyé. "
                                    "Ouvre le formulaire et termine à la main (toutes les réponses sont dans le message "
                                    "d'aperçu).", link)
    elif out.status == "invalid":
        telegram.send_message(chat, "⚠️ Le site a refusé l'envoi : " + escape(out.detail or "champ invalide")
                              + ". Corrige en répondant au message d'aperçu, puis renvoie.", link)
    elif out.status == "blocked":
        telegram.send_message(chat, "⛔ Pas envoyé : la page a refusé un champ obligatoire (" + escape(out.detail)
                              + "). Corrige en répondant au message d'aperçu.", link)
    elif out.status == "error":
        telegram.send_message(chat, f"⚠️ Navigateur en échec ({escape(out.detail)}). Rien n'a été confirmé.", link)
    else:
        telegram.send_message(chat, "❓ Envoi cliqué, mais pas de page de confirmation reconnue. Vérifie tes mails ou "
                                    "le site, puis dis-moi si c'est parti.",
                              [[{"text": "✅ C'est envoyé", "callback_data": f"aa:{opp_id}:done"},
                                {"text": "Ouvrir", "url": url}]])


def _mark_applied(sessions, opp_id: int) -> None:  # noqa: ANN001
    from summer_hunter.crm.service import CRM

    with sessions() as session:
        opp = Repository(session).opportunity(opp_id)
        if opp is not None:
            CRM(session).confirm_applied(opp, "telegram")
            session.commit()


def confirm_done(settings, sessions, opp_id: int) -> str:  # noqa: ANN001
    if not _LOCK.at(settings.data_dir).acquire(blocking=False):
        return "Occupé, réessaie dans une minute"
    try:
        state = load_state(settings.data_dir, opp_id)
        if not state or state.get("status") not in ("unconfirmed", "submitted"):
            return "Rien à confirmer"
        state["status"] = "submitted"
        save_state(settings.data_dir, opp_id, state)
    finally:
        _LOCK.release()
    _mark_applied(sessions, opp_id)
    return "Notée postulée ✅"


def cancel(settings, opp_id: int) -> str:  # noqa: ANN001
    if not _LOCK.at(settings.data_dir).acquire(blocking=False):
        return "Envoi ou remplissage en cours : impossible d'annuler maintenant"
    try:
        state = load_state(settings.data_dir, opp_id)
        if not state or state.get("status") not in RETRYABLE:
            return "Rien à annuler"
        state["status"] = "cancelled"
        save_state(settings.data_dir, opp_id, state)
        return "Annulé, rien n'a été envoyé"
    finally:
        _LOCK.release()


def ask_for_link(settings, sessions, telegram, opp_id: int) -> None:  # noqa: ANN001
    """No form the bot can fill for this offer: ask for the application link (reply to the message)."""
    msg_id = telegram.send_message(
        settings.telegram_chat_id,
        f"🤖 #{opp_id} : je n'ai pas de lien de candidature que je sais remplir (Greenhouse, Lever, Ashby, Workday). "
        "<b>Réponds à ce message avec le lien du formulaire</b> pour que je postule à ta place.")
    with sessions() as session:
        Repository(session).set_kv(f"aa_link:{msg_id}", str(opp_id))
        session.commit()


def attach_link(settings, sessions, telegram, opp_id: int, text: str) -> None:  # noqa: ANN001
    """The user replied with the application link of a pasted offer: keep it, then prepare as for any link."""
    chat = settings.telegram_chat_id
    url = ats.best_url(text)
    if url is None:
        telegram.send_message(chat, "🤖 Lien non géré : il faut un lien Greenhouse, Lever, Ashby ou Workday "
                                    "(ou une page carrière avec gh_jid). Les autres sites, postule à la main.")
        return
    with sessions() as session:
        opp = Repository(session).opportunity(opp_id)
        if opp is None:
            return
        opp.apply_url = url
        session.commit()
    prepare(settings, sessions, telegram, opp_id, explicit=False)


def start(fn, *args) -> None:  # noqa: ANN001
    threading.Thread(target=fn, args=args, daemon=True, name="auto-apply").start()


# Workday ------------------------------------------------------------------------------------------

# Only stable personal details are reused from one form to the next; everything else stays with its offer.
_GLOBAL_OK = re.compile(r"^(address line [12]|city|postal code|zip( code)?|state|region|province|county|"
                        r"country|country region|phone device type|country phone code|middle name)$")


def _memory_path(data_dir: Path) -> Path:
    return data_dir / "apply" / "memory.json"


def memory(data_dir: Path) -> dict[str, Any]:
    try:
        return json.loads(_memory_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(data_dir: Path, a: ans.Answer) -> None:
    """Keep the user's answer to a generic question (address, postal code...) for the next forms.
    Questions about a given company, past jobs, salary or visa are never reused elsewhere."""
    if not _GLOBAL_OK.match(a.key) or a.source != ans.USER:
        return
    mem = memory(data_dir)
    mem[a.key] = a.value
    p = _memory_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def account_email(data_dir: Path, fallback: str) -> str:
    """The e-mail used to create accounts (chosen in the Mini App), else the CV's."""
    try:
        value = json.loads((data_dir / "apply" / "account.json").read_text(encoding="utf-8")).get("email")
    except (OSError, ValueError):
        value = None
    return str(value or fallback)


def set_account_email(data_dir: Path, email: str) -> None:
    p = data_dir / "apply" / "account.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"email": email}), encoding="utf-8")
    tmp.replace(p)


def _tenant_flag(data_dir: Path, host: str, flag: str | None = None) -> bool:
    """Per-company facts learnt from its pages (data/apply/tenants.json); with `flag`, record it."""
    p = data_dir / "apply" / "tenants.json"
    try:
        known = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    if flag:
        known.setdefault(host, {})[flag] = True
        p.parent.mkdir(parents=True, exist_ok=True)
        p.with_suffix(".tmp").write_text(json.dumps(known, indent=1), encoding="utf-8")
        p.with_suffix(".tmp").replace(p)
        return True
    return bool((known.get(host) or {}).get("single_profile"))


def _workday_pass(settings, sessions, telegram, state: dict[str, Any], cfg: dict[str, Any],  # noqa: ANN001
                  submit: bool = False) -> None:
    """One browser run through the Workday wizard; caller holds _LOCK and saved the state."""
    from summer_hunter.apply import workday
    from summer_hunter.web.cv import load_content
    from summer_hunter.web.letter import build_provider, experience_facts, facts_block, verified_skills

    chat, opp_id = settings.telegram_chat_id, int(state["opp_id"])
    content = load_content(settings.config_dir)
    header = content.get("header") or {}
    who = ans.Applicant(header, cfg)
    profile = load_yaml(settings.config_dir / "candidate_profile.yaml")
    citizenship = list((profile.get("immigration") or {}).get("citizenship") or [])
    target = ats.Target(**state["target"])
    known = {**memory(settings.data_dir), **(state.get("known") or {})}

    def model(items: list[ans.Answer]) -> None:
        if not items:
            return
        try:
            facts = facts_block(content, experience_facts(content, verified_skills(settings.config_dir)))
            ans.fill_with_model(build_provider(settings.llm), items, facts, state["title"], state["company"],
                                state.get("posting", ""))
        except Exception as exc:  # noqa: BLE001 - optional
            log.warning("workday: model step skipped", extra={"error": str(exc)[:200]})

    shot = _dir(settings.data_dir, opp_id) / ("result.png" if submit else "workday.png")
    if submit:                                   # from here the browser may click: never resend blindly
        state["status"] = "submitting"
        save_state(settings.data_dir, opp_id, state)
    try:
        email = state.get("email") or account_email(settings.data_dir, who.email)
        res = workday.run(target.form_url, email, workday.account_name(target.board, email), known, who, cfg,
                          citizenship, state["company"], state.get("resume"), shot, submit=submit,
                          headless=bool(cfg.get("headless", True)), timeout_s=int(cfg.get("timeout_s", 45)),
                          model=model, approved_fp=state.get("review_fp") if submit else None,
                          job_title=state.get("title") or "")
    except Exception:
        if submit:
            state["status"] = "unconfirmed"
            save_state(settings.data_dir, opp_id, state)
        raise
    if res.detail == "single_profile":
        _tenant_flag(settings.data_dir, target.board, "single_profile")
    strict = _tenant_flag(settings.data_dir, target.board)
    if res.status in ("account_exists", "auth_failed") and strict:
        res.detail = "single_profile"
    if res.status in ("account_exists", "auth_failed") and not submit and not state.get("alias_tried") \
            and res.detail != "single_profile":
        alias = workday.plus_alias(email, target.board)
        if alias and alias.lower() != email.lower():  # a fresh account with name+company@gmail.com (same inbox)
            state["email"], state["alias_tried"] = alias, True
            save_state(settings.data_dir, opp_id, state)
            telegram.send_message(chat, f"🤖 Compte existant ou mot de passe refusé : je crée un compte neuf avec "
                                        f"<code>{escape(alias)}</code> (les mails arrivent dans ta boîte habituelle).")
            _workday_pass(settings, sessions, telegram, state, cfg, submit=False)
            return
    status = {"review": "ready"}.get(res.status, res.status)
    if submit and res.clicked and status != "submitted":
        status = "unconfirmed"                  # the final click happened: only the user can tell what followed
    if submit and not res.clicked and status != "submitted":
        status = status if status in RETRYABLE else "error"
    changed = res.status == "review" and res.step == "changed"
    if res.status == "review":
        state["review_fp"] = res.detail         # the Review page as shown in the screenshot sent now
    state["status"], state["step"] = status, ("Review" if changed else res.step)
    state["detail"] = "" if res.status == "review" else res.detail
    state["changed"] = changed
    state["questions"] = [a.to_dict() for a in res.questions]
    state["filled"] = [a.to_dict() for a in res.filled]
    if status == "ready":
        state["rev"] = int(state.get("rev") or 0) + 1
    save_state(settings.data_dir, opp_id, state)
    if res.screenshot:
        cap = f"Workday #{opp_id} · étape « {res.step or '?'} »"
        telegram.send_photo(chat, res.screenshot, cap + (" (envoyée)" if status == "submitted" else ""))
    if status == "submitted":
        _mark_applied(sessions, opp_id)
        telegram.send_message(chat, f"✅ Candidature Workday envoyée à <b>{escape(state['company'])}</b>. Notée postulée.")
        return
    text, buttons = render_workday(state)
    if status in ("account_exists", "auth_failed") and (btn := account_button(sessions)):
        buttons = [btn, *buttons]
    state["qrev"] = int(state.get("qrev") or 0) + 1
    save_state(settings.data_dir, opp_id, state)
    msg_id = telegram.send_message(chat, text, buttons)
    with sessions() as session:
        Repository(session).set_kv(f"aa_msg:{msg_id}", f"{opp_id}:{state['qrev']}")
        session.commit()


def account_button(sessions) -> list[dict[str, Any]]:  # noqa: ANN001
    """Telegram button opening the Mini App window where the e-mail and password for accounts are typed."""
    with sessions() as session:
        url = Repository(session).get_kv("webapp_url") or ""
    if not url.startswith("https://"):
        return []
    return [{"text": "🔑 E-mail et mot de passe des comptes", "web_app": {"url": f"{url.rstrip('/')}/#/account"}}]


def send_account_window(settings, sessions, telegram) -> None:  # noqa: ANN001
    btn = account_button(sessions)
    if not btn:
        telegram.send_message(settings.telegram_chat_id, "🔑 Mini App indisponible : ouvre-la depuis le menu du bot.")
        return
    telegram.send_message(settings.telegram_chat_id,
                          "🔑 Fenêtre sécurisée : l'e-mail et le mot de passe utilisés pour créer tes comptes candidat. "
                          "Le mot de passe va dans le trousseau du Mac, jamais dans ce chat.", [btn])


def render_workday(state: dict[str, Any]) -> tuple[str, list[list[dict[str, str]]]]:
    opp_id, status = int(state["opp_id"]), state.get("status")
    head = f"🤖 <b>{escape(state['title'][:80])}</b> · {escape(state['company'][:50])} (Workday)"
    url = ats.Target(**state["target"]).form_url
    cancel = [{"text": "❌ Annuler", "callback_data": f"aa:{opp_id}:no"}]
    if status == "verify_email":
        return (head + "\n\n📧 Compte créé. Workday t'a envoyé un mail de confirmation : ouvre-le et clique le lien "
                "(depuis ton téléphone, ça marche), puis appuie sur le bouton.",
                [[{"text": "✅ C'est confirmé, continuer", "callback_data": f"aa:{opp_id}:cont"}], cancel])
    if status in ("account_exists", "auth_failed") and state.get("detail") == "single_profile":
        return (head + "\n\n🔑 Ce site interdit d'avoir plusieurs comptes (risque d'élimination), donc je ne crée pas "
                "de compte en double. Clique « Forgot your password? » sur le site, choisis TON mot de passe générique, "
                "puis appuie sur Réessayer.",
                [[{"text": "Ouvrir", "url": url}, {"text": "🔁 Réessayer", "callback_data": f"aa:{opp_id}:cont"}], cancel])
    if status in ("account_exists", "auth_failed"):
        why = ("Un compte existe déjà chez cette entreprise avec ton e-mail, et je n'ai pas son mot de passe."
               if status == "account_exists" else "Le mot de passe enregistré est refusé : je n'insiste pas.")
        return (head + f"\n\n🔑 {why} Réinitialise-le sur le site (lien ci-dessous), puis enregistre-le SUR LE MAC "
                f"(jamais dans Telegram) : <code>summer-hunter apply-password {escape(ats.Target(**state['target']).board)}</code>"
                ", et appuie sur Réessayer.",
                [[{"text": "Ouvrir", "url": url}, {"text": "🔁 Réessayer", "callback_data": f"aa:{opp_id}:cont"}], cancel])
    lines = [head, ""]
    filled = [ans.from_dict(a) for a in state.get("filled", [])]
    shown = [a for a in filled if a.source in (ans.RULE, ans.MODEL, ans.USER)]
    if shown:
        lines.append("Réponses données (hors identité) :")
        for a in shown[:25]:
            val = "Oui" if a.value is True else "Non" if a.value is False else \
                (", ".join(a.value) if isinstance(a.value, list) else str(a.value))
            note = f" <i>({escape(a.note)})</i>" if a.source == ans.MODEL else ""
            lines.append(f"{ICON.get(a.source, '•')} {escape(a.label[:70])} → {escape(val[:400])}{note}")
        lines.append("")
    if status == "ready" and state.get("changed"):
        lines.append("⚠️ La page de relecture n'est plus celle que tu avais validée : RIEN envoyé. Voici la nouvelle "
                     "capture ; vérifie-la, puis envoie.")
        return "\n".join(lines)[:4000], [[{"text": "✅ Envoyer la candidature",
                                            "callback_data": f"aa:{opp_id}:go:{int(state.get('rev') or 0)}"}], cancel]
    if status == "ready":
        lines.append("📋 Page de relecture atteinte : tout est rempli (brouillon chez Workday, PAS envoyé). Vérifie la "
                     "capture, puis envoie.")
        return "\n".join(lines)[:4000], [[{"text": "✅ Envoyer la candidature",
                                            "callback_data": f"aa:{opp_id}:go:{int(state.get('rev') or 0)}"}], cancel]
    questions = [ans.from_dict(q) for q in state.get("questions", [])]
    if status == "needs" and questions:
        lines.append(f"⚠️ Étape « {escape(state.get('step') or '?')} » : il me faut ces réponses. Réponds à ce message, "
                     "une ligne par champ : <code>numéro: réponse</code>.")
        for n, a in enumerate(questions, 1):
            opts = f"\n     choix : {escape(' | '.join(a.options[:10]))}" if a.options and len(a.options) <= 10 else ""
            hint = f" ({escape(a.note)})" if a.note else ""
            lines.append(f"<b>{n}. {escape(a.label[:90])}</b>{hint}{opts}")
        return "\n".join(lines)[:4000], [cancel]
    if status in ("unconfirmed", "submitting"):
        lines.append("❓ Envoi cliqué, mais pas de confirmation lue. Vérifie tes mails ou ton compte Workday : je ne "
                     "renverrai pas tout seul.")
        return "\n".join(lines)[:4000], [[{"text": "✅ C'est envoyé", "callback_data": f"aa:{opp_id}:done"},
                                            {"text": "Ouvrir", "url": url}]]
    lines.append(f"⚠️ Arrêt à l'étape « {escape(state.get('step') or '?')} » : {escape(state.get('detail') or status)}. "
                 "Rien n'a été envoyé.")
    return "\n".join(lines)[:4000], [[{"text": "🔁 Réessayer", "callback_data": f"aa:{opp_id}:cont"},
                                        {"text": "Ouvrir", "url": url}], cancel]


def resume_workday(settings, sessions, telegram, opp_id: int) -> None:  # noqa: ANN001
    """After the e-mail confirmation or a failed step: run the wizard again from where Workday kept the draft."""
    chat = settings.telegram_chat_id
    with _LOCK.at(settings.data_dir):
        state = load_state(settings.data_dir, opp_id)
        if not state or state.get("kind") != "workday" or state.get("status") not in RETRYABLE:
            telegram.send_message(chat, "🤖 Rien à reprendre pour cette offre.")
            return
        telegram.send_message(chat, "🤖 Je reprends le formulaire Workday…")
        try:
            _workday_pass(settings, sessions, telegram, state, config(settings.config_dir))
        except Exception as exc:  # noqa: BLE001
            log.exception("workday resume failed")
            telegram.send_message(chat, f"🤖 Reprise impossible : {type(exc).__name__}.")
