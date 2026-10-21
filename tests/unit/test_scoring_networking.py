"""Scenario D (cold calling), Scenario G (hiring-manager post), networking and priority rules."""

from __future__ import annotations

from summer_hunter.collectors.linkedin_public import build_post, opportunity_from_post
from summer_hunter.engine.extraction import extract_facts
from summer_hunter.engine.networking import classify_post
from summer_hunter.engine.scoring import ScoringContext, score_fit, score_networking, score_priority
from summer_hunter.models.eligibility import Decision
from summer_hunter.models.opportunity import PostClass, Relationship
from summer_hunter.models.scoring import Adjustment
from tests.conftest import FILLER


def fit_for(title, desc, settings, profile, title_rules, priority=None, learned=None):
    facts = extract_facts(title, desc, settings.scoring["cold_calling"]["high_volume_phrases"])
    ctx = ScoringContext(profile=profile, rules=settings.scoring, company_priority=priority, learned=learned or [])
    return score_fit(title, desc, facts, title_rules.classify(title), ctx)


# Scenario D --------------------------------------------------------------------------------------
def test_scenario_d_high_volume_cold_calling_gets_major_penalty(settings, profile, title_rules):
    base = FILLER + "You will qualify inbound leads for the enterprise team."
    calls = FILLER + "You will make 40+ cold calls per day to qualify leads for the enterprise team."
    clean = fit_for("Sales Intern", base, settings, profile, title_rules)
    heavy = fit_for("Sales Intern", calls, settings, profile, title_rules)
    assert heavy.cold_calls_per_day == 40
    assert clean.score - heavy.score >= 15
    assert any("cold calling" in w.lower() for w in heavy.weaknesses)
    assert any(a.label == "cold-calling penalty" for a in heavy.adjustments)


def test_business_development_without_calls_is_not_penalised(settings, profile, title_rules):
    report = fit_for("Business Development Intern", FILLER + "You will map strategic partnerships.", settings, profile, title_rules)
    assert not any(a.label == "cold-calling penalty" for a in report.adjustments)
    assert report.score >= 70


def test_target_family_outranks_generic_software_engineering(settings, profile, title_rules):
    se = fit_for("Solutions Engineer Intern", FILLER, settings, profile, title_rules, priority=1)
    swe = fit_for("Software Engineer Intern", FILLER + "Python, Java, distributed systems.", settings, profile, title_rules, priority=1)
    assert se.score >= 80 and se.score - swe.score >= 30
    assert swe.score <= 50
    assert any("capped" in a.label for a in swe.adjustments)


def test_explanations_are_present_and_bounded(settings, profile, title_rules):
    report = fit_for("Product Marketing Intern", FILLER, settings, profile, title_rules, priority=1)
    assert 1 <= len(report.reasons) <= 3
    assert len(report.weaknesses) <= 2
    assert set(report.relevant_experiences) <= {e.id for e in profile.experiences}
    assert report.career_value


def test_learned_preference_is_applied_and_visible(settings, profile, title_rules):
    plain = fit_for("Product Manager Intern", FILLER, settings, profile, title_rules)
    boosted = fit_for("Product Manager Intern", FILLER, settings, profile, title_rules,
                      learned=[Adjustment(label="learned preference: job_family=product_management", delta=6)])
    assert boosted.score == min(100, plain.score + 6)


# Networking & priority ------------------------------------------------------------------------------
def test_networking_score_table(settings):
    rules = settings.scoring
    assert score_networking([], rules).score == 20
    for rel, expected in (("hiring_manager_post", 100), ("recruiter_post", 95), ("team_member_post", 85),
                          ("named_recruiter_on_vacancy", 75)):
        assert score_networking([{"relationship": rel, "confidence": 0.9, "evidence": "x", "name": "A"}], rules).score == expected
    low = score_networking([{"relationship": "hiring_manager_post", "confidence": 0.4, "evidence": "x", "name": "A"}], rules)
    assert low.score == 85


def test_priority_formula_and_rejection(settings, profile, title_rules, evaluate):
    fit = fit_for("Solutions Engineer Intern", FILLER, settings, profile, title_rules, priority=1)
    net = score_networking([], settings.scoring)
    report, _ = evaluate("Solutions Engineer Intern", FILLER, "Singapore")
    pr = score_priority(fit, report, net, settings.scoring, 1)
    assert pr.priority_score == round(0.75 * fit.score + 0.25 * 20, 1)
    rejected, _ = evaluate("Solutions Engineer Intern", FILLER, "Paris, France")
    pr2 = score_priority(fit, rejected, net, settings.scoring, 1)
    assert pr2.priority_score is None and not pr2.notify and pr2.tier == "rejected"


