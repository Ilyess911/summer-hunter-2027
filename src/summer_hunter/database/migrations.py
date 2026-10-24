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
    engine = create_engine(database_url, future=True, pool_pre_ping=not database_url.startswith("sqlite"))
    if not database_url.startswith("sqlite"):
        return engine                       # hosted Postgres: no SQLite pragmas

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    return engine


# Columns added after the first release: (table, column, SQL type). Applied idempotently.
ADDED_COLUMNS = [
    ("source_records", "closed_at", "DATETIME"),
    # 23 Oct 2026: industry & robotics track
    ("opportunities", "recruitability_score", "INTEGER"),
    ("opportunities", "industrial_priority", "FLOAT"),
    ("opportunities", "career_track", "VARCHAR(30)"),
    ("opportunities", "career_category", "VARCHAR(60)"),
    ("fit_evaluations", "recruitability", "JSON"),
    # 23 Oct 2026 (V3): compensation, global priority, strategist, deadlines
    ("opportunities", "semantic_family", "VARCHAR(60)"),
    ("opportunities", "compensation_status", "VARCHAR(30)"),
    ("opportunities", "salary_min", "FLOAT"),
    ("opportunities", "salary_max", "FLOAT"),
    ("opportunities", "salary_currency", "VARCHAR(3)"),
    ("opportunities", "salary_period", "VARCHAR(12)"),
    ("opportunities", "salary_basis", "VARCHAR(8)"),
    ("opportunities", "monthly_min_eur", "FLOAT"),
    ("opportunities", "monthly_max_eur", "FLOAT"),
    ("opportunities", "living_cost_eur", "FLOAT"),
    ("opportunities", "monthly_balance_eur", "FLOAT"),
    ("opportunities", "compensation_confidence", "VARCHAR(10)"),
    ("opportunities", "compensation_score", "INTEGER"),
    ("opportunities", "global_priority", "FLOAT"),
    ("opportunities", "global_provisional", "BOOLEAN"),
    ("opportunities", "next_action", "VARCHAR(30)"),
    ("opportunities", "strategy_category", "VARCHAR(12)"),
    ("opportunities", "application_deadline", "DATE"),
    ("opportunities", "deadline_type", "VARCHAR(12)"),
    ("opportunities", "reopened_at", "DATETIME"),
    ("fit_evaluations", "compensation", "JSON"),
    ("fit_evaluations", "strategy", "JSON"),
    ("recruiter_contacts", "verified_at", "DATETIME"),
    # 24 Oct 2026 (V4, Summer Hunter OS): networking
    ("crm_contacts", "category", "VARCHAR(30)"),
    ("crm_contacts", "verification", "VARCHAR(20)"),
    ("crm_contacts", "dismissed", "BOOLEAN"),
]


def _add_missing_columns(engine: Engine) -> None:
    from sqlalchemy import inspect

    pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        inspector = inspect(conn)
        for table, column, sqltype in ADDED_COLUMNS:
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                if pg:
                    sqltype = {"DATETIME": "TIMESTAMPTZ", "FLOAT": "DOUBLE PRECISION"}.get(sqltype, sqltype)
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}")


def init_db(engine: Engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        row = session.get(KeyValue, "schema_version")
        if row is None:
            session.add(KeyValue(key="schema_version", value=str(SCHEMA_VERSION)))
        elif int(row.value) > SCHEMA_VERSION:
            raise RuntimeError(f"Database schema v{row.value} is newer than this code (v{SCHEMA_VERSION}).")
        elif int(row.value) < SCHEMA_VERSION:
            row.value = str(SCHEMA_VERSION)
        from summer_hunter.crm.migrate import migrate_legacy_applications

        migrate_legacy_applications(session)   # v2: legacy "applied" rows become CRM applications (once)
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
