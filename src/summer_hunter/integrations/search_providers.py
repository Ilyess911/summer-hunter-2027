"""Authorized web-search APIs used for public LinkedIn discovery (no scraping of linkedin.com)."""

from __future__ import annotations

import abc
from dataclasses import dataclass

from summer_hunter.utils.http import PoliteClient


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    age: str | None = None


class SearchProvider(abc.ABC):
    name = "search"

    @abc.abstractmethod
    async def search(self, http: PoliteClient, query: str, count: int) -> list[SearchResult]:
        ...


class BraveSearch(SearchProvider):
    """Brave Search API: https://api.search.brave.com (requires an API key)."""

    name = "brave"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def search(self, http: PoliteClient, query: str, count: int) -> list[SearchResult]:
        resp = await http.request("GET", "https://api.search.brave.com/res/v1/web/search", check_robots=False,
                                  params={"q": query, "count": min(count, 20)},
                                  headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"})
        data = resp.json()
        return [SearchResult(r.get("url", ""), r.get("title", ""), r.get("description", ""), r.get("age") or r.get("page_age"))
                for r in (data.get("web") or {}).get("results", [])]


class GoogleCSE(SearchProvider):
    """Google Programmable Search JSON API (key + engine id)."""

    name = "google_cse"

    def __init__(self, api_key: str, cx: str) -> None:
        self.api_key, self.cx = api_key, cx

    async def search(self, http: PoliteClient, query: str, count: int) -> list[SearchResult]:
        resp = await http.request("GET", "https://www.googleapis.com/customsearch/v1", check_robots=False,
                                  params={"key": self.api_key, "cx": self.cx, "q": query, "num": min(count, 10)})
        data = resp.json()
        return [SearchResult(i.get("link", ""), i.get("title", ""), i.get("snippet", "")) for i in data.get("items", []) or []]
