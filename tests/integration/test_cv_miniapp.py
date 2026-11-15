"""Mini App CV tab: AI CV Builder API (auth, generate, edit, history, profile, signed downloads)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from summer_hunter.web.server import create_app
from tests.integration.test_cv_document import needs_tex
from tests.integration.test_webapp import TOKEN, USER, init_data


class FakeTG:
    def __init__(self) -> None:
        self.docs: list[str] = []

    def send_document(self, chat_id, path, caption=""):  # noqa: ANN001
        self.docs.append(path.name)
        return 1


@pytest.fixture
def app(settings, sessions):
    settings.telegram_bot_token, settings.telegram_chat_id = TOKEN, USER
    tg = FakeTG()
    return TestClient(create_app(settings, sessions, tg)), {"X-Telegram-Init-Data": init_data()}, tg


def test_cv_api_requires_auth(app):
    client, _, _ = app
    for path in ("/api/cv/history", "/api/cv/profile"):
        assert client.get(path).status_code == 401
    assert client.post("/api/cv", json={"text": "Tech Sales"}).status_code == 401


def test_profile_tab(app):
    client, h, _ = app
    p = client.get("/api/cv/profile", headers=h).json()
    assert any(f["text"] == "RoboDK" for f in p["pending"]) and p["confirmed"] == []
    fid = next(f["id"] for f in p["pending"] if f["text"] == "RoboDK")
    assert client.post(f"/api/cv/profile/{fid}", headers=h, json={"decision": "ok"}).json()["status"] == "USER_CONFIRMED"
    assert client.post(f"/api/cv/profile/{fid}", headers=h, json={"decision": "hack"}).status_code == 400
    added = client.post("/api/cv/profile", headers=h, json={"text": "Certification AWS Cloud Practitioner"}).json()
    assert added["created"] and added["status"] == "INFERRED" and added["kind"] == "Certification"
    assert client.post("/api/cv/profile/99999", headers=h, json={"decision": "ok"}).status_code == 404


def test_bad_inputs(app):
    client, h, _ = app
    assert client.post("/api/cv", headers=h, json={"text": " "}).status_code == 400
    assert client.post("/api/cv", headers=h, json={"text": "x" * 20001}).status_code == 400
    assert client.get("/api/cv/999", headers=h).status_code == 404


@needs_tex
def test_generate_edit_history_download(app):
    client, h, tg = app
    v = client.post("/api/cv", headers=h, json={"text": "stage Tech Sales chez Salesforce à Dublin", "language": "fr", "pages": "1"}).json()
    assert v["tag"] == "CV-0001 v1" and v["company"] == "Salesforce" and v["location"] == "Ireland"
    assert v["language"] == "fr" and v["ok"] and v["report"] and set(v["files"]) == {"pdf", "docx"}
    pdf = client.get(v["files"]["pdf"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert client.get(v["files"]["docx"]).content[:2] == b"PK"
    assert client.get(v["files"]["pdf"].split("?")[0]).status_code == 403           # unsigned
    assert client.get(f"/files/cvb/{v['id']}/tex?exp=9999999999&sig=x").status_code == 403

    r = client.post(f"/api/cv/{v['id']}/edit", headers=h, json={"text": "en anglais, retire Amundi"}).json()
    assert r["version"]["tag"] == "CV-0001 v2" and r["version"]["language"] == "en"
    assert any("retirer" in u for u in r["understood"])
    bad = client.post(f"/api/cv/{v['id']}/edit", headers=h, json={"text": "bonjour"})
    assert bad.status_code == 422 and bad.json()["examples"]
    fact = client.post(f"/api/cv/{v['id']}/edit", headers=h, json={"text": "J'ai obtenu une certification IBM"}).json()
    assert fact["facts"] and "version" not in fact

    regen = client.post(f"/api/cv/{r['version']['id']}/regenerate", headers=h).json()
    assert regen["tag"] == "CV-0001 v3"
    assert client.post(f"/api/cv/{regen['id']}/save", headers=h).json()["saved"] is True
    hist = client.get("/api/cv/history", headers=h).json()["items"]
    assert len(hist) == 1 and [x["version"] for x in hist[0]["versions"]] == [1, 2, 3] and hist[0]["saved"]
    assert client.post(f"/api/cv/{v['id']}/send", headers=h).json()["sent"] == 2 and len(tg.docs) == 2
