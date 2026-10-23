"""Link each application to its CV and cover letter, version them, and freeze what was submitted.

Layout (same folder the cover-letter generator already writes to):
    data/applications/<Company>/<Role>/cv.pdf, cv.tex, cover_letter.pdf, cover_letter.tex
    data/applications/<Company>/<Role>/submitted-<YYYY-MM-DD>/   frozen copy of what was sent
The CV generator writes to data/cv/<opportunity id>/; its newest output is copied in as cv.pdf/cv.tex.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from summer_hunter.database.models import CRMApplication
from summer_hunter.web.cv import clean_role

if TYPE_CHECKING:
    from summer_hunter.crm.service import CRM


def safe_dir(name: str) -> str:
    # Must stay identical to web.letter._safe_dir so letters and CRM share one folder.
    return re.sub(r"[^\w .&()-]+", " ", name).strip()[:80] or "untitled"


def application_folder(data_dir: Path, company: str, title: str) -> Path:
    return data_dir / "applications" / safe_dir(company) / safe_dir(clean_role(title))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _newest(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime) if folder.exists() else []
    return files[-1] if files else None


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def sync_documents(crm: CRM, app: CRMApplication) -> None:
    """Pick up newly generated documents. Once submitted, the recorded versions never move."""
    if app.submitted_folder:
        return
    opp = app.opportunity
    folder = application_folder(crm.data_dir, opp.company_name, opp.title)
    app.folder = str(folder)
    cv_dir = crm.data_dir / "cv" / str(opp.id)
    cv_pdf, cv_tex = _newest(cv_dir, "*.pdf"), _newest(cv_dir, "*.tex")
    if cv_pdf or cv_tex:
        folder.mkdir(parents=True, exist_ok=True)
        for src, name in ((cv_pdf, "cv.pdf"), (cv_tex, "cv.tex")):
            if src and (not (folder / name).exists() or _hash(src) != _hash(folder / name)):
                shutil.copyfile(src, folder / name)
    _record(crm, app, folder / "cv.pdf" if (folder / "cv.pdf").exists() else folder / "cv.tex", "cv")
    letter = folder / "cover_letter.pdf"
    _record(crm, app, letter if letter.exists() else folder / "cover_letter.tex", "letter")


def _record(crm: CRM, app: CRMApplication, path: Path, kind: str) -> None:
    if not path.exists():
        return
    digest = _hash(path)
    if digest == getattr(app, f"{kind}_hash"):
        return
    setattr(app, f"{kind}_hash", digest)
    setattr(app, f"{kind}_path", str(path))
    setattr(app, f"{kind}_version", (getattr(app, f"{kind}_version") or 0) + 1)
    setattr(app, f"{kind}_generated_at", _mtime(path))
    crm.log_event("document_updated", "system", app, new=f"{kind} v{getattr(app, f'{kind}_version')}", notes=path.name)


def freeze_documents(crm: CRM, app: CRMApplication, note: str = "") -> None:
    """Copy the documents as they are at submission time; later regenerations cannot touch the copy.

    The folder is created even when no document exists yet, with a manifest saying so: a CV
    generated after the submission must never be mistaken for the version that was sent.
    """
    if app.submitted_folder:
        return
    sync_documents(crm, app)
    opp = app.opportunity
    folder = Path(app.folder) if app.folder else application_folder(crm.data_dir, opp.company_name, opp.title)
    sources = [p for p in (Path(app.cv_path) if app.cv_path else None, Path(app.letter_path) if app.letter_path else None) if p]
    target = folder / f"submitted-{(app.date_applied or crm.today()).isoformat()}"
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in sources:
        for sibling in (src, src.with_suffix(".tex"), src.with_suffix(".pdf")):
            if sibling.exists():
                shutil.copyfile(sibling, target / sibling.name)
                copied.append(sibling.name)
    manifest = [f"Application {opp.company_name} / {opp.title}", f"Submitted on {app.date_applied}",
                f"Frozen at {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
                f"CV v{app.cv_version or 0}, cover letter v{app.letter_version or 0}",
                "Files: " + (", ".join(sorted(set(copied))) or "none existed at submission time")]
    if note:
        manifest.append(note)
    (target / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    app.folder = str(folder)
    app.submitted_folder = str(target)
    crm.log_event("documents_frozen", "system", app, new=target.name,
                  notes=f"cv v{app.cv_version or 0}, cover letter v{app.letter_version or 0}" + (f"; {note}" if note else ""))
