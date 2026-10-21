"""Lever Postings API (public, documented: https://github.com/lever/postings-api)."""

from __future__ import annotations

from summer_hunter.collectors.base import CollectContext, Collector, parse_dt
from summer_hunter.models.opportunity import RawOpportunity
from summer_hunter.utils.http import HttpError, RobotsDisallowed
from summer_hunter.utils.text import clean_text, html_to_text

API = "https://api.lever.co/v0/postings"


class LeverCollector(Collector):
    def __init__(self, company: str, token: str) -> None:
        self.company = company
        self.token = token
        self.name = f"lever:{token}"

    async def collect(self, ctx: CollectContext):
        try:
            postings = await ctx.http.get_json(f"{API}/{self.token}", params={"mode": "json"})
        except (HttpError, RobotsDisallowed, ValueError) as exc:
            return self.result(status="failed", errors=[f"list: {exc}"])
        if not isinstance(postings, list):
            return self.result(status="failed", errors=["unexpected payload"])
        records, unchanged = [], 0
        for p in postings:
            title = (p.get("text") or "").strip()
            if not ctx.title_rules.worth_collecting(title):
                continue
            pid = str(p.get("id"))
            text = p.get("descriptionPlain") or html_to_text(p.get("description"))
            for block in p.get("lists", []) or []:
                text += f"\n{block.get('text', '')}\n{html_to_text(block.get('content'))}"
            text += "\n" + (p.get("additionalPlain") or html_to_text(p.get("additional")))
            marker = str(p.get("updatedAt") or p.get("createdAt") or "")
            if ctx.is_unchanged(self.name, pid, marker):
                unchanged += 1
                continue
            cats = p.get("categories") or {}
            locations = cats.get("allLocations") or [cats.get("location")]
            records.append(RawOpportunity(
                source=self.name, source_id=pid,
                url=p.get("hostedUrl") or f"https://jobs.lever.co/{self.token}/{pid}",
                apply_url=p.get("applyUrl"),
                title=title, company=self.company,
                location_text="; ".join([x for x in locations if x]) or None,
                country_hint=p.get("country"),
                description_text=clean_text(text),
                posted_at=parse_dt(p.get("createdAt")),
                updated_marker=marker,
                employment_type_hint=cats.get("commitment"),
                remote_hint=(p.get("workplaceType") == "remote") or None,
            ))
        return self.result(records=records, skipped_unchanged=unchanged, scanned=len(postings))