def test_strategic_bypass_never_overrides_hard_exclusion(settings, profile, title_rules, evaluate):
    fit = fit_for("Solutions Engineer Intern", FILLER, settings, profile, title_rules, priority=1)
    fit = fit.model_copy(update={"score": 72})
    net = score_networking([], settings.scoring)
    ok, _ = evaluate("Solutions Engineer Intern", FILLER, "Singapore")
    assert score_priority(fit, ok, net, settings.scoring, 1).strategic_bypass
    bad, _ = evaluate("Solutions Engineer Intern", FILLER, "Tel Aviv, Israel")
    assert bad.decision == Decision.REJECTED
    assert not score_priority(fit, bad, net, settings.scoring, 1).notify


# Scenario G ------------------------------------------------------------------------------------------
HM_POST = ("My team is hiring a Solutions Engineering Intern for summer 2027 in Singapore! You'll work with me on "
           "enterprise AI deployments, run demos and discovery with customers. Apply here: https://boards.greenhouse.io/acme/jobs/123")


def test_scenario_g_hiring_manager_post(settings, hunter, sessions):
    post = build_post("https://www.linkedin.com/posts/jane-doe_hiring-activity-7000000000000000000-abcd?utm=x", HM_POST,
                      "Jane Doe", "https://www.linkedin.com/in/jane-doe/", "Head of Solutions Engineering APAC at Acme AI", "manual")
    assert post.classification == PostClass.CONFIRMED_INTERNSHIP
    raw = opportunity_from_post(post)
    assert raw is not None and raw.company == "Acme AI"
    assert raw.title == "Solutions Engineering Intern"
    assert raw.contacts[0].relationship == Relationship.HIRING_MANAGER_POST
    from summer_hunter.database.repository import Repository
    from summer_hunter.pipeline import CycleStats

    with sessions() as session:
        repo = Repository(session)
        _, opp = hunter.ingest_post(repo, post, CycleStats())
        hunter.evaluate(repo, opp, None, CycleStats())
        session.commit()
        assert opp.linkedin_post_url == "https://www.linkedin.com/posts/jane-doe_hiring-activity-7000000000000000000-abcd/"
        contacts = repo.contacts(opp.id)
        assert contacts[0].profile_url == "https://www.linkedin.com/in/jane-doe/"
        assert contacts[0].source_post_url == opp.linkedin_post_url
        assert "my team is hiring" in contacts[0].evidence.lower()
        assert opp.networking_score >= 95
        assert opp.country_code == "SG" or opp.country_code is None


def test_post_classes():
    assert classify_post("Top 5 tips to land a summer internship in tech").label == PostClass.CAREER_ADVICE
    assert classify_post("Our 2027 internship programme applications are now open! Join our early careers cohort.",
                         "Campus Recruiter at Visa").label in {PostClass.GENERAL_CAMPAIGN, PostClass.CONFIRMED_INTERNSHIP}
    assert classify_post("We're hiring interns, apply: https://jobs.lever.co/x/1", "Talent Acquisition Partner at X").label \
        == PostClass.RECRUITER_WITH_VACANCY
    assert classify_post("Reposting: we're hiring summer interns at my company, spread the word").label == PostClass.REPOST
    assert classify_post("Great quarter for the team, congrats everyone!").label == PostClass.IRRELEVANT
    upcoming = classify_post("My team will be hiring a product marketing intern soon, stay tuned.")
    assert upcoming.label == PostClass.HIRING_MANAGER_UPCOMING


def test_company_membership_alone_is_not_hiring_manager():
    cls = classify_post("Proud to work at Acme. We just launched our AI platform for interns' onboarding.", "Engineer at Acme")
    assert cls.relationship != Relationship.HIRING_MANAGER_POST


def test_post_without_company_is_kept_as_lead_only():
    post = build_post("https://www.linkedin.com/posts/x_activity-1/", "We're hiring a business development intern for 2027!",
                      None, None, None, "search:brave", 0.8)
    assert opportunity_from_post(post) is None


def test_country_preference_penalty(settings, profile, title_rules):
    from summer_hunter.engine.extraction import extract_facts as ef

    facts = ef("Solutions Engineer Intern", FILLER)
    base = ScoringContext(profile=profile, rules=settings.scoring, company_priority=1)
    pen = ScoringContext(profile=profile, rules=settings.scoring, company_priority=1, country_penalties={"US": -12})
    fam = title_rules.classify("Solutions Engineer Intern")
    a = score_fit("Solutions Engineer Intern", FILLER, facts, fam, base, country="US")
    b = score_fit("Solutions Engineer Intern", FILLER, facts, fam, pen, country="US")
    c = score_fit("Solutions Engineer Intern", FILLER, facts, fam, pen, country="SG")
    assert b.score == a.score - 12 and c.score == a.score
