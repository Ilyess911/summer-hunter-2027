"""Global application priority: one ranking built from separate, visible dimensions.

    recruitability 30% · career fit 20% · compensation 20% · networking 10% · quality 10% · urgency 5% · ease 5%
    (conversion mode; exploration mode shifts weight toward fit and quality, see config)

These are initial, configurable weights, not validated probabilities. Rules:
- an opportunity that failed eligibility is not ranked (None);
- unknown compensation is never a zero: its weight is redistributed, a small penalty applies and the
  score is flagged provisional;
- confirmed unpaid internships are filtered out unless the user allows them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from summer_hunter.engine.compensation import PAID_CONFIRMED, UNPAID_CONFIRMED, CompensationReport
from summer_hunter.utils.text import normalize_for_match

OFFICIAL = {"greenhouse", "lever", "ashby", "workday", "career_page"}


@dataclass
class GlobalPriority:
    score: float | None
    provisional: bool = False
    components: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    filtered: str | None = None               # why it is kept out of the ranked lists
    effort_minutes: int = 0
    notes: list[str] = field(default_factory=list)


def quality(sources: list[str], description: str, watch_priority: int | None, dates_pass: bool, paid: bool) -> float:
    q = 30.0 if any(s.split(":")[0] in OFFICIAL for s in sources) else 10.0
    q += 20 if len(description or "") > 1500 else 10 if len(description or "") > 600 else 0
    q += {1: 25, 2: 15, 3: 8}.get(watch_priority or 0, 0)
    q += 15 if dates_pass else 0
    q += 10 if paid else 0
    return min(100.0, q)


def urgency(deadline: date | None, rolling: bool, posted: datetime | None, today: date) -> float:
    if deadline and deadline >= today:
        days = (deadline - today).days
        return 100.0 if days <= 7 else 80.0 if days <= 14 else 60.0 if days <= 30 else 40.0
    if posted:
        age = (today - posted.date()).days
        base = 70.0 if age <= 7 else 50.0 if age <= 30 else 30.0
        return min(100.0, base + (10 if rolling else 0))
    return 40.0


def ease(description: str, missing_mandatory: int, language_unknown: bool, documents_ready: bool) -> tuple[float, int]:
    """0-100 (higher is easier) and a rough preparation time in minutes."""
    norm = normalize_for_match(description or "")
    value, minutes = 70.0, 45
    if "cover letter" in norm or "motivation letter" in norm or "lettre de motivation" in norm:
        value -= 15
        minutes += 30
    if any(k in norm for k in ("online assessment", "case study", "coding test", "assessment centre", "assessment center")):
        value -= 10
        minutes += 60
    value -= 10 * min(missing_mandatory, 3)
    minutes += 20 * min(missing_mandatory, 3)
    if language_unknown:
        value -= 15
    if documents_ready:
        value += 15
        minutes = max(15, minutes - 30)
    return max(0.0, min(100.0, value)), minutes


def compute(fit: int, recruit: int, networking: int, comp: CompensationReport, rejected: bool, quality_score: float,
            urgency_score: float, ease_score: float, effort_minutes: int, mode: str, rules: dict[str, Any],
            comp_cfg: dict[str, Any]) -> GlobalPriority:
    gp_cfg = rules.get("global_priority") or {}
    prefs = comp_cfg.get("preferences") or {}
    gp = GlobalPriority(score=None, effort_minutes=effort_minutes)
    if rejected:
        gp.filtered = "failed a mandatory eligibility check"
        return gp
    if comp.status == UNPAID_CONFIRMED and not prefs.get("include_unpaid_internships", False) \
            and gp_cfg.get("unpaid_filtered_when_excluded", True):
        gp.filtered = "confirmed unpaid (include_unpaid_internships is false)"
        return gp
    floor = prefs.get("minimum_monthly_salary_eur")
    if floor is not None and comp.monthly_min_eur is not None and comp.monthly_min_eur < float(floor):
        gp.filtered = f"paie sous le minimum de {floor} EUR/mois que tu as fixé"
        return gp
    floor_b = prefs.get("minimum_estimated_monthly_balance_eur")
    if floor_b is not None and comp.monthly_balance_eur is not None and comp.monthly_balance_eur < float(floor_b):
        gp.filtered = f"solde estimé sous le minimum de {floor_b} EUR/mois que tu as fixé"
        return gp
    weights = dict((gp_cfg.get("weights") or {}).get(mode) or (gp_cfg.get("weights") or {}).get("conversion") or
                   {"recruitability": .30, "career_fit": .20, "compensation": .20, "networking": .10, "quality": .10,
                    "urgency": .05, "ease": .05})
    comps = {"recruitability": float(recruit), "career_fit": float(fit), "networking": float(networking),
             "quality": quality_score, "urgency": urgency_score, "ease": ease_score}
    penalty = 0.0
    if comp.score is not None:
        comps["compensation"] = float(comp.score)
    else:
        weights.pop("compensation", None)        # unknown pay: weight redistributed, never a zero
        gp.provisional = True
        penalty = float(gp_cfg.get("unknown_compensation_penalty", 5))
        gp.notes.append("Rémunération inconnue : score provisoire (poids redistribué, légère pénalité).")
    if comp.status == UNPAID_CONFIRMED:
        penalty += float(gp_cfg.get("unpaid_penalty", 25))
    if prefs.get("prefer_paid_internships", True) and comp.status == PAID_CONFIRMED:
        gp.notes.append("Stage payé confirmé.")
    total_w = sum(weights.values()) or 1.0
    value = sum(comps[k] * w for k, w in weights.items() if k in comps) / total_w - penalty
    gp.score = round(max(0.0, min(100.0, value)), 1)
    gp.components = {k: round(v, 1) for k, v in comps.items()}
    gp.weights = {k: round(w / total_w, 3) for k, w in weights.items()}
    return gp
