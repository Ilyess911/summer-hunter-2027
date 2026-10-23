"""Title/company normalization, internship detection and job-family classification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from summer_hunter.utils.text import normalize_for_match

_TITLE_NOISE = re.compile(
    r"\b(intern(ship)?s?|stagiaire|stage|praktikum|praktikant\w*|summer|spring|fall|autumn|winter|off[- ]?cycle|"
    r"20[2-3]\d|program(me)?|student|students|university|graduate|undergraduate|mba|co-?op|placement|"
    r"[a-z]{2,} ?(\(m/w/d\)|\(f/h\)|\(h/f\)|\(m/f/d\)))\b"
)
_COMPANY_NOISE = re.compile(r"\b(inc|inc\.|llc|ltd|limited|gmbh|ag|sa|sas|plc|corp|corporation|co|company|group|holdings?|technologies)\b\.?")


def normalize_title(title: str) -> str:
    t = normalize_for_match(title)
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = _TITLE_NOISE.sub(" ", t)
    t = re.sub(r"[^a-z0-9& ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_company(name: str) -> str:
    c = normalize_for_match(name)
    c = _COMPANY_NOISE.sub(" ", c)
    c = re.sub(r"[^a-z0-9 ]+", " ", c)
    return re.sub(r"\s+", " ", c).strip()


BUSINESS_TECH = "business_tech"
INDUSTRY_ROBOTICS = "industry_robotics"


@dataclass(frozen=True)
class FamilyMatch:
    id: str
    tier: int
    alignment: int
    matched: str | None
    track: str = BUSINESS_TECH
    category: str = "Other"


class TitleRules:
    def __init__(self, preferences: dict[str, Any], scoring: dict[str, Any]) -> None:
        self.intern_patterns = [re.compile(p, re.I) for p in preferences.get("internship_title_patterns", [])]
        self.excluded_patterns = [re.compile(p, re.I) for p in preferences.get("excluded_title_patterns", [])]
        categories = ((scoring.get("tracks") or {}).get(BUSINESS_TECH) or {}).get("categories", {})
        self.families = [
            (fam["id"], int(fam["tier"]), int(fam["alignment"]), [re.compile(p, re.I) for p in fam["patterns"]],
             fam.get("track", BUSINESS_TECH), fam.get("category") or categories.get(fam["id"], "Other"))
            for fam in scoring.get("job_families", [])
        ]
        self.skip_families = set(scoring.get("skip_families_at_collection", []))
        default = scoring.get("default_family", {"id": "unclassified", "tier": 0, "alignment": 35})
        self.default = FamilyMatch(default["id"], int(default["tier"]), int(default["alignment"]), None, BUSINESS_TECH, "Other")
        self.category_of = {fid: cat for fid, _, _, _, _, cat in self.families} | {self.default.id: "Other"}
        self.track_of = {fid: trk for fid, _, _, _, trk, _ in self.families} | {self.default.id: BUSINESS_TECH}

    def looks_like_internship(self, title: str) -> bool:
        norm = normalize_for_match(title)
        if any(p.search(norm) for p in self.excluded_patterns):
            return False
        return any(p.search(norm) for p in self.intern_patterns)

    def worth_collecting(self, title: str) -> bool:
        """Internship title in a family we may recommend (generic engineering/hardware are skipped)."""
        return self.looks_like_internship(title) and self.classify(title).id not in self.skip_families

    def classify(self, title: str) -> FamilyMatch:
        norm = normalize_for_match(title)
        for fam_id, tier, alignment, patterns, track, category in self.families:
            for pattern in patterns:
                m = pattern.search(norm)
                if m:
                    return FamilyMatch(fam_id, tier, alignment, m.group(0), track, category)
        return self.default

