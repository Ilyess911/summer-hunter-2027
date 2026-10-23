"""Classification from responsibilities, for titles that do not say what the job is.

"Customer Solutions Intern" or "Field Deployment Intern" carry no target keyword; their
responsibilities do. The title family is kept whenever it is conclusive; a vague or generic
title (see `semantic.apply_when_title_family_in`) takes the family whose responsibility cues
are clearly the most present (minimum hits and a margin over the runner-up).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from summer_hunter.utils.text import normalize_for_match


@dataclass
class SemanticMatch:
    family: str | None
    hits: int
    evidence: list[str] = field(default_factory=list)
    runner_up: str | None = None
    applied: bool = False


def _hits(norm: str, cues: list[str]) -> list[str]:
    return [c for c in cues if re.search(r"(?<![a-z0-9])" + re.escape(normalize_for_match(c)) + r"(?![a-z0-9])", norm)]


def classify(title: str, description: str, rules: dict[str, Any], title_family: str) -> SemanticMatch:
    cfg = rules.get("semantic") or {}
    norm = normalize_for_match(f"{title}. {description}")
    ranked = sorted(((fam, _hits(norm, cues)) for fam, cues in (cfg.get("families") or {}).items()),
                    key=lambda item: -len(item[1]))
    if not ranked or not ranked[0][1]:
        return SemanticMatch(None, 0)
    best, ev = ranked[0]
    second = ranked[1] if len(ranked) > 1 else (None, [])
    match = SemanticMatch(best, len(ev), ev[:5], second[0])
    match.applied = (title_family in set(cfg.get("apply_when_title_family_in", [])) and len(ev) >= int(cfg.get("min_hits", 3))
                     and len(ev) - len(second[1]) >= int(cfg.get("min_margin", 2)) and best != title_family)
    return match
