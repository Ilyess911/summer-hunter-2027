"""AI CV Builder lot 1: permanent profile store and /cv_profile on Telegram."""

from __future__ import annotations

import httpx
import pytest

from summer_hunter.bot.handlers import COMMANDS, Bot
from summer_hunter.cv_builder.profile import (INFERRED, REJECTED, SEED, USER_CONFIRMED, ProfileStore, clean_statement,
                                              fact_key, guess_kind)
from summer_hunter.database.migrations import init_db, make_engine
from summer_hunter.engine import knowledge
from summer_hunter.integrations.anthropic_client import NullProvider
from summer_hunter.integrations.telegram_client import TelegramClient
from summer_hunter.web.cv import load_content
from tests.integration.test_crm import CHAT, TOKEN, FakeTelegram


@pytest.fixture
def store(sessions):
    with sessions() as session:
        yield ProfileStore(session)


def test_seed_is_idempotent_and_never_usable(store, settings):
    assert store.seed() == len(SEED)
    assert store.seed() == 0
    assert {f.status for f in store.facts()} == {INFERRED}
    claims = store.claims(settings.profile_raw, load_content(settings.config_dir))
    texts = {c.text for c in knowledge.usable(claims)}
    assert not any("RoboDK" in t or "Hackathon" in t or "MATLAB" in t for t in texts)
    assert any(c.text == "RoboDK" and c.status == knowledge.INFERRED for c in claims)


def test_confirm_makes_a_fact_usable_and_reject_removes_it(store, settings):
    fact = store.propose("J'ai obtenu une certification IBM").fact
    assert fact.kind == "certification" and fact.status == INFERRED
    cv = load_content(settings.config_dir)
    assert fact.text not in {c.text for c in knowledge.usable(store.claims(settings.profile_raw, cv))}
    store.decide(fact.id, USER_CONFIRMED)
    assert fact.text in {c.text for c in knowledge.usable(store.claims(settings.profile_raw, cv))}
    store.decide(fact.id, REJECTED)
    assert fact.text not in {c.text for c in store.claims(settings.profile_raw, cv)}
    assert store.facts(REJECTED) == [fact]


def test_same_claim_is_not_stored_twice(store):
    first = store.propose("Ajoute : RoboDK", kind="skill")
    again = store.propose("robodk", kind="skill")
    assert first.created and not again.created and again.fact.id == first.fact.id
    assert fact_key("skill", "Été 2026, C++ !") == fact_key("skill", "ete 2026 c++")


def test_kind_guess_and_statement_cleaning():
    assert guess_kind("Hackathon remporté avec mon équipe") == "achievement"
    assert guess_kind("Stage chez Safran en 2025") == "experience"
    assert guess_kind("Je maîtrise SolidWorks") == "skill"
    assert guess_kind("TOEFL 110") == "language"
    assert guess_kind("Il pleut") == "other"
    assert clean_statement("  ajoute que   j'ai un TOEFL.  ") == "j'ai un TOEFL"


def test_profile_survives_a_restart(settings):
    engine_url = settings.database_url
    sessions = init_db(make_engine(engine_url))
    with sessions() as s:
        fid = ProfileStore(s).propose("Certification AWS Cloud Practitioner").fact.id
        ProfileStore(s).decide(fid, USER_CONFIRMED)
        s.commit()
    reopened = init_db(make_engine(engine_url))
    with reopened() as s:
        assert [f.id for f in ProfileStore(s).confirmed("certification")] == [fid]


def _bot(settings, sessions):
    settings.telegram_bot_token, settings.telegram_chat_id = TOKEN, CHAT
    fake = FakeTelegram()
    bot = Bot(settings, sessions, TelegramClient(TOKEN, transport=httpx.MockTransport(fake)), NullProvider())
    bot.cvb.auto_email = False          # tests that need the automatic email turn it on explicitly
    bot.cvb.background_email = False
    return bot, fake


def _say(bot, text):
    bot.handle_update({"update_id": 1, "message": {"chat": {"id": int(CHAT)}, "text": text}})


def _press(bot, data):
    bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": data,
                                                          "message": {"message_id": 9, "chat": {"id": int(CHAT)}}}})


def test_telegram_cv_profile_flow(settings, sessions):
    assert "cv_profile" in {c for c, _ in COMMANDS}
    bot, fake = _bot(settings, sessions)
    _say(bot, "/cv_profile")
    texts = "\n".join(m["text"] for m in fake.sent)
    assert "Profil CV permanent" in texts and "Vérifié" in texts and "À confirmer" in texts and "RoboDK" in texts
    _say(bot, "/cv_profile add J'ai obtenu une certification IBM")
    assert "Souhaites-tu ajouter" in fake.sent[-1]["text"]
    with sessions() as s:
        fid = next(f.id for f in ProfileStore(s).facts() if "IBM" in f.text)
    _press(bot, f"pf:{fid}:ok")
    assert "confirmé" in fake.sent[-1]["text"]
    _say(bot, "/cv_profile all")
    assert "certification IBM" in fake.sent[-1]["text"]
    _press(bot, f"pf:{fid}:undo")
    with sessions() as s:
        assert ProfileStore(s).get(fid).status == INFERRED
    _press(bot, "pf:99999:ok")
    _say(bot, "/cv_profile add")
    assert "Écris le fait" in fake.sent[-1]["text"]


def test_fact_texts_are_not_logged(settings, sessions, caplog):
    bot, _ = _bot(settings, sessions)
    with caplog.at_level("INFO"):
        _say(bot, "/cv_profile add Certification secrète XYZ-42")
    assert "XYZ-42" not in caplog.text
