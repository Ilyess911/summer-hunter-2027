"""AI CV Builder lot 2: request analysis (free requests and postings), deterministic, no LLM."""

from __future__ import annotations

import pytest

from summer_hunter.cv_builder.intent import MAX_QUESTIONS, analyze, context_from, detect_countries
from summer_hunter.cv_builder.profile import ProfileStore
from tests.integration.test_cv_profile import _bot, _say


@pytest.fixture
def ctx(settings, sessions):
    with sessions() as session:
        ProfileStore(session).seed()
        session.commit()
        return context_from(settings, session)


@pytest.mark.parametrize("text, role, family, track, company, country, language", [
    ("Fais-moi un CV pour un stage Robotics Engineer chez une startup à Singapour.",
     "Robotics Engineer", "robotics_specialized", "industry", None, "SG", "en"),
    ("Je veux un CV pour du Business Development dans une entreprise SaaS.",
     "Business Development", "go_to_market", "business", None, None, "en"),
    ("Prépare un CV pour une candidature spontanée chez Google Cloud.",
     "Technical Sales / Sales Engineering", "technical_sales", "business", "Google", None, "en"),
    ("Je cherche un stage en transformation digitale dans l'industrie.",
     "transformation digitale", "industrial_digital", "industry", None, None, "en"),
    ("CV pour AWS Singapore", "Technical Sales / Sales Engineering", "technical_sales", "business",
     "Amazon Web Services", "SG", "en"),
    ("CV en français pour un stage maintenance aéronautique au Maroc", "maintenance aéronautique",
     "maintenance_reliability", "industry", None, "MA", "fr"),
    ("Tech Sales", "Technical Sales / Sales Engineering", "technical_sales", "business", None, None, "en"),
])
def test_free_requests(ctx, text, role, family, track, company, country, language):
    r = analyze(text, ctx)
    assert (r.target_role, r.family, r.track, r.company, r.country_code, r.language) == \
        (role, family, track, company, country, language)
    assert r.mode in {"target", "spontaneous"} and r.requirements == []     # no posting: no employer requirements


def test_spontaneous_application_never_invents_an_offer(ctx):
    r = analyze("CV pour AWS Singapore", ctx)
    assert r.mode == "spontaneous" and r.role_source == "inferred"
    assert r.requirements == [] and r.expected_skills        # usual skills only, labelled as such


def test_options_pages_style_emphasis(ctx):
    r = analyze("CV pour un stage Tech Sales, 2 pages, style moderne, en français. Mets davantage en avant Nexya", ctx)
    assert (r.pages, r.style, r.language) == (2, "modern", "fr")
    assert r.emphasis and "Nexya" in r.emphasis[0]


POSTING = """Solutions Engineer Intern
Acme Robotics - Munich, Germany

About the role
You will support customer demos and technical discovery with enterprise clients.

Requirements
- Strong Python skills (required)
- SQL is required
- Experience with ROS is a plus
- Excellent communication in English
"""


def test_posting_requirements_and_coverage(ctx):
    r = analyze(POSTING, ctx)
    assert r.mode == "offer" and r.target_role == "Solutions Engineer Intern" and r.country_code == "DE"
    assert r.family == "technical_sales" and r.language == "en"
    cov = {q.name: (q.level, q.coverage) for q in r.requirements}
    assert cov["python"] == ("mandatory", "covered")
    assert cov["sql"] == ("mandatory", "partial")              # academic coursework only, never "covered"
    assert all(c != "covered" for n, (_, c) in cov.items() if n not in {"python"} and "commun" not in n)
    assert len(r.questions) <= MAX_QUESTIONS


def test_pending_profile_skill_is_to_confirm_not_covered(ctx):
    posting = POSTING.replace("Experience with ROS is a plus", "C++ is required")
    r = analyze(posting, ctx)
    cpp = next((q for q in r.requirements if q.name.lower() in {"c++", "cpp"}), None)
    if cpp is None:
        pytest.skip("C++ not in the requirement lexicon")
    assert cpp.coverage == "to_confirm" and any("c++" in q.lower() for q in r.questions)


def test_german_posting_gets_an_english_cv(ctx):
    text = ("Praktikum Product Marketing (m/w/d)\nDoctolib - Berlin\n\n" +
            "Deine Aufgaben: Du unterstützt das Team bei der Einführung neuer Produkte und der Marktanalyse. "
            "Wir suchen eine motivierte Person mit Interesse an Gesundheit und digitalen Produkten, die mit uns arbeitet. " * 4)
    r = analyze(text, ctx)
    assert r.language == "en" and "allemand" in r.language_reason and r.country_code == "DE"


def test_body_country_needs_to_be_alone():
    assert set(detect_countries("Offices in Germany and Ireland")) == {"DE", "IE"}


def test_header_names_the_employer_not_tools_in_body(ctx):
    text = "Professional Affairs Intern\nStryker - Warsaw, Poland\n\n" + "Use Microsoft Office daily. " * 30
    r = analyze(text, ctx)
    assert r.company != "Microsoft" and r.country_code == "PL"


def test_telegram_cv_analyze(settings, sessions):
    bot, fake = _bot(settings, sessions)
    _say(bot, "/cv_analyze CV pour un stage Robotics Engineer chez une startup à Singapour")
    out = fake.sent[-1]["text"]
    assert "Analyse de la demande" in out and "Robotics Engineer" in out and "Singapore" in out
    assert "pas une exigence d'entreprise" in out
    _say(bot, "/cv_analyze " + POSTING)
    out = fake.sent[-1]["text"]
    assert "Exigences de l'offre" in out and "Partiel" in out
    _say(bot, "/cv_analyze")
    assert "Colle une offre" in fake.sent[-1]["text"]
