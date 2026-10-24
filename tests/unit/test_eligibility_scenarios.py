"""Required scenarios A, B, C, E, F, J plus eligibility edge cases."""

from __future__ import annotations

from summer_hunter.engine import eligibility as elig
from summer_hunter.models.eligibility import CriterionStatus as S, Decision, SponsorshipStatus
from tests.conftest import FILLER


def crit(report, name):
    return report.by_name(name)


# Scenario A — high fit, wrong language -------------------------------------------------------
def test_scenario_a_mandatory_thai_is_rejected(evaluate):
    desc = FILLER + "Requirements: Fluent Thai is required for daily communication with local clients. Excellent English."
    report, _ = evaluate("Technical Sales Intern (May-August 2027)", desc, "Bangkok, Thailand")
    assert crit(report, "language").status == S.FAIL
    assert "Thai" in crit(report, "language").explanation
    assert report.decision == Decision.REJECTED


def test_optional_language_is_not_a_rejection(evaluate):
    desc = FILLER + "Japanese language skills are a plus but not required."
    report, _ = evaluate("Solutions Engineer Intern", desc, "Tokyo, Japan")
    assert crit(report, "language").status == S.PASS
    assert report.decision != Decision.REJECTED


def test_language_used_as_market_adjective_is_ignored(evaluate):
    desc = FILLER + "You will analyse the German market and support German customers in English."
    report, _ = evaluate("Business Development Intern", desc, "Dublin, Ireland")
    assert crit(report, "language").status == S.PASS


# Scenario B — incompatible dates ------------------------------------------------------------
def test_scenario_b_january_to_june_is_rejected(evaluate):
    desc = FILLER + "This internship runs from January to June 2027 (full time)."
    report, _ = evaluate("AI Product Management Intern", desc, "London, United Kingdom")
    assert crit(report, "dates").status == S.FAIL
    assert report.decision == Decision.REJECTED


def test_scenario_b_flexible_dates_need_verification_instead(evaluate):
    desc = FILLER + "This internship runs from January to June 2027. Start dates are flexible for the right candidate."
    report, _ = evaluate("AI Product Management Intern", desc, "London, United Kingdom")
    assert crit(report, "dates").status == S.UNKNOWN
    assert report.decision == Decision.NEEDS_VERIFICATION


def test_may_to_august_passes_dates_and_duration(evaluate):
    desc = FILLER + "The internship runs from May 17 to August 13, 2027 (12 weeks)."
    report, _ = evaluate("Product Marketing Intern", desc, "Singapore")
    assert crit(report, "dates").status == S.PASS
    assert crit(report, "duration").status == S.PASS


def test_six_month_internship_cannot_fit(evaluate):
    desc = FILLER + "This is a 6-month internship starting in March 2027."
    report, _ = evaluate("Business Development Intern", desc, "Amsterdam, Netherlands")
    assert crit(report, "dates").status == S.FAIL or crit(report, "duration").status == S.FAIL
    assert report.decision == Decision.REJECTED


def test_past_cycle_is_rejected(evaluate):
    report, _ = evaluate("Summer 2026 Intern - Sales", FILLER, "New York, NY")
    assert crit(report, "dates").status == S.FAIL


def test_southern_hemisphere_summer_needs_verification(evaluate):
    report, _ = evaluate("Summer 2027 Solutions Intern", FILLER, "Sydney, Australia")
    assert crit(report, "dates").status == S.UNKNOWN
    assert "décembre" in crit(report, "dates").explanation


# Scenario C — visa unknown --------------------------------------------------------------------
def test_scenario_c_singapore_visa_unspecified_needs_verification(evaluate):
    desc = FILLER + "The internship runs from June to August 2027."
    report, _ = evaluate("Product Marketing Intern", desc, "Singapore")
    wa = crit(report, "work_authorization")
    assert wa.status == S.UNKNOWN
    assert report.sponsorship == SponsorshipStatus.NOT_SPECIFIED
    assert report.decision == Decision.NEEDS_VERIFICATION


