"""End-to-end cycle against mocked public APIs (no network)."""

from __future__ import annotations

import asyncio
import shutil

import httpx
import pytest
from sqlalchemy import func, select

from summer_hunter.database.models import CollectionRun, Opportunity, SourceRecord
from summer_hunter.database.repository import Repository
from summer_hunter.integrations.anthropic_client import FakeProvider, NullProvider
from summer_hunter.pipeline import Hunter
from tests.conftest import FILLER, FIXTURES

GH_JOBS = {"jobs": [
    {"id": 101, "title": "Product Marketing Intern - Summer 2027", "updated_at": "2026-10-20T10:00:00Z", "location": {"name": "Dublin, Ireland"}},
    {"id": 102, "title": "Software Engineer, Intern", "updated_at": "2026-10-20T10:00:00Z", "location": {"name": "Dublin, Ireland"}},
    {"id": 103, "title": "Account Executive", "updated_at": "2026-10-20T10:00:00Z", "location": {"name": "London"}},
    {"id": 104, "title": "Solutions Engineer Intern", "updated_at": "2026-10-20T10:00:00Z", "location": {"name": "Paris, France"}},
]}


def gh_detail(job_id: int, title: str, location: str, body: str) -> dict:
    return {"id": job_id, "title": title, "absolute_url": f"https://stripe.com/jobs/search?gh_jid={job_id}",
            "location": {"name": location}, "content": body.replace("<", "&lt;").replace(">", "&gt;"),
            "updated_at": "2026-10-20T10:00:00Z", "first_published": "2026-10-01T00:00:00Z", "company_name": "Stripe"}


PMM_BODY = "<p>" + FILLER + "The internship runs from June 7 to August 27, 2027 in our Dublin office.</p>"
LEVER = [{"id": "aaa-1", "text": "Business Development Intern, Summer 2027", "hostedUrl": "https://jobs.lever.co/acme/aaa-1",
          "applyUrl": "https://jobs.lever.co/acme/aaa-1/apply", "categories": {"location": "Singapore", "commitment": "Intern"},
          "country": "SG", "descriptionPlain": FILLER + " You will make 45 cold calls per day.", "lists": [], "additionalPlain": "",
          "createdAt": 1790000000000}]


class Api:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.ashby_status = 500

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            if "careers.blocked.test" in url:
                return httpx.Response(200, text="User-agent: *\nDisallow: /")
            return httpx.Response(404)
        if url == "https://boards-api.greenhouse.io/v1/boards/stripe/jobs":
            return httpx.Response(200, json=GH_JOBS)
        if url.startswith("https://boards-api.greenhouse.io/v1/boards/stripe/jobs/"):
            job_id = int(url.rsplit("/", 1)[-1])
            job = next(j for j in GH_JOBS["jobs"] if j["id"] == job_id)
            body = PMM_BODY if job_id == 101 else "<p>" + FILLER + "</p>"
            return httpx.Response(200, json=gh_detail(job_id, job["title"], job["location"]["name"], body))
        if url.startswith("https://api.lever.co/v0/postings/acme"):
            return httpx.Response(200, json=LEVER)
        if url.startswith("https://api.ashbyhq.com/posting-api/job-board/broken"):
            return httpx.Response(self.ashby_status, json={"error": "boom"})
        if url.startswith("https://careers.blocked.test"):
            raise AssertionError("robots.txt must prevent this request")
        return httpx.Response(404)


@pytest.fixture
def wired(settings, sessions, tmp_path):
    alerts = tmp_path / "alerts"
    alerts.mkdir()
    shutil.copy(FIXTURES / "linkedin_alert.eml", alerts / "a.eml")
    settings.linkedin_alerts_dir = alerts
    settings.__dict__["companies"] = [
        {"name": "Stripe", "ats": "greenhouse", "token": "stripe", "priority": 1, "sector": "fintech"},
        {"name": "Acme", "ats": "lever", "token": "acme", "priority": 2},
        {"name": "Broken Co", "ats": "ashby", "token": "broken"},
        {"name": "Blocked Inc", "ats": "career_page", "urls": ["https://careers.blocked.test/jobs"]},
    ]
    api = Api()
    hunter = Hunter(settings, sessions, provider=NullProvider(), transport=httpx.MockTransport(api))
    hunter.http_min_interval, hunter.http_retries = 0.0, 0   # retry/backoff covered in test_http.py
    return hunter, api, sessions


