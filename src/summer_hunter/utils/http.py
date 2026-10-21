"""Polite async HTTP: timeouts, retries with exponential backoff, per-host rate limits, robots.txt."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    pass


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class HostPolicy:
    min_interval: float = 1.0      # seconds between requests to the same host
    concurrency: int = 2


@dataclass
class _HostState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_request: float = 0.0
    semaphore: asyncio.Semaphore | None = None


class PoliteClient:
    """Shared HTTP client for all collectors.

    `official_api_hosts` are vendor-documented public APIs (e.g. Greenhouse Job Board API):
    their robots.txt is honoured when it parses, but a 401/403/404 on robots.txt is read as
    "no policy" instead of "disallow all", since those hosts serve no crawlable site.
    """

    def __init__(
        self,
        user_agent: str,
        timeout: float = 20.0,
        max_retries: int = 3,
        default_policy: HostPolicy | None = None,
        host_policies: dict[str, HostPolicy] | None = None,
        official_api_hosts: set[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        respect_robots: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.max_retries = max_retries
        self.default_policy = default_policy or HostPolicy()
        self.host_policies = host_policies or {}
        self.official_api_hosts = official_api_hosts or set()
        self.respect_robots = respect_robots
        self._hosts: dict[str, _HostState] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": user_agent, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"},
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _policy(self, host: str) -> HostPolicy:
        return self.host_policies.get(host, self.default_policy)

    def _state(self, host: str) -> _HostState:
        state = self._hosts.get(host)
        if state is None:
            state = _HostState(semaphore=asyncio.Semaphore(self._policy(host).concurrency))
            self._hosts[host] = state
        return state

    async def _throttle(self, host: str) -> None:
        state = self._state(host)
        async with state.lock:
            wait = state.last_request + self._policy(host).min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            state.last_request = time.monotonic()

    async def allowed_by_robots(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                resp = await self._client.get(f"{origin}/robots.txt")
                if resp.status_code in (401, 403):
                    if parts.netloc in self.official_api_hosts:
                        parser = None
                    else:
                        parser.disallow_all = True
                elif resp.status_code >= 400:
                    parser = None  # no robots.txt: everything allowed
                else:
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    async def request(self, method: str, url: str, check_robots: bool = True, **kwargs) -> httpx.Response:
        """`check_robots=False` is only for keyed, authorized APIs (search APIs, Telegram), never for crawling."""
        if check_robots and not await self.allowed_by_robots(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        state = self._state(host)
        attempt = 0
        assert state.semaphore is not None
        async with state.semaphore:
            while True:
                await self._throttle(host)
                try:
                    resp = await self._client.request(method, url, **kwargs)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt >= self.max_retries:
                        raise HttpError(f"{type(exc).__name__} on {url}") from exc
                    await self._backoff(attempt, None)
                    attempt += 1
                    continue
                if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                    await self._backoff(attempt, resp.headers.get("retry-after"))
                    attempt += 1
                    continue
                if resp.status_code >= 400:
                    raise HttpError(f"HTTP {resp.status_code} on {url}", status=resp.status_code)
                return resp

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(2 ** attempt + random.uniform(0, 0.5), 30.0)
        if retry_after and retry_after.isdigit():
            delay = min(max(delay, float(retry_after)), 120.0)
        log.debug("retry backoff", extra={"delay": round(delay, 2), "attempt": attempt})
        await asyncio.sleep(delay)

    async def get_json(self, url: str, **kwargs):
        return (await self.request("GET", url, **kwargs)).json()

    async def post_json(self, url: str, payload: dict, **kwargs):
        return (await self.request("POST", url, json=payload, **kwargs)).json()

    async def get_text(self, url: str, **kwargs) -> str:
        return (await self.request("GET", url, **kwargs)).text
