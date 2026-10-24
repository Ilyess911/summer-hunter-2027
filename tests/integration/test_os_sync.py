"""Hosted <-> Mac synchronization: id collisions, lost acknowledgements, conflicts, edits during a sync, protocol."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from summer_hunter.crm.service import CRM
from summer_hunter.database.migrations import init_db, make_engine
from summer_hunter.database.models import CRMContact, OutreachMessage
from summer_hunter.os import sync
from summer_hunter.os.api import create_app
from tests.integration.test_os_api import PASSWORD, H, _relay, env, seed  # noqa: F401 - env is an autouse fixture

TOKEN = "t" * 40
AUTH = {"authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def pair(settings, sessions, hunter, tmp_path):
    """A Mac database and a hosted app that already received one snapshot."""
    seed(sessions, hunter)
    hosted_sessions = init_db(make_engine(f"sqlite:///{tmp_path / 'hosted.db'}"))
    hosted = TestClient(create_app(settings, hosted_sessions, hosted=True), base_url="https://testserver")
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return _relay(hosted, req)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hosted.hosted_sessions = hosted_sessions
    assert sync.run(settings, sessions, "https://hosted.test", TOKEN, client)["swapped"]
    assert hosted.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return hosted, client, calls


def _opps(hosted):
    return hosted.get("/api/opportunities").json()["items"]


def test_rows_created_on_both_sides_never_mix(settings, sessions, pair):
    hosted, client, _ = pair
    opp = _opps(hosted)[0]["id"]
    # Same next id on both sides: the Mac adds a contact while the hosted app adds another one.
    with sessions() as s:
        from summer_hunter.os import networking as net

        mac_contact = net.add_user_contact(s, "Formic", "Mac Person", None, None, "other", opp)
        s.commit()
        mac_id = mac_contact.id
    online = hosted.post("/api/contacts", json={"company": "Formic", "name": "Online Person", "category": "talent_acquisition",
                                                 "opportunity_id": opp}, headers=H).json()
    assert online["id"] == mac_id                                         # the collision the mapping must resolve
    hosted.post(f"/api/contacts/{online['id']}/dismiss", headers=H).raise_for_status()
    draft = hosted.post(f"/api/contacts/{online['id']}/messages", json={"opportunity_id": opp}, headers=H).json()
    hosted.post(f"/api/messages/{draft['id']}/sent", headers=H).raise_for_status()
    out = sync.run(settings, sessions, "https://hosted.test", TOKEN, client)
    assert not out["failed"] and out["swapped"]
    with sessions() as s:
        assert s.get(CRMContact, mac_id).dismissed is False                 # the Mac's own contact is untouched
        replayed = s.query(CRMContact).filter_by(name="Online Person").one()
        assert replayed.id != mac_id and replayed.dismissed is True
        sent = s.query(OutreachMessage).filter_by(contact_id=replayed.id).one()
        assert sent.status == "sent" and sent.text == draft["text"]       # exact wording kept
    assert "Mac Person" in {c["name"] for c in hosted.get("/api/contacts").json()}   # hosted now shows the Mac's truth


def test_a_lost_acknowledgement_never_replays_twice(settings, sessions, pair):
    from summer_hunter.database.models import CRMEvent, Interaction

    hosted, _, _ = pair
    opp = _opps(hosted)[0]["id"]
    app_id = hosted.post(f"/api/opportunities/{opp}/track", headers=H).json()["application_id"]
    hosted.post(f"/api/applications/{app_id}/status", json={"status": "APPLIED", "confirm_submission": True}, headers=H).raise_for_status()
    hosted.post(f"/api/applications/{app_id}/interview", json={"at": "2026-11-03 10:00", "final": False}, headers=H).raise_for_status()
    contact = hosted.post("/api/contacts", json={"company": "Formic", "name": "Ann Lee", "opportunity_id": opp}, headers=H).json()
    hosted.post(f"/api/contacts/{contact['id']}/interactions", json={"kind": "note", "note": "met at a fair"}, headers=H).raise_for_status()

    def drop_ack(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/ack"):
            raise httpx.ConnectError("network down")
        return _relay(hosted, req)

    with pytest.raises(httpx.ConnectError):
        sync.run(settings, sessions, "https://hosted.test", TOKEN, httpx.Client(transport=httpx.MockTransport(drop_ack)))
    with sessions() as s:
        before = (s.query(CRMEvent).count(), s.query(Interaction).count())
    out = sync.run(settings, sessions, "https://hosted.test", TOKEN, httpx.Client(transport=httpx.MockTransport(lambda r: _relay(hosted, r))))
    assert out["applied"] == 0 and out["swapped"]
    with sessions() as s:
        assert (s.query(CRMEvent).count(), s.query(Interaction).count()) == before
        assert CRM(s).application_for(opp).status == "INTERVIEW"


def test_an_older_online_edit_does_not_overwrite_a_newer_mac_change(settings, sessions, pair):
    hosted, client, _ = pair
    opp = _opps(hosted)[0]["id"]
    app_id = hosted.post(f"/api/opportunities/{opp}/track", headers=H).json()["application_id"]
    sync.run(settings, sessions, "https://hosted.test", TOKEN, client)
    hosted.patch(f"/api/applications/{app_id}", json={"notes": "online note", "next_action": "call"}, headers=H).raise_for_status()
    with sessions() as s:
        crm = CRM(s)
        crm.update_fields(crm.application_for(opp), {"notes": "mac note"}, "web")
        s.commit()
    out = sync.run(settings, sessions, "https://hosted.test", TOKEN, client)
    assert [c["field"] for c in out["conflicts"]] == ["notes"]
    with sessions() as s:
        app = CRM(s).application_for(opp)
        assert app.notes == "mac note" and app.next_action == "call"      # non-conflicting field applied
    local = TestClient(create_app(settings, sessions, hosted=False))
    local.post("/api/auth/login", json={"password": PASSWORD})
    assert local.get("/api/meta").json()["sync_conflicts"] == 1


def test_edits_made_during_an_upload_are_not_lost(settings, sessions, pair):
    hosted, _, _ = pair
    opp = _opps(hosted)[0]["id"]
    injected = []

    def edit_during_push(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/done") and not injected:
            injected.append(hosted.post(f"/api/opportunities/{opp}/track", headers=H).json())
        return _relay(hosted, req)

    out = sync.run(settings, sessions, "https://hosted.test", TOKEN, httpx.Client(transport=httpx.MockTransport(edit_during_push)))
    assert injected and out["swapped"] and out["applied"] == 1            # refused once, pulled again, then swapped
    with sessions() as s:
        assert CRM(s).application_for(opp) is not None
    assert any(i["status"] for i in hosted.get("/api/pipeline").json()["items"])


def test_failed_replay_stops_and_keeps_the_hosted_copy(settings, sessions, pair):
    from summer_hunter.database.models import SyncChange

    hosted, client, calls = pair
    with hosted.hosted_sessions() as s:
        s.add(SyncChange(kind="bogus_kind", payload={"_gen": 1}))
        s.commit()
    calls.clear()
    out = sync.run(settings, sessions, "https://hosted.test", TOKEN, client)
    assert out["failed"] and "unknown change kind" in out["failed"][0]["error"] and not out["swapped"]
    assert not any(p.endswith("/begin") for p in calls)                    # nothing pushed over pending edits
    assert len(hosted.get("/api/sync/changes", headers=AUTH).json()["changes"]) == 1


def test_sync_requires_https_and_matching_protocol(settings, sessions):
    with pytest.raises(sync.SyncError, match="https"):
        sync.run(settings, sessions, "http://hosted.example", TOKEN)
    old = httpx.MockTransport(lambda req: httpx.Response(200, json=[]))
    with pytest.raises(sync.SyncError, match="protocol"):
        sync.run(settings, sessions, "https://hosted.test", TOKEN, httpx.Client(transport=old))


def test_sync_routes_only_exist_online_and_logout_needs_the_csrf_header(settings, sessions):
    local = TestClient(create_app(settings, sessions, hosted=False))
    assert local.post("/api/sync/begin", headers=AUTH).status_code == 404
    assert local.post("/api/auth/logout").status_code == 403
    assert local.post("/api/auth/logout", headers=H).status_code == 200


def test_giving_up_after_repeated_online_edits_is_a_failure(settings, sessions, pair):
    hosted, _, _ = pair

    def always_editing(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/done"):
            hosted.post("/api/actions/x/done", headers=H)
        return _relay(hosted, req)

    out = sync.run(settings, sessions, "https://hosted.test", TOKEN, httpx.Client(transport=httpx.MockTransport(always_editing)), attempts=2)
    assert not out["swapped"] and out["failed"][-1]["kind"] == "swap"
