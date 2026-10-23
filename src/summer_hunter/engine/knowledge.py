"""Candidate knowledge base: every claim with its source and verification status.

    VERIFIED        on the approved CV (config/cv_content.yaml, verbatim) or the verified profile
    USER_CONFIRMED  stated by the candidate in writing but not on the CV (profile `skills.user_confirmed`)
    INFERRED        derived by the system (e.g. experience keywords); never presented as a fact
    UNKNOWN         explicitly unknown in the profile

Only VERIFIED and USER_CONFIRMED claims may appear as established facts in application material.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VERIFIED, USER_CONFIRMED, INFERRED, UNKNOWN = "VERIFIED", "USER_CONFIRMED", "INFERRED", "UNKNOWN"
USABLE = {VERIFIED, USER_CONFIRMED}


@dataclass(frozen=True)
class Claim:
    kind: str          # education | experience | achievement | skill | language | availability | identity
    text: str
    source: str
    status: str
    ref: str | None = None


def build(profile: dict[str, Any], cv: dict[str, Any]) -> list[Claim]:
    out: list[Claim] = []
    for ed in cv.get("education", []) or []:
        out.append(Claim("education", f"{ed.get('degree')} at {ed.get('school')} ({ed.get('dates')})", "cv", VERIFIED))
    for exp in cv.get("experiences", []) or []:
        for b in exp.get("bullets", []) or []:
            out.append(Claim("experience", b["text"], "cv", VERIFIED, exp.get("org")))
    for pr in cv.get("projects", []) or []:
        for b in pr.get("bullets", []) or []:
            out.append(Claim("experience", b["text"], "cv", VERIFIED, pr.get("name")))
    for a in profile.get("achievements", []) or []:
        out.append(Claim("achievement", a, "profile", VERIFIED))
    skills = profile.get("skills", {}) or {}
    out += [Claim("skill", s, "profile", VERIFIED) for s in skills.get("verified", []) or []]
    out += [Claim("skill", s, "candidate statement 2026-10-23", USER_CONFIRMED) for s in skills.get("user_confirmed", []) or []]
    held = {s.lower() for s in (skills.get("verified") or []) + (skills.get("user_confirmed") or [])}
    for exp in profile.get("experiences", []) or []:
        for kw in exp.get("keywords", []) or []:
            if kw.lower() not in held:
                out.append(Claim("skill", kw, f"keyword of {exp.get('organization')}", INFERRED, exp.get("id")))
    for lang in profile.get("languages", []) or []:
        status = VERIFIED if lang.get("evidence") or lang.get("level") == "native" else USER_CONFIRMED
        out.append(Claim("language", f"{lang['language']} ({lang['level']})", lang.get("evidence") or "profile", status))
    imm = profile.get("immigration", {}) or {}
    for key in ("requires_visa_sponsorship",):
        if str(imm.get(key)).upper() == "UNKNOWN":
            out.append(Claim("identity", key.replace("_", " "), "profile", UNKNOWN))
    if not imm.get("work_authorization"):
        out.append(Claim("identity", "work authorization outside the EU/EEA/CH", "profile", UNKNOWN))
    return out


def usable(claims: list[Claim], kind: str | None = None) -> list[Claim]:
    return [c for c in claims if c.status in USABLE and (kind is None or c.kind == kind)]
