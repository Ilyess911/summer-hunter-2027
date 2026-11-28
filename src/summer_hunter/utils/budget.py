"""Monthly cap on every paid model call (analysis, emails, off-market scout), on top of the daily caps.

The user's API credit renews on the 11th (100 USD); this bot may use at most `monthly_budget_usd` of it
(25 USD, decided 27 Nov 2026). A cycle runs from the 11th to the 10th of the next month (Europe/Paris dates,
the same `local_date` the llm_usage rows carry)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select

from summer_hunter.database.models import LLMUsage

DEFAULT_CAP = 25.0
DEFAULT_CYCLE_DAY = 11


def cycle_start(day: date, cycle_day: int = DEFAULT_CYCLE_DAY) -> date:
    if day.day >= cycle_day:
        return day.replace(day=cycle_day)
    year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    return date(year, month, cycle_day)


def month_spent(session, day: date, cycle_day: int = DEFAULT_CYCLE_DAY) -> float:  # noqa: ANN001
    start = cycle_start(day, cycle_day).isoformat()
    return float(session.scalar(select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0))
                                .where(LLMUsage.local_date >= start, LLMUsage.local_date <= day.isoformat())) or 0.0)


def month_left(session, day: date, llm_cfg: dict[str, Any]) -> float:  # noqa: ANN001
    cap = float(llm_cfg.get("monthly_budget_usd", DEFAULT_CAP))
    return cap - month_spent(session, day, int(llm_cfg.get("budget_cycle_day", DEFAULT_CYCLE_DAY)))


def month_line(session, day: date, llm_cfg: dict[str, Any]) -> str:  # noqa: ANN001
    cap = float(llm_cfg.get("monthly_budget_usd", DEFAULT_CAP))
    spent = month_spent(session, day, int(llm_cfg.get("budget_cycle_day", DEFAULT_CYCLE_DAY)))
    warn = float(llm_cfg.get("monthly_warn_usd", cap * 0.8))
    flag = " ⚠️" if spent >= warn else ""
    return f"Mois API : {spent:.2f} $ / {cap:.0f} $ (remise à zéro le {int(llm_cfg.get('budget_cycle_day', 11))}){flag}"
