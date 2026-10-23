"""Application pack: outreach draft, three interview talking points and the gaps to prepare.

The CV and the cover letter come from the existing generators (web/cv.py, web/letter.py). This
adds the rest of the pack, built only from VERIFIED / USER_CONFIRMED knowledge-base claims:
talking points are verbatim CV bullets ranked by relevance to the posting. Nothing is sent.
"""

from __future__ import annotations

from typing import Any

from summer_hunter.engine import knowledge
from summer_hunter.web.cv import _score, _tokens, load_content


def talking_points(config_dir, profile_raw: dict[str, Any], title: str, description: str, n: int = 3) -> list[str]:  # noqa: ANN001
    claims = knowledge.usable(knowledge.build(profile_raw, load_content(config_dir)), "experience")
    keys = _tokens(f"{title} {description}")
    ranked = sorted(claims, key=lambda c: -_score(c.text, [], keys))
    out, seen = [], set()
    for c in ranked:
        if c.ref in seen:
            continue                 # one point per experience: three different arguments
        seen.add(c.ref)
        out.append(f"{(c.ref or '').replace('--', '–')}: {c.text}")
        if len(out) == n:
            break
    return out


def build_pack(config_dir, profile_raw: dict[str, Any], opp, strategy: dict | None, networking: dict | None,  # noqa: ANN001
               recruit: dict | None) -> dict[str, Any]:
    reco = networking or {}
    message = reco.get("message") or (
        f"Hello, I am applying for the {opp.title} internship at {opp.company_name}. I am a final-year engineering student "
        f"(ESILV, industry and robotics) and would value a short exchange about the team. Thank you, Ilyess")
    return {
        "outreach": message,
        "outreach_target": (reco.get("contact") or {}).get("name"),
        "searches": reco.get("searches", []),
        "talking_points": talking_points(config_dir, profile_raw, opp.title, opp.description_text or ""),
        "gaps": (strategy or {}).get("gaps") or (recruit or {}).get("missing_requirements") or [],
        "arguments": (strategy or {}).get("arguments", []),
    }
