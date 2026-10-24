"""ASGI entry point for the hosted (Vercel) deployment: Postgres, hosted mode, configuration checked at start.

The hosted app only serves the last snapshot pushed by the Mac and queues edits for it; discovery, document
generation and AI calls never run there.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from summer_hunter.database.migrations import init_db, make_engine
from summer_hunter.os.api import create_app
from summer_hunter.utils.settings import Settings

REQUIRED = ("DATABASE_URL", "SH_OS_PASSWORD_HASH", "SH_OS_SECRET", "SH_SYNC_TOKEN")


def database_url(raw: str) -> str:
    """Providers hand out postgres:// URLs; SQLAlchemy needs the psycopg 3 driver named explicitly."""
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix):]
    return raw


def build() -> FastAPI:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"hosted mode needs {', '.join(missing)}")
    if len(os.environ["SH_OS_SECRET"]) < 32 or len(os.environ["SH_SYNC_TOKEN"]) < 32:
        raise RuntimeError("SH_OS_SECRET and SH_SYNC_TOKEN must be at least 32 characters")
    os.environ["SH_OS_MODE"] = "hosted"
    os.environ.setdefault("SH_DATA_DIR", "/tmp/summer-hunter")  # serverless file systems are read-only except /tmp
    settings = Settings.load()
    settings.database_url = database_url(os.environ["DATABASE_URL"])
    return create_app(settings, init_db(make_engine(settings.database_url)), hosted=True)