def opp_by_title(session, fragment):
    return session.execute(select(Opportunity).where(Opportunity.title.like(f"%{fragment}%"))).scalars().all()


def test_full_cycle_dedup_failures_and_eligibility(wired):
    hunter, api, sessions = wired
    stats = asyncio.run(hunter.run_cycle())

    # Error isolation: the broken source fails, the blocked page is skipped by robots.txt, the rest work.
    assert stats.sources["ashby:broken"]["status"] == "failed"
    assert any("robots" in e for e in stats.sources["career_page:blocked_inc"]["errors"])
    assert stats.sources["greenhouse:stripe"]["status"] == "ok"
    assert stats.sources["linkedin_alerts"]["status"] == "ok"
    # Only internship titles in target families were fetched in detail.
    assert not any(u.endswith("/jobs/103") or u.endswith("/jobs/102") for u in api.calls)

    with sessions() as session:
        repo = Repository(session)
        # Scenario H: LinkedIn alert + Greenhouse = one opportunity with two source records.
        pmm = opp_by_title(session, "Product Marketing Intern")
        assert len(pmm) == 1
        sources = {s.source for s in pmm[0].sources}
        assert sources == {"greenhouse:stripe", "linkedin_alerts"}
        assert pmm[0].official_url.startswith("https://stripe.com/jobs")
        assert pmm[0].country_code == "IE"
        assert pmm[0].eligibility_status == "NEEDS_VERIFICATION"   # visa unspecified, degree equivalence unknown
        ev = repo.latest_eligibility(pmm[0].id)
        assert {c["criterion"]: c["status"] for c in ev.criteria}["dates"] == "PASS"

        # Scenario F end to end.
        paris = opp_by_title(session, "Solutions Engineer Intern")
        assert [o for o in paris if o.country_code == "FR"][0].eligibility_status == "REJECTED"

        # Scenario D end to end: cold calling penalised.
        bd = opp_by_title(session, "Business Development Intern")[0]
        fit = repo.latest_fit(bd.id).report
        assert fit["cold_calls_per_day"] == 45

        # Source health is recorded per source.
        health = {h.source: h for h in repo.health()}
        assert health["ashby:broken"].consecutive_failures == 1
        assert health["greenhouse:stripe"].consecutive_failures == 0

        # Alert-only listing (no description) stays NEEDS_VERIFICATION, never ELIGIBLE.
        acme = [o for o in opp_by_title(session, "Solutions Engineer Intern") if o.country_code == "SG"][0]
        assert acme.eligibility_status == "NEEDS_VERIFICATION"


def test_restart_is_incremental_and_idempotent(wired):
    hunter, api, sessions = wired
    asyncio.run(hunter.run_cycle())
    with sessions() as session:
        n_opps = session.scalar(select(func.count()).select_from(Opportunity))
        n_src = session.scalar(select(func.count()).select_from(SourceRecord))
    api.calls.clear()
    stats = asyncio.run(hunter.run_cycle())
    assert stats.new_opportunities == 0
    assert stats.sources["greenhouse:stripe"]["unchanged"] >= 2
    assert stats.sources["greenhouse:stripe"]["records"] == 0   # unchanged detail not re-ingested
    assert stats.evaluated == 0                               # unchanged content not re-evaluated
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Opportunity)) == n_opps
        assert session.scalar(select(func.count()).select_from(SourceRecord)) == n_src
        runs = session.execute(select(CollectionRun)).scalars().all()
        assert [r.status for r in runs] == ["partial", "partial"]


def test_dry_run_persists_nothing(wired):
    hunter, _, sessions = wired
    stats = asyncio.run(hunter.run_cycle(dry_run=True))
    assert stats.new_opportunities > 0
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Opportunity)) == 0
        run = session.execute(select(CollectionRun)).scalar_one()
        assert run.dry_run and run.status == "partial"


