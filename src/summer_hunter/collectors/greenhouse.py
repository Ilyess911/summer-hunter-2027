"""Greenhouse Job Board API (public, documented: https://developers.greenhouse.io/job-board.html)."""

from __future__ import annotations

import logging

from summer_hunter.collectors.base import CollectContext, Collector, parse_dt
from summer_hunter.models.opportunity import RawOpportunity
from summer_hunter.utils.http import HttpError, RobotsDisallowed
from summer_hunter.utils.text import html_to_text

log = logging.getLogger(__name__)
API = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseCollector(Collector):
    def __init__(self, company: str, token: str) -> None:
        self.company = company
        self.token = token
        self.name = f"greenhouse:{token}"

    async def collect(self, ctx: CollectContext):
        try:
            listing = await ctx.http.get_json(f"{API}/{self.token}/jobs")
        except (HttpError, RobotsDisallowed, ValueError) as exc:
            return self.result(status="failed", errors=[f"list: {exc}"])
        jobs = listing.get("jobs", []) if isinstance(listing, dict) else []
        records, errors, unchanged, fetched = [], [], 0, 0
        for job in jobs:
            title = (job.get("title") or "").strip()
            if not ctx.title_rules.worth_collecting(title):
                continue
            job_id = str(job.get("id"))
            marker = job.get("updated_at")
            if ctx.is_unchanged(self.name, job_id, marker):
                unchanged += 1
                continue
            if fetched >= ctx.max_detail_fetches:
                errors.append("detail fetch cap reached; remaining jobs deferred to next cycle")
                break
            try:
                detail = await ctx.http.get_json(f"{API}/{self.token}/jobs/{job_id}", params={"pay_transparency": "true"})
                fetched += 1
            except (HttpError, RobotsDisallowed, ValueError) as exc:
                errors.append(f"job {job_id}: {exc}")
                continue
            location = (detail.get("location") or {}).get("name") or (job.get("location") or {}).get("name")
            offices = [o.get("name") for o in detail.get("offices", []) if o.get("name")]
            records.append(RawOpportunity(
                source=self.name,
                source_id=job_id,
                url=detail.get("absolute_url") or job.get("absolute_url") or f"https://boards.greenhouse.io/{self.token}/jobs/{job_id}",
                apply_url=detail.get("absolute_url"),
                title=title,
                company=detail.get("company_name") or self.company,
                location_text="; ".join(dict.fromkeys([x for x in [location, *offices] if x])) or None,
                description_text=html_to_text(detail.get("content")) + pay_line(detail.get("pay_input_ranges")),
                posted_at=parse_dt(detail.get("first_published") or job.get("first_published")),
                updated_marker=marker,
                official_job_id=str(detail.get("requisition_id")) if detail.get("requisition_id") not in (None, "", "See Opening ID") else None,
            ))
        status = "partial" if errors and records else ("failed" if errors and not records and not unchanged else "ok")
        return self.result(records=records, errors=errors, skipped_unchanged=unchanged, scanned=len(jobs), status=status,
                           seen_ids=[str(j.get("id")) for j in jobs], listing_complete=bool(jobs) or isinstance(listing, dict))


def pay_line(ranges) -> str:  # noqa: ANN001
    """Greenhouse pay-transparency ranges (official, in cents) as a plain sentence the extractor reads."""
    out = []
    for r in ranges or []:
        try:
            lo, hi = int(r["min_cents"]) / 100, int(r["max_cents"]) / 100
        except (KeyError, TypeError, ValueError):
            continue
        cur = str(r.get("currency_type") or "").upper()
        if cur and lo > 0:
            title = str(r.get("title") or "")
            period = next((p for k, p in (("hour", "per hour"), ("month", "per month"), ("week", "per week"),
                                          ("annual", "per year"), ("year", "per year")) if k in title.lower()), "")
            out.append(f"\nPay range ({title or 'base'}): {cur} {lo:,.2f} - {cur} {hi:,.2f} {period}".rstrip())
    return "".join(out)
