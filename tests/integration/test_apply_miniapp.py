"""Mini App auto-apply: offer support, start guard, and the write-only account password (22 Nov 2026)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from summer_hunter.apply import service as auto
from summer_hunter.apply import workday
from summer_hunter.web.server import create_app
from tests.integration.test_webapp import TOKEN, USER, init_data


@pytest.fixture()
def vault(monkeypatch):  # noqa: ANN001, ANN201
    store: dict[str, str] = {}
    monkeypatch.setattr(workday, "keychain_get", lambda acct: store.get(acct))
    monkeypatch.setattr(workday, "keychain_set", lambda acct, pw: store.__setitem__(acct, pw))
    return store


def _client(settings, sessions, telegram=None):  # noqa: ANN001, ANN202
    settings.telegram_bot_token, settings.telegram_chat_id = TOKEN, USER
    return TestClient(create_app(settings, sessions, telegram)), {"X-Telegram-Init-Data": init_data()}


def test_account_requires_auth(settings, sessions, vault) -> None:  # noqa: ANN001
    client, _ = _client(settings, sessions)
    assert client.get("/api/apply/account").status_code == 401


def test_account_password_is_write_only(settings, sessions, vault) -> None:  # noqa: ANN001
    client, h = _client(settings, sessions)
    assert client.get("/api/apply/account", headers=h).json()["password_set"] is False
    bad = client.post("/api/apply/account", headers=h, json={"email": "nope", "password": ""})
    assert bad.status_code == 400
    weak = client.post("/api/apply/account", headers=h, json={"email": "me@example.com", "password": "abcdefgh"})
    assert weak.status_code == 400 and "majuscule" in weak.json()["error"]
    ok = client.post("/api/apply/account", headers=h, json={"email": "jobs@example.com", "password": "Strong#Pass9"})
    assert ok.status_code == 200 and "Strong" not in ok.text
    got = client.get("/api/apply/account", headers=h).json()
    assert got == {"email": "jobs@example.com", "password_set": True}
    assert vault[workday.DEFAULT_ACCOUNT] == "Strong#Pass9"
    assert auto.account_email(settings.data_dir, "cv@example.com") == "jobs@example.com"
    keep = client.post("/api/apply/account", headers=h, json={"email": "jobs2@example.com", "password": ""})
    assert keep.status_code == 200 and vault[workday.DEFAULT_ACCOUNT] == "Strong#Pass9"   # empty keeps it


def test_offer_support_and_start_guard(settings, sessions, vault) -> None:  # noqa: ANN001
    from summer_hunter.database.models import Opportunity

    opp = Opportunity(canonical_key="k1", company_name="Acme", normalized_company="acme", title="Intern",
                      normalized_title="intern", official_url="https://acme.wd1.myworkdayjobs.com/Ext/job/Paris/Intern_JR1",
                      apply_url="https://acme.wd1.myworkdayjobs.com/Ext/job/Paris/Intern_JR1", content_hash="h")
    with sessions() as s:
        s.add(opp)
        s.commit()
        opp_id = opp.id
    client, h = _client(settings, sessions)
    a = client.get(f"/api/apply/{opp_id}", headers=h).json()
    assert a["supported"] and a["platform"] == "Workday" and a["account"] is True
    assert a["can_start"] is False                                  # no Telegram client in this process: no start
    assert client.post(f"/api/apply/{opp_id}", headers=h, json={}).status_code == 409
    auto.save_state(settings.data_dir, opp_id, {"opp_id": opp_id, "status": "submitted"})
    client2, h2 = _client(settings, sessions, telegram=object())
    assert client2.post(f"/api/apply/{opp_id}", headers=h2, json={}).status_code == 409   # already sent
