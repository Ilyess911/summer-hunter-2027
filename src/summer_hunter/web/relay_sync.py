"""Drain the VPS relay into the local agent API (run by launchd every 2 minutes, see relay/README.md).

Each report Instinct sent while the Mac was unreachable is replayed against the local Mini App
server (same endpoint, same token, same rules) and acknowledged with the local answer. A report is
acknowledged only once the local server answered it with a final code (not 5xx, not unreachable),
so nothing is lost if the Mac goes to sleep mid-drain. Then the current application list is pushed
to the relay, which serves it to Instinct while the Mac is away.

Disabled unless SH_RELAY_URL and SH_RELAY_TOKEN are set.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)
LOCAL = "http://127.0.0.1:8787"
ALLOWED_PATHS = {"/agent/v1/status", "/agent/v1/applications"}
# Final answers of the local rules: the report was judged, so it may leave the queue. Anything else
# (401/403 token mismatch, 404 API disabled, 3xx, 429, 5xx) means "not judged": stop and retry later,
# in order, so a later report is never applied before an earlier one.
FINAL = {200, 201, 400, 409, 413, 422}


@dataclass
class SyncReport:
    configured: bool = True
    replayed: int = 0
    acked: int = 0
    deferred: int = 0
    snapshot: bool = False
    errors: list[str] = field(default_factory=list)


def run_once(local: str = LOCAL, client: httpx.Client | None = None) -> SyncReport:
    relay = (os.environ.get("SH_RELAY_URL") or "").rstrip("/")
    relay_token = (os.environ.get("SH_RELAY_TOKEN") or "").strip()
    agent_token = (os.environ.get("SH_AGENT_TOKEN") or "").strip()
    if not relay.startswith("https://") or len(relay_token) < 32 or len(agent_token) < 32:
        return SyncReport(configured=False)
    report = SyncReport()
    own = client is None
    http = client or httpx.Client(timeout=20)
    to_relay = {"Authorization": f"Bearer {relay_token}"}
    to_local = {"Authorization": f"Bearer {agent_token}", "Content-Type": "application/json"}
    try:
        try:
            items = http.get(f"{relay}/relay/v1/queue", headers=to_relay).raise_for_status().json().get("items", [])
        except (httpx.HTTPError, ValueError) as exc:
            report.errors.append(f"relay queue: {type(exc).__name__}")
            return report
        try:
            ready = http.get(f"{local}/agent/v1/applications", headers=to_local).status_code == 200
        except httpx.HTTPError:
            ready = False
        if not ready:                               # local server down or token mismatch: touch nothing
            report.errors.append("local agent API not ready")
            report.deferred = len(items)
            return report
        for n, item in enumerate(items):
            path = item.get("path")
            if path not in ALLOWED_PATHS:          # the relay only queues these; anything else is dropped unseen
                _ack(http, relay, to_relay, item["id"], 400, '{"error": "path not allowed"}', report)
                continue
            try:
                resp = http.post(f"{local}{path}", content=(item.get("body") or "").encode(), headers=to_local)
            except httpx.HTTPError as exc:
                report.errors.append(f"local server: {type(exc).__name__}")
                report.deferred += len(items) - n
                break
            report.replayed += 1
            if resp.status_code not in FINAL:
                report.errors.append(f"local answered {resp.status_code}")
                report.deferred += len(items) - n  # this one and every later one, retried in order next run
                break
            _ack(http, relay, to_relay, item["id"], resp.status_code, resp.text, report)
        try:
            snap = http.get(f"{local}/agent/v1/applications", headers=to_local)
            if snap.status_code == 200:
                http.put(f"{relay}/relay/v1/snapshot", content=snap.content,
                         headers=to_relay | {"Content-Type": "application/json"}).raise_for_status()
                report.snapshot = True
        except httpx.HTTPError as exc:
            report.errors.append(f"snapshot: {type(exc).__name__}")
    finally:
        if own:
            http.close()
    log.info("relay sync", extra={"replayed": report.replayed, "acked": report.acked, "deferred": report.deferred,
                                  "snapshot": report.snapshot, "errors": report.errors[:3]})
    return report


def _ack(http: httpx.Client, relay: str, headers: dict, item_id: int, code: int, text: str, report: SyncReport) -> None:
    try:
        http.post(f"{relay}/relay/v1/ack", json={"id": item_id, "code": code, "result": text[:2000]},
                  headers=headers).raise_for_status()
        report.acked += 1
    except httpx.HTTPError as exc:
        report.errors.append(f"ack: {type(exc).__name__}")
