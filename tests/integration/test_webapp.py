"""Mini App: Telegram initData auth, endpoints, signed downloads, LaTeX safety."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from summer_hunter.database.repository import Repository
from summer_hunter.integrations.anthropic_client import NullProvider
from summer_hunter.models.opportunity import RawOpportunity
from summer_hunter.pipeline import CycleStats, Hunter
from summer_hunter.web import auth
from summer_hunter.web.cv import render, tailor, tex
from summer_hunter.web.server import create_app
from tests.conftest import FILLER

TOKEN = "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ"
USER = "424242"


def init_data(user_id=USER, age=0):
    return auth.sign_init_data({"auth_date": str(int(time.time()) - age), "query_id": "q",
                                "user": json.dumps({"id": int(user_id), "first_name": "I"})}, TOKEN)


@pytest.fixture
def client(settings, sessions):
    settings.telegram_bot_token, settings.telegram_chat_id = TOKEN, USER
    hunter = Hunter(settings, sessions, provider=NullProvider())
    with sessions() as session:
        repo = Repository(session)
        raw = RawOpportunity(source="manual", source_id="1", url="https://jobs.example.com/1", title="Solutions Engineer Intern",
                             company="Acme & Co", location_text="Singapore", description_text=FILLER)
        hunter.evaluate(repo, hunter.ingest(repo, raw, CycleStats()), None, CycleStats())
        session.commit()
    return TestClient(create_app(settings, sessions, None))


def test_api_requires_valid_telegram_init_data(client):
    assert client.get("/api/opportunities").status_code == 401
    assert client.get("/api/opportunities", headers={"X-Telegram-Init-Data": "user=%7B%7D&hash=bad"}).status_code == 401
    assert client.get("/api/opportunities", headers={"X-Telegram-Init-Data": init_data(user_id="999")}).status_code == 401
    assert client.get("/api/opportunities", headers={"X-Telegram-Init-Data": init_data(age=3 * 86400)}).status_code == 401
    ok = client.get("/api/opportunities?view=top", headers={"X-Telegram-Init-Data": init_data()})
    assert ok.status_code == 200 and ok.json()["items"][0]["company"] == "Acme & Co"


def test_detail_action_and_cv_flow(client, settings):
    h = {"X-Telegram-Init-Data": init_data()}
    d = client.get("/api/opportunities/1", headers=h).json()
    assert d["criteria"] and d["fit_report"]["score"] == d["fit"]
    assert client.post("/api/opportunities/1/action", headers=h, json={"action": "save"}).json()["user_status"] == "saved"
    assert client.post("/api/opportunities/1/action", headers=h, json={"action": "hack"}).status_code == 400
    r = client.post("/api/opportunities/1/cv", headers=h).json()
    tex_url = r["files"]["tex"]
    dl = client.get(tex_url)
    assert dl.status_code == 200 and b"\\documentclass" in dl.content
    assert b"Acme \\& Co" in dl.content
    # tampered or expired links are refused
    assert client.get(tex_url.replace("sig=", "sig=0")).status_code == 403
    path = tex_url.split("?")[0]
    assert client.get(f"{path}?exp=1&sig=x").status_code == 403


def test_signed_link_cannot_escape_cv_folder(client, settings):
    link = auth.sign_link("/files/cv/1/..%2F..%2Ftest.db", TOKEN)
    assert client.get(link).status_code in (403, 404)


def test_latex_injection_is_escaped():
    evil = r"\input{/etc/passwd} \write18{rm -rf ~} $x$ #1 % _"
    assert "\\input" not in tex(evil) and "\\write18" not in tex(evil)
    cv = tailor({"header": {"name": "N"}, "experiences": [], "skills": {}}, evil, evil, "technical_sales")
    doc = render(cv, evil)
    assert "\\input{/etc" not in doc and "\\write18{" not in doc


def test_tailoring_reorders_without_inventing():
    content = {"headlines": {"technical_sales": "TS", "default": "D"},
               "experiences": [{"org": "A", "role": "R", "bullets": [{"text": "Organised events", "tags": []},
                                                                       {"text": "Ran customer demos", "tags": ["demo"]}]}],
               "skills": {"S": ["cooking", "customer discovery"]}}
    cv = tailor(content, "Sales Engineer Intern", "customer demo discovery", "technical_sales")
    assert cv["headline"] == "TS"
    assert cv["experiences"][0]["bullets"][0]["text"] == "Ran customer demos"
    assert cv["skills"]["S"][0] == "customer discovery"
    texts = {b["text"] for e in cv["experiences"] for b in e["bullets"]}
    assert texts <= {"Organised events", "Ran customer demos"}


def test_init_data_with_signature_field_is_accepted(client):
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": int(USER)}), "signature": "abc-ed25519"}
    assert client.get("/api/opportunities", headers={"X-Telegram-Init-Data": auth.sign_init_data(fields, TOKEN)}).status_code == 200


def test_industry_track_swaps_summary_skills_and_reports_gaps_without_adding_them():
    content = {"summary": "Sales profile", "industry_families": ["industrial_operations"],
               "experiences": [{"org": "A", "bullets": [{"text": "Ran customer demos"},
                                                         {"text": "Analyzed MRO repair data", "tracks": ["industry"]}]},
                               {"org": "B", "bullets": [{"text": "Kept CRM data"}]}],
               "projects": [{"name": "Events", "tracks": ["business"], "bullets": [{"text": "Organised events"}]},
                            {"name": "RUL", "tracks": ["industry"], "bullets": [{"text": "Predicted engine life"}]}],
               "skills": {"Sales": ["CRM"]},
               "tracks": {"industry": {"summary": "Industrial profile", "experience_order": ["A"],
                                       "max_bullets": {"A": 1}, "skills": {"Industrial": ["5S"]}}}}
    job = "Apply lean and Kaizen in MRO repair processes; 3D printing software and AI familiarity."
    cv = tailor(content, "Manufacturing Intern", job, "industrial_operations")
    assert cv["summary"] == "Industrial profile" and list(cv["skills"]) == ["Industrial"]
    assert [e["org"] for e in cv["experiences"]] == ["A"]
    assert [b["text"] for b in cv["experiences"][0]["bullets"]] == ["Analyzed MRO repair data"]
    assert [p["name"] for p in cv["projects"]] == ["RUL"]
    assert cv["gaps"] == ["Lean", "Kaizen", "3D printing / additive manufacturing"]
    business = tailor(content, "Sales Intern", "customer demos", "technical_sales")
    assert business["summary"] == "Sales profile" and [p["name"] for p in business["projects"]] == ["Events"]
    assert all(b["text"] != "Analyzed MRO repair data" for e in business["experiences"] for b in e["bullets"])


def test_real_cv_mentions_toeic_once_on_both_tracks():
    from summer_hunter.web.cv import load_content
    content = load_content(Path(__file__).resolve().parents[2] / "config")
    for family in ("technical_sales", "industrial_operations"):
        doc = render(tailor(content, "Intern", "", family), "X")
        assert doc.count("TOEIC") == 1
