"""Hunter performance: what the bot finds, how reliable it is, and what it costs.

Rates are shown with their sample size; below `SMALL_SAMPLE` they are flagged as indicative only.
Missing salary information is counted as "unknown", never as unpaid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from summer_hunter.crm import metrics as crm_metrics
from summer_hunter.crm.service import CRM
from summer_hunter.database.models import Contact, LLMUsage, Opportunity, SourceRecord, UserFeedback
from summer_hunter.database.repository import Repository

SMALL_SAMPLE = 20
GOOD_RATINGS = {"EXCELLENT", "RELEVANT"}
RECOMMENDED = {"APPLY_NOW", "PREPARE_APPLICATION", "CONTACT_RECRUITER", "VERIFY_ELIGIBILITY", "VERIFY_COMPENSATION"}


@dataclass
class Rate:
    value: float | None
    numerator: int
    denominator: int

    @property
    def small(self) -> bool:
        return self.denominator < SMALL_SAMPLE

    def label(self) -> str:
        if self.value is None:
            return "n/a (0)"
        return f"{self.value:.0%} ({self.numerator}/{self.denominator}{', indicative' if self.small else ''})"


def _rate(num: int, den: int) -> Rate:
    return Rate(round(num / den, 4) if den else None, num, den)


@dataclass
class Performance:
    discovered: int = 0
    relevant: int = 0
    paid_confirmed: int = 0
    compensation_unknown: int = 0
    ineligible: int = 0
    duplicates_merged: int = 0
    contacts: int = 0
    applications_created: int = 0
    applications_submitted: int = 0
    interviews: int = 0
    offers: int = 0
    precision_at_10: Rate = field(default_factory=lambda: Rate(None, 0, 0))
    actionability: Rate = field(default_factory=lambda: Rate(None, 0, 0))
    paid_rate: Rate = field(default_factory=lambda: Rate(None, 0, 0))
    compensation_coverage: Rate = field(default_factory=lambda: Rate(None, 0, 0))
    response_rate: float | None = None
    interview_rate: float | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: int = 0
    llm_cost_usd: float = 0.0
    by_region: dict[str, int] = field(default_factory=dict)


def compute(session, regions: dict[str, set[str]] | None = None) -> Performance:  # noqa: ANN001
    repo = Repository(session)
    p = Performance()
    opps = list(session.execute(select(Opportunity)).scalars())
    p.discovered = len(opps)
    relevant = [o for o in opps if (o.family_tier or 0) > 0 and o.eligibility_status != "REJECTED" and o.status == "active"]
    p.relevant = len(relevant)
    p.paid_confirmed = sum(o.compensation_status == "PAID_CONFIRMED" for o in relevant)
    p.compensation_unknown = sum(o.compensation_status in (None, "COMPENSATION_UNKNOWN") for o in relevant)
    p.ineligible = sum(o.eligibility_status == "REJECTED" for o in opps)
    p.duplicates_merged = max(0, (session.scalar(select(func.count()).select_from(SourceRecord)) or 0) - len(opps))
    p.contacts = session.scalar(select(func.count()).select_from(Contact)) or 0
    m = crm_metrics.compute(CRM(session))
    p.applications_created, p.applications_submitted = m.tracked, m.submitted
    p.interviews, p.offers = m.reached_interview, m.offers
    p.response_rate, p.interview_rate = m.response_rate, m.interview_rate
    p.paid_rate = _rate(p.paid_confirmed, p.relevant)
    known = sum(o.compensation_status not in (None, "COMPENSATION_UNKNOWN") for o in relevant)
    p.compensation_coverage = _rate(known, p.relevant)

    ratings = {}
    for fb in session.execute(select(UserFeedback).where(UserFeedback.action == "rating").order_by(UserFeedback.id)).scalars():
        ratings[fb.opportunity_id] = fb.reason                  # latest rating wins
    top10 = sorted((o for o in relevant if o.global_priority is not None), key=lambda o: -o.global_priority)[:10]
    rated_top = [o for o in top10 if o.id in ratings]
    p.precision_at_10 = _rate(sum(ratings[o.id] in GOOD_RATINGS for o in rated_top), len(rated_top))
    tracked = {a.opportunity_id for a in CRM(session).applications()}
    reviewed = [o for o in opps if o.next_action in RECOMMENDED and (o.id in ratings or o.id in tracked)]
    actionable = [o for o in reviewed if ratings.get(o.id) != "INELIGIBLE" and o.eligibility_status != "REJECTED"]
    p.actionability = _rate(len(actionable), len(reviewed))

    for h in repo.health():
        runs = h.total_runs or 0
        p.sources.append({"source": h.source, "status": h.last_status, "runs": runs, "failures": h.consecutive_failures or 0,
                          "last_items": h.last_item_count})
    p.llm_calls = session.scalar(select(func.count()).select_from(LLMUsage)) or 0
    p.llm_cost_usd = float(session.scalar(select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0))) or 0.0)
    for name, codes in (regions or {}).items():
        p.by_region[name] = sum(o.country_code in codes for o in relevant)
    return p
