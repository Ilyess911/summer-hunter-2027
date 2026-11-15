"""Application email engine: strategy per situation and role, one drafting call, checks, one revision, fallback.

The model is scripted here (no local LLM in tests). Real drafts are evaluated by scripts/eval_emails.py and
kept in docs/email_examples/ for human review.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from summer_hunter.cv_builder import application_email as ae
from summer_hunter.cv_builder import service
from summer_hunter.cv_builder.context import from_request
from summer_hunter.cv_builder.document import compose as compose_cv
from summer_hunter.cv_builder.intent import analyze, context_from
from summer_hunter.integrations.anthropic_client import LLMError, LLMProvider, LLMResponse
from summer_hunter.web.cv import load_content

FCCS = (Path(__file__).parents[1] / "fixtures" / "fccs_request.txt").read_text(encoding="utf-8").strip()
GOOD = [
    "Four years of engineering school gave me the method; co-founding a small agency showed me what I actually enjoy, "
    "which is the moment a technical idea becomes something a client is ready to pay for. That is the direction I want "
    "to take, and the reason I am writing to you.",
    "At Air France Industries I learned how industrial teams make decisions and what they need from the people who "
    "work with them. Nexya Agency then put me on the other side of the table: finding B2B prospects, listening to what "
    "they needed and explaining our solution in their terms. Organising conferences at Vinc'IA added a taste for "
    "bringing the right people into the same room.",
    "A chamber of commerce is where those three things meet, and helping French companies grow in Singapore is the "
    "kind of work I would like to learn from the inside.",
    "Could one of your teams take an intern between April and August 2027? If someone else handles this, I would be "
    "grateful if you could forward my message. My CV is attached.",
]


class Scripted(LLMProvider):
    name = "scripted"
    model = "scripted-model"

    def __init__(self, *payloads: dict[str, Any], fail: bool = False) -> None:
        self.payloads = list(payloads)
        self.prompts: list[str] = []
        self.fail = fail

    def complete_json(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> LLMResponse:
        self.prompts.append(system + "\n" + user)
        if self.fail:
            raise LLMError("down")
        data = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return LLMResponse(data=dict(data), input_tokens=100, output_tokens=100, model=self.model)


def draft(paragraphs: list[str], subject: str = "Spontaneous Application | Business Development Internship | April–August 2027"):  # noqa: ANN201
    return {"why_organisation": "x", "why_role": "x", "why_me": "x", "differentiator": "x", "action": "x",
            "subject": subject, "paragraphs": paragraphs}


@pytest.fixture
def env(settings, sessions):
    with sessions() as s:
        actx = context_from(settings, s)
    content = load_content(settings.config_dir)
    orgs = sorted(set(actx.companies) | set(actx.own_orgs))

    def run(text: str, provider: LLMProvider | None, **kw):  # noqa: ANN202
        req = analyze(text, actx)
        cv, _ = compose_cv(content, req, [])
        cv.pop("_content", None)
        ctx = from_request(req, settings.profile_raw)
        mail = ae.write(cv, content, ctx, req.track, req.family, req.situation, settings.config_dir, settings.profile_raw,
                        orgs, actx.own_orgs, provider, source_text=req.source_text if req.mode != "offer" else "",
                        posting=req.source_text if req.mode == "offer" else "", rng=random.Random(0), **kw)
        return req, mail
    return run, actx


# Situations and story lines (cases A to J) ----------------------------------------------------------
@pytest.mark.parametrize("text, situation, thread", [
    (FCCS, "spontaneous", "international"),                                                                     # A
    ("Candidature spontanée à la CCI France UAE (Dubai) pour un stage Business Development", "spontaneous", "international"),  # B
    ("Candidature spontanée chez Airbus pour un stage Business Development à Singapour", "spontaneous", "business"),         # C
    ("Fais-moi un CV pour un stage Robotics Engineer chez une startup à Singapour.", "spontaneous", "robotics"),              # D
    ("Candidature spontanée chez Capgemini Invent pour un stage Strategy & Operations", "spontaneous", "strategy"),          # E
    ("Prise de contact avec le CEO de Mistral AI pour un stage AI Business Development", "executive", "business"),            # G
    ("Relance de ma candidature chez Salesforce, stage Tech Sales", "follow_up", "business"),
    ("Demande de mise en relation avec l'équipe partenariats de Doctolib", "introduction", "business"),
    ("Message à la recruteuse de Datadog pour un stage Sales Engineer", "recruiter", "business"),
])
def test_situation_and_story_line(env, text, situation, thread):
    run, _ = env
    provider = Scripted(draft(GOOD))
    req, mail = run(text, provider)
    assert req.situation == situation
    assert mail.strategy["thread"] == thread
    assert ae.WORDS[situation] == tuple(mail.strategy["words"])
    assert "ACTION WANTED: " + ae.ASK[situation] in provider.prompts[0]


def test_f_posting_uses_the_posting_and_the_offer_ask(env):
    run, _ = env
    posting = ("Business Development Intern\nCompany: Acme Robotics\nMunich, Germany\n\nResponsibilities\n"
               "Prospect industrial customers for our autonomous mobile robots.\n" + "Work with sales. " * 40)
    provider = Scripted(draft(GOOD))
    req, mail = run(posting, provider)
    assert req.situation == "offer" and "<posting>" in provider.prompts[0] and ae.ASK["offer"] in provider.prompts[0]


def test_a_fccs_prompt_has_verified_organisation_facts_and_no_reference_text(env):
    run, _ = env
    provider = Scripted(draft(GOOD))
    _, mail = run(FCCS, provider)
    prompt = provider.prompts[0]
    assert "supports the development of French businesses in Singapore" in prompt
    assert "As I approach the end of my engineering degree" not in prompt      # reasoning, not a template to copy
    assert "Never mention: ESSEC, ESCP, AST" in prompt
    assert mail.model == "scripted-model" and mail.warnings == [] and mail.attempts == 1


def test_j_unknown_organisation_must_not_be_described(env):
    run, _ = env
    provider = Scripted(draft(["Acme Ventures helps companies expand across the Gulf and supports founders. " + GOOD[0], *GOOD[1:]]),
                        draft(GOOD))
    _, mail = run("Candidature spontanée chez Acme Ventures pour un stage Business Development", provider)
    assert "none verified: do not describe" in provider.prompts[0]
    assert "describes the organisation without a verified source" in provider.prompts[1]          # sent back once
    assert mail.attempts == 2 and mail.warnings == []


# Checks ---------------------------------------------------------------------------------------------
def test_cliches_and_reference_wording_trigger_one_revision(env):
    run, _ = env
    bad = [GOOD[0] + " I am passionate about business and what particularly attracts me is your mission.", *GOOD[1:]]
    provider = Scripted(draft(bad), draft(GOOD))
    _, mail = run(FCCS, provider)
    assert "passionate" in provider.prompts[1] and "reuses the reference email" in provider.prompts[1]
    assert mail.attempts == 2 and mail.warnings == []


def test_invented_number_wrong_noun_and_leadership_inflation_block(env):
    run, _ = env
    bad = [GOOD[0], "At Nexya Agency I managed the sales team, and at Vinc'IA our conferences drew 140 attendees. " + GOOD[1],
           *GOOD[2:]]
    provider = Scripted(draft(bad))                       # the model keeps the same text on revision
    _, mail = run(FCCS, provider)
    assert mail.model == "deterministic" and "bloquée" in mail.warnings[0]
    problems = provider.prompts[1]
    assert "140 attendees" in problems and "the facts do not say he led" in problems


def test_never_mention_admissions(env):
    run, _ = env
    bad = [GOOD[0] + " This internship would also strengthen my ESSEC application.", *GOOD[1:]]
    provider = Scripted(draft(bad))
    _, mail = run(FCCS, provider)
    assert mail.model == "deterministic" and "ESSEC" in provider.prompts[1]


def test_cv_copy_and_skill_lists_are_flagged(env):
    run, _ = env
    copied = ("At Nexya Agency I conducted B2B customer outreach through prospecting calls and email campaigns, identifying "
              "business needs and presenting tailored digital solutions. My skills include Business Development, Lead "
              "Generation, Customer Outreach, CRM and Public Speaking.")
    provider = Scripted(draft([GOOD[0], copied, *GOOD[2:]]), draft(GOOD))
    _, mail = run(FCCS, provider)
    assert "repeats the CV almost word for word" in provider.prompts[1] and "lists skills" in provider.prompts[1]
    assert mail.warnings == []


def test_recent_opening_is_not_reused(env):
    run, _ = env
    provider = Scripted(draft(GOOD), draft(["Here is a different start about the request for an internship in Singapore. "
                                            + GOOD[0], *GOOD[1:]]))
    _, mail = run(FCCS, provider, recent_openings=[GOOD[0].split(";")[0]])
    assert "starts like a recent email" in provider.prompts[1]


def test_model_down_falls_back_to_the_deterministic_email(env):
    run, _ = env
    _, mail = run(FCCS, Scripted(fail=True))
    assert mail.model == "deterministic" and "indisponible" in mail.warnings[0]
    assert mail.body.startswith("Dear FCCS team,")


def test_i_revision_request_sends_the_current_email_and_the_instruction(env):
    run, _ = env
    first = Scripted(draft(GOOD))
    _, mail = run(FCCS, first)
    second = Scripted(draft(GOOD))
    run(FCCS, second, revision=ae.REVISIONS["natural"][2], previous=mail)
    assert "REVISION REQUESTED: Make it sound more natural" in second.prompts[0] and "CURRENT EMAIL" in second.prompts[0]


@pytest.mark.parametrize("text, code, what", [
    ("Plus naturel", "natural", None), ("plus convaincant stp", "convincing", None), ("Moins formel", "informal", None),
    ("Plus personnel", "personal", None), ("Revois l'accroche", "hook", None), ("Change la conclusion", "closing", None),
    ("Explique mieux ma motivation", "motivation", None), ("Ne parle pas de Amundi", "skip", "Amundi"),
    ("mets en avant Nexya", None, None),
])
def test_revision_commands(text, code, what):
    assert ae.detect_revision(text) == (code, what)


def test_skip_removes_the_experience_from_the_argument(env):
    run, _ = env
    provider = Scripted(draft(GOOD))
    _, mail = run(FCCS, provider, skip=["Vinc'IA"])
    assert all(e["org"] != "Vinc'IA" for e in mail.strategy["evidence"]) and "DO NOT MENTION: Vinc'IA" in provider.prompts[0]


# Signature, storage, Telegram -------------------------------------------------------------------------
def test_signature_is_structured_text_and_html(env):
    run, _ = env
    _, mail = run(FCCS, Scripted(draft(GOOD)))
    assert mail.body.endswith("Ilyess Assadi\nEngineering Student | ESILV × Air France Industries\n+33 6 63 47 21 73\n"
                              "ilyess.assadipro@gmail.com\nLinkedIn: linkedin.com/in/ilyess-assadi")
    assert mail.body.count("Ilyess Assadi\nEngineering Student") == 1
    assert 'href="https://www.linkedin.com/in/ilyess-assadi"' in mail.html_signature
    assert 'href="mailto:ilyess.assadipro@gmail.com"' in mail.html_signature


def test_h_new_company_email_after_a_previous_one(settings, sessions):
    first = service.generate(settings, sessions, FCCS)
    second = service.generate(settings, sessions, "Candidature spontanée chez Google Cloud pour un stage Tech Sales")
    p1, p2 = Scripted(draft(GOOD)), Scripted(draft([p.replace("chamber of commerce", "cloud company").replace(
        "French companies grow in Singapore", "customers adopt cloud services") for p in GOOD]))
    m1 = service.email_for(settings, sessions, first.version_id, provider=p1)
    m2 = service.email_for(settings, sessions, second.version_id, provider=p2)
    assert m1.body.startswith("Dear FCCS team,") and m2.body.startswith("Dear Google team,")
    assert "FCCS" not in p2.prompts[0].split("<request>")[0] and "Google" in p2.prompts[0]


def test_stored_email_is_served_and_revisions_are_kept(settings, sessions):
    b = service.generate(settings, sessions, FCCS)
    p = Scripted(draft(GOOD))
    m1 = service.email_for(settings, sessions, b.version_id, provider=p)
    m2 = service.email_for(settings, sessions, b.version_id, provider=Scripted(fail=True))
    assert m2.body == m1.body                                    # served from storage, no model call
    p3 = Scripted(draft([GOOD[0].replace("Four years", "Several years"), *GOOD[1:]]))
    m3 = service.email_for(settings, sessions, b.version_id, revise="natural", provider=p3)
    assert "Several years" in m3.body and "REVISION REQUESTED" in p3.prompts[0]


def test_telegram_application_ready_and_email_buttons(settings, sessions):
    from tests.integration.test_cv_profile import _bot, _press

    bot, fake = _bot(settings, sessions)
    bot.cvb.generate("Candidature spontanée chez Google Cloud pour un stage Tech Sales", background=False)
    card = next(m["text"] for m in fake.sent if "APPLICATION READY" in m["text"])
    for label in ("Company : Google", "Position : Tech Sales", "Application Type : Candidature spontanée",
                  "Availability : April–August 2027"):
        assert label in card
    _press(bot, "cvm:1")
    kb = fake.sent[-1]["reply_markup"]["inline_keyboard"]
    codes = [b.get("callback_data") for row in kb for b in row if b.get("callback_data", "").startswith("cvw:")]
    assert codes == ["cvw:1:natural", "cvw:1:convincing", "cvw:1:informal", "cvw:1:personal", "cvw:1:hook",
                     "cvw:1:closing", "cvw:1:motivation"]
    _press(bot, "cvw:1:natural")                  # no model in tests: deterministic email, still delivered
    assert "Email de candidature" in fake.sent[-1]["text"]
    assert bot.cvb.handle_text("Plus naturel") is True
