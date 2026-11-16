#!/usr/bin/env python3
"""Summer Hunter relay: an always-on front door for the Instinct agent API, run on the VPS.

Standard library only (Python 3.11+). Instinct calls the relay instead of the Mac:

- the relay forwards each call to the Mac (MAC_URL) and returns the Mac's answer unchanged;
- when the Mac cannot be reached (asleep, offline, blocked network), a POST is queued and answered
  202 {"queued": true}; a GET /applications is answered from the last list the Mac returned;
- the Mac drains the queue (GET /relay/v1/queue, POST /relay/v1/ack) and refreshes the list
  (PUT /relay/v1/snapshot) with its own token, whenever it is awake.

The relay never decides anything: every report is checked by the Mac's own rules. It stores no
plaintext token, only SHA-256 digests (AGENT_TOKEN_SHA256, RELAY_TOKEN_SHA256); Instinct's
Authorization header is passed through to the Mac unchanged. Queued bodies (mail excerpts) are
erased as soon as the Mac acknowledges them; finished rows are purged after PURGE_DAYS.
Bodies are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("sh_relay")

MAX_BODY = 32_000
MAX_SNAPSHOT = 512_000
MAX_PENDING = 500
PURGE_DAYS = 7
FORWARD_TIMEOUT = 12
AGENT_RATE, RELAY_RATE, FAIL_RATE = 60, 120, 20          # per minute
PREFIX = "/summer-hunter"                                 # Funnel mount; accepted with or without it
AGENT_PATHS = {"/agent/v1/applications": {"GET", "POST"}, "/agent/v1/status": {"POST"}}
UNREACHABLE = {502, 503, 504}                             # what the tunnel answers when the Mac is gone
MAX_WORKERS = 16                                          # concurrent connections; more are refused
SOCKET_TIMEOUT = 15


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Rate:
    def __init__(self, per_min: int) -> None:
        self.per_min, self.hits, self.lock = per_min, [], threading.Lock()

    def allow(self) -> bool:
        t = time.monotonic()
        with self.lock:
            self.hits = [h for h in self.hits if t - h < 60]
            if len(self.hits) >= self.per_min:
                return False
            self.hits.append(t)
            return True


class Store:
    def __init__(self, path: str) -> None:
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if path != ":memory:":
            os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, body BLOB, digest TEXT NOT NULL,
            received_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', result_code INTEGER,
            result TEXT, done_at TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS queue_state ON queue(state)")
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")

    def enqueue(self, path: str, body: bytes) -> tuple[int, bool] | None:
        """(id, already_queued); None when the queue is full. The same body is queued once."""
        digest = hashlib.sha256(path.encode() + b"\0" + body).hexdigest()
        with self.lock:
            row = self.db.execute("SELECT id FROM queue WHERE digest=? AND state='pending'", (digest,)).fetchone()
            if row:
                return row[0], True
            if self.db.execute("SELECT COUNT(*) FROM queue WHERE state='pending'").fetchone()[0] >= MAX_PENDING:
                return None
            cur = self.db.execute("INSERT INTO queue(path, body, digest, received_at) VALUES (?,?,?,?)",
                                  (path, body, digest, now()))
            return cur.lastrowid, False

    def pending(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, path, body, received_at FROM queue WHERE state='pending' ORDER BY id LIMIT ?",
                                   (limit,)).fetchall()
        return [{"id": i, "path": p, "body": b.decode("utf-8", "replace"), "received_at": r} for i, p, b, r in rows]

    def ack(self, item_id: int, code: int, result: str) -> bool:
        with self.lock:
            cur = self.db.execute("UPDATE queue SET state='done', body=NULL, result_code=?, result=?, done_at=? "
                                  "WHERE id=? AND state='pending'", (code, result[:2000], now(), item_id))
            return cur.rowcount == 1

    def purge(self) -> None:
        cutoff = datetime.fromtimestamp(time.time() - PURGE_DAYS * 86400, timezone.utc).isoformat(timespec="seconds")
        with self.lock:
            self.db.execute("DELETE FROM queue WHERE state='done' AND done_at < ?", (cutoff,))

    def counts(self) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT state, COUNT(*) FROM queue GROUP BY state").fetchall()
        return dict(rows)

    def put(self, k: str, v: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO kv(k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def get(self, k: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else None


class Config:
    def __init__(self, env: dict | None = None) -> None:
        env = os.environ if env is None else env
        self.agent_sha = (env.get("AGENT_TOKEN_SHA256") or "").strip().lower()
        self.relay_sha = (env.get("RELAY_TOKEN_SHA256") or "").strip().lower()
        self.mac_url = (env.get("MAC_URL") or "").rstrip("/")
        self.db_path = env.get("RELAY_DB") or "relay.db"
        self.port = int(env.get("RELAY_PORT") or 8790)
        if len(self.agent_sha) != 64 or len(self.relay_sha) != 64 or self.agent_sha == self.relay_sha:
            raise SystemExit("AGENT_TOKEN_SHA256 and RELAY_TOKEN_SHA256 must be two different SHA-256 hex digests")
        if not self.mac_url.startswith("https://"):
            raise SystemExit("MAC_URL must be an https:// URL")


def bearer_ok(header: str, expected_sha: str) -> bool:
    scheme, _, token = (header or "").strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or len(token) < 32:
        return False
    return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected_sha)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would resend the Authorization header to the new origin."""

    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def forward(cfg: Config, method: str, path: str, body: bytes | None, auth: str) -> tuple[int, bytes] | None:
    """The Mac's answer, or None when the Mac cannot be reached."""
    req = urllib.request.Request(cfg.mac_url + path, data=body, method=method,
                                 headers={"Authorization": auth, "Content-Type": "application/json",
                                          "User-Agent": "summer-hunter-relay/1"})
    try:
        with _OPENER.open(req, timeout=FORWARD_TIMEOUT) as resp:   # fixed https base URL
            return resp.status, resp.read(MAX_SNAPSHOT + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in UNREACHABLE or 300 <= exc.code < 400:      # a redirect is never followed: treat as away
            return None
        return exc.code, exc.read(MAX_SNAPSHOT + 1)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None


def make_handler(cfg: Config, store: Store, forwarder=forward):  # noqa: ANN001, ANN201
    agent_rate, relay_rate, fail_rate = Rate(AGENT_RATE), Rate(RELAY_RATE), Rate(FAIL_RATE)

    class Handler(BaseHTTPRequestHandler):
        server_version = "relay"
        sys_version = ""
        protocol_version = "HTTP/1.0"      # one request per connection: no idle keep-alive threads
        timeout = SOCKET_TIMEOUT

        def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002 - default log prints request lines; ours is below
            return

        def send(self, code: int, payload: dict | bytes, extra: dict | None = None) -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
            if self.route != "/healthz" and code != 401:
                log.info("%s %s -> %s", self.command, self.route, code)

        def read_body(self, limit: int) -> bytes | None:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > limit:
                self.send(413, {"error": "body too large"})
                return None
            return self.rfile.read(n) if n else b""

        def auth(self, expected: str, rate: Rate) -> bool:
            if not bearer_ok(self.headers.get("Authorization", ""), expected):
                self.send(429 if not fail_rate.allow() else 401, {"error": "unauthorized"})
                return False
            if not rate.allow():
                self.send(429, {"error": "rate limited"})
                return False
            return True

        def handle_any(self) -> None:
            self.route = "?"
            path = self.path.split("?", 1)[0]
            if path.startswith(PREFIX + "/"):
                path = path[len(PREFIX):]
            self.route = path
            if path == "/healthz" and self.command == "GET":
                return self.send(200, {"ok": True})
            if path in AGENT_PATHS:
                if self.command not in AGENT_PATHS[path]:
                    return self.send(405, {"error": "method not allowed"})
                return self.agent(path)
            if path.startswith("/relay/v1/"):
                return self.relay(path)
            return self.send(404, {"error": "not found"})

        def agent(self, path: str) -> None:
            if not self.auth(cfg.agent_sha, agent_rate):
                return
            body = None
            if self.command == "POST":
                body = self.read_body(MAX_BODY)
                if body is None:
                    return
                try:
                    if not isinstance(json.loads(body.decode("utf-8") if body else "null"), dict):
                        return self.send(400, {"error": "expected a JSON object"})
                except ValueError:                      # UnicodeDecodeError included: UTF-8 only
                    return self.send(400, {"error": "invalid JSON (UTF-8 expected)"})
            answer = forwarder(cfg, self.command, path, body, self.headers.get("Authorization", ""))
            if answer is not None:
                code, data = answer
                if len(data) > MAX_SNAPSHOT:
                    return self.send(502, {"error": "Summer Hunter answer too large"})
                if path == "/agent/v1/applications" and self.command == "GET" and code == 200 and len(data) <= MAX_SNAPSHOT:
                    store.put("snapshot", data.decode("utf-8", "replace"))
                    store.put("snapshot_at", now())
                return self.send(code, data, {"X-Relay": "forwarded"})
            if self.command == "GET":
                snap = store.get("snapshot")
                if not snap:
                    return self.send(503, {"error": "Summer Hunter is offline and no application list is cached yet"})
                try:
                    data = json.loads(snap)
                except ValueError:
                    return self.send(503, {"error": "cached list unreadable"})
                data["relay"] = {"source": "cache", "as_of": store.get("snapshot_at"),
                                 "note": "Summer Hunter is offline; this list may be out of date. Reports are queued."}
                return self.send(200, data, {"X-Relay": "cache"})
            queued = store.enqueue(path, body or b"")
            if queued is None:
                return self.send(503, {"error": "relay queue full, retry later"})
            item_id, again = queued
            return self.send(202, {"queued": True, "queue_id": item_id, "duplicate": again,
                                   "note": "Summer Hunter is offline; this report will be checked and applied when it reconnects."},
                             {"X-Relay": "queued"})

        def relay(self, path: str) -> None:
            if not self.auth(cfg.relay_sha, relay_rate):
                return
            if path == "/relay/v1/queue" and self.command == "GET":
                store.purge()
                return self.send(200, {"items": store.pending(), "counts": store.counts()})
            if path == "/relay/v1/ack" and self.command == "POST":
                body = self.read_body(MAX_BODY)
                if body is None:
                    return
                try:
                    data = json.loads(body)
                    item_id, code = int(data["id"]), int(data["code"])
                    result = str(data.get("result", ""))
                except (ValueError, KeyError, TypeError):
                    return self.send(400, {"error": "expected {id, code, result}"})
                return self.send(200, {"acked": store.ack(item_id, code, result)})
            if path == "/relay/v1/snapshot" and self.command == "PUT":
                body = self.read_body(MAX_SNAPSHOT)
                if body is None:
                    return
                try:
                    if not isinstance(json.loads(body).get("applications"), list):
                        raise ValueError
                except (ValueError, AttributeError):
                    return self.send(400, {"error": "expected the /agent/v1/applications JSON"})
                store.put("snapshot", body.decode("utf-8", "replace"))
                store.put("snapshot_at", now())
                return self.send(200, {"stored": True})
            return self.send(404, {"error": "not found"})

        def do_GET(self) -> None:  # noqa: N802
            self.handle_any()

        def do_POST(self) -> None:  # noqa: N802
            self.handle_any()

        def do_PUT(self) -> None:  # noqa: N802
            self.handle_any()

    return Handler


class BoundedServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with at most MAX_WORKERS connections handled at once; extra ones are closed."""

    daemon_threads = True

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.slots = threading.BoundedSemaphore(MAX_WORKERS)

    def process_request(self, request, client_address) -> None:  # noqa: ANN001
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:  # noqa: ANN001
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config()
    store = Store(cfg.db_path)
    server = BoundedServer(("127.0.0.1", cfg.port), make_handler(cfg, store))
    server.daemon_threads = True
    log.info("relay listening on 127.0.0.1:%s", cfg.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
