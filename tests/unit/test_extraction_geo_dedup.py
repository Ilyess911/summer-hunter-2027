"""Extraction details, geography, deduplication (scenario H at unit level), scheduler window, alerts parsing."""

from __future__ import annotations

from datetime import datetime, timedelta
from email import message_from_string
from email.policy import default as default_policy
from zoneinfo import ZoneInfo

import pytest

from summer_hunter.collectors.career_pages import parse_jobpostings
from summer_hunter.collectors.linkedin_alerts import parse_alert_text, parse_message
from summer_hunter.engine.deduplication import Candidate, find_match, make_key
from summer_hunter.engine.extraction import extract_facts
from summer_hunter.engine.geo import resolve_location
from summer_hunter.integrations.advisories import classify_advisory_html
from summer_hunter.scheduler.nightly_runner import AlreadyRunning, Window, single_instance
from tests.conftest import fixture_text


@pytest.mark.parametrize("text,expected", [
    ("New York, NY", ["US"]), ("California - Palo Alto", ["US"]), ("Toronto, ON, Canada", ["CA"]),
    ("London, ON", ["CA"]), ("Dublin, CA", ["US"]), ("Singapore", ["SG"]), ("Paris, France; Dublin, Ireland", ["FR", "IE"]),
    ("Remote - France", ["FR"]), ("Zürich, Switzerland", ["CH"]), ("Hong Kong SAR", ["HK"]), ("Dubai", ["AE"]),
])
def test_resolve_location(text, expected):
    assert resolve_location(text).countries == expected


def test_remote_without_country_is_unknown():
    loc = resolve_location("Remote")
    assert loc.remote and loc.countries == []


def test_duration_ignores_experience_mentions():
    facts = extract_facts("Sales Intern", "Requires 6 months of experience in sales. The internship lasts 10-12 weeks.")
    assert facts.duration.min_weeks == 10 and facts.duration.max_weeks == 12


def test_french_dates_and_duration():
    facts = extract_facts("Stage Business Developer", "Stage de 4 mois, d'avril à juillet 2027, basé à Genève.")
    assert facts.periods[0].start.month == 4 and facts.periods[0].end.month == 7
    assert round(facts.duration.min_weeks) == 17


def test_cold_call_count_variants():
    assert extract_facts("SDR Intern", "Make 50 outbound calls a day.").cold_calls_per_day == 50
    assert extract_facts("SDR Intern", "Daily targets include 60 dials.").cold_calls_per_day == 60


# Scenario H (unit) -------------------------------------------------------------------------------------------
def test_dedup_matches_linkedin_and_career_site_titles():
    ats = make_key("Stripe, Inc.", "Product Marketing Intern - Summer 2027", "IE")
    li = make_key("Stripe", "Product Marketing Internship (Summer 2027)", "IE")
    assert ats.company == li.company and ats.title == li.title
    cands = [Candidate(7, ats.company, ats.title, "IE")]
    assert find_match(li, None, cands) == 7


def test_dedup_does_not_merge_different_roles_or_countries():
    a = make_key("Stripe", "Product Marketing Intern", "IE")
    cands = [Candidate(1, a.company, a.title, "IE")]
    assert find_match(make_key("Stripe", "Software Engineer Intern", "IE"), None, cands) is None
    assert find_match(make_key("Stripe", "Product Marketing Intern", "SG"), None, cands) is None


def test_dedup_prefers_official_job_id():
    cands = [Candidate(3, "acme", "solutions engineer", "US", "JR123")]
    assert find_match(make_key("Acme", "SE Intern (renamed)", "GB"), "JR123", cands) == 3


# Scheduler -------------------------------------------------------------------------------------------------
def test_window_and_dst():
    w = Window(ZoneInfo("Europe/Paris"), datetime.strptime("00:00", "%H:%M").time(), datetime.strptime("06:00", "%H:%M").time())
    tz = ZoneInfo("Europe/Paris")
    assert w.contains(datetime(2026, 10, 22, 3, 0, tzinfo=tz))
    assert not w.contains(datetime(2026, 10, 22, 7, 0, tzinfo=tz))
    # DST end (25 Oct 2026, 03:00 -> 02:00): the window lasts 7 real hours, 01:30 UTC is inside it.
    start, end = w.bounds(datetime(2026, 10, 25).date())
    assert (end.astimezone(ZoneInfo("UTC")) - start.astimezone(ZoneInfo("UTC"))) == timedelta(hours=7)
    assert w.contains(datetime(2026, 10, 25, 1, 30, tzinfo=ZoneInfo("UTC")))
    # DST start (28 Mar 2027, 02:00 -> 03:00): 5 real hours.
    start, end = w.bounds(datetime(2027, 3, 28).date())
    assert (end.astimezone(ZoneInfo("UTC")) - start.astimezone(ZoneInfo("UTC"))) == timedelta(hours=5)
    # Times given in another zone are converted, not compared naively.
    assert w.contains(datetime(2026, 7, 1, 23, 30, tzinfo=ZoneInfo("UTC")))   # 01:30 Paris (CEST)


def test_single_instance_lock(tmp_path):
    lock = tmp_path / "x.lock"
    with single_instance(lock):
        with pytest.raises(AlreadyRunning):
            with single_instance(lock):
                pass
    with single_instance(lock):
        pass


# Sources parsing --------------------------------------------------------------------------------------------
def test_linkedin_alert_email_parsing():
    msg = message_from_string(fixture_text("linkedin_alert.eml"), policy=default_policy)
    jobs, received = parse_message(msg)
    titles = {j["title"]: j for j in jobs}
    assert "Product Marketing Intern - Summer 2027" in titles
    assert titles["Product Marketing Intern - Summer 2027"]["company"] == "Stripe"
    assert titles["Product Marketing Intern - Summer 2027"]["location"] == "Dublin, County Dublin, Ireland"
    assert received is not None


def test_linkedin_alert_plain_text_fallback():
    text = "Solutions Engineer Intern\nAcme AI\nSingapore\nView job: https://www.linkedin.com/comm/jobs/view/4012345678/?x=1\n"
    jobs = parse_alert_text(text)
    assert jobs == [{"job_id": "4012345678", "title": "Solutions Engineer Intern", "company": "Acme AI", "location": "Singapore"}]


def test_jsonld_jobposting():
    html = fixture_text("career_page_jsonld.html")
    postings = parse_jobpostings(html)
    assert postings and postings[0]["title"] == "Sales Engineering Intern (Summer 2027)"


def test_advisory_classification():
    assert classify_advisory_html("<p>Il est formellement déconseillé de se rendre dans la province X.</p>")[0] == "PARTIAL_ZONES_ADVISED_AGAINST"
    assert classify_advisory_html("<p>Vigilance normale.</p>")[0] == "NO_FORMAL_WARNING_FOUND"


def test_unquoted_norway_is_reported(tmp_path, settings):
    import shutil

    from summer_hunter.utils.settings import Settings

    cfg = tmp_path / "cfg"
    shutil.copytree(settings.config_dir, cfg)
    p = cfg / "search_preferences.yaml"
    p.write_text(p.read_text().replace("'NO'", "NO"))
    with pytest.raises(ValueError, match="Norway"):
        _ = Settings(config_dir=cfg, data_dir=tmp_path).preferences
