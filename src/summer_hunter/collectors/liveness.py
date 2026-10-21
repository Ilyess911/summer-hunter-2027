"""Liveness check of the offers actually shown to the user.

Listing APIs can lag behind the real career site (e.g. a Lever board still listed by the API
while every hosted job page returns 404). Each cycle, the shortlisted ATS records are checked
on the page or endpoint the candidate would use; a gone posting closes the source record.
"""

from __future__ import annotations

from summer_hunter.utils.http import HttpError, PoliteClient, RobotsDisallowed

GONE = {404, 410}


async def is_gone(http: PoliteClient, source: str, source_id: str, url: str) -> bool | None:
    """True = posting gone, False = still open, None = could not tell (never closes)."""
    kind = source.split(":")[0]
    try:
        if kind == "workday":
            tenant, site = source.split(":", 1)[1].split("/", 1)
            base = url.split(f"/{site}")[0]
            data = await http.get_json(f"{base}/wday/cxs/{tenant}/{site}{source_id}")
            info = data.get("jobPostingInfo") or {}
            return info.get("canApply") is False
        if kind == "greenhouse":
            token = source.split(":", 1)[1]
            await http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{source_id}")
            return False
        if kind == "lever":
            await http.request("GET", url)
            return False
    except HttpError as exc:
        return True if exc.status in GONE else None
    except (RobotsDisallowed, ValueError, AttributeError):
        return None
    return None
