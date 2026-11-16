"""VPS relay: forwards when the Mac answers, queues when it does not, and the Mac drain replays and acks."""

import hashlib
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location("sh_relay", Path(__file__).resolve().parents[2] / "relay" / "sh_relay.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

AGENT = "a" * 40
RELAY = "r" * 40


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


@pytest.fixture
def relay(tmp_path):
    state = {"mac": None, "calls": []}

    def fake_forward(cfg, method, path, body, auth):  # noqa: ANN001
        state["calls"].append((method, path, auth))
        return state["mac"]

    cfg = R.Config({"AGENT_TOKEN_SHA256": sha(AGENT), "RELAY_TOKEN_SHA256": sha(RELAY), "MAC_URL": "https://mac.example",
                    "RELAY_DB": str(tmp_path / "relay.db")})
    store = R.Store(cfg.db_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), R.make_handler(cfg, store, fake_forward))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    yield base, state, store
    srv.shutdown()


def agent(base, method, path, body=None, token=AGENT):  # noqa: ANN001
    return httpx.request(method, base + path, json=body, headers={"Authorization": f"Bearer {token}"})


def test_config_refuses_missing_or_shared_tokens():
    with pytest.raises(SystemExit):
        R.Config({"AGENT_TOKEN_SHA256": sha(AGENT), "RELAY_TOKEN_SHA256": sha(AGENT), "MAC_URL": "https://m"})
    with pytest.raises(SystemExit):
        R.Config({"AGENT_TOKEN_SHA256": sha(AGENT), "RELAY_TOKEN_SHA256": sha(RELAY), "MAC_URL": "http://m"})


def test_auth_and_token_separation(relay):
    base, state, _ = relay
    assert agent(base, "GET", "/agent/v1/applications", token="x" * 40).status_code == 401
    assert agent(base, "GET", "/relay/v1/queue", token=AGENT).status_code == 401       # agent token cannot drain
    assert agent(base, "POST", "/agent/v1/status", {"a": 1}, token=RELAY).status_code == 401
    assert state["calls"] == []


def test_forwards_when_mac_answers_and_passes_auth_through(relay):
    base, state, store = relay
    state["mac"] = (409, b'{"changed": false}')
    r = agent(base, "POST", "/summer-hunter/agent/v1/status", {"application": "APP-0001"})
    assert r.status_code == 409 and r.json() == {"changed": False} and r.headers["x-relay"] == "forwarded"
    assert state["calls"][-1] == ("POST", "/agent/v1/status", f"Bearer {AGENT}")
    state["mac"] = (200, b'{"applications": [{"id": "APP-0001"}]}')
    assert agent(base, "GET", "/agent/v1/applications").json()["applications"][0]["id"] == "APP-0001"
    assert store.get("snapshot")


def test_queues_when_mac_is_away_and_serves_cached_list(relay):
    base, state, store = relay
    state["mac"] = None
    assert agent(base, "GET", "/agent/v1/applications").status_code == 503      # nothing cached yet
    body = {"application": "APP-0007", "status": "REJECTED", "message_id": "<m1>"}
    r1 = agent(base, "POST", "/agent/v1/status", body)
    r2 = agent(base, "POST", "/agent/v1/status", body)
    assert r1.status_code == 202 and r1.json()["queued"] and r2.json()["duplicate"]
    assert r1.json()["queue_id"] == r2.json()["queue_id"]
    store.put("snapshot", json.dumps({"applications": [], "rules": "x"}))
    cached = agent(base, "GET", "/agent/v1/applications")
    assert cached.status_code == 200 and cached.json()["relay"]["source"] == "cache"


def test_rejects_bad_bodies_and_unknown_paths(relay):
    base, state, _ = relay
    state["mac"] = None
    h = {"Authorization": f"Bearer {AGENT}"}
    assert httpx.post(base + "/agent/v1/status", content=b"[1]", headers=h).status_code == 400
    assert httpx.post(base + "/agent/v1/status", content=b"x" * (R.MAX_BODY + 1), headers=h).status_code == 413
    assert agent(base, "GET", "/agent/v1/status").status_code == 405
    assert agent(base, "GET", "/etc/passwd").status_code == 404


