"""Telegram digest, cards, commands and callbacks against a fake Bot API."""

from __future__ import annotations

import json

import httpx
import pytest

from summer_hunter.bot.handlers import Bot
from summer_hunter.database.repository import Repository
from summer_hunter.integrations.anthropic_client import NullProvider
from summer_hunter.integrations.telegram_client import TelegramClient, TelegramError
from summer_hunter.models.opportunity import RawOpportunity
from summer_hunter.notifications.notifier import Notifier
from summer_hunter.pipeline import CycleStats, Hunter
from tests.conftest import FILLER

TOKEN = "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ"
CHAT = "424242"


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.calls: list[str] = []
        self.next_id = 1

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(method)
        body = json.loads(request.content or b"{}")
        if method == "sendMessage":
            self.sent.append(body)
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        return httpx.Response(200, json={"ok": True, "result": True})


@pytest.fixture
def world(settings, sessions):
    settings.telegram_bot_token, settings.telegram_chat_id = TOKEN, CHAT
    hunter = Hunter(settings, sessions, provider=NullProvider())
    stats = CycleStats()
    with sessions() as session:
        repo = Repository(session)
        good = RawOpportunity(source="manual", source_id="1", url="https://jobs.example.com/1", apply_url="https://jobs.example.com/1/apply",
                              title="Solutions Engineer Intern <script>", company="Stripe", location_text="Singapore",
                              description_text=FILLER + "The internship runs from June 1 to August 20, 2027. Visa sponsorship is available.")
        bad = RawOpportunity(source="manual", source_id="2", url="https://jobs.example.com/2", title="Sales Engineer Intern",
                             company="Acme", location_text="Paris, France", description_text=FILLER)
        for raw in (good, bad):
            hunter.evaluate(repo, hunter.ingest(repo, raw, stats), None, stats)
        session.commit()
    fake = FakeTelegram()
    client = TelegramClient(TOKEN, transport=httpx.MockTransport(fake))
    return settings, sessions, client, fake


def test_digest_cards_and_dedup(world):
    settings, sessions, client, fake = world
    notifier = Notifier(settings, sessions, client)
    report = notifier.morning_digest()
    assert report.sent >= 2 and not report.errors
    digest, card = fake.sent[0], fake.sent[1]
    assert "MORNING DIGEST" in digest["text"] and "Discovered: <b>2</b>" in digest["text"]
    assert "Rejected: 1" in digest["text"]
    assert "&lt;script&gt;" in card["text"] and "<script>" not in card["text"]   # escaped
    assert "Career Fit:" in card["text"] and "Eligibility:" in card["text"] and "Networking:" in card["text"]
    buttons = [b for row in card["reply_markup"]["inline_keyboard"] for b in row]
    labels = {b["text"] for b in buttons}
    assert {"Apply", "Save", "Dismiss", "Applied", "Explain Score", "Networking msg"} <= labels
    assert all(len(b.get("callback_data", "")) <= 64 for b in buttons)
    assert "Paris" not in "".join(m["text"] for m in fake.sent[1:])          # rejected never carded
    # Second run the same day: nothing re-sent.
    again = notifier.morning_digest()
    assert again.sent == 0 and again.skipped_duplicates >= 2
    assert notifier.digest_sent_today()


def test_commands_and_callbacks(world):
    settings, sessions, client, fake = world
    bot = Bot(settings, sessions, client, NullProvider())
    for cmd in ("start", "help", "top10", "today", "stats", "saved", "applied", "rejected", "status", "settings"):
        bot.handle_update({"update_id": 1, "message": {"chat": {"id": int(CHAT)}, "text": f"/{cmd}"}})
    texts = "\n".join(m["text"] for m in fake.sent)
    assert "Top 10" in texts and "Recently rejected" in texts and "Settings" in texts
    assert TOKEN not in texts

    def press(data):
        bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "data": data,
                                                              "message": {"message_id": 5, "chat": {"id": int(CHAT)}}}})

    with sessions() as session:
        opp_id = Repository(session).top(1)[0].id
    press(f"sv:{opp_id}")
    press(f"ex:{opp_id}")
    press(f"nm:{opp_id}")
    press(f"dm:{opp_id}")
    press(f"rs:{opp_id}:cold")
    press("zz:notanumber")
    with sessions() as session:
        repo = Repository(session)
        opp = repo.opportunity(opp_id)
        assert opp.user_status == "dismissed"
        actions = [f.action for f in repo.feedback_rows()]
        assert actions == ["save", "dismiss", "reason"]
        prefs = {(p.dimension, p.key): p.delta for p in repo.preference_adjustments()}
        assert prefs[("pattern", "cold_calling")] < 0
    texts = "\n".join(m["text"] for m in fake.sent)
    assert "Score breakdown" in texts
    assert "Draft networking message" in texts and "Not sent" in texts


def test_unauthorized_chat_is_ignored(world):
    settings, sessions, client, fake = world
    bot = Bot(settings, sessions, client, NullProvider())
    bot.handle_update({"update_id": 1, "message": {"chat": {"id": 999}, "text": "/top10"}})
    bot.handle_update({"update_id": 2, "callback_query": {"id": "x", "data": "sv:1", "message": {"chat": {"id": 999}}}})
    assert fake.sent == []
    with sessions() as session:
        assert Repository(session).feedback_rows() == []


def test_telegram_errors_never_leak_token():
    def fail(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = TelegramClient(TOKEN, transport=httpx.MockTransport(fail))
    with pytest.raises(TelegramError) as exc:
        client.send_message(CHAT, "hi")
    assert TOKEN not in str(exc.value)


def test_digest_without_telegram_prints(settings, sessions):
    report = Notifier(settings, sessions, None).morning_digest()
    assert report.dry_run and report.messages and report.sent == 0
