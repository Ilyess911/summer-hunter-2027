"""Networking recommendation for one opportunity: who, why, which angle, what to say, when to follow up.

Only contacts with public evidence (stored by the networking engine or entered by the user) are
named. When none exists, the recommendation is a LinkedIn people search the user runs themselves
(ESILV alumni, former Air France Industries / Airbus colleagues at the company), never a guessed
name. Nothing is ever sent automatically.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

RANK = {"hiring_manager_post": 0, "recruiter_post": 1, "named_recruiter_on_vacancy": 2, "team_member_post": 3,
        "recruiting_team": 4, "other_evidence": 5}
WHY = {"hiring_manager_post": "manages the team and posted about this hiring",
       "recruiter_post": "recruits for this role and announced it publicly",
       "named_recruiter_on_vacancy": "named as the recruiter on the vacancy",
       "team_member_post": "works in the team and shared the opening",
       "recruiting_team": "part of the recruiting team",
       "other_evidence": "publicly linked to the opening"}
FOLLOW_UP_BUSINESS_DAYS = 5


def people_search(company: str, keywords: str) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote(f"{keywords} {company}")


def recommend(company: str, title: str, contacts: list[dict[str, Any]], recruit_report: dict, track: str | None) -> dict[str, Any]:
    good = sorted((c for c in contacts if float(c.get("confidence", 0)) >= 0.6), key=lambda c: RANK.get(c.get("relationship"), 9))
    angle = (recruit_report.get("reasons") or ["your engineering background in industry and robotics"])[0].rstrip(".")
    hook = "aircraft maintenance engineering at Air France Industries" if track == "industry_robotics" \
        else "technical and customer-facing work on AI and SaaS projects"
    searches = [
        {"label": "Alumni ESILV", "url": people_search(company, "ESILV")},
        {"label": "Ex-Air France Industries", "url": people_search(company, "Air France Industries")},
        {"label": "Ex-Airbus", "url": people_search(company, "Airbus")},
    ]
    if not good:
        return {"contact": None, "why": "No verified contact for this opening.", "angle": angle, "message": None,
                "follow_up": None, "searches": searches}
    best = good[0]
    first = (best.get("name") or "").split()[0] if best.get("name") else ""
    message = (f"Hello {first}, I saw the {title} opening at {company}. I am a final-year engineering student (ESILV, "
               f"industry and robotics) with experience in {hook}. I would value a short exchange on the team's priorities "
               f"before applying. Thank you, Ilyess")[:600]
    return {"contact": {k: best.get(k) for k in ("name", "relationship", "profile_url", "evidence")},
            "why": WHY.get(best.get("relationship"), "publicly linked to the opening"), "angle": angle,
            "message": message, "follow_up": f"{FOLLOW_UP_BUSINESS_DAYS} business days without reply (one follow-up only)",
            "searches": searches}