def test_mac_drain_replays_acks_and_pushes_snapshot(relay, monkeypatch):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from summer_hunter.web import relay_sync

    base, state, store = relay
    state["mac"] = None
    agent(base, "POST", "/agent/v1/status", {"application": "APP-0007", "message_id": "<m1>"})
    agent(base, "POST", "/agent/v1/applications", {"company": "Celonis", "title": "Intern"})
    seen = []

    async def status(request):  # noqa: ANN001
        seen.append((request.url.path, request.headers["authorization"], await request.json()))
        return JSONResponse({"changed": True}, status_code=200)

    async def apps(request):  # noqa: ANN001
        if request.method == "POST":
            seen.append((request.url.path, request.headers["authorization"], await request.json()))
            return JSONResponse({"id": "APP-0099", "created": True}, status_code=201)
        return JSONResponse({"applications": [{"id": "APP-0099"}]})

    local = Starlette(routes=[Route("/agent/v1/status", status, methods=["POST"]),
                              Route("/agent/v1/applications", apps, methods=["GET", "POST"])])
    relay_client = httpx.Client()
    from starlette.testclient import TestClient

    local_client = TestClient(local, base_url="http://local")

    class Mux:
        def get(self, url, **kw):  # noqa: ANN001, ANN003
            return (local_client if url.startswith("http://local") else relay_client).get(url, **kw)

        def post(self, url, **kw):  # noqa: ANN001, ANN003
            return (local_client if url.startswith("http://local") else relay_client).post(url, **kw)

        def put(self, url, **kw):  # noqa: ANN001, ANN003
            return relay_client.put(url, **kw)

    monkeypatch.setenv("SH_RELAY_TOKEN", RELAY)
    monkeypatch.setenv("SH_AGENT_TOKEN", AGENT)
    # the drain talks to the relay over plain http in this test
    monkeypatch.setenv("SH_RELAY_URL", base.replace("http://", "https://"))
    import summer_hunter.web.relay_sync as rs

    class Plain(Mux):
        def _fix(self, url):  # noqa: ANN001
            return url.replace("https://127.0.0.1", "http://127.0.0.1")

        def get(self, url, **kw):  # noqa: ANN001, ANN003
            return super().get(self._fix(url), **kw)

        def post(self, url, **kw):  # noqa: ANN001, ANN003
            return super().post(self._fix(url), **kw)

        def put(self, url, **kw):  # noqa: ANN001, ANN003
            return super().put(self._fix(url), **kw)

    report = rs.run_once(local="http://local", client=Plain())
    assert report.configured and report.acked == 2 and report.snapshot and not report.errors
    assert [s[0] for s in seen] == ["/agent/v1/status", "/agent/v1/applications"]
    assert all(s[1] == f"Bearer {AGENT}" for s in seen)
    assert store.pending() == [] and store.counts() == {"done": 2}
    assert json.loads(store.get("snapshot"))["applications"][0]["id"] == "APP-0099"


def test_drain_keeps_items_when_local_server_is_down(relay, monkeypatch):
    from summer_hunter.web import relay_sync as rs

    base, state, store = relay
    state["mac"] = None
    agent(base, "POST", "/agent/v1/status", {"application": "APP-0001"})
    monkeypatch.setenv("SH_RELAY_TOKEN", RELAY)
    monkeypatch.setenv("SH_AGENT_TOKEN", AGENT)
    monkeypatch.setenv("SH_RELAY_URL", "https://x")

    class C:
        def get(self, url, **kw):  # noqa: ANN001, ANN003
            if url.startswith("https://x"):
                return httpx.get(base + url[len("https://x"):], **kw)
            raise httpx.ConnectError("down")

        def post(self, url, **kw):  # noqa: ANN001, ANN003
            raise httpx.ConnectError("down")

        def put(self, url, **kw):  # noqa: ANN001, ANN003
            raise httpx.ConnectError("down")

    report = rs.run_once(local="http://local", client=C())
    assert report.acked == 0 and report.deferred == 1
    assert len(store.pending()) == 1


