"""Never confuse the recipient organisation with a former employer or an earlier application.

Regression for 15 Nov 2026: a 3 kB spontaneous request to the French Chamber of Commerce in Singapore was
read as a posting and its own experience list ("Airbus Helicopters — …") supplied 'Airbus' as the target.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from summer_hunter.cv_builder import edits, service
from summer_hunter.cv_builder.context import ContextViolation, from_request, validate_cv, validate_email
from summer_hunter.cv_builder.intent import analyze, context_from, is_offer
from summer_hunter.cv_builder.versions import VersionStore
from tests.integration.test_cv_document import needs_tex
from tests.integration.test_cv_profile import _bot, _say

FCCS = (Path(__file__).parents[1] / "fixtures" / "fccs_request.txt").read_text(encoding="utf-8").strip()
FCCS_NAME = "French Chamber of Commerce in Singapore"


@pytest.fixture
def actx(settings, sessions):
    with sessions() as s:
        return context_from(settings, s)


def recipients(mail) -> str:  # noqa: ANN001
    return " ".join(mail.parts[k] for k in ("subject", "greeting", "opening", "closing"))


# The exact request that failed --------------------------------------------------------------------
def test_real_fccs_request_is_read_as_a_spontaneous_request_to_the_fccs(actx):
    assert not is_offer(FCCS)
    r = analyze(FCCS, actx)
    assert (r.mode, r.company, r.company_aliases, r.company_source) == ("spontaneous", FCCS_NAME, ["FCCS"], "explicit")
    assert r.target_role == "Business Development & International Operations Intern" and r.role_source == "stated"
    assert (r.country_code, r.language, r.pages) == ("SG", "en", 1)
    assert r.requirements == []                                         # no invented posting
    assert r.emphasis[:3] == ["Nexya Agency", "Air France Industries", "Vinc'IA"]


def test_own_employers_never_become_the_target_by_default(actx):
    text = "Je veux un CV business, en anglais.\n\nExpériences :\n- Airbus Helicopters, apprenti\n- Amundi Asset Management"
    r = analyze(text, actx)
    assert r.company is None and r.mode == "target"


# TEST 1 to 6 --------------------------------------------------------------------------------------
@needs_tex
def test_1_airbus_request_targets_airbus(settings, sessions):
    b = service.generate(settings, sessions, "Candidature spontanée chez Airbus pour un stage Business Development à Singapour")
    assert b.req.company == "Airbus" and b.req.company_source == "explicit"
    mail = service.email_for(settings, sessions, b.version_id)
    assert mail.parts["greeting"] == "Dear Airbus team," and "at Airbus" in mail.parts["opening"]


@needs_tex
def test_2_fccs_right_after_airbus_is_not_contaminated(settings, sessions):
    bot, fake = _bot(settings, sessions)
    bot.cvb.buffer_wait_s = 0.05
    bot.cvb.generate("Candidature spontanée chez Airbus pour un stage Business Development à Singapour", background=False)
    # the FCCS request arrives as a plain message while the Airbus CV is still "active": a new application, not an edit
    assert bot.cvb.handle_text("CV pour une candidature spontanée à la French Chamber of Commerce in Singapore (FCCS)") is True
    deadline = time.time() + 30
    while time.time() < deadline:
        with sessions() as s:
            latest = VersionStore(s, settings.data_dir, settings.config_dir).latest()
        if latest and latest.id == 2:
            break
        time.sleep(0.1)
    with bot.cvb._lock:
        pass
    with sessions() as s:
        v2 = VersionStore(s, settings.data_dir, settings.config_dir).get(2)
        assert v2.company == FCCS_NAME and v2.parent_id is None and v2.lineage_id == 2       # a new CV, not v2 of Airbus
    mail = service.email_for(settings, sessions, 2)
    assert "Airbus" not in recipients(mail) and mail.parts["greeting"] == "Dear FCCS team,"
    # the long original request through /cv as well
    built = service.generate(settings, sessions, FCCS)
    assert built.req.company == FCCS_NAME
    cv_text = str(built.res.cv)
    assert "Airbus Helicopters" in cv_text                              # TEST 5: former employer stays in the CV…
    mail = service.email_for(settings, sessions, built.version_id)
    assert "Airbus" not in recipients(mail)                             # …but is never the recipient
    assert "the French Chamber of Commerce in Singapore (FCCS)" in mail.parts["opening"]


@needs_tex
def test_3_google_cloud_after_fccs(settings, sessions):
    service.generate(settings, sessions, FCCS)
    b = service.generate(settings, sessions, "Prépare un CV pour une candidature spontanée chez Google Cloud.")
    assert b.req.company == "Google"
    mail = service.email_for(settings, sessions, b.version_id)
    assert "FCCS" not in mail.text and "Chamber" not in mail.text and mail.parts["greeting"] == "Dear Google team,"


def test_4_spontaneous_without_offer_invents_no_posting(actx):
    for text in (FCCS, "CV pour AWS Singapore", "Candidature spontanée chez Airbus"):
        r = analyze(text, actx)
        assert r.mode == "spontaneous" and r.requirements == [] and r.company


def test_5_validator_allows_airbus_as_experience_and_rejects_it_as_recipient(settings, actx):
    req = analyze(FCCS, actx)
    ctx = from_request(req, settings.profile_raw)
    orgs = sorted(set(actx.companies) | set(actx.own_orgs))
    proof = "At Airbus Helicopters, I supported industrial logistics."
    good = {"subject": "Spontaneous application – BD internship", "greeting": "Dear FCCS team,",
            "opening": "I am writing to apply at the French Chamber of Commerce in Singapore (FCCS).", "closing": "", "proof": proof,
            "slots": [FCCS_NAME, "FCCS"]}
    assert validate_email(good, ctx, orgs, actx.own_orgs) == []
    bad = good | {"greeting": "Dear Airbus team,", "opening": "I am writing to apply at Airbus Singapore."}
    problems = validate_email(bad, ctx, orgs, actx.own_orgs)
    assert any("Airbus" in p for p in problems)
    cv = {"summary": "Final-year engineering student. Seeking an internship at Airbus from April 2027, to contribute to growth."}
    assert any("Airbus" in p for p in validate_cv(cv, ctx, orgs, actx.own_orgs))
    fake_exp = good | {"proof": "At Google, I led the cloud migration."}
    assert any("Google" in p for p in validate_email(fake_exp, ctx, orgs, actx.own_orgs))


def test_5b_former_employer_read_outside_the_request_is_rejected(settings, actx):
    req = analyze(FCCS, actx)
    from dataclasses import replace

    wrong = from_request(replace(req, company="Airbus", company_aliases=[], company_source="posting_body"), settings.profile_raw)
    assert validate_cv({"summary": ""}, wrong, ["Airbus"], actx.own_orgs)


@needs_tex
def test_6_role_change_updates_cv_and_email(settings, sessions):
    b = service.generate(settings, sessions, FCCS)
    e = edits.parse("Pour un poste de Sales Engineer")
    assert not edits.is_new_request("Pour un poste de Sales Engineer", FCCS_NAME)
    b2 = service.derive(settings, sessions, b.version_id, "Pour un poste de Sales Engineer", e.overrides)
    assert b2.req.target_role == "Sales Engineer" and b2.req.company == FCCS_NAME and b2.req.family == "technical_sales"
    mail = service.email_for(settings, sessions, b2.version_id)
    assert "Sales Engineer internship" in mail.subject and "FCCS" in mail.parts["greeting"]


def test_edit_vs_new_request_routing():
    assert edits.is_new_request("CV pour Google Cloud", "Airbus")
    assert edits.is_new_request("Fais-le pour Google", "Airbus")
    assert not edits.is_new_request("mets en avant Nexya", "Airbus")
    assert not edits.is_new_request("en anglais, une page", "Airbus")
    assert not edits.is_new_request("retire Amundi", "Airbus")


@needs_tex
def test_blocked_build_is_never_recorded(settings, sessions, monkeypatch):
    from summer_hunter.cv_builder import context as ctxmod

    monkeypatch.setattr(ctxmod, "validate_cv", lambda *a, **k: ["cible incohérente (test)"])
    with pytest.raises(ContextViolation):
        service.generate(settings, sessions, "CV pour AWS Singapore")
    with sessions() as s:
        assert VersionStore(s, settings.data_dir, settings.config_dir).latest() is None



# Paths found by the independent review (Codex, 15 Nov 2026) ----------------------------------------
@pytest.mark.parametrize("text, company, source", [
    ("Voici mon parcours chez Airbus Helicopters.\n\nJe souhaite une candidature spontanée à la French Chamber of Commerce "
     "in Singapore (FCCS).", FCCS_NAME, "explicit"),
    ("Contexte\nJe postule chez Airbus pour un stage.\nCompétences : Salesforce et Microsoft Office.\n" + "Profil motivé. " * 50,
     "Airbus", "explicit"),
    ("Candidature spontanée à Singapore Airlines, compétences Microsoft Office.", "Singapore Airlines", "explicit"),
    ("Candidature spontanée au Singapore Economic Development Board, partenaire Microsoft",
     "Singapore Economic Development Board", "explicit"),
    ("Business Development Intern\nAirbus — Singapore\nPartner ecosystem: Azure\nResponsibilities\n" + "Support partners. " * 40,
     "Airbus", "posting_header"),
    ("Business Development Intern\nSingapore\n6-month internship\nAbout the employer\nAirbus builds aircraft. Works with "
     "Microsoft.\nResponsibilities\n" + "Do things. " * 40, "Airbus", "posting_intro"),
    ("J'ai fait mon apprentissage chez Airbus Helicopters et je veux un CV business en anglais.", None, None),
])
def test_review_paths_pick_the_real_recipient(actx, text, company, source):
    r = analyze(text, actx)
    assert (r.company, r.company_source) == (company, source)


def test_ambiguous_posting_header_asks_instead_of_guessing(actx):
    r = analyze("Business Development Intern\nAirbus and Salesforce joint programme\nSingapore\n" + "Do things. " * 80, actx)
    assert r.company is None and any("Quelle entreprise" in q for q in r.questions)


def test_alias_colliding_with_another_company_is_dropped(actx):
    r = analyze("Candidature spontanée chez Amazon Robotics (SAP)", actx)
    assert r.company == "Amazon Robotics" and r.company_aliases == []


def test_validators_fail_closed(settings, actx):
    from dataclasses import replace

    orgs = sorted(set(actx.companies) | set(actx.own_orgs))
    fccs = from_request(analyze(FCCS, actx), settings.profile_raw)
    assert validate_cv({"summary": "Final-year engineer. Ready to join Airbus."}, fccs, orgs, actx.own_orgs)
    assert validate_cv({"summary": "x", "recipient": "Airbus"}, fccs, orgs, actx.own_orgs)
    base = {"subject": "Application", "greeting": "Dear FCCS team,", "opening": "I apply at the FCCS.", "closing": "",
            "proof": "", "slots": ["FCCS"]}
    assert validate_email(base | {"greeting": "Dear Acme team,"}, fccs, orgs, actx.own_orgs)       # unknown org, still blocked
    assert validate_email(base | {"slots": []}, fccs, orgs, actx.own_orgs)                         # no recipient slot
    heli = replace(fccs, target_company="Airbus Helicopters", company_aliases=())
    assert validate_email(base | {"greeting": "Dear Airbus team,", "opening": "I apply at Airbus Helicopters.",
                                  "slots": ["Airbus"]}, heli, orgs, actx.own_orgs)              # prefix is not the target
    # a role named after software vendors is not a recipient
    role = base | {"subject": "Application – SAP / Oracle / Outreach internship"}
    assert validate_email(role, fccs, orgs, actx.own_orgs) == []


def test_edits_with_a_company_word_stay_edits():
    assert not edits.is_new_request("Mets davantage en avant mon projet pour Airbus", FCCS_NAME)
    assert not edits.is_new_request("Change le titre pour Business Development", FCCS_NAME)
    assert edits.is_new_request("Candidature spontanée chez Google Cloud", FCCS_NAME)
