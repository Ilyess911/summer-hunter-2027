"""Analytics and Hunter Performance read models. Real records only; samples are shown with their size."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from summer_hunter.crm import metrics as crm_metrics
from summer_hunter.crm.service import CRM
from summer_hunter.database.models import (
    CollectionRun,
    CRMContact,
    Interaction,
    LLMUsage,
    Opportunity,
    OutreachMessage,
    SourceRecord,
)
from summer_hunter.database.repository import Repository, as_utc
from summer_hunter.engine import analytics
from summer_hunter.engine.geo import country_name
from summer_hunter.engine.regions import REGIONS

BINS = [0, 500, 1000, 1500, 2000, 3000, 4000, 6000, 10**9]


def _bin_label(lo: int, hi: int) -> str:
    return f"{lo // 1000}k+ €" if hi >= 10**9 else f"{lo:,}-{hi:,} €".replace(",", " ")


def analytics_report(s: Session) -> dict[str, Any]:
    crm = CRM(s)
    m = crm_metrics.compute(crm)
    apps = crm.applications()
    relevant = [o for o in s.execute(select(Opportunity).where(Opportunity.status == "active", Opportunity.family_tier > 0,
                                                               Opportunity.eligibility_status != "REJECTED")).scalars()]
    paid = [o.monthly_min_eur for o in relevant if o.compensation_status == "PAID_CONFIRMED" and o.monthly_min_eur is not None]
    hist = Counter()
    for v in paid:
        for lo, hi in zip(BINS, BINS[1:], strict=False):
            if lo <= v < hi:
                hist[_bin_label(lo, hi)] += 1
    sent = [i for i in s.execute(select(Interaction).where(Interaction.kind == "message_sent")).scalars()]
    replied_contacts = {i.contact_id for i in s.execute(select(Interaction).where(Interaction.kind == "reply")).scalars()}
    messaged = {i.contact_id for i in sent}
    weeks = Counter((as_utc(i.occurred_at).date() - timedelta(days=as_utc(i.occurred_at).weekday())).isoformat() for i in sent)
    sources: dict[str, dict[str, int]] = {}
    tracked = {a.opportunity_id for a in apps}
    rel_ids = {o.id for o in relevant}
    for rec in s.execute(select(SourceRecord)).scalars():
        key = rec.source.split(":")[0]
        row = sources.setdefault(key, {"listings": 0, "relevant": 0, "tracked": 0})
        row["listings"] += 1
        row["relevant"] += rec.opportunity_id in rel_ids
        row["tracked"] += rec.opportunity_id in tracked
    return {
        "funnel": [{"stage": k, "value": v} for k, v in m.funnel],
        "rates": {"response": m.response_rate, "interview": m.interview_rate, "interview_to_offer": m.interview_to_offer,
                  "success": m.success_rate, "submitted": m.submitted},
        "applications_over_time": [{"week": w, "value": n} for w, n in m.by_week],
        "by_country": [{"name": k, "value": v} for k, v in m.by_country],
        "by_family": [{"name": k, "value": v} for k, v in m.by_family],
        "compensation": {"histogram": [{"bin": _bin_label(lo, hi), "value": hist.get(_bin_label(lo, hi), 0)}
                                       for lo, hi in zip(BINS, BINS[1:], strict=False)],
                         "paid_count": sum(o.compensation_status == "PAID_CONFIRMED" for o in relevant),
                         "converted_count": len(paid), "average_min_eur": round(sum(paid) / len(paid)) if paid else None,
                         "balance_available": sum(o.monthly_balance_eur is not None for o in relevant),
                         "by_country": [{"name": country_name(c) if c else "Unknown", "value": n} for c, n in
                                        Counter(o.country_code for o in relevant if o.compensation_status == "PAID_CONFIRMED").most_common(10)]},
        "outreach": {"sent": len(sent), "contacts_messaged": len(messaged), "replied": len(messaged & replied_contacts),
                     "response_rate": round(len(messaged & replied_contacts) / len(messaged), 3) if messaged else None,
                     "per_week": [{"week": w, "value": n} for w, n in sorted(weeks.items())],
                     "drafts": sum(1 for _ in s.execute(select(OutreachMessage.id).where(OutreachMessage.status == "draft")))},
        "sources": sorted(({"source": k, **v} for k, v in sources.items()), key=lambda r: -r["relevant"]),
        "small_sample": m.submitted < analytics.SMALL_SAMPLE,
    }


def performance_report(s: Session, last_sync: str | None) -> dict[str, Any]:
    p = analytics.compute(s, REGIONS)
    runs = list(s.execute(select(CollectionRun).where(CollectionRun.dry_run.is_(False)).order_by(CollectionRun.id.desc()).limit(15)).scalars())
    last_ok = next((r for r in runs if r.status in {"ok", "partial"}), None)
    repo = Repository(s)
    usage = repo.usage_summary(30)
    return {
        "last_success": as_utc(last_ok.finished_at or last_ok.started_at).isoformat() if last_ok else None,
        "last_sync": last_sync,
        "discovered": p.discovered, "relevant": p.relevant,
        "duplicate_rate": round(p.duplicates_merged / (p.discovered + p.duplicates_merged), 3) if p.discovered else None,
        "compensation_coverage": {"value": p.compensation_coverage.value, "label": p.compensation_coverage.label()},
        "precision_at_10": {"value": p.precision_at_10.value, "label": p.precision_at_10.label()},
        "actionability": {"value": p.actionability.value, "label": p.actionability.label()},
        "paid_rate": {"value": p.paid_rate.value, "label": p.paid_rate.label()},
        "llm": {"calls": p.llm_calls, "cost_usd": round(p.llm_cost_usd, 4),
                "per_day": [{"day": d, "calls": n, "cost": round(c, 4)} for d, n, c in usage]},
        "sources": [{**x, "error": h.last_error if (h := next((y for y in repo.health() if y.source == x["source"]), None)) else None}
                    for x in p.sources],
        "runs": [{"id": r.id, "trigger": r.trigger, "status": r.status, "started": as_utc(r.started_at).isoformat(),
                  "finished": as_utc(r.finished_at).isoformat() if r.finished_at else None,
                  "errors": (r.errors or [])[:20], "stats": {k: v for k, v in (r.stats or {}).items() if k != "sources"}} for r in runs],
        "contacts": s.scalar(select(CRMContact.id).limit(1)) is not None,
        "llm_rows": s.scalar(select(LLMUsage.id).limit(1)) is not None,
    }
