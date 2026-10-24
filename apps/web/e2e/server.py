"""Seeded API server for the Playwright suite: a throwaway database, a frozen test profile, no network."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import uvicorn

from summer_hunter.database.migrations import init_db, make_engine
from summer_hunter.database.repository import Repository
from summer_hunter.integrations.anthropic_client import NullProvider
from summer_hunter.models.opportunity import RawOpportunity
from summer_hunter.os import security
from summer_hunter.os.api import create_app
from summer_hunter.pipeline import CycleStats, Hunter
from summer_hunter.utils.settings import PROJECT_ROOT, Settings

PASSWORD = "e2e-password-123"
DATES = "The internship runs from April 6 to August 28, 2027. "
JOBS = [
    ("Robotics Deployment Intern", "Formic", "Munich, Germany",
     "Deploy robots, customer onboarding, KPI tracking, data analysis and excel reporting. Pay: €1,500 per month."),
    ("Maintenance Engineering Intern", "Royal Air Maroc", "Casablanca, Morocco",
     "Maintenance KPIs and data analysis. Gratification 6 000 MAD par mois."),
    ("Solutions Engineer Intern", "Stripe", "Singapore", "Technical demos, discovery calls with enterprise customers."),
]


def main(port: int) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="sh-e2e-"))
    config = tmp / "config"
    shutil.copytree(PROJECT_ROOT / "config", config)
    shutil.copy(PROJECT_ROOT / "tests/fixtures/candidate_profile_test.yaml", config / "candidate_profile.yaml")
    settings = Settings(root=PROJECT_ROOT, config_dir=config, data_dir=tmp, database_url=f"sqlite:///{tmp / 'e2e.db'}",
                        telegram_bot_token=None, telegram_chat_id=None, anthropic_api_key=None)
    os.environ.setdefault("SH_OS_SECRET", "e" * 48)
    os.environ["SH_OS_PASSWORD_HASH"] = security.hash_password(PASSWORD, n=2 ** 10)
    sessions = init_db(make_engine(settings.database_url))
    hunter = Hunter(settings, sessions, provider=NullProvider())
    stats = CycleStats()
    with sessions() as s:
        repo = Repository(s)
        for i, (title, company, loc, extra) in enumerate(JOBS):
            raw = RawOpportunity(source="greenhouse:e2e", source_id=str(i), url=f"https://jobs.example.com/{i}", title=title,
                                 company=company, location_text=loc, description_text=DATES + extra)
            hunter.evaluate(repo, hunter.ingest(repo, raw, stats), None, stats)
        s.commit()
    uvicorn.run(create_app(settings, sessions, hosted=False), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8820)
