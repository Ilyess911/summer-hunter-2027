"""Official career pages exposing schema.org JobPosting JSON-LD (robots.txt enforced, no JS rendering)."""

from __future__ import annotations

import json

from bs4 import BeautifulSoup

from summer_hunter.collectors.base import CollectContext, Collector, parse_dt
from summer_hunter.models.opportunity import RawOpportunity
from summer_hunter.utils.http import HttpError, RobotsDisallowed
from summer_hunter.utils.text import html_to_text, sha256


def _iter_jobpostings(node):
    if isinstance(node, list):
        for item in node:
            yield from _iter_jobpostings(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            yield node
        for key in ("@graph", "itemListElement", "item"):
            if key in node:
                yield from _iter_jobpostings(node[key])


def parse_jobpostings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        out.extend(_iter_jobpostings(data))
    return out


def _location(posting: dict) -> tuple[str | None, str | None]:
    loc = posting.get("jobLocation")
    locs = loc if isinstance(loc, list) else [loc] if loc else []
    names, country = [], None
    for item in locs:
        addr = (item or {}).get("address") or {}
        if isinstance(addr, str):
            names.append(addr)
            continue
        parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else None]
        names.append(", ".join(p for p in parts if p))
        c = addr.get("addressCountry")
        country = country or (c.get("name") if isinstance(c, dict) else c)
    if posting.get("jobLocationType") == "TELECOMMUTE":
        names.append("Remote")
    return ("; ".join(n for n in names if n) or None), country


class CareerPageCollector(Collector):
    def __init__(self, company: str, urls: list[str]) -> None:
        self.company = company
        self.urls = urls
        self.name = f"career_page:{company.lower().replace(' ', '_')}"

    async def collect(self, ctx: CollectContext):
        records, errors = [], []
        for url in self.urls:
            try:
                html = await ctx.http.get_text(url)
            except RobotsDisallowed:
                errors.append(f"{url}: disallowed by robots.txt (skipped)")
                continue
            except HttpError as exc:
                errors.append(f"{url}: {exc}")
                continue
            for posting in parse_jobpostings(html):
                title = (posting.get("title") or "").strip()
                if not title or not ctx.title_rules.worth_collecting(title):
                    continue
                ident = posting.get("identifier")
                job_id = (ident.get("value") if isinstance(ident, dict) else ident) or sha256(url, title)[:20]
                location, country = _location(posting)
                org = posting.get("hiringOrganization") or {}
                records.append(RawOpportunity(
                    source=self.name, source_id=str(job_id), url=posting.get("url") or url, apply_url=posting.get("url"),
                    title=title, company=(org.get("name") if isinstance(org, dict) else None) or self.company,
                    location_text=location, country_hint=country,
                    description_text=html_to_text(posting.get("description")),
                    posted_at=parse_dt(posting.get("datePosted")),
                    employment_type_hint=str(posting.get("employmentType") or "") or None,
                ))
        status = "failed" if errors and not records and len(errors) == len(self.urls) else ("partial" if errors else "ok")
        return self.result(records=records, errors=errors, scanned=len(self.urls), status=status)