def test_sponsorship_statuses_are_distinguished(evaluate):
    cases = {
        "Visa sponsorship is available for this role.": SponsorshipStatus.AVAILABLE,
        "We are unable to provide visa sponsorship for this position.": SponsorshipStatus.UNAVAILABLE,
        "Candidates must have the right to work in the UK.": SponsorshipStatus.EXISTING_AUTH_REQUIRED,
        "Please indicate your work authorization status in the form.": SponsorshipStatus.UNCLEAR,
        "": SponsorshipStatus.NOT_SPECIFIED,
    }
    for sentence, expected in cases.items():
        report, _ = evaluate("Sales Engineer Intern", FILLER + sentence, "London, UK")
        assert report.sponsorship == expected, sentence


def test_sponsorship_available_passes_work_authorization(evaluate):
    report, _ = evaluate("Sales Engineer Intern", FILLER + "Visa sponsorship is available.", "Toronto, ON, Canada")
    assert crit(report, "work_authorization").status == S.PASS


def test_no_sponsorship_and_profile_says_no_authorization_rejects(evaluate, elig_ctx, profile):
    p = profile.model_copy(update={"work_authorization": {"GB": False}, "requires_sponsorship": True})
    ctx = elig.EligibilityContext(profile=p, excluded_countries={"FR", "IL"}, safety_excluded=set(), override_allow=set(), advisories={})
    report, _ = evaluate("Sales Engineer Intern", FILLER + "We do not offer visa sponsorship.", "London, UK", ctx=ctx)
    assert crit(report, "work_authorization").status == S.FAIL


# Scenario E — graduation mismatch --------------------------------------------------------------
def test_scenario_e_graduation_window_with_unknown_month(evaluate):
    desc = FILLER + "Candidates must be graduating between December 2027 and June 2028."
    report, _ = evaluate("Associate Product Marketing Manager Intern", desc, "Dublin, Ireland")
    g = crit(report, "graduation")
    assert g.status == S.UNKNOWN
    assert "INCONNU" in g.explanation
    assert report.decision == Decision.NEEDS_VERIFICATION


def test_graduation_window_resolves_once_month_is_known(evaluate, profile):
    desc = FILLER + "Candidates must be graduating between December 2027 and June 2028."
    for month, expected in ((12, S.PASS), (9, S.FAIL)):
        p = profile.model_copy(update={"graduation_month": month})
        ctx = elig.EligibilityContext(profile=p, excluded_countries={"FR", "IL"}, safety_excluded=set(), override_allow=set(), advisories={})
        report, _ = evaluate("APMM Intern", desc, "Dublin, Ireland", ctx=ctx)
        assert crit(report, "graduation").status == expected


def test_graduation_slash_format_is_read(evaluate):
    desc = FILLER + "Bachelor's degree with graduation date between december/2027 and july/2028."
    report, _ = evaluate("Sales Strategy Intern", desc, "Mexico City, Mexico")
    assert crit(report, "graduation").status == S.UNKNOWN


def test_graduating_in_2028_only_rejects(evaluate):
    report, _ = evaluate("Sales Intern", FILLER + "Must be planning on graduating in 2028.", "Seattle, WA")
    assert crit(report, "graduation").status == S.FAIL


def test_penultimate_year_needs_verification_when_profile_unknown(evaluate):
    report, _ = evaluate("Strategy Intern", FILLER + "You must be returning to university after the internship.", "Zurich, Switzerland")
    assert crit(report, "graduation").status == S.UNKNOWN


# Scenario F — excluded countries -----------------------------------------------------------------
def test_scenario_f_france_and_israel_rejected(evaluate):
    for location in ("Paris, France", "Tel Aviv, Israel", "Remote - France"):
        report, _ = evaluate("Solutions Engineer Intern", FILLER, location)
        assert crit(report, "country").status == S.FAIL, location
        assert report.decision == Decision.REJECTED
        assert report.hard_exclusion


def test_multi_location_with_allowed_alternative_passes(evaluate):
    report, _ = evaluate("Solutions Engineer Intern", FILLER, "Paris, France; London, United Kingdom")
    assert crit(report, "country").status == S.PASS
    assert "France" in crit(report, "country").explanation


