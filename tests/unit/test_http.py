"""PoliteClient: retries with backoff, Retry-After, robots.txt, rate limiting."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from summer_hunter.utils import http as http_mod
from summer_hunter.utils.http import HostPolicy, HttpError, PoliteClient, RobotsDisallowed


@pytest.fixture
def no_sleep(monkeypatch):
    delays = []
    real_sleep = asyncio.sleep

    async def fake_sleep(d):
        delays.append(d)
        await real_sleep(0)

    monkeypatch.setattr(http_mod.asyncio, "sleep", fake_sleep)
    return delays


def client(handler, **kw):
    return PoliteClient("test-agent", transport=httpx.MockTransport(handler), default_policy=HostPolicy(min_interval=0), **kw)


def test_retries_transient_errors_with_backoff(no_sleep):
    attempts = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts["n"] += 1
        return httpx.Response(503) if attempts["n"] < 3 else httpx.Response(200, json={"ok": True})

    async def run():
        async with client(handler, max_retries=3) as c:
            return await c.get_json("https://api.example.com/x")

    assert asyncio.run(run()) == {"ok": True}
    assert attempts["n"] == 3
    assert len(no_sleep) == 2 and no_sleep[1] > no_sleep[0]


def test_retry_after_is_honoured_and_gives_up(no_sleep):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429, headers={"retry-after": "7"})

    async def run():
        async with client(handler, max_retries=2) as c:
            await c.get_json("https://api.example.com/x")

    with pytest.raises(HttpError) as exc:
        asyncio.run(run())
    assert exc.value.status == 429
    assert all(d >= 7 for d in no_sleep)


def test_robots_disallow_blocks_request():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(200, text="ok")

    async def run():
        async with client(handler) as c:
            assert await c.get_text("https://site.example.com/public") == "ok"
            await c.get_text("https://site.example.com/private/x")

    with pytest.raises(RobotsDisallowed):
        asyncio.run(run())
    assert "/private/x" not in seen


def test_official_api_401_robots_is_not_disallow_all():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(401)
        return httpx.Response(200, json={"jobs": []})

    async def run():
        async with client(handler, official_api_hosts={"api.ashbyhq.com"}) as c:
            ok = await c.get_json("https://api.ashbyhq.com/posting-api/job-board/x")
            with pytest.raises(RobotsDisallowed):
                await c.get_json("https://random.example.com/jobs")
            return ok

    assert asyncio.run(run()) == {"jobs": []}


def test_per_host_rate_limit():
    def handler(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, text="x")

    async def run():
        async with PoliteClient("t", transport=httpx.MockTransport(handler), default_policy=HostPolicy(min_interval=0.2)) as c:
            start = time.monotonic()
            for _ in range(3):
                await c.get_text("https://h.example.com/a")
            return time.monotonic() - start

    assert asyncio.run(run()) >= 0.4
