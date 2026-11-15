"""AI CV Builder service shared by the Telegram bot and the Mini App (two processes).

Builds are serialized across processes by a file lock (data/cv_builder/.build.lock), so version
numbers and files stay consistent whichever surface asked for the CV.
"""

from __future__ import annotations

import fcntl
import logging
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from summer_hunter.cv_builder import prefs
from summer_hunter.cv_builder.document import Rendered, _norm, blocks
from summer_hunter.cv_builder.intent import CVRequest, analyze, classify_family, context_from
from summer_hunter.cv_builder.profile import ProfileStore
from summer_hunter.cv_builder.versions import VersionStore, label
from summer_hunter.database.models import CVVersion
from summer_hunter.database.repository import Repository
from summer_hunter.engine.geo import country_name
from summer_hunter.utils.settings import Settings

log = logging.getLogger(__name__)


@dataclass
class Built:
    version_id: int
    tag: str
    req: CVRequest
    res: Rendered


def _guard(settings: Settings, session: Session):  # noqa: ANN202
    """Blocking check run on every CV before it is recorded: the target line addresses the target only."""
    from summer_hunter.cv_builder.context import ContextViolation, from_request, validate_cv

    actx = context_from(settings, session)
    orgs = sorted(set(actx.companies) | set(actx.own_orgs))

    def check(req: CVRequest, cv: dict[str, Any]) -> None:
        problems = validate_cv(cv, from_request(req, settings.profile_raw), orgs, actx.own_orgs)
        if problems:
            raise ContextViolation(problems)
    return check


@contextmanager
def build_lock(data_dir: Path) -> Iterator[None]:
    path = data_dir / "cv_builder" / ".build.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _store(session: Session) -> ProfileStore:
    store = ProfileStore(session)
    if store.seed():
        session.flush()
    return store


def generate(settings: Settings, sessions: sessionmaker[Session], text: str,
             overrides: dict[str, Any] | None = None) -> Built:
    """New CV (v1) from a free request or a posting; `overrides` = choices made in the UI (language, pages)."""
    with build_lock(settings.data_dir), sessions() as session:
        store = _store(session)
        req = analyze(text, context_from(settings, session))
        req = prefs.apply(req, prefs.load(Repository(session)), text)
        if overrides:
            clean = {k: v for k, v in overrides.items() if k in {"language", "pages"} and v}
            if "language" in clean:
                clean["language_reason"] = "choisi dans l'app"
            req = replace(req, **clean)
        row, res = VersionStore(session, settings.data_dir, settings.config_dir).create(
            req, store.confirmed(), validator=_guard(settings, session))
        session.commit()
        return Built(row.id, label(row), req, res)


def derive(settings: Settings, sessions: sessionmaker[Session], vid: int, change: str,
           overrides: dict[str, Any]) -> Built | None:
    """Next version of a CV. A new target role re-targets family and track, nothing else."""
    with build_lock(settings.data_dir), sessions() as session:
        vs = VersionStore(session, settings.data_dir, settings.config_dir)
        parent = vs.get(vid)
        if parent is None:
            return None
        overrides = dict(overrides)
        if "target_role" in overrides:
            ctx = context_from(settings, session)
            family = classify_family(ctx, overrides["target_role"], "", False)
            overrides["family"] = family
            overrides["track"] = ("industry" if family in set(ctx.cv_content.get("industry_families", []))
                                  else "product" if family in set(ctx.cv_content.get("product_families", []))
                                  else "business")
        for attempt in (1, 2):
            try:
                row, res = vs.derive(parent, _store(session).confirmed(), change, validator=_guard(settings, session),
                                     **overrides)
                session.commit()
                break
            except IntegrityError:          # the other process took this version number first
                session.rollback()
                parent = vs.get(vid)
                if attempt == 2:
                    raise
        return Built(row.id, label(row), CVRequest.from_dict(row.request), res)


# Presentation shared by both surfaces -------------------------------------------------------------

def report_items(req: CVRequest, cv: dict[str, Any], checks: list[dict[str, Any]], notes: list[str]) -> list[tuple[str, str]]:
    """(label, text) lines of the optimization report, in French."""
    shown = _norm(" ".join(t for _, t in blocks(cv)))
    out: list[tuple[str, str]] = []
    strengths = [e.get("org", "").split(" -- ")[0] for e in cv.get("experiences", [])[:2]]
    strengths += [p.get("name", "").split(" -- ")[0] for p in cv.get("projects", [])[:2]]
    out.append(("Mis en avant", ", ".join(s for s in strengths if s)))
    kw = [k for k in req.keywords if _norm(k) and _norm(k) in shown]
    if kw:
        out.append(("Mots-clés présents", ", ".join(kw[:10])))
    if req.mode == "offer":
        missing = [r.name for r in req.requirements if r.coverage == "missing" and r.level == "mandatory"]
        partial = [r.name for r in req.requirements if r.coverage in {"partial", "to_confirm"}]
        if missing:
            out.append(("Non couvert (jamais ajouté)", ", ".join(missing)))
        if partial:
            out.append(("Partiel ou à confirmer", ", ".join(partial)))
    if cv.get("gaps"):
        out.append(("L'offre cite, ton CV ne montre pas", ", ".join(cv["gaps"])))
    out += [("Note", n) for n in notes]
    failed = [c for c in checks if not c.get("ok")]
    out.append(("Contrôles", "tous OK (dates, sections, faits vérifiés, pages, PDF = DOCX)" if not failed
                else "; ".join(f"{c['name']} : {c['detail']}" for c in failed)))
    out += [("Question", q) for q in req.questions]
    return out


def payload(row: CVVersion) -> dict[str, Any]:
    req = CVRequest.from_dict(row.request or {})
    return {"id": row.id, "lineage": row.lineage_id, "version": row.version, "tag": label(row),
            "role": row.target_role, "company": row.company,
            "location": country_name(row.country_code) if row.country_code else None,
            "language": row.language, "pages": row.pages, "saved": bool(row.saved), "change": row.change,
            "created": row.created_at.isoformat() if row.created_at else None,
            "ok": all(c.get("ok") for c in (row.checks or [])),
            "report": [{"label": a, "text": b} for a, b in report_items(req, row.content or {}, row.checks or [], row.notes or [])],
            "has_pdf": bool(row.pdf_path and Path(row.pdf_path).exists()),
            "has_docx": bool(row.docx_path and Path(row.docx_path).exists())}


def email_for(settings: Settings, sessions: sessionmaker[Session], vid: int, language: str | None = None):  # noqa: ANN201
    """Cover email for a CV version, from the same ApplicationContext as the CV. Drafted only, never sent.

    None if the version is unknown; raises ContextViolation if the email would address anyone but the target.
    """
    from summer_hunter.cv_builder.context import ContextViolation, from_request, validate_email
    from summer_hunter.cv_builder.email import compose

    with sessions() as session:
        row = VersionStore(session, settings.data_dir, settings.config_dir).get(vid)
        if row is None:
            return None
        req = CVRequest.from_dict(row.request or {})
        if language in {"en", "fr"}:
            req = replace(req, language=language)
        actx = context_from(settings, session)
    ctx = from_request(req, settings.profile_raw)
    mail = compose(row.content or {}, ctx, req.track)
    problems = validate_email(mail.parts, ctx, sorted(set(actx.companies) | set(actx.own_orgs)), actx.own_orgs)
    if problems:
        raise ContextViolation(problems)
    return mail
