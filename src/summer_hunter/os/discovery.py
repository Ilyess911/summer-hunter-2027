"""Contact discovery through authorized public sources.

With BRAVE_SEARCH_API_KEY, public profile search results ("site:linkedin.com/in") are stored as
UNVERIFIED candidates; the user confirms each one before it is treated as verified. Without a key,
the app offers the LinkedIn searches for the user to run. No scraping, no login, no guessed emails.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from summer_hunter.database.models import Opportunity
from summer_hunter.os.networking import add_search_candidates

BRAVE = "https://api.search.brave.com/res/v1/web/search"
TEAM_WORDS = {"industry_robotics": "operations OR deployment OR maintenance OR engineering manager",
              "business_tech": "sales OR solutions OR partnerships OR product manager"}


def people_search_links(opp: Opportunity) -> list[dict[str, str]]:
    base = "https://www.linkedin.com/search/results/people/?keywords="
    company = opp.company_name
    return [{"label": f"Recruiters at {company}", "url": base + quote(f"{company} recruiter OR talent acquisition")},
            {"label": f"University recruiting at {company}", "url": base + quote(f"{company} university recruiter OR early careers")},
            {"label": f"ESILV alumni at {company}", "url": base + quote(f"ESILV {company}")},
            {"label": f"Ex-Air France / Airbus at {company}", "url": base + quote(f"{company} \"Air France\" OR Airbus")}]


def queries(opp: Opportunity) -> list[str]:
    c = f'"{opp.company_name}"'
    return [f'site:linkedin.com/in {c} ("talent acquisition" OR recruiter OR "university recruiter" OR "early careers")',
            f"site:linkedin.com/in {c} ({TEAM_WORDS.get(opp.career_track or '', 'manager')})"]


def search_contacts(session: Session, opp: Opportunity, api_key: str, client: httpx.Client | None = None) -> int:
    own = client is None
    client = client or httpx.Client(timeout=20)
    results: list[dict[str, Any]] = []
    try:
        for q in queries(opp):
            resp = client.get(BRAVE, params={"q": q, "count": 10}, headers={"X-Subscription-Token": api_key,
                                                                            "Accept": "application/json"})
            if resp.status_code != 200:
                break
            for r in (resp.json().get("web") or {}).get("results", []):
                results.append({"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("description", "")})
    finally:
        if own:
            client.close()
    return add_search_candidates(session, opp, results)
