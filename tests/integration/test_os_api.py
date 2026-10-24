"""Summer Hunter OS API: auth, CRM, networking, timing, messages, analytics, hosted sync."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from summer_hunter.database.migrations import init_db, make_engine
from summer_hunter.database.models import Contact, ContactEvidence, CRMContact, Interaction, Opportunity
from summer_hunter.os import messages as msg
from summer_hunter.os import networking as net
from summer_hunter.os import security
from summer_hunter.os.api import create_app
from summer_hunter.utils.settings import load_yaml

PASSWORD = "correct horse battery"
H = {security.CSRF_HEADER: "1"}
DATES = "The internship runs from April 6 to August 28, 2027. "


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SH_OS_SECRET", "s" * 48)
    monkeypatch.setenv("SH_OS_PASSWORD_HASH", security.hash_password(PASSWORD, n=2 ** 10))
    monkeypatch.setenv("SH_SYNC_TOKEN", "t" * 40)
    security._ATTEMPTS.clear()


@pytest.fixture
def cfg(settings):
    return load_yaml(settings.config_dir / "networking.yaml")


def seed(sessions, hunter):
    from summer_hunter.models.opportunity import ContactCandidate, RawOpportunity, Relationship
    from summer_hunter.pipeline import CycleStats

    stats = CycleStats()
    with sessions() as s:
        from summer_hunter.database.repository import Repository

        repo = Repository(s)
        for i, (title, loc, extra) in enumerate([
            ("Robotics Deployment Intern", "Munich, Germany", "Deploy robots, customer onboarding, KPI tracking. Pay: €1,500 per month."),
            ("Maintenance Engineering Intern", "Casablanca, Morocco", "Maintenance KPIs and data analysis. Gratification 6 000 MAD par mois."),
            ("Solutions Engineer Intern", "Singapore", "Technical demos, discovery calls with enterprise customers."),
        ]):
            raw = RawOpportunity(source="greenhouse:acme", source_id=str(i), url=f"https://jobs.example.com/{i}", title=title,
                                 company=["Formic", "Royal Air Maroc", "Stripe"][i], location_text=loc, description_text=DATES + extra,
                                 contacts=[ContactCandidate(name="Jane Doe", role="University Recruiter", profile_url="https://www.linkedin.com/in/janedoe",
                                                            source_post_url="https://www.linkedin.com/posts/janedoe-hiring",
                                                            relationship=Relationship.RECRUITER_POST, confidence=0.9,
                                                            evidence="We are hiring interns for summer 2027")] if i == 0 else [])
            opp = hunter.ingest(repo, raw, stats)
            hunter.evaluate(repo, opp, None, stats)
        s.commit()


@pytest.fixture
def client(settings, sessions, hunter):
    seed(sessions, hunter)
    c = TestClient(create_app(settings, sessions, hosted=False))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


# Auth ------------------------------------------------------------------------------------------------
def test_authentication_and_authorization(settings, sessions):
    c = TestClient(create_app(settings, sessions, hosted=False))
    assert c.get("/api/health").json()["ok"]
    assert c.get("/api/opportunities").status_code == 401                         # no session
    assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    assert c.get("/api/opportunities").status_code == 200
    assert c.post("/api/opportunities/1/track").status_code == 403                 # CSRF header missing
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get("/api/meta").status_code == 401
    forged = TestClient(create_app(settings, sessions, hosted=False))
    forged.cookies.set(security.COOKIE, "v1.9999999999.x.deadbeef")
    assert forged.get("/api/meta").status_code == 401


def test_login_is_rate_limited(settings, sessions):
    c = TestClient(create_app(settings, sessions, hosted=False))
    codes = [c.post("/api/auth/login", json={"password": "nope"}).status_code for _ in range(10)]
    assert codes[-1] == 429


def test_password_hash_and_session_expiry():
    stored = security.hash_password("long enough password", n=2 ** 10)
    assert security.verify_password("long enough password", stored) and not security.verify_password("other", stored)
    token = security.make_session(now=1000)
    assert security.valid_session(token, now=1001) and not security.valid_session(token, now=1000 + security.SESSION_SECONDS + 1)
    with pytest.raises(ValueError):
        security.hash_password("short")


# CRM ---------------------------------------------------------------------------------------------------
def test_opportunities_list_filters_and_detail(client):
    data = client.get("/api/opportunities").json()
    assert data["total"] == 3 and {i["company"] for i in data["items"]} == {"Formic", "Royal Air Maroc", "Stripe"}
    assert client.get("/api/opportunities", params={"region": "africa"}).json()["total"] == 1
    assert client.get("/api/opportunities", params={"paid": True}).json()["total"] == 2
    assert client.get("/api/opportunities", params={"page_size": 1, "page": 2}).json()["items"][0]
    assert client.get("/api/opportunities", params={"min_recruit": 101}).status_code == 422          # validated
    first = data["items"][0]
    detail = client.get(f"/api/opportunities/{first['id']}").json()
    assert detail["criteria"] and "compensation_report" in detail and detail["description"]
    assert client.get("/api/opportunities/999").status_code == 404


def test_application_tracking_and_kanban(client):
    opp = client.get("/api/opportunities", params={"q": "Robotics"}).json()["items"][0]
    app_id = client.post(f"/api/opportunities/{opp['id']}/track", headers=H).json()["application_id"]
    assert client.post(f"/api/opportunities/{opp['id']}/track", headers=H).json() == {"application_id": app_id, "created": False}
    assert client.post(f"/api/applications/{app_id}/status", json={"status": "PREPARING"}, headers=H).json()["status"] == "PREPARING"
    r = client.post(f"/api/applications/{app_id}/status", json={"status": "APPLIED"}, headers=H)
    assert r.status_code == 409                                                    # submission must be confirmed
    r = client.post(f"/api/applications/{app_id}/status", json={"status": "APPLIED", "confirm_submission": True}, headers=H)
    assert r.json()["status"] == "APPLIED" and r.json()["date_applied"]
    assert client.post(f"/api/applications/{app_id}/status", json={"status": "TO_APPLY"}, headers=H).status_code == 422
    assert client.post(f"/api/applications/{app_id}/interview", json={"at": "2026-11-05 14:00"}, headers=H).json()["status"] == "INTERVIEW"
    client.patch(f"/api/applications/{app_id}", json={"notes": "Called by Jane", "next_action_date": "2026-11-01"}, headers=H)
    detail = client.get(f"/api/applications/{app_id}").json()
    assert detail["notes"] == "Called by Jane" and any(e["type"] == "interview_scheduled" for e in detail["timeline"])
    board = client.get("/api/pipeline").json()
    assert [c["status"] for c in board["columns"]][:3] == ["DISCOVERED", "SHORTLISTED", "TO_APPLY"]
    assert any(i["id"] == app_id and i["status"] == "INTERVIEW" for i in board["items"])


# Networking --------------------------------------------------------------------------------------------
def test_public_contacts_are_imported_once_and_ranked(client, sessions):
    out = client.post("/api/networking/refresh", headers=H).json()
    assert out["created"] == 1 and out["scored"] >= 1
    again = client.post("/api/networking/refresh", headers=H).json()
    assert again["created"] == 0
    with sessions() as s:
        assert s.query(CRMContact).count() == 1                                   # deduplicated
    contacts = client.get("/api/contacts").json()
    jane = contacts[0]
    assert jane["category"] == "university_recruiter" and jane["verification"] == "verified" and jane["score"] >= 70
    assert "tied to this opening" in jane["explanation"]


def test_contact_dedup_across_opportunities_and_manual_add(client, sessions):
    opps = client.get("/api/opportunities").json()["items"]
    stripe = next(o for o in opps if o["company"] == "Stripe")
    body = {"company": "Stripe", "name": "Alex Martin", "position": "Solutions Engineering Manager",
            "linkedin_url": "https://www.linkedin.com/in/alexmartin", "category": "hiring_manager", "opportunity_id": stripe["id"]}
    a = client.post("/api/contacts", json=body, headers=H).json()
    b = client.post("/api/contacts", json=body | {"name": "alex  martin"}, headers=H).json()
    assert a["id"] == b["id"]
    bad = client.post("/api/contacts", json=body | {"linkedin_url": "https://evil.example.com/in/x"}, headers=H)
    assert bad.status_code == 422
    assert client.post("/api/contacts", json=body | {"category": "ceo_friend"}, headers=H).status_code == 422


def test_search_results_stay_unverified_until_confirmed(sessions, settings, cfg, hunter):
    seed(sessions, hunter)
    with sessions() as s:
        opp = s.query(Opportunity).filter_by(company_name="Stripe").one()
        n = net.add_search_candidates(s, opp, [
            {"url": "https://www.linkedin.com/in/sam-lee", "title": "Sam Lee - University Recruiter at Stripe | LinkedIn", "snippet": "Stripe"},
            {"url": "https://www.linkedin.com/in/other", "title": "Other Person - Recruiter at Acme | LinkedIn", "snippet": "Acme"},
            {"url": "https://example.com/in/x", "title": "Bad - Recruiter at Stripe"}])
        assert n == 1
        sam = s.query(CRMContact).filter_by(name="Sam Lee").one()
        assert sam.verification == "unverified"
        net.recompute(s, cfg)
        link = s.query(net.ContactOpportunity).filter_by(contact_id=sam.id).one()
        assert link.timing_confidence == "low" and "not confirmed" in link.explanation
        net.confirm_contact(s, sam)
        assert sam.verification == "verified"


def test_contact_ranking_rules(cfg):
    opp = Opportunity(id=1, title="Robotics Deployment Intern", company_name="Formic", career_track="industry_robotics")
    vp = CRMContact(id=1, name="Vic P", company="Formic", position="VP Operations", category="team_manager", verification="verified",
                    linkedin_url="https://www.linkedin.com/in/vic")
    rec = CRMContact(id=2, name="Rae C", company="Formic", position="University Recruiter", category="university_recruiter",
                     verification="verified", linkedin_url="https://www.linkedin.com/in/rae")
    ev = [ContactEvidence(contact_id=2, opportunity_id=1, kind="vacancy", url="u", verified=True,
                          retrieved_at=datetime.now(timezone.utc))]
    s_vp, s_rec = net.score_contact(vp, opp, [], cfg), net.score_contact(rec, opp, ev, cfg)
    assert s_rec.score > s_vp.score and any("Senior title" in x for x in s_vp.explanation)
    assert set(s_rec.components) == {"involvement", "relevance", "connection", "recruiting_activity", "accessibility"}


def test_timing_engine(cfg):
    from summer_hunter.database.models import CRMApplication

    opp = Opportunity(id=1, title="Robotics Deployment Intern", company_name="Formic", application_deadline=None)
    today = date(2026, 10, 26)       # Monday
    hm = CRMContact(id=1, name="H M", company="Formic", category="hiring_manager", verification="verified")
    rec = CRMContact(id=2, name="R C", company="Formic", category="talent_acquisition", verification="verified")
    alum = CRMContact(id=3, name="A L", company="Formic", category="alumni_esilv", verification="verified")
    assert net.timing(hm, opp, None, [], cfg, today).action == "CONTACT_BEFORE_APPLICATION"
    t = net.timing(rec, opp, None, [], cfg, today)
    assert t.action == "CONTACT_AFTER_APPLICATION" and "Apply first" in t.reason
    applied = CRMApplication(opportunity_id=1, status="APPLIED", date_applied=today)
    assert net.timing(rec, opp, applied, [], cfg, today).on == date(2026, 10, 28)
    assert "not for a referral" in net.timing(alum, opp, None, [], cfg, today).reason
    sent = [Interaction(contact_id=1, kind="message_sent", occurred_at=datetime(2026, 10, 26, 9, tzinfo=timezone.utc))]
    assert net.timing(hm, opp, None, sent, cfg, today).action == "WAIT"
    assert net.timing(hm, opp, None, sent, cfg, date(2026, 11, 2)).action == "FOLLOW_UP"
    twice = sent + [Interaction(contact_id=1, kind="message_sent", occurred_at=datetime(2026, 11, 2, 9, tzinfo=timezone.utc))]
    assert net.timing(hm, opp, None, twice, cfg, date(2026, 11, 20)).action == "DO_NOT_CONTACT"      # one follow-up only
    replied = sent + [Interaction(contact_id=1, kind="reply", occurred_at=datetime(2026, 10, 27, tzinfo=timezone.utc))]
    assert net.timing(hm, opp, None, replied, cfg, today).action == "WAIT"
    rejected = CRMApplication(opportunity_id=1, status="REJECTED", date_applied=today)
    assert net.timing(hm, opp, rejected, [], cfg, today).action == "DO_NOT_CONTACT"


# Messages ----------------------------------------------------------------------------------------------
def _ctx():
    opp = Opportunity(id=1, title="Robotics Deployment Intern (Summer 2027)", company_name="Formic", career_track="industry_robotics",
                      country_code="US", description_text="Own customer onboarding and KPI tracking.")
    hm = CRMContact(id=1, name="Jane Doe", company="Formic", category="hiring_manager", verification="verified")
    post = [ContactEvidence(contact_id=1, opportunity_id=1, kind="public_post", url="https://linkedin.com/posts/x", verified=True)]
    return opp, hm, post


def test_personalized_message_uses_evidence(cfg):
    opp, hm, post = _ctx()
    d = msg.compose("before_applying", hm, opp, None, post, [], cfg)
    assert d.validation["ok"], d.validation
    assert "your post about the Robotics Deployment internship" in d.text and "Air France Industries" in d.text
    assert d.personalization == "full" and {r["kind"] for r in d.evidence_refs} >= {"public_post"}
    assert 60 <= d.validation["words"] <= 125 and "—" not in d.text
    for banned in ("hope you're doing well", "passionate", "love to connect"):
        assert banned not in d.text.lower()


def test_no_evidence_means_limited_personalization_and_no_false_claims(cfg):
    opp, hm, _ = _ctx()
    bare = Opportunity(id=1, title=opp.title, company_name="Formic", career_track="industry_robotics", country_code="US")
    d = msg.compose("before_applying", hm, bare, None, [], [], cfg)
    assert d.personalization == "limited" and "your post" not in d.text.lower()
    fake = msg.Draft(text="Hi Jane, I saw your post and I applied already. We both know Paul from Siemens Industries? Ilyess",
                     kind="initial", variant="default", language="en")
    issues = msg.validate(fake, hm, None, [], [], cfg)["issues"]
    assert any("post that was not retrieved" in i for i in issues) and any("application that is not recorded" in i for i in issues)


def test_message_limits_and_variants(cfg):
    opp, hm, post = _ctx()
    conn = msg.compose("connection_request", hm, opp, None, post, [], cfg)
    assert conn.validation["chars"] <= 250 and conn.validation["ok"]
    texts = {v: msg.compose("before_applying", hm, opp, None, post, [], cfg, v).text for v in msg.VARIANTS}
    assert len(set(texts.values())) == len(texts)                                    # genuinely different
    assert len(texts["shorter"]) < len(texts["default"]) and texts["more_formal"].startswith("Dear Jane")
    fr = msg.compose("before_applying", hm, Opportunity(id=2, title="Stagiaire Maintenance", company_name="RAM", country_code="MA",
                                                         career_track="industry_robotics"), None, [], [], cfg)
    assert fr.language == "fr" and fr.text.startswith("Bonjour Jane")


def test_referral_needs_an_existing_conversation(cfg):
    opp, _, _ = _ctx()
    alum = CRMContact(id=3, name="Ali B", company="Formic", category="alumni_esilv", verification="verified")
    first = msg.compose("referral_request", alum, opp, None, [], [], cfg)
    assert first.kind == "alumni" and "refer" not in first.text.lower()
    replied = [Interaction(contact_id=3, kind="reply", occurred_at=datetime.now(timezone.utc))]
    assert "referring me" in msg.compose("referral_request", alum, opp, None, [], replied, cfg).text


def test_outreach_workflow_end_to_end(client, sessions):
    client.post("/api/networking/refresh", headers=H)
    jane = client.get("/api/contacts").json()[0]
    opp_id = jane["opportunity"]["id"]
    m = client.post(f"/api/contacts/{jane['id']}/messages", json={"opportunity_id": opp_id}, headers=H).json()
    assert m["status"] == "draft" and m["validation"]["ok"] and m["generator"] == "template"
    shorter = client.post(f"/api/messages/{m['id']}/variant", json={"variant": "shorter"}, headers=H).json()
    assert len(shorter["text"]) < len(m["text"])
    sent = client.post(f"/api/messages/{m['id']}/sent", headers=H).json()
    assert sent["status"] == "sent" and sent["sent"]
    detail = client.get(f"/api/contacts/{jane['id']}").json()
    assert detail["interactions"][0]["kind"] == "message_sent" and detail["status"] == "MESSAGE_SENT"
    assert detail["opportunities"][0]["timing"]["action"] in {"WAIT", "FOLLOW_UP"}
    r = client.post(f"/api/contacts/{jane['id']}/interactions", json={"kind": "follow_up_scheduled", "opportunity_id": opp_id,
                                                                     "follow_up_on": "2026-11-10"}, headers=H)
    assert r.status_code == 200
    assert client.get(f"/api/contacts/{jane['id']}").json()["next_follow_up"] == "2026-11-10"
    client.post(f"/api/contacts/{jane['id']}/interactions", json={"kind": "reply", "opportunity_id": opp_id}, headers=H)
    after = client.get(f"/api/contacts/{jane['id']}").json()
    assert after["status"] == "REPLIED" and after["opportunities"][0]["timing"]["action"] == "WAIT"


# Dashboards ----------------------------------------------------------------------------------------------
def test_command_center_analytics_performance_search(client):
    cc = client.get("/api/command-center").json()
    assert set(cc["counts"]) >= {"active", "new", "awaiting", "interviews", "offers", "paid", "deadlines"}
    assert all(p["href"].startswith("/") for p in cc["priorities"])
    an = client.get("/api/analytics").json()
    assert {"funnel", "rates", "compensation", "outreach", "sources"} <= set(an) and an["small_sample"] is True
    assert an["compensation"]["paid_count"] == 2
    perf = client.get("/api/performance").json()
    assert {"sources", "runs", "compensation_coverage", "precision_at_10", "llm"} <= set(perf)
    found = client.get("/api/search", params={"q": "formic"}).json()
    assert found["opportunities"] and found["companies"] == ["Formic"]


def test_schema_v4_migration_keeps_data(tmp_path):
    import sqlite3

    db = tmp_path / "v3.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE crm_contacts (id INTEGER PRIMARY KEY, company VARCHAR(200), name VARCHAR(160), normalized_name VARCHAR(160),"
                " contact_type VARCHAR(30), interaction_status VARCHAR(30), source VARCHAR(300), version INTEGER)")
    con.execute("INSERT INTO crm_contacts VALUES (1,'Acme','Jo','jo','Recruiter','IDENTIFIED','manual',1)")
    con.commit()
    con.close()
    init_db(make_engine(f"sqlite:///{db}"))
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(crm_contacts)")}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"category", "verification", "dismissed"} <= cols
    assert {"contact_evidence", "contact_opportunities", "outreach_messages", "interactions", "sync_changes"} <= tables
    assert con.execute("SELECT name FROM crm_contacts").fetchone() == ("Jo",)


# Hosted mode and sync ------------------------------------------------------------------------------------------
def test_hosted_sync_round_trip(settings, sessions, hunter, tmp_path):
    from summer_hunter.os import sync

    seed(sessions, hunter)
    hosted_sessions = init_db(make_engine(f"sqlite:///{tmp_path / 'hosted.db'}"))
    hosted = TestClient(create_app(settings, hosted_sessions, hosted=True), base_url="https://testserver")
    assert hosted.post("/api/sync/begin").status_code == 401                      # sync needs the token
    transport = httpx.MockTransport(lambda req: _relay(hosted, req))
    out = sync.run(settings, sessions, "https://hosted.test", "t" * 40, httpx.Client(transport=transport))
    assert out["pushed"]["opportunities"] == 3 and not out["failed"]
    assert hosted.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    opps = hosted.get("/api/opportunities").json()
    assert opps["total"] == 3 and hosted.get("/api/meta").json()["last_sync"]
    opp_id = opps["items"][0]["id"]
    app_id = hosted.post(f"/api/opportunities/{opp_id}/track", headers=H).json()["application_id"]
    hosted.patch(f"/api/applications/{app_id}", json={"notes": "edited online"}, headers=H)
    assert hosted.post(f"/api/opportunities/{opp_id}/documents/cv", headers=H).status_code == 409    # LaTeX stays on the Mac
    out2 = sync.run(settings, sessions, "https://hosted.test", "t" * 40, httpx.Client(transport=transport))
    assert out2["applied"] >= 2 and not out2["failed"]
    with sessions() as s:
        from summer_hunter.crm.service import CRM

        local_app = CRM(s).application_for(opp_id)
        assert local_app is not None and local_app.notes == "edited online"
    assert hosted.get("/api/sync/changes", headers={"authorization": "Bearer " + "t" * 40}).json() == []


@pytest.mark.skipif(not os.environ.get("SH_TEST_POSTGRES_URL"), reason="set SH_TEST_POSTGRES_URL to a throwaway Postgres")
def test_hosted_entry_point_on_postgres(settings, sessions, hunter, monkeypatch):
    """The real hosted build (hosted.build) on Postgres: snapshot push twice, online edit, pull, persistent rate limit."""
    from summer_hunter.database.models import Base
    from summer_hunter.os import hosted as hosted_mod
    from summer_hunter.os import sync

    url = hosted_mod.database_url(os.environ["SH_TEST_POSTGRES_URL"])
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", os.environ["SH_TEST_POSTGRES_URL"])
    monkeypatch.setenv("SH_CONFIG_DIR", str(settings.config_dir))
    monkeypatch.setenv("SH_DATA_DIR", str(settings.data_dir / "hosted"))
    monkeypatch.delenv("SH_OS_MODE", raising=False)
    seed(sessions, hunter)
    hosted = TestClient(hosted_mod.build(), base_url="https://testserver")
    assert hosted.get("/api/health").json()["mode"] == "hosted"
    transport = httpx.MockTransport(lambda req: _relay(hosted, req))
    for _ in range(2):                                     # second push replaces rows and resets sequences
        out = sync.run(settings, sessions, "https://hosted.test", "t" * 40, httpx.Client(transport=transport))
        assert out["pushed"]["opportunities"] == 3 and not out["failed"]
    assert hosted.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    opp_id = hosted.get("/api/opportunities").json()["items"][0]["id"]
    app_id = hosted.post(f"/api/opportunities/{opp_id}/track", headers=H).json()["application_id"]
    assert hosted.patch(f"/api/applications/{app_id}", json={"notes": "edited on postgres"}, headers=H).status_code == 200
    hosted.post("/api/contacts", json={"name": "Jane Roe", "company": "Formic", "category": "talent_acquisition",
                                        "opportunity_id": opp_id}, headers=H).raise_for_status()   # new row after a pushed id range
    out = sync.run(settings, sessions, "https://hosted.test", "t" * 40, httpx.Client(transport=transport))
    assert out["applied"] >= 3 and not out["failed"]
    with sessions() as s:
        from summer_hunter.crm.service import CRM

        assert CRM(s).application_for(opp_id).notes == "edited on postgres"
    # The login limiter is stored in the database: a fresh instance (another serverless cold start) still refuses.
    other = TestClient(hosted_mod.build(), base_url="https://testserver")
    codes = [other.post("/api/auth/login", json={"password": "nope"}, headers={"x-forwarded-for": "203.0.113.9"}).status_code
             for _ in range(8)]
    assert codes[-1] == 401
    again = TestClient(hosted_mod.build(), base_url="https://testserver")
    assert again.post("/api/auth/login", json={"password": PASSWORD}, headers={"x-forwarded-for": "203.0.113.9"}).status_code == 429


def test_hosted_build_refuses_incomplete_configuration(monkeypatch):
    from summer_hunter.os import hosted as hosted_mod

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        hosted_mod.build()
    assert hosted_mod.database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def _relay(client: TestClient, req: httpx.Request) -> httpx.Response:
    r = client.request(req.method, req.url.path, params=dict(req.url.params), content=req.content,
                       headers={k: v for k, v in req.headers.items() if k.lower() in {"authorization", "content-type"}})
    return httpx.Response(r.status_code, content=r.content, headers={"content-type": r.headers.get("content-type", "application/json")})


def test_telegram_bot_still_works(settings, sessions):
    from summer_hunter.bot.handlers import Bot
    from summer_hunter.integrations.anthropic_client import NullProvider
    from summer_hunter.integrations.telegram_client import TelegramClient
    from tests.integration.test_crm import CHAT, TOKEN, FakeTelegram

    settings.telegram_bot_token, settings.telegram_chat_id = TOKEN, CHAT
    fake = FakeTelegram()
    bot = Bot(settings, sessions, TelegramClient(TOKEN, transport=httpx.MockTransport(fake)), NullProvider())
    for cmd in ("/top10", "/crm", "/priority"):
        bot.handle_update({"update_id": 1, "message": {"chat": {"id": int(CHAT)}, "text": cmd}})
    assert len(fake.sent) == 3
    _ = (Contact, timedelta)
