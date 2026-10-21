"""Ashby public Job Posting API (documented: https://developers.ashbyhq.com/docs/public-job-posting-api)."""

from __future__ import annotations

from summer_hunter.collectors.base import CollectContext, Collector, parse_dt
from summer_hunter.models.opportunity import RawOpportunity
from summer_hunter.utils.http import HttpError, RobotsDisallowed
from summer_hunter.utils.text import clean_text, html_to_text

API = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyCollector(Collector):
    def __init__(self, company: str, token: str) -> None:
        self.company = company
        self.token = token
        self.name = f"ashby:{token}"

    async def collect(self, ctx: CollectContext):
        try:
            data = await ctx.http.get_json(f"{API}/{self.token}", params={"includeCompensation": "true"})
        except (HttpError, RobotsDisallowed, ValueError) as exc:
            return self.result(status="failed", errors=[f"list: {exc}"])
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        records, unchanged = [], 0
        for j in jobs:
            title = (j.get("title") or "").strip()
            rules = ctx.title_rules
            intern_typed = j.get("employmentType") == "Intern" and rules.classify(title).id not in rules.skip_families
            if j.get("isListed") is False or not (rules.worth_collecting(title) or intern_typed):
                continue
            jid = str(j.get("id"))
            marker = j.get("publishedAt")
            if ctx.is_unchanged(self.name, jid, marker):
                unchanged += 1
                continue
            locations = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations", []) or []]
            address = ((j.get("address") or {}).get("postalAddress") or {})
            comp = (j.get("compensation") or {}).get("compensationTierSummary")
            text = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml"))
            if comp:
                text += f"\nCompensation: {comp}"
            records.append(RawOpportunity(
                source=self.name, source_id=jid,
                url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{self.token}/{jid}",
                apply_url=j.get("applyUrl"),
                title=title, company=self.company,
                location_text="; ".join(dict.fromkeys([x for x in locations if x])) or None,
                country_hint=address.get("addressCountry"),
                description_text=clean_text(text),
                posted_at=parse_dt(j.get("publishedAt")),
                updated_marker=marker,
                employment_type_hint=j.get("employmentType"),
                remote_hint=bool(j.get("isRemote")) or None,
            ))
        return self.result(records=records, skipped_unchanged=unchanged, scanned=len(jobs),
                           seen_ids=[str(j.get("id")) for j in jobs], listing_complete=isinstance(data, dict) and "jobs" in data)
