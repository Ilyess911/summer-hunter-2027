"""Recruitment modes. EXPLORATION favours breadth (fit, quality, early signals); CONVERSION favours
applications likely to move (recruitability, confirmed pay, accessible contacts). Switched only by
the user (/mode), never automatically."""

from __future__ import annotations

from typing import Any

from summer_hunter.database.repository import Repository

MODES = ("exploration", "conversion")
KEY = "recruitment_mode"


def get(repo: Repository, rules: dict[str, Any]) -> str:
    value = repo.get_kv(KEY) or (rules.get("global_priority") or {}).get("default_mode", "conversion")
    return value if value in MODES else "conversion"


def set_mode(repo: Repository, mode: str) -> str:
    mode = mode.strip().lower()
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")
    repo.set_kv(KEY, mode)
    return mode
