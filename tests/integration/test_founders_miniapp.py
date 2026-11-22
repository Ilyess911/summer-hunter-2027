"""Mini App Réseau tab: founders list, marks, run guard (22 Nov 2026)."""

from __future__ import annotations

import json

from starlette.testclient import TestClient

from summer_hunter.crm import founders
from summer_hunter.database.models import CRMContact, LLMUsage
from summer_hunter.database.repository import Repository
from summer_hunter.web.server import create_app
from tests.integration.test_webapp import TOKEN, USER, init_data


def _seed(sessions, n=2):  # noqa: ANN001, ANN202
    ids = []
    with sessions() as s:
        for k in range(n):
            notes = {"city": "Canggu", "city_verified": True, "country": "ID", "sector": "branding", "what": "Studio",
                     "why": "Petite équipe", "website": "", "message": f"Bonjour P{k}", "linkedin_search": "https://www.linkedin.com/search/x"}
            row = CRMContact(company=f"Co{k}", name=f"P{k} Nom", normalized_name=f"p{k} nom", contact_type="Other",
                             interaction_status="TO_CONTACT", source="https://ex.example/a", category="founder_abroad",
                             verification="verified", dismissed=False, notes=json.dumps(notes))
            s.add(row)
            s.flush()
            ids.append(row.id)
        Repository(s).set_kv(founders.KV_DAY + founders.today().isoformat(), json.dumps(ids))
        s.commit()
    return ids


def _client(settings, sessions):  # noqa: ANN001, ANN202
    settings.telegram_bot_token, settings.telegram_chat_id = TOKEN, USER
    return TestClient(create_app(settings, sessions, None)), {"X-Telegram-Init-Data": init_data()}


def test_founders_api_requires_auth(settings, sessions):
    client, _ = _client(settings, sessions)
    assert client.get("/api/founders").status_code == 401


def test_list_filters_and_marks(settings, sessions):
    ids = _seed(sessions)
    client, h = _client(settings, sessions)
    data = client.get("/api/founders", headers=h).json()
    assert data["counts"] == {"todo": 2, "sent": 0, "all": 2}
    assert data["today"]["done"] and not data["today"]["can_run"]
    assert all(x["today"] for x in data["items"]) and data["items"][0]["country"]
    r = client.post(f"/api/founders/{ids[0]}", headers=h, json={"action": "sent"}).json()
    assert r["item"]["contacted"]
    client.post(f"/api/founders/{ids[1]}", headers=h, json={"action": "no"})
    data = client.get("/api/founders?filter=sent", headers=h).json()
    assert [x["id"] for x in data["items"]] == [ids[0]] and data["counts"]["todo"] == 0
    r = client.post(f"/api/founders/{ids[1]}", headers=h, json={"action": "undo"}).json()
    assert not r["item"]["dismissed"] and r["item"]["status"] == "TO_CONTACT"
    assert client.post(f"/api/founders/{ids[1]}", headers=h, json={"action": "bogus"}).status_code == 400
    assert client.post("/api/founders/99999", headers=h, json={"action": "sent"}).status_code == 404


def test_run_is_refused_when_done_or_over_budget(settings, sessions):
    client, h = _client(settings, sessions)
    settings.anthropic_api_key = "test"
    with sessions() as s:
        s.add(LLMUsage(local_date=founders.today().isoformat(), provider="a", model="m", purpose="founder_scout", cost_usd=0.2))
        s.commit()
    r = client.post("/api/founders/run", headers=h, json={})
    assert r.status_code == 409 and "plafond" in r.json()["error"]
    _seed(sessions, 1)
    assert "déjà faite" in client.post("/api/founders/run", headers=h, json={}).json()["error"]
