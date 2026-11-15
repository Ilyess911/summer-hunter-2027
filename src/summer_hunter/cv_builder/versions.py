"""AI CV Builder lot 4: append-only CV versions.

A new request creates CV-0007 v1; an edit of any version creates the next version of the same CV
(v2, v3…) with its own files: nothing is overwritten. Files live in
data/cv_builder/CV-0007/v2/CV_Robotics_Engineer_Singapore_2027_v2.{pdf,docx,tex}.
Each row records the analyzed request, the exact CV printed and the profile used.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from summer_hunter.cv_builder.document import Rendered, build
from summer_hunter.cv_builder.intent import CVRequest
from summer_hunter.database.models import CVVersion

EDITABLE = {"language", "pages", "style", "emphasis", "exclude", "target_role", "company", "country_code", "track",
            "family", "personalization"}


def label(v: CVVersion) -> str:
    return f"CV-{(v.lineage_id or v.id):04d} v{v.version}"


def profile_snapshot(config_dir: Path, confirmed: list[Any]) -> dict[str, Any]:
    digest = hashlib.sha1()
    for name in ("cv_content.yaml", "candidate_profile.yaml", "cv_content_fr.yaml"):
        path = config_dir / name
        if path.exists():
            digest.update(path.read_bytes())
    return {"yaml_sha1": digest.hexdigest()[:16],
            "facts": [{"id": getattr(f, "id", None), "kind": f.kind, "text": f.text} for f in confirmed]}


class VersionStore:
    def __init__(self, session: Session, data_dir: Path, config_dir: Path) -> None:
        self.s = session
        self.root = data_dir / "cv_builder"
        self.config_dir = config_dir

    # Queries -------------------------------------------------------------------------------------------

    def get(self, version_id: int) -> CVVersion | None:
        return self.s.get(CVVersion, version_id)

    def lineage(self, lineage_id: int) -> list[CVVersion]:
        return list(self.s.execute(select(CVVersion).where(CVVersion.lineage_id == lineage_id)
                                   .order_by(CVVersion.version)).scalars())

    def latest(self) -> CVVersion | None:
        return self.s.execute(select(CVVersion).order_by(CVVersion.id.desc()).limit(1)).scalar_one_or_none()

    def history(self, limit: int = 10) -> list[CVVersion]:
        """Latest version of each CV, most recent first."""
        newest = (select(CVVersion.lineage_id, func.max(CVVersion.version).label("v"))
                  .group_by(CVVersion.lineage_id).subquery())
        q = (select(CVVersion).join(newest, (CVVersion.lineage_id == newest.c.lineage_id) & (CVVersion.version == newest.c.v))
             .order_by(CVVersion.id.desc()).limit(limit))
        return list(self.s.execute(q).scalars())

    # Writes --------------------------------------------------------------------------------------------

    def create(self, req: CVRequest, confirmed: list[Any], parent: CVVersion | None = None,
               change: str | None = None) -> tuple[CVVersion, Rendered]:
        """Build and record a new version. v1 of a new CV without `parent`, next version of its CV with one."""
        if parent is not None:
            lineage_id = parent.lineage_id or parent.id
            version = 1 + (self.s.execute(select(func.max(CVVersion.version))
                                          .where(CVVersion.lineage_id == lineage_id)).scalar() or 0)
        else:
            lineage_id, version = None, 1
        # Render first, outside any write transaction: pdflatex can take seconds and SQLite allows one writer,
        # so holding the lock here would block the bot's own writes (offsets, buttons) meanwhile.
        staging = self.root / "_staging" / f"{time.time_ns()}"
        res = build(self.config_dir, staging, req, confirmed, version=version)
        row = CVVersion(lineage_id=lineage_id, parent_id=parent.id if parent else None, version=version,
                        source_text=req.source_text[:20000], stem=res.stem, change=(change or "")[:300] or None,
                        target_role=req.target_role, company=req.company, country_code=req.country_code,
                        language=req.language, pages=res.pages, style=res.style, request=req.to_dict(), content=res.cv,
                        profile=profile_snapshot(self.config_dir, confirmed),
                        checks=[{"name": c.name, "ok": c.ok, "detail": c.detail} for c in res.checks])
        self.s.add(row)
        self.s.flush()
        if row.lineage_id is None:
            row.lineage_id = row.id
        out_dir = self.root / f"CV-{row.lineage_id:04d}" / f"v{version}"
        if out_dir.exists() and any(out_dir.iterdir()):
            # Only a build that was rolled back can leave files without a row: keep them aside.
            owned = self.s.execute(select(CVVersion.id).where(CVVersion.pdf_path.like(f"{out_dir}%"))).first()
            if owned:
                raise FileExistsError(f"{out_dir} already holds a version")   # never overwrite
            out_dir.rename(out_dir.with_name(f"{out_dir.name}.orphan-{row.id}-{int(time.time())}"))
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        if out_dir.exists():
            out_dir.rmdir()                     # empty directory only
        staging.rename(out_dir)
        for attr in ("pdf_path", "docx_path", "tex_path"):
            path = getattr(res, attr)
            if path:
                setattr(res, attr, out_dir / path.name)
                setattr(row, attr, str(out_dir / path.name))
        self.s.flush()
        return row, res

    def derive(self, parent: CVVersion, confirmed: list[Any], change: str, **overrides: Any) -> tuple[CVVersion, Rendered]:
        """Next version of `parent`'s CV with some request fields changed (language, pages, emphasis…)."""
        unknown = set(overrides) - EDITABLE
        if unknown:
            raise ValueError(f"not editable: {sorted(unknown)}")
        req = CVRequest.from_dict(parent.request)
        if "emphasis" in overrides:
            overrides["emphasis"] = list(dict.fromkeys([*overrides["emphasis"], *req.emphasis]))[:3]
        if "exclude" in overrides:
            overrides["exclude"] = list(dict.fromkeys([*req.exclude, *overrides["exclude"]]))[:6]
        if "language" in overrides:
            overrides.setdefault("language_reason", "modification demandée")
        req = replace(req, **overrides)
        return self.create(req, confirmed, parent=parent, change=change)

    def set_saved(self, version_id: int, saved: bool = True) -> CVVersion | None:
        row = self.get(version_id)
        if row is not None:
            row.saved = saved
            self.s.flush()
        return row

    @staticmethod
    def files(row: CVVersion) -> list[Path]:
        return [Path(p) for p in (row.pdf_path, row.docx_path) if p and Path(p).exists()]
