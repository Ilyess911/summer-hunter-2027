"""Early recruitment signals: public hints that internships may open, kept apart from vacancies.

CONFIRMED_OPENING   a post or listing tied to a real, identifiable opening
RECRUITMENT_SIGNAL  an announced campaign, upcoming programme, talent pool or "my team will hire"
COMPANY_WATCH       expansion news (new site, team growth, robotics deployment) worth watching

A signal is never shown as a vacancy. It is confirmed only when an official posting from the
same company appears after it.
"""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select

from summer_hunter.database.models import LinkedInPost, Opportunity, RecruitmentSignal, utcnow
from summer_hunter.database.repository import Repository, as_utc
from summer_hunter.engine.normalization import normalize_company
from summer_hunter.utils.text import normalize_for_match

CONFIRMED, SIGNAL, WATCH = "CONFIRMED_OPENING", "RECRUITMENT_SIGNAL", "COMPANY_WATCH"
TALENT_POOL = re.compile(r"\b(talent (community|pool|network)|future (opportunities|openings|internships)|expression of interest|"
                         r"general (application|interest)|evergreen|pipeline (role|requisition)|join our talent)\b")
WATCH_CUES = re.compile(r"\b(new (site|plant|factory|facility|office|hub)|opening (a|our) new|expan(d|ding|sion) (our|the) team|"
                        r"growing (our|the) team|scal(e|ing) (up )?our|new (robotics|automation) deployment|deploying robots|"
                        r"raised (a |our )?(series|seed)|funding round|inaugur\w+)\b")
POST_LEVEL = {"confirmed_internship": CONFIRMED, "recruiter_announcement_with_vacancy": CONFIRMED,
              "hiring_manager_upcoming_opening": SIGNAL, "general_recruitment_campaign": SIGNAL}


def classify_post(post: LinkedInPost) -> str | None:
    level = POST_LEVEL.get(post.classification)
    if level == CONFIRMED and post.opportunity_id is None:
        level = SIGNAL                      # no identifiable opening behind it yet
    if level is None and WATCH_CUES.search(normalize_for_match(post.text or "")):
        level = WATCH
    return level


def sync(repo: Repository) -> dict[str, int]:
    """Refresh signals from stored posts and talent-pool listings, then confirm those an opening now backs.

    Bulk-loaded: one query per table, whatever the number of rows.
    """
    existing = {r.source_url: r for r in repo.s.execute(select(RecruitmentSignal)).scalars()}
    opps = list(repo.s.execute(select(Opportunity).where(Opportunity.status == "active")).scalars())
    by_id = {o.id: o for o in opps}
    added = 0

    def upsert(url: str, level: str, company: str | None, summary: str, source: str, published, opp_id) -> None:  # noqa: ANN001
        nonlocal added
        row = existing.get(url)
        if row is None:
            row = RecruitmentSignal(level=level, company=company, summary=summary[:500], source=source, source_url=url,
                                    published_at=published, opportunity_id=opp_id,
                                    confirmed_at=utcnow() if level == CONFIRMED else None)
            repo.s.add(row)
            existing[url] = row
            added += 1
        elif level == CONFIRMED and row.level != CONFIRMED:
            row.level, row.opportunity_id, row.confirmed_at = CONFIRMED, opp_id or row.opportunity_id, utcnow()

    for post in repo.s.execute(select(LinkedInPost)).scalars():
        level = classify_post(post)
        if level:
            opp = by_id.get(post.opportunity_id) if post.opportunity_id else None
            upsert(post.post_url, level, opp.company_name if opp else None, (post.text or "")[:300], "linkedin_post",
                   post.published_at, post.opportunity_id)
    real_by_company: dict[str, list[Opportunity]] = {}
    for o in opps:
        if TALENT_POOL.search(normalize_for_match(o.title)):
            upsert(o.official_url, SIGNAL, o.company_name, f"Talent pool / future openings: {o.title}", "ats_listing",
                   o.source_published_at or o.first_discovered_at, None)
        elif (o.family_tier or 0) > 0:
            real_by_company.setdefault(o.normalized_company, []).append(o)
    repo.s.flush()
    confirmed = 0
    for sig in existing.values():
        if sig.level == CONFIRMED or not sig.company:
            continue
        since = as_utc(sig.published_at or sig.detected_at or utcnow()) - timedelta(days=1)
        later = [o for o in real_by_company.get(normalize_company(sig.company), []) if as_utc(o.first_discovered_at) >= since]
        if later:
            sig.level, sig.opportunity_id, sig.confirmed_at = CONFIRMED, later[0].id, utcnow()
            confirmed += 1
    repo.s.flush()
    return {"added": added, "confirmed": confirmed}


def recent(repo: Repository, days: int = 30, levels: set[str] | None = None) -> list[RecruitmentSignal]:
    cutoff = utcnow() - timedelta(days=days)
    rows = repo.s.execute(select(RecruitmentSignal).where(RecruitmentSignal.detected_at >= cutoff)
                          .order_by(RecruitmentSignal.detected_at.desc())).scalars()
    return [r for r in rows if not levels or r.level in levels]