# Regressions from the independent review ---------------------------------------------------------
def _drain(base, monkeypatch, local_status):  # noqa: ANN001
    """Run the drain with a local server answering GET 200 and each POST with the next code of local_status."""
    from summer_hunter.web import relay_sync as rs

    monkeypatch.setenv("SH_RELAY_TOKEN", RELAY)
    monkeypatch.setenv("SH_AGENT_TOKEN", AGENT)
    monkeypatch.setenv("SH_RELAY_URL", "https://x")
    codes, posted = list(local_status), []

    class C:
        def _relay(self, url):  # noqa: ANN001
            return base + url[len("https://x"):]

        def get(self, url, **kw):  # noqa: ANN001, ANN003
            if url.startswith("https://x"):
                return httpx.get(self._relay(url), **kw)
            return httpx.Response(200, json={"applications": []})

        def post(self, url, **kw):  # noqa: ANN001, ANN003
            if url.startswith("https://x"):
                return httpx.post(self._relay(url), **kw)
            posted.append(kw["content"])
            return httpx.Response(codes.pop(0), json={})

        def put(self, url, **kw):  # noqa: ANN001, ANN003
            return httpx.put(self._relay(url), **kw)

    return rs.run_once(local="http://local", client=C()), posted


def test_unjudged_answers_are_never_acked_and_order_is_kept(relay, monkeypatch):
    base, state, store = relay
    state["mac"] = None
    agent(base, "POST", "/agent/v1/status", {"application": "APP-0001", "status": "INTERVIEW"})
    agent(base, "POST", "/agent/v1/status", {"application": "APP-0001", "status": "REJECTED"})
    for code in (401, 403, 404, 302, 429, 500):
        report, posted = _drain(base, monkeypatch, [code, 200])
        assert report.acked == 0 and len(posted) == 1 and len(store.pending()) == 2, code
    report, posted = _drain(base, monkeypatch, [409, 200])
    assert report.acked == 2 and store.pending() == []
    assert b"INTERVIEW" in posted[0] and b"REJECTED" in posted[1]


def test_local_token_mismatch_touches_nothing(relay, monkeypatch):
    from summer_hunter.web import relay_sync as rs

    base, state, store = relay
    state["mac"] = None
    agent(base, "POST", "/agent/v1/status", {"application": "APP-0001"})
    monkeypatch.setenv("SH_RELAY_TOKEN", RELAY)
    monkeypatch.setenv("SH_AGENT_TOKEN", AGENT)
    monkeypatch.setenv("SH_RELAY_URL", "https://x")

    class C:
        def get(self, url, **kw):  # noqa: ANN001, ANN003
            if url.startswith("https://x"):
                return httpx.get(base + url[len("https://x"):], **kw)
            return httpx.Response(401, json={})

        def post(self, url, **kw):  # noqa: ANN001, ANN003
            raise AssertionError("nothing may be replayed")

    report = rs.run_once(local="http://local", client=C())
    assert report.acked == 0 and len(store.pending()) == 1


def test_non_utf8_json_is_refused(relay):
    base, state, _ = relay
    state["mac"] = None
    body = json.dumps({"a": 1}).encode("utf-16")
    r = httpx.post(base + "/agent/v1/status", content=body, headers={"Authorization": f"Bearer {AGENT}"})
    assert r.status_code == 400


def test_oversized_mac_answer_is_not_truncated_into_a_200(relay):
    base, state, store = relay
    store.put("snapshot", '{"applications": []}')
    state["mac"] = (200, b"x" * (R.MAX_SNAPSHOT + 1))
    assert agent(base, "GET", "/agent/v1/applications").status_code == 502
    assert store.get("snapshot") == '{"applications": []}'


def test_redirects_are_never_followed():
    import urllib.request

    req = urllib.request.Request("https://a.example/x", headers={"Authorization": "Bearer t"})
    assert R._NoRedirect().redirect_request(req, None, 302, "Found", {}, "https://evil.example/") is None
