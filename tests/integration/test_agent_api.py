"""Agent API (Instinct): bearer auth, listing, conservative status reports."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from summer_hunter.crm.service import CRM
from summer_hunter.web.server import create_app
from tests.integration.test_crm import make_opp

TOKEN = "t" * 40


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, chat_id, text, buttons=None):  # noqa: ANN001
        self.sent.append(text)
        return 1


@pytest.fixture
def env(settings, sessions, monkeypatch):
    monkeypatch.setenv("SH_AGENT_TOKEN", TOKEN)
    settings.telegram_chat_id = "1"
    with sessions() as session:
        crm = CRM(session, settings.data_dir, settings.timezone)
        for n, (company, title) in enumerate([("Philips", "Production Engineering Internship"),
                                              ("Stripe", "Financial Data Analyst Intern")], 1):
            opp = make_opp(session, n, company_name=company, normalized_company=company.lower(), title=title)
            crm.confirm_applied(opp, "cli", applied_on=date.today() - timedelta(days=5))
        session.commit()
    tg = FakeTelegram()
    return TestClient(create_app(settings, sessions, tg)), tg, sessions


H = {"Authorization": f"Bearer {TOKEN}"}


def report(**over):  # noqa: ANN003
    body = {"application": "APP-0001", "status": "REJECTED", "message_id": "<r1@philips.com>",
            "received_at": datetime.now(timezone.utc).isoformat(), "sender": "Philips Careers <careers@philips.com>",
            "subject": "Your application", "evidence": "Unfortunately we will not be moving forward with your application."}
    body.update(over)
    return body


def test_auth_and_listing(env):
    client, _, _ = env
    assert client.get("/agent/v1/applications").status_code == 401
    assert client.get("/agent/v1/applications", headers={"Authorization": "Bearer wrong"}).status_code == 401
    rows = client.get("/agent/v1/applications", headers=H).json()["applications"]
    assert [r["id"] for r in rows] == ["APP-0001", "APP-0002"]


def test_disabled_without_token(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setenv("SH_AGENT_TOKEN", "short")
    assert client.get("/agent/v1/applications", headers={"Authorization": "Bearer short"}).status_code == 404


def test_valid_report_applies_once_and_notifies(env):
    client, tg, sessions = env
    r = client.post("/agent/v1/status", headers=H, json=report())
    assert r.status_code == 200 and r.json()["changed"] is True and len(tg.sent) == 1
    again = client.post("/agent/v1/status", headers=H, json=report())
    assert again.json()["changed"] is False and len(tg.sent) == 1


def test_guards(env):
    client, tg, _ = env
    assert client.post("/agent/v1/status", headers=H, json=report(status="OFFER_ACCEPTED")).status_code == 422
    assert client.post("/agent/v1/status", headers=H, json=report(received_at="2026-11-01T10:00:00")).status_code == 422
    assert client.post("/agent/v1/status", headers=H, json=report(application="APP-0099")).status_code == 404
    wrong = client.post("/agent/v1/status", headers=H, json=report(application="APP-0002", message_id="<x>"))
    assert wrong.status_code == 409 and "Instinct propose" in tg.sent[-1]     # Philips mail, Stripe application
    mixed = client.post("/agent/v1/status", headers=H, json=report(
        status="INTERVIEW", message_id="<y>", evidence="Unfortunately we will not be moving forward with your application."))
    assert mixed.status_code == 409
    old = client.post("/agent/v1/status", headers=H, json=report(
        message_id="<z>", received_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat()))
    assert old.json()["changed"] is False
    assert client.post("/agent/v1/status", headers=H, content=b"x" * 40000).status_code == 413


def test_neutral_evidence_future_time_and_shared_company_are_refused(env, settings):
    client, _, sessions = env
    neutral = client.post("/agent/v1/status", headers=H, json=report(
        message_id="<n1>", evidence="If you are not selected, we will keep your profile."))
    assert neutral.status_code == 409
    future = client.post("/agent/v1/status", headers=H, json=report(
        message_id="<n2>", received_at=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat()))
    assert future.status_code == 422
    with sessions() as session:
        crm = CRM(session, settings.data_dir, settings.timezone)
        opp = make_opp(session, 3, company_name="Philips", normalized_company="philips", title="IT AI Solution Analyst Intern")
        crm.confirm_applied(opp, "cli", applied_on=date.today() - timedelta(days=5))
        session.commit()
    shared = client.post("/agent/v1/status", headers=H, json=report(message_id="<n3>"))
    assert shared.status_code == 409          # two Philips applications, the mail names no job
    named = client.post("/agent/v1/status", headers=H, json=report(
        message_id="<n4>", subject="Philips - Production Engineering Internship"))
    assert named.json()["changed"] is True


def test_failed_logins_never_lock_out_the_agent(env):
    client, _, _ = env
    for _ in range(30):
        client.get("/agent/v1/applications", headers={"Authorization": "Bearer nope"})
    assert client.get("/agent/v1/applications", headers=H).status_code == 200
    assert client.get("/agent/v1/applications", headers=H).headers["cache-control"] == "no-store"
