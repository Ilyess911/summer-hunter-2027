"""Schema creation, versioning and backups."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from summer_hunter.database.models import SCHEMA_VERSION, Base, KeyValue


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    return engine


def init_db(engine: Engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        row = session.get(KeyValue, "schema_version")
        if row is None:
            session.add(KeyValue(key="schema_version", value=str(SCHEMA_VERSION)))
        elif int(row.value) > SCHEMA_VERSION:
            raise RuntimeError(f"Database schema v{row.value} is newer than this code (v{SCHEMA_VERSION}).")
        session.commit()
    return factory


def backup_sqlite(db_path: Path, backup_dir: Path, keep: int = 14) -> Path:
    """Online-safe copy through the SQLite backup API, keeping the newest `keep` files."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"summer_hunter-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    backups = sorted(backup_dir.glob("summer_hunter-*.db"))
    for old in backups[:-keep]:
        old.unlink()
    return target


def schema_version(session: Session) -> int:
    row = session.execute(select(KeyValue).where(KeyValue.key == "schema_version")).scalar_one_or_none()
    return int(row.value) if row else 0
