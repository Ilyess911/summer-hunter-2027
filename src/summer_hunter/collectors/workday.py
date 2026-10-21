"""Workday career sites (public JSON endpoints behind *.myworkdayjobs.com, robots.txt permits them)."""

from __future__ import annotations

from summer_hunter.collectors.base import CollectContext, Collector
from summer_hunter.models.opportunity import RawOpportunity
from summer_hunter.utils.http import HttpError, RobotsDisallowed
from summer_hunter.utils.text import html_to_text

PAGE = 20
MAX_PAGES = 8
SEARCH_TERMS = ("intern", "internship")


class WorkdayCollector(Collector):
    def __init__(self, company: str, tenant: str, host: str, site: str) -> None:
        self.company = company
        self.tenant, self.host, self.site = tenant, host, site
        self.base = f"https://{tenant}.{host}.myworkdayjobs.com"
        self.name = f"workday:{tenant}/{site}"

    async def collect(self, ctx: CollectContext):
        api = f"{self.base}/wday/cxs/{self.tenant}/{self.site}"
        seen: dict[str, dict] = {}
        errors: list[str] = []
        for term in SEARCH_TERMS:
            for page in range(MAX_PAGES):
                try:
                    data = await ctx.http.post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": PAGE, "offset": page * PAGE, "searchText": term})
                except (HttpError, RobotsDisallowed, ValueError) as exc:
                    errors.append(f"search '{term}' page {page}: {exc}")
                    break
                postings = data.get("jobPostings", []) or []
                for p in postings:
                    if p.get("externalPath"):
                        seen.setdefault(p["externalPath"], p)
                if len(postings) < PAGE:
                    break
        if not seen and errors:
            return self.result(status="failed", errors=errors)
        records, unchanged, fetched = [], 0, 0
        for path, p in seen.items():
            title = (p.get("title") or "").strip()
            if not ctx.title_rules.worth_collecting(title):
                continue
            req_id = (p.get("bulletFields") or [path.rsplit("_", 1)[-1]])[0]
            # Workday exposes no change marker in search results; re-verify a stored job weekly.
            if ctx.is_unchanged(self.name, path, None):
                unchanged += 1
                continue
            if fetched >= ctx.max_detail_fetches:
                errors.append("detail fetch cap reached; remaining jobs deferred")
                break
            try:
                detail = (await ctx.http.get_json(f"{api}{path}")).get("jobPostingInfo", {})
                fetched += 1
            except (HttpError, RobotsDisallowed, ValueError, AttributeError) as exc:
                errors.append(f"{path}: {exc}")
                continue
            country = ((detail.get("jobRequisitionLocation") or {}).get("country") or {}).get("alpha2Code")
            locations = [detail.get("location")] + [x for x in (detail.get("additionalLocations") or []) if isinstance(x, str)]
            records.append(RawOpportunity(
                source=self.name, source_id=path,
                url=detail.get("externalUrl") or f"{self.base}/{self.site}{path}",
                apply_url=detail.get("externalUrl"),
                title=detail.get("title") or title, company=self.company,
                location_text="; ".join(dict.fromkeys([x for x in locations if x])) or p.get("locationsText"),
                country_hint=country,
                description_text=html_to_text(detail.get("jobDescription")),
                posted_at=None,   # Workday's startDate is the posting date, not the internship start
                updated_marker=None,
                employment_type_hint=detail.get("timeType"),
                official_job_id=detail.get("jobReqId") or req_id,
            ))
        status = "partial" if errors else "ok"
        return self.result(records=records, errors=errors, skipped_unchanged=unchanged, scanned=len(seen), status=status)