def test_llm_pass_runs_only_on_shortlist_and_is_cached(wired, settings):
    hunter, _, sessions = wired
    payload = {
        "internship_start": "2027-06-07", "internship_end": "2027-08-27",
        "dates_evidence_quote": "The internship runs from June 7 to August 27, 2027", "duration_weeks": 12,
        "requirements": [], "responsibilities_summary": "", "scores": {k: 90 for k in settings.scoring["weights"]},
        "top_reasons": ["Product marketing for payments"], "weaknesses": ["Visa unclear"],
        "relevant_experience_ids": ["medicalia"], "missing_skills": [], "career_value": "Strong GTM path",
        "cold_calls_per_day": None, "injection_attempt_detected": False,
    }
    provider = FakeProvider(payload)
    hunter.provider = provider
    settings.__dict__["llm"] = settings.llm | {"daily_budget_usd": 1.0}
    asyncio.run(hunter.run_cycle())
    analysed = len(provider.calls)
    assert analysed >= 1
    with sessions() as session:
        pmm = opp_by_title(session, "Product Marketing Intern")[0]
        fit = Repository(session).latest_fit(pmm.id)
        assert fit.method == "deterministic+llm"
        assert opp_by_title(session, "Software Engineer") == []   # off-target family: never fetched or analysed
    # Force re-evaluation: cache hit, no new call.
    from summer_hunter.pipeline import CycleStats

    with sessions() as session:
        hunter.evaluate_pending(Repository(session), CycleStats(), force=True)
        session.commit()
    assert len(provider.calls) == analysed


def test_api_failure_handling_for_all_sources(settings, sessions):
    settings.__dict__["companies"] = [{"name": "Stripe", "ats": "greenhouse", "token": "stripe"}]

    def down(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("unreachable")

    hunter = Hunter(settings, sessions, provider=NullProvider(), transport=httpx.MockTransport(down))
    hunter.http_retries, hunter.http_min_interval = 0, 0.0
    stats = asyncio.run(hunter.run_cycle())
    assert stats.sources["greenhouse:stripe"]["status"] == "failed"
    with sessions() as session:
        run = session.execute(select(CollectionRun)).scalar_one()
        assert run.status == "partial" and run.finished_at is not None


def test_vanished_offers_are_closed_and_reopened(wired):
    hunter, api, sessions = wired
    asyncio.run(hunter.run_cycle())
    with sessions() as session:
        pmm = opp_by_title(session, "Product Marketing Intern")[0]
        assert pmm.status == "active"
    removed = GH_JOBS["jobs"].pop(0)                      # the PMM listing disappears from Greenhouse
    try:
        stats = asyncio.run(hunter.run_cycle())
        with sessions() as session:
            pmm = opp_by_title(session, "Product Marketing Intern")[0]
            assert pmm.status == "closed" and stats.closed >= 1
            assert pmm.id not in [o.id for o in Repository(session).top(50)]
    finally:
        GH_JOBS["jobs"].insert(0, removed)
    asyncio.run(hunter.run_cycle())                       # it comes back: reopened
    with sessions() as session:
        assert opp_by_title(session, "Product Marketing Intern")[0].status == "active"


def test_shortlisted_lever_offer_with_dead_page_is_closed(wired):
    hunter, api, sessions = wired
    asyncio.run(hunter.run_cycle())
    original = api.__call__

    def handler(request):
        if request.url.host == "jobs.lever.co":
            return httpx.Response(404)
        return original(request)

    hunter.transport = httpx.MockTransport(handler)
    with sessions() as session:   # make the BD offer part of the shortlist the user sees
        opp_by_title(session, "Business Development Intern")[0].notify = True
        session.commit()
    stats = asyncio.run(hunter.run_cycle())
    with sessions() as session:
        bd = opp_by_title(session, "Business Development Intern")[0]
        assert bd.status == "closed" and stats.closed >= 1
