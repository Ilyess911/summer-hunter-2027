"""Cover email for a CV: drafted from verified CV content only, never sent."""

from __future__ import annotations

import re

import pytest

from summer_hunter.cv_builder.document import compose as compose_cv
from summer_hunter.cv_builder.email import _clause, compose
from summer_hunter.cv_builder.intent import analyze, context_from
from summer_hunter.web.cv import load_content
from tests.integration.test_cv_document import needs_tex
from tests.integration.test_cv_profile import _bot, _press


@pytest.fixture
def make(settings, sessions):
    with sessions() as s:
        ctx = context_from(settings, s)
    content = load_content(settings.config_dir)

    def _make(text, language=None):  # noqa: ANN001, ANN202
        req = analyze(text, ctx)
        cv, _ = compose_cv(content, req, [])
        cv.pop("_content", None)
        return compose(cv, req, settings.profile_raw, language), cv
    return _make


def test_english_spontaneous_email_quotes_only_cv_bullets(make):
    mail, cv = make("CV pour AWS Singapore")
    assert mail.subject.startswith("Spontaneous application – Technical Sales / Sales Engineering internship (")
    assert "Dear Amazon Web Services team," in mail.body and "in Singapore" in mail.body
    bullets = {b["text"].rstrip(".").lower() for e in cv["experiences"] + cv["projects"] for b in e["bullets"]}
    quoted = re.findall(r"(?:At [^,]+, I|I also) ([^.]+)\.", mail.body)
    assert quoted and all(q.lower() in bullets for q in quoted)        # nothing paraphrased or invented
    assert cv["header"]["email"] in mail.body and "Kind regards," in mail.body


def test_french_email_is_neutral_and_never_translates_bullets(make):
    mail, cv = make("CV en français pour un stage maintenance aéronautique chez Safran au Maroc")
    assert mail.language == "fr" and mail.subject.startswith("Candidature spontanée – Stage en maintenance aéronautique (")
    assert "au sein de Safran au Maroc" in mail.body and "Madame, Monsieur," in mail.body
    assert not any(b["text"] in mail.body for e in cv["experiences"] for b in e["bullets"])
    assert not re.search(r"\b(ravi|heureux|motivé)\b", mail.body)          # gender-neutral wording


def test_offer_email_is_not_called_spontaneous(make):
    posting = "Solutions Engineer Intern\nAcme Robotics - Munich, Germany\n\nRequirements\n- Python required\n" + "x " * 300
    mail, _ = make(posting)
    assert mail.subject.startswith("Application – Solutions Engineer internship") and "spontaneously" not in mail.body


def test_language_override_and_prepositions(make):
    assert make("CV pour AWS Singapore", "fr")[0].body.count("à Singapour") == 1
    assert "aux États-Unis" in make("CV pour un stage Tech Sales chez Stripe à New York", "fr")[0].body


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
    assert "Mail de candidature" in msg["text"] and "<pre>" in msg["text"] and "rien n'est envoyé" in msg["text"]
    kb = msg["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["copy_text"]["text"].startswith("Spontaneous application") and kb[1][0]["callback_data"] == "cvm:1:fr"
    _press(bot, "cvm:1:fr")
    assert "Madame, Monsieur" in fake.sent[-1]["text"] and len(fake.docs) == docs      # no file, no email sent


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
