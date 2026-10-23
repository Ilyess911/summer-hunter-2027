"""Dashboard figures, computed in Python from stored records (never from spreadsheet formulas)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select

from summer_hunter.crm import statuses as st
from summer_hunter.crm.followups import all_suggestions
from summer_hunter.crm.service import CRM
from summer_hunter.database.models import Opportunity
from summer_hunter.engine.geo import country_name


def rate(num: int, den: int) -> float | None:
    """A ratio, or None when there is nothing to divide by."""
    return round(num / den, 4) if den else None


@dataclass
class Metrics:
    discovered: int = 0
    shortlisted: int = 0
    tracked: int = 0
    submitted: int = 0
    awaiting: int = 0
    interviews_scheduled: int = 0
    offers: int = 0
    rejections: int = 0
    withdrawn: int = 0
    followups: int = 0
    responded: int = 0
    reached_interview: int = 0
    response_rate: float | None = None
    interview_rate: float | None = None
    interview_to_offer: float | None = None
    success_rate: float | None = None
    by_status: list[tuple[str, int]] = field(default_factory=list)
    by_country: list[tuple[str, int]] = field(default_factory=list)
    by_family: list[tuple[str, int]] = field(default_factory=list)
    by_week: list[tuple[str, int]] = field(default_factory=list)
    funnel: list[tuple[str, int]] = field(default_factory=list)


def compute(crm: CRM, today: date | None = None) -> Metrics:
    today = today or crm.today()
    apps = crm.applications()
    m = Metrics()
    m.discovered = crm.s.scalar(select(func.count()).select_from(Opportunity)) or 0
    saved = {oid for (oid,) in crm.s.execute(select(Opportunity.id).where(Opportunity.user_status == "saved"))}
    m.shortlisted = len(saved | {a.opportunity_id for a in apps})
    m.tracked = len(apps)
    submitted = [a for a in apps if a.date_applied is not None]
    m.submitted = len(submitted)
    m.awaiting = sum(a.status == "APPLIED" for a in apps)
    m.interviews_scheduled = sum(a.interview_at is not None and crm.local_date(a.interview_at) >= today
                                 and a.status not in st.TERMINAL - {"OFFER_ACCEPTED"} for a in apps)
    m.offers = sum(a.status in st.OFFERS or a.furthest_status in st.OFFERS for a in apps)
    m.rejections = sum(a.status == "REJECTED" for a in apps)
    m.withdrawn = sum(a.status == "WITHDRAWN" for a in apps)
    m.followups = sum(r.kind != "deadline" for r in all_suggestions(crm, today))

    def depth(a) -> int:  # noqa: ANN001
        return st.RANK.get(a.furthest_status, -1)

    m.responded = sum(depth(a) > st.RANK["APPLIED"] or a.status == "REJECTED" for a in submitted)
    m.reached_interview = sum(depth(a) >= st.RANK["INTERVIEW"] for a in submitted)
    offers_after_apply = sum(depth(a) >= st.RANK["OFFER_RECEIVED"] for a in submitted)
    m.response_rate = rate(m.responded, m.submitted)
    m.interview_rate = rate(m.reached_interview, m.submitted)
    m.interview_to_offer = rate(offers_after_apply, m.reached_interview)
    m.success_rate = rate(offers_after_apply, m.submitted)

    counts = Counter(a.status for a in apps)
    m.by_status = [(s, counts.get(s, 0)) for s in st.STATUSES]
    m.by_country = Counter(country_name(a.opportunity.country_code) for a in apps).most_common(12)
    m.by_family = Counter((a.opportunity.job_family or "unknown").replace("_", " ") for a in apps).most_common(12)
    weeks = Counter(a.date_applied - timedelta(days=a.date_applied.weekday()) for a in submitted)
    m.by_week = [(w.isoformat(), n) for w, n in sorted(weeks.items())]
    m.funnel = [("Tracked", m.tracked), ("Submitted", m.submitted), ("Response", m.responded),
                ("Interview", m.reached_interview), ("Offer", offers_after_apply),
                ("Accepted", sum(a.status == "OFFER_ACCEPTED" for a in submitted))]
    return m
