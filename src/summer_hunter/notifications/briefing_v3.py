"""V3 sections of the daily briefing, built only from stored records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape

from sqlalchemy import select

from summer_hunter.bot.v3_handlers import line
from summer_hunter.crm import followups
from summer_hunter.crm.service import CRM
from summer_hunter.database.models import CRMReminder, FxRate, Opportunity
from summer_hunter.database.repository import Repository
from summer_hunter.engine import signals
from summer_hunter.engine.regions import REGIONS
from summer_hunter.engine.strategist import balanced
from summer_hunter.pipeline import in_backoff

DEADLINE_THRESHOLDS = (7, 3, 1)


@dataclass
class Briefing:
    headline: list[str] = field(default_factory=list)
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    reminders: list[followups.Reminder] = field(default_factory=list)
    signal_ids: list[int] = field(default_factory=list)
    buttons: list[list[dict[str, str]]] = field(default_factory=list)


def opportunity_deadlines(repo: Repository, pool: list[Opportunity], today: date) -> list[followups.Reminder]:
    """7 / 3 / 1-day reminders for relevant, untracked opportunities with a stated future deadline."""
    tracked = {a.opportunity_id for a in CRM(repo.s).applications()}
    out = []
    for o in pool:
        if not o.application_deadline or o.application_deadline <= today or o.id in tracked:
            continue
        left = (o.application_deadline - today).days
        crossed = sorted(t for t in DEADLINE_THRESHOLDS if left <= t)
        if not crossed:
            continue
        keys = [f"deadline_opp:{o.id}:{o.application_deadline.isoformat()}:{t}" for t in crossed]
        if repo.s.execute(select(CRMReminder.id).where(CRMReminder.dedupe_key == keys[0])).first():
            continue
        out.append(followups.Reminder("deadline", keys[0], f"#{o.id} {o.company_name} · {o.title[:60]} : date limite le "
                                      f"{o.application_deadline:%d/%m} (J-{left})", o.application_deadline, covers=keys[1:]))
    return out


def build(repo: Repository, new: list[Opportunity], rules: dict, today: date) -> Briefing:
    b = Briefing()
    pool = repo.top_global(500)
    relevant_new = [o for o in new if (o.family_tier or 0) > 0 and o.eligibility_status != "REJECTED"]
    africa = REGIONS["africa"]
    b.headline = [
        f"{len(relevant_new)} new relevant internship(s)",
        f"{sum(o.compensation_status == 'PAID_CONFIRMED' for o in relevant_new)} confirmed paid",
        f"{sum(o.country_code in africa for o in relevant_new)} in Africa",
        f"{sum((o.networking_score or 0) >= 75 for o in relevant_new)} with a recruiter or manager contact",
    ]
    alloc = (rules.get("strategist") or {}).get("allocation", {"AMBITIOUS": .25, "TARGETED": .5, "SAFETY": .25})
    actionable = [o for o in pool if o.next_action in {"APPLY_NOW", "PREPARE_APPLICATION", "CONTACT_RECRUITER",
                                                         "VERIFY_ELIGIBILITY", "VERIFY_COMPENSATION"}]
    top = balanced(actionable, 5, alloc, lambda o: o.strategy_category, lambda o: o.global_priority)
    paid = sorted((o for o in pool if o.monthly_min_eur is not None), key=lambda o: -o.monthly_min_eur)
    value = sorted((o for o in pool if o.monthly_balance_eur is not None), key=lambda o: -o.monthly_balance_eur)
    if top:
        b.headline.append(f"TOP RECOMMENDATION: {top[0].title[:60]} @ {top[0].company_name[:30]}")
    if paid:
        b.headline.append(f"BEST COMPENSATION: {paid[0].title[:60]} @ {paid[0].company_name[:30]} (€{paid[0].monthly_min_eur:,.0f}/month)")
    b.sections.append(("HIGH-PRIORITY APPLICATIONS", [line(o) for o in top]))
    b.sections.append(("BEST-PAID INTERNSHIPS", [line(o) for o in paid[:3]]))
    b.sections.append(("BEST FINANCIAL VALUE", [line(o) + f" · solde {o.monthly_balance_eur:+,.0f} €" for o in value[:3]]
                       or ["No sourced living costs configured yet: balances are not estimated."]))
    b.sections.append(("AFRICAN OPPORTUNITIES", [line(o) for o in sorted((o for o in pool if o.country_code in africa),
                                                                          key=lambda o: -o.global_priority)[:3]]))
    b.sections.append(("NETWORKING OPPORTUNITIES", [line(o) for o in sorted((o for o in pool if (o.networking_score or 0) >= 75),
                                                                             key=lambda o: -o.global_priority)[:3]]))
    b.reminders = opportunity_deadlines(repo, pool, today)
    soon = sorted((o for o in pool if o.application_deadline and today <= o.application_deadline <= today + timedelta(days=14)),
                  key=lambda o: o.application_deadline)
    b.sections.append(("APPLICATION DEADLINES", [f"#{o.id} {escape(o.company_name[:30])} · {escape(o.title[:50])} : "
                                                 f"{o.application_deadline:%d/%m}" for o in soon[:5]]))
    fresh = [s for s in signals.recent(repo, 7) if not s.notified][:4]
    b.signal_ids = [s.id for s in fresh]             # only what is shown is marked as delivered
    b.sections.append(("EARLY RECRUITMENT SIGNALS", [f"{escape(s.level)} · {escape(s.company or '?')} : {escape(s.summary[:100])}"
                                                     for s in fresh[:4]]))
    health = repo.health()
    failing = [h for h in health if (h.consecutive_failures or 0) >= 2]
    resting = [h for h in health if in_backoff(h)]
    run = repo.last_run(successful_only=False)
    fx_day = repo.s.execute(select(FxRate.rate_date).order_by(FxRate.rate_date.desc()).limit(1)).scalar_one_or_none()
    b.sections.append(("BOT HEALTH", [
        f"Last run: {run.status if run else 'none'} · sources failing: {len(failing)} · resting (backoff): {len(resting)}",
        f"Exchange rates: {fx_day.isoformat() if fx_day else 'not loaded yet'}",
    ]))
    ids = [o.id for o in top[:3]]
    b.buttons = [[{"text": f"🎯 #{i}", "callback_data": f"ex:{i}"} for i in ids]] if ids else []
    return b
