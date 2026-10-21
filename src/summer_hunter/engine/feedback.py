"""Explicit, interpretable preference learning from user feedback (no opaque retraining)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from summer_hunter.database.repository import Repository
from summer_hunter.models.scoring import Adjustment

# Dismiss reasons offered in Telegram, mapped to the dimension they teach about.
DISMISS_REASONS = {
    "cold": ("pattern", "cold_calling", "Too much cold calling"),
    "role": ("job_family", None, "Not interested in this role type"),
    "company": ("company", None, "Not interested in this company"),
    "country": ("country", None, "Not interested in this country"),
    "other": (None, None, "Other"),
}


def recompute(repo: Repository, rules: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild preference_adjustments from the full feedback history (since the last reset)."""
    cfg = rules["feedback"]
    reset_at = repo.get_kv("preferences_reset_at")
    totals: dict[tuple[str, str], float] = defaultdict(float)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for fb in repo.feedback_rows():
        if reset_at and fb.created_at and fb.created_at.isoformat() < reset_at[:19]:
            continue
        if fb.action in {"save", "applied", "dismiss"}:
            delta = {"save": cfg["save_delta"], "applied": cfg["applied_delta"], "dismiss": cfg["dismiss_delta"]}[fb.action]
            for dim, key in (("job_family", fb.job_family), ("company", fb.company), ("country", fb.country)):
                if key:
                    # Company/country signals are weaker than the role-family signal.
                    weight = 1.0 if dim == "job_family" else 0.5
                    totals[(dim, key)] += delta * weight
                    counts[(dim, key)] += 1
        elif fb.action == "reason" and fb.reason in DISMISS_REASONS:
            dim, fixed_key, _ = DISMISS_REASONS[fb.reason]
            key = fixed_key or {"job_family": fb.job_family, "company": fb.company, "country": fb.country}.get(dim or "")
            if dim and key:
                totals[(dim, key)] += cfg["reason_delta"]
                counts[(dim, key)] += 1
    rows = []
    for (dim, key), value in totals.items():
        bounded = max(cfg["min_adjustment"], min(cfg["max_adjustment"], value))
        if bounded:
            rows.append({"dimension": dim, "key": key, "delta": round(bounded, 1), "evidence_count": counts[(dim, key)]})
    repo.replace_preference_adjustments(rows)
    return rows


def adjustments_for(prefs: list[Any], job_family: str | None, company: str | None, country: str | None,
                    cold_calling: bool) -> list[Adjustment]:
    out = []
    for p in prefs:
        applies = (p.dimension == "job_family" and p.key == job_family) or (p.dimension == "company" and p.key == company) \
            or (p.dimension == "country" and p.key == country) or (p.dimension == "pattern" and p.key == "cold_calling" and cold_calling)
        if applies:
            out.append(Adjustment(label=f"learned preference: {p.dimension}={p.key} ({p.evidence_count} feedback)", delta=p.delta))
    return out
