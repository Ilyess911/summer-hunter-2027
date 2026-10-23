"""Export / import entry points shared by the Telegram bot, the CLI and the nightly run."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from summer_hunter.crm.excel_export import FILENAME, export_workbook
from summer_hunter.crm.excel_import import ImportReport, WorkbookError, import_workbook
from summer_hunter.crm.service import CRM
from summer_hunter.database.migrations import backup_sqlite
from summer_hunter.utils.settings import Settings

log = logging.getLogger(__name__)


def crm_dir(settings: Settings) -> Path:
    return settings.data_dir / "crm"


def workbook_path(settings: Settings) -> Path:
    return crm_dir(settings) / FILENAME


def _db_path(settings: Settings) -> Path | None:
    url = settings.database_url
    return Path(url.removeprefix("sqlite:///")) if url.startswith("sqlite:///") else None


def export(settings: Settings, sessions: sessionmaker[Session], path: Path | None = None) -> Path:
    path = path or workbook_path(settings)
    with sessions() as session:
        crm = CRM(session, settings.data_dir, settings.timezone)
        for app in crm.applications():
            crm.sync_documents(app)
        export_workbook(crm, path, settings.timezone, crm_dir(settings) / "backups")
        session.commit()
    log.info("crm workbook exported", extra={"path": str(path)})
    return path


def import_file(settings: Settings, sessions: sessionmaker[Session], path: Path, prefer_excel: bool = False,
                dry_run: bool = False) -> ImportReport:
    """Import authorized edits; on a clean import the workbook is regenerated with fresh row versions."""
    def backup() -> Path:
        imports = crm_dir(settings) / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, imports / f"import-{datetime.now():%Y%m%d-%H%M%S}-{path.name}")
        db = _db_path(settings)
        return backup_sqlite(db, settings.data_dir / "backups") if db and db.exists() else imports

    with sessions() as session:
        crm = CRM(session, settings.data_dir, settings.timezone)
        report = import_workbook(crm, path, prefer_excel=prefer_excel, dry_run=dry_run, backup=backup)
        if dry_run:
            session.rollback()
        else:
            session.commit()
    local = workbook_path(settings)
    if not dry_run and report.changed and not report.conflicts and not report.errors:
        # Refresh the local mirror, unless it is another file holding edits of its own.
        if path.resolve() == local.resolve() or local_edits_pending(settings, sessions) is None:
            export(settings, sessions)
    log.info("crm workbook imported", extra={"summary": report.summary()})
    return report


def local_edits_pending(settings: Settings, sessions: sessionmaker[Session]) -> ImportReport | None:
    """Dry-run of the local workbook: tells whether regenerating it would discard your edits."""
    path = workbook_path(settings)
    if not path.exists():
        return None
    try:
        report = import_file(settings, sessions, path, dry_run=True)
    except WorkbookError:
        return None
    return report if (report.applied or report.conflicts or report.errors) else None


def nightly_export(settings: Settings, sessions: sessionmaker[Session]) -> str | None:
    """Refresh the workbook after the night run, unless it holds edits not imported yet. Returns a warning."""
    pending = local_edits_pending(settings, sessions)
    if pending:
        return "Le classeur Excel contient des modifications non synchronisées : lance /sync (il n'a pas été régénéré)."
    try:
        export(settings, sessions)
    except OSError as exc:
        log.warning("crm export failed", extra={"error": str(exc)})
        return f"Export Excel impossible : {exc}"
    return None