def test_user_safety_exclusion_is_applied(evaluate, profile):
    ctx = elig.EligibilityContext(profile=profile, excluded_countries={"FR", "IL"}, safety_excluded={"XX", "EG"},
                                  override_allow=set(), advisories={})
    report, _ = evaluate("Sales Intern", FILLER, "Cairo, Egypt", ctx=ctx)
    assert crit(report, "safety").status == S.FAIL
    assert report.decision == Decision.REJECTED


def test_advisory_partial_zones_is_not_an_exclusion(evaluate, profile):
    adv = {"TH": {"level": "PARTIAL_ZONES_ADVISED_AGAINST", "source_url": "https://www.diplomatie.gouv.fr/x", "retrieved_at": "2026-10-21"}}
    ctx = elig.EligibilityContext(profile=profile, excluded_countries={"FR", "IL"}, safety_excluded=set(), override_allow=set(), advisories=adv)
    report, _ = evaluate("Sales Intern", FILLER, "Bangkok, Thailand", ctx=ctx)
    s = crit(report, "safety")
    assert s.status == S.UNKNOWN and "diplomatie.gouv.fr" in s.explanation


# Scenario J — unknown data stays unknown ----------------------------------------------------------
def test_scenario_j_unknown_fields_are_never_fabricated(evaluate):
    report, facts = evaluate("Business Development Intern", FILLER, "Singapore")
    assert crit(report, "dates").status == S.UNKNOWN
    assert crit(report, "work_authorization").status == S.UNKNOWN
    assert facts.periods == [] and facts.duration is None
    assert report.decision == Decision.NEEDS_VERIFICATION
    # Nothing unknown is reported as a pass.
    for name in ("dates", "duration", "work_authorization"):
        assert crit(report, name).status != S.PASS


def test_never_eligible_merely_because_nothing_found(evaluate):
    report, _ = evaluate("Sales Engineer Intern", "", "London, UK")
    assert report.decision != Decision.ELIGIBLE


def test_fully_specified_listing_can_be_eligible(evaluate, profile):
    p = profile.model_copy(update={"masters_enrolled": True})
    ctx = elig.EligibilityContext(profile=p, excluded_countries={"FR", "IL"}, safety_excluded=set(), override_allow=set(), advisories={})
    desc = FILLER + ("The internship runs from June 1 to August 21, 2027. Visa sponsorship is available. "
                     "Paid internship with a monthly stipend.")
    report, _ = evaluate("Solutions Engineer Intern", desc, "Singapore", ctx=ctx)
    assert report.decision == Decision.ELIGIBLE, [(c.criterion, c.status) for c in report.criteria]


def test_permanent_and_apprenticeship_are_rejected(evaluate):
    for title in ("Sales Apprentice", "Working Student Sales (Werkstudent)"):
        report, _ = evaluate(title, FILLER, "Munich, Germany")
        assert crit(report, "internship_status").status == S.FAIL


def test_unpaid_is_rejected_unless_allowed(evaluate):
    report, _ = evaluate("Marketing Intern", FILLER + "This is an unpaid internship.", "Berlin, Germany")
    assert crit(report, "compensation").status == S.FAIL
    assert report.decision == Decision.REJECTED


def test_phd_only_and_clearance(evaluate):
    report, _ = evaluate("Research Intern", FILLER + "Applicants must be currently enrolled in a PhD program.", "Boston, MA")
    assert crit(report, "academic").status == S.FAIL
    report, _ = evaluate("Deployment Strategist Intern", FILLER + "Requires an active US security clearance.", "Washington, D.C.")
    assert crit(report, "citizenship").status == S.UNKNOWN   # citizenship UNKNOWN in profile -> never guessed


def test_mandatory_professional_license_rejects(evaluate):
    report, _ = evaluate("Wealth Sales Intern", FILLER + "A valid Series 7 license is required.", "New York, NY")
    assert crit(report, "qualifications").status == S.FAIL
