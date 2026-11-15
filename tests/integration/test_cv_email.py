"""Cover email for a CV: built from the CV's ApplicationContext, verified content only, never sent."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from summer_hunter.cv_builder.context import from_request, validate_email
from summer_hunter.cv_builder.document import compose as compose_cv
from summer_hunter.cv_builder.email import _clause, _de, compose
from summer_hunter.cv_builder.intent import analyze, context_from
from summer_hunter.web.cv import load_content
from tests.integration.test_cv_document import needs_tex
from tests.integration.test_cv_profile import _bot, _press


@pytest.fixture
def make(settings, sessions):
    with sessions() as s:
        actx = context_from(settings, s)
    content = load_content(settings.config_dir)
    orgs = sorted(set(actx.companies) | set(actx.own_orgs))

    def _make(text, language=None):  # noqa: ANN001, ANN202
        req = analyze(text, actx)
        if language:
            req = replace(req, language=language)
        cv, _ = compose_cv(content, req, [])
        cv.pop("_content", None)
        ctx = from_request(req, settings.profile_raw)
        mail = compose(cv, ctx, req.track)
        assert validate_email(mail.parts, ctx, orgs, actx.own_orgs) == []
        return mail, cv
    return _make


def test_english_spontaneous_email_quotes_only_cv_bullets(make):
    mail, cv = make("CV pour AWS Singapore")
    assert mail.subject.startswith("Spontaneous application – Technical Sales / Sales Engineering internship (")
    assert mail.body.startswith("Dear Amazon Web Services team,") and "in Singapore" in mail.body
    bullets = {b["text"].rstrip(".").lower() for e in cv["experiences"] + cv["projects"] for b in e["bullets"]}
    quoted = re.findall(r"At [^,]+, I ([^.]+)\.", mail.body)
    assert quoted and all(q.lower() in bullets for q in quoted)        # verbatim, nothing invented
    assert "Would one of your teams be able to host an intern between April and August 2027?" in mail.body
    assert "pass my message on to the relevant colleague" in mail.body and "If my profile fits" not in mail.body
    assert mail.body.endswith("Ilyess Assadi\nEngineering Student | ESILV × Air France Industries\n+33 6 63 47 21 73\n"
                              "ilyess.assadipro@gmail.com\nLinkedIn: linkedin.com/in/ilyess-assadi")


def test_french_email_is_neutral_and_never_translates_bullets(make):
    mail, cv = make("CV en français pour un stage maintenance aéronautique chez Safran au Maroc")
    assert mail.language == "fr" and mail.subject.startswith("Candidature spontanée – Stage en maintenance aéronautique (")
    assert "au sein de Safran au Maroc" in mail.body and mail.body.startswith("Madame, Monsieur,")
    assert not any(b["text"] in mail.body for e in cv["experiences"] for b in e["bullets"])
    assert not re.search(r"\b(ravi|heureux|motivé|intéressé)\b", mail.body)
    assert "Élève ingénieur | ESILV × Air France Industries" in mail.body and "accueillir quelqu'un en stage" in mail.body


def test_offer_email_is_not_called_spontaneous(make):
    posting = "Solutions Engineer Intern\nAcme Robotics - Munich, Germany\n\nRequirements\n- Python required\n" + "x " * 300
    mail, _ = make(posting)
    assert mail.subject.startswith("Application – Solutions Engineer internship") and "spontaneously" not in mail.body


def test_french_articles_and_prepositions(make):
    assert "à Singapour" in make("CV pour AWS Singapore", "fr")[0].body
    assert "au sein d'Amazon Web Services" in make("CV pour AWS Singapore", "fr")[0].body
    assert _de("French Chamber of Commerce in Singapore") == "de la French Chamber of Commerce in Singapore"
    assert _de("Salesforce") == "de Salesforce"


def test_clause_only_takes_verbs():
    assert _clause("Automate the consolidation of data.") == "automate the consolidation of data"
    assert _clause("Academic collaborative robotics project.") is None


@needs_tex
def test_bot_email_button_never_sends_anything(settings, sessions):
    bot, fake = _bot(settings, sessions)
    bot.cvb.generate("CV pour AWS Singapore", background=False)
    docs = len(fake.docs)
    _press(bot, "cvm:1")
    msg = fake.sent[-1]
    assert "Email de candidature" in msg["text"] and "<pre>" in msg["text"] and "rien n'est envoyé" in msg["text"]
    kb = msg["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["copy_text"]["text"].startswith("Spontaneous application") and kb[-1][0]["callback_data"] == "cvm:1:fr"
    assert kb[1][0]["callback_data"] == "cvw:1:natural"
    _press(bot, "cvm:1:fr")
    assert "Madame, Monsieur" in fake.sent[-1]["text"] and len(fake.docs) == docs


@needs_tex
def test_miniapp_email_endpoint(settings, sessions):
    from starlette.testclient import TestClient

    from summer_hunter.web.server import create_app
    from tests.integration.test_webapp import TOKEN, USER, init_data

    settings.telegram_bot_token, settings.telegram_chat_id = TOKEN, USER
    client, h = TestClient(create_app(settings, sessions, None)), {"X-Telegram-Init-Data": init_data()}
    v = client.post("/api/cv", headers=h, json={"text": "CV pour AWS Singapore"}).json()
    m = client.post(f"/api/cv/{v['id']}/email", headers=h, json={}).json()
    assert m["text"].startswith("Subject: Spontaneous application") and m["language"] == "en"
    assert client.post(f"/api/cv/{v['id']}/email", headers=h, json={"language": "fr"}).json()["text"].startswith("Objet :")
    assert client.post("/api/cv/999/email", headers=h, json={}).status_code == 404
    assert client.post(f"/api/cv/{v['id']}/email", json={}).status_code == 401
