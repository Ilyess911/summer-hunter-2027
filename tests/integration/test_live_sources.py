"""Live checks against real public endpoints. Run explicitly: uv run pytest -m live"""

from __future__ import annotations

import asyncio

import pytest

from summer_hunter.collectors.ashby import AshbyCollector
from summer_hunter.collectors.base import CollectContext
from summer_hunter.collectors.greenhouse import GreenhouseCollector
from summer_hunter.collectors.lever import LeverCollector
from summer_hunter.collectors.workday import WorkdayCollector
from summer_hunter.integrations.advisories import fetch_advisory
from summer_hunter.utils.http import PoliteClient

pytestmark = pytest.mark.live


def run(collector, title_rules):
    async def go():
        async with PoliteClient("SummerHunter2027-tests/1.0", official_api_hosts={"boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com"}) as http:
            return await collector.collect(CollectContext(http=http, title_rules=title_rules, max_detail_fetches=3))
    return asyncio.run(go())


@pytest.mark.parametrize("collector", [
    GreenhouseCollector("Stripe", "stripe"),
    LeverCollector("Palantir", "palantir"),
    AshbyCollector("OpenAI", "openai"),
    WorkdayCollector("Salesforce", "salesforce", "wd12", "Futureforce_Internships"),
])
def test_live_collector(collector, title_rules):
    result = run(collector, title_rules)
    assert result.status in {"ok", "partial"}, result.errors
    assert result.scanned > 0
    for rec in result.records:
        assert rec.url.startswith("https://") and rec.title and rec.company


def test_live_advisory():
    async def go():
        async with PoliteClient("SummerHunter2027-tests/1.0") as http:
            return await fetch_advisory(http, "SG")
    level, summary, url = asyncio.run(go())
    assert level in {"NO_FORMAL_WARNING_FOUND", "PARTIAL_ZONES_ADVISED_AGAINST"}
    assert "diplomatie.gouv.fr" in url
