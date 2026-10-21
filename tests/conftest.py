from __future__ import annotations

from pathlib import Path

import pytest

from summer_hunter.database.migrations import init_db, make_engine
from summer_hunter.database.repository import Repository
from summer_hunter.engine import eligibility as elig
from summer_hunter.engine.extraction import extract_facts
from summer_hunter.engine.geo import resolve_location
from summer_hunter.engine.normalization import TitleRules
from summer_hunter.integrations.anthropic_client import NullProvider
from summer_hunter.models.candidate import CandidateProfile
from summer_hunter.utils.settings import PROJECT_ROOT, Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(root=PROJECT_ROOT, config_dir=PROJECT_ROOT / "config", data_dir=tmp_path,
                    database_url=f"sqlite:///{tmp_path / 'test.db'}", telegram_bot_token=None, telegram_chat_id=None,
                    anthropic_api_key=None)


@pytest.fixture
def sessions(settings: Settings):
    return init_db(make_engine(settings.database_url))


@pytest.fixture
def repo(sessions):
    with sessions() as session:
        yield Repository(session)


@pytest.fixture
def profile(settings: Settings) -> CandidateProfile:
    return CandidateProfile.from_yaml(settings.profile_raw)


@pytest.fixture
def title_rules(settings: Settings) -> TitleRules:
    return TitleRules(settings.preferences, settings.scoring)


@pytest.fixture
def elig_ctx(profile: CandidateProfile) -> elig.EligibilityContext:
    return elig.EligibilityContext(profile=profile, excluded_countries={"FR", "IL"}, safety_excluded=set(),
                                   override_allow=set(), advisories={})


@pytest.fixture
def evaluate(elig_ctx):
    def _run(title: str, description: str, location: str, country_hint: str | None = None, ctx=None):
        facts = extract_facts(title, description)
        loc = resolve_location(location, country_hint)
        return elig.evaluate(facts, loc, ctx or elig_ctx, "https://example.test/job"), facts
    return _run


@pytest.fixture
def hunter(settings, sessions):
    from summer_hunter.pipeline import Hunter

    return Hunter(settings, sessions, provider=NullProvider())


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# A long neutral body so restriction-scan criteria have enough text to scan.
FILLER = (
    "You will work with enterprise customers and cross-functional teams across product, engineering and sales. "
    "You will prepare technical demonstrations, support discovery calls, analyse customer requirements and help "
    "shape solution design and positioning for our cloud AI platform. You will present your project to executives "
    "at the end of the program and receive mentorship from senior solutions engineers. "
)
