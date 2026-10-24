"""Read models for Summer Hunter OS: what the web app shows, computed from the existing engines.

No business rule lives here: scores, statuses, compensation and timing come from the engines
(pipeline, crm, compensation, strategist, networking). This module only queries and shapes them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from summer_hunter.crm import followups
from summer_hunter.crm import statuses as st
from summer_hunter.crm.service import CRM
from summer_hunter.crm.validation import app_code, contact_code
from summer_hunter.database.models import (
    CollectionRun,
    ContactEvidence,
    ContactOpportunity,
    CRMApplication,
    CRMContact,
    CRMEvent,
    Interaction,
    KeyValue,
    Opportunity,
    OutreachMessage,
    RecruitmentSignal,
)
from summer_hunter.database.repository import Repository, as_utc
from summer_hunter.engine.geo import country_name
from summer_hunter.engine.regions import REGIONS, region_of
from summer_hunter.notifications.formatter import safe_url
from summer_hunter.os.networking import CATEGORY_LABEL

SORTS = {"priority": Opportunity.global_priority, "fit": Opportunity.fit_score, "recruitability": Opportunity.recruitability_score,
         "compensation": Opportunity.monthly_min_eur, "discovered": Opportunity.first_discovered_at,
         "company": Opportunity.company_name, "deadline": Opportunity.application_deadline}


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return as_utc(v).isoformat()
    return v.isoformat()


def opp_row(o: Opportunity, app: CRMApplication | None = None) -> dict[str, Any]:
    return {
        "id": o.id, "company": o.company_name, "title": o.title, "country": country_name(o.country_code) if o.country_code else None,
        "country_code": o.country_code, "city": o.city, "region": region_of(o.country_code), "location": o.location_text,
        "family": o.job_family, "category": o.career_category, "track": o.career_track,
        "fit": o.fit_score, "recruitability": o.recruitability_score, "networking": o.networking_score,
        "priority": o.global_priority, "provisional": bool(o.global_provisional), "eligibility": o.eligibility_status,
        "compensation": {"status": o.compensation_status, "monthly_min_eur": o.monthly_min_eur, "monthly_max_eur": o.monthly_max_eur,
                         "currency": o.salary_currency, "period": o.salary_period, "basis": o.salary_basis,
                         "confidence": o.compensation_confidence, "score": o.compensation_score,
                         "balance_eur": o.monthly_balance_eur, "living_cost_eur": o.living_cost_eur},
        "next_action": o.next_action, "strategy": o.strategy_category, "deadline": _iso(o.application_deadline),
        "deadline_type": o.deadline_type, "discovered": _iso(o.first_discovered_at), "verified": _iso(o.last_verified_at),
        "status": o.status, "user_status": o.user_status,
        "application": {"id": app.id, "code": app_code(app.id), "status": app.status} if app else None,
        "apply_url": safe_url(o.apply_url) or safe_url(o.official_url),
    }


def _apps_by_opp(s: Session) -> dict[int, CRMApplication]:
    return {a.opportunity_id: a for a in s.execute(select(CRMApplication)).scalars()}


def list_opportunities(s: Session, q: str = "", region: str | None = None, country: str | None = None, category: str | None = None,
                       track: str | None = None, paid: bool | None = None, min_recruit: int | None = None,
                       eligibility: str | None = None, status: str | None = None, deadline_days: int | None = None,
                       min_monthly: float | None = None, sort: str = "priority", order: str = "desc", page: int = 1,
                       page_size: int = 50, include_hidden: bool = False) -> dict[str, Any]:
    stmt = select(Opportunity).where((Opportunity.family_tier > 0) | Opportunity.id.in_(select(CRMApplication.opportunity_id)))
    if not include_hidden:
        stmt = stmt.where(Opportunity.status == "active", Opportunity.eligibility_status != "REJECTED",
                          or_(Opportunity.next_action.is_(None), Opportunity.next_action != "DISCARD"))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Opportunity.title).like(like), func.lower(Opportunity.company_name).like(like),
                              func.lower(Opportunity.location_text).like(like)))
    if region in REGIONS:
        stmt = stmt.where(Opportunity.country_code.in_(sorted(REGIONS[region])))
    if country:
        stmt = stmt.where(Opportunity.country_code == country.upper())
    if category:
        stmt = stmt.where(Opportunity.career_category == category)
    if track:
        stmt = stmt.where(Opportunity.career_track == track)
    if paid:
        stmt = stmt.where(Opportunity.compensation_status == "PAID_CONFIRMED")
    if min_recruit is not None:
        stmt = stmt.where(Opportunity.recruitability_score >= min_recruit)
    if eligibility:
        stmt = stmt.where(Opportunity.eligibility_status == eligibility)
    if status == "untracked":
        stmt = stmt.where(~Opportunity.id.in_(select(CRMApplication.opportunity_id)))
    elif status == "tracked":
        stmt = stmt.where(Opportunity.id.in_(select(CRMApplication.opportunity_id)))
    if deadline_days is not None:
        today = datetime.now(timezone.utc).date()
        stmt = stmt.where(Opportunity.application_deadline.between(today, today + timedelta(days=deadline_days)))
    if min_monthly is not None:
        stmt = stmt.where(Opportunity.monthly_min_eur >= min_monthly)
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = SORTS.get(sort, Opportunity.global_priority)
    stmt = stmt.order_by(col.desc().nulls_last() if order == "desc" else col.asc().nulls_last(), Opportunity.id.desc())
    page_size = max(1, min(page_size, 200))
    rows = list(s.execute(stmt.offset((max(page, 1) - 1) * page_size).limit(page_size)).scalars())
    apps = _apps_by_opp(s)
    facets = {
        "categories": sorted({c for (c,) in s.execute(select(Opportunity.career_category).distinct()) if c}),
        "countries": sorted({(c, country_name(c)) for (c,) in s.execute(select(Opportunity.country_code).distinct()) if c},
                            key=lambda x: x[1]),
    }
    return {"items": [opp_row(o, apps.get(o.id)) for o in rows], "total": total, "page": page, "page_size": page_size,
            "facets": {"categories": facets["categories"], "countries": [{"code": c, "name": n} for c, n in facets["countries"]]}}


def opportunity_detail(s: Session, opp_id: int) -> dict[str, Any] | None:
    o = s.get(Opportunity, opp_id)
    if o is None:
        return None
    repo = Repository(s)
    fit, ev = repo.latest_fit(o.id), repo.latest_eligibility(o.id)
    app = CRM(s).application_for(o.id)
    data = opp_row(o, app)
    data.update({
        "description": (o.description_text or "")[:20000], "official_url": safe_url(o.official_url),
        "sources": [{"source": x.source, "url": safe_url(x.url), "first_seen": _iso(x.first_seen_at), "last_seen": _iso(x.last_seen_at)}
                    for x in o.sources],
        "criteria": ev.criteria if ev else [], "sponsorship": ev.sponsorship if ev else None,
        "fit_report": fit.report if fit else None, "recruitability_report": fit.recruitability if fit else None,
        "compensation_report": fit.compensation if fit else None, "strategy": fit.strategy if fit else None,
        "contacts": contacts_for_opportunity(s, o.id),
        "signals": [{"level": g.level, "summary": g.summary, "url": safe_url(g.source_url), "published": _iso(g.published_at)}
                    for g in s.execute(select(RecruitmentSignal).where(RecruitmentSignal.opportunity_id == o.id)).scalars()],
    })
    return data


def contact_row(c: CRMContact) -> dict[str, Any]:
    return {"id": c.id, "code": contact_code(c.id), "name": c.name, "company": c.company, "position": c.position,
            "category": c.category or "other", "category_label": CATEGORY_LABEL.get(c.category or "other", "Other"),
            "linkedin_url": safe_url(c.linkedin_url), "verification": c.verification or "verified",
            "status": c.interaction_status, "next_follow_up": _iso(c.next_followup_date), "dismissed": bool(c.dismissed)}


def _link_row(link: ContactOpportunity) -> dict[str, Any]:
    return {"score": link.score, "components": link.score_components, "explanation": link.explanation,
            "timing": {"action": link.timing_action, "date": _iso(link.timing_date), "reason": link.timing_reason,
                       "confidence": link.timing_confidence}, "note": link.relationship_note}


def contacts_for_opportunity(s: Session, opp_id: int) -> list[dict[str, Any]]:
    out = []
    for link in s.execute(select(ContactOpportunity).where(ContactOpportunity.opportunity_id == opp_id)
                          .order_by(ContactOpportunity.score.desc().nulls_last())).scalars():
        c = s.get(CRMContact, link.contact_id)
        if c and not c.dismissed:
            out.append(contact_row(c) | _link_row(link))
    return out


def contact_detail(s: Session, contact_id: int) -> dict[str, Any] | None:
    c = s.get(CRMContact, contact_id)
    if c is None:
        return None
    links = []
    for link in s.execute(select(ContactOpportunity).where(ContactOpportunity.contact_id == c.id)).scalars():
        o = s.get(Opportunity, link.opportunity_id)
        if o:
            links.append({"opportunity": {"id": o.id, "title": o.title, "company": o.company_name, "country": country_name(o.country_code),
                                          "priority": o.global_priority}} | _link_row(link))
    links.sort(key=lambda x: -(x["score"] or 0))
    return contact_row(c) | {
        "notes": c.notes, "source": c.source,
        "evidence": [{"kind": e.kind, "url": safe_url(e.url), "excerpt": e.excerpt, "verified": e.verified, "retrieved": _iso(e.retrieved_at),
                      "opportunity_id": e.opportunity_id}
                     for e in s.execute(select(ContactEvidence).where(ContactEvidence.contact_id == c.id)).scalars()],
        "opportunities": links,
        "messages": [message_row(m) for m in s.execute(select(OutreachMessage).where(OutreachMessage.contact_id == c.id)
                                                       .order_by(OutreachMessage.id.desc())).scalars()],
        "interactions": [{"id": i.id, "kind": i.kind, "at": _iso(i.occurred_at), "note": i.note, "opportunity_id": i.opportunity_id,
                          "follow_up_on": _iso(i.follow_up_on)}
                         for i in s.execute(select(Interaction).where(Interaction.contact_id == c.id)
                                            .order_by(Interaction.occurred_at.desc())).scalars()],
    }


def message_row(m: OutreachMessage) -> dict[str, Any]:
    return {"id": m.id, "contact_id": m.contact_id, "opportunity_id": m.opportunity_id, "kind": m.kind, "variant": m.variant,
            "text": m.text, "generator": m.generator, "evidence": m.evidence_refs or [], "validation": m.validation or {},
            "personalization": m.personalization, "status": m.status, "created": _iso(m.created_at), "sent": _iso(m.sent_at)}


def list_contacts(s: Session, q: str = "", category: str | None = None, verification: str | None = None) -> list[dict[str, Any]]:
    stmt = select(CRMContact).where(or_(CRMContact.dismissed.is_(None), CRMContact.dismissed.is_(False)))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(CRMContact.name).like(like), func.lower(CRMContact.company).like(like),
                              func.lower(CRMContact.position).like(like)))
    if category:
        stmt = stmt.where(CRMContact.category == category)
    if verification:
        stmt = stmt.where(CRMContact.verification == verification)
    best: dict[int, ContactOpportunity] = {}
    for link in s.execute(select(ContactOpportunity)).scalars():
        if link.contact_id not in best or (link.score or 0) > (best[link.contact_id].score or 0):
            best[link.contact_id] = link
    rows = []
    for c in s.execute(stmt).scalars():
        link = best.get(c.id)
        row = contact_row(c)
        if link:
            o = s.get(Opportunity, link.opportunity_id)
            row |= _link_row(link) | {"opportunity": {"id": o.id, "title": o.title} if o else None}
        rows.append(row)
    rows.sort(key=lambda r: -(r.get("score") or 0))
    return rows


def networking_queue(s: Session, limit: int = 30) -> list[dict[str, Any]]:
    """Who to contact next: due timing first, then score. DO_NOT_CONTACT and WAIT-without-date are left out."""
    today = datetime.now(timezone.utc).date()
    out = []
    for link in s.execute(select(ContactOpportunity).where(ContactOpportunity.timing_action.in_(
            ["CONTACT_BEFORE_APPLICATION", "CONTACT_AFTER_APPLICATION", "FOLLOW_UP"]))).scalars():
        c, o = s.get(CRMContact, link.contact_id), s.get(Opportunity, link.opportunity_id)
        if not c or not o or c.dismissed or o.status != "active":
            continue
        due = link.timing_date is not None and link.timing_date <= today
        out.append(contact_row(c) | _link_row(link) | {
            "opportunity": {"id": o.id, "title": o.title, "company": o.company_name, "priority": o.global_priority},
            "due": due})
    out.sort(key=lambda r: (not r["due"], -(r["score"] or 0)))
    return out[:limit]


def application_row(a: CRMApplication) -> dict[str, Any]:
    o = a.opportunity
    return {"id": a.id, "code": app_code(a.id), "status": a.status, "opportunity_id": o.id, "company": o.company_name, "title": o.title,
            "country": country_name(o.country_code) if o.country_code else None, "priority": o.global_priority,
            "compensation": {"monthly_min_eur": o.monthly_min_eur, "status": o.compensation_status, "basis": o.salary_basis},
            "deadline": _iso(a.deadline), "date_applied": _iso(a.date_applied), "next_action": a.next_action,
            "next_action_date": _iso(a.next_action_date), "interview_at": _iso(a.interview_at), "version": a.version,
            "updated": _iso(a.updated_at)}


def application_detail(s: Session, app_id: int) -> dict[str, Any] | None:
    crm = CRM(s)
    a = crm.application(app_id)
    if a is None:
        return None
    return application_row(a) | {
        "opportunity": opportunity_detail(s, a.opportunity_id),
        "notes": a.notes, "recruiter": a.recruiter_name, "furthest_status": a.furthest_status,
        "documents": {"cv": {"version": a.cv_version, "generated": _iso(a.cv_generated_at), "available": bool(a.cv_path)},
                      "letter": {"version": a.letter_version, "generated": _iso(a.letter_generated_at), "available": bool(a.letter_path)},
                      "submitted_folder": bool(a.submitted_folder)},
        "timeline": [{"at": _iso(e.created_at), "type": e.event_type, "previous": e.previous_value, "new": e.new_value,
                      "source": e.source, "notes": e.notes} for e in reversed(crm.events(a.id))],
        "allowed_statuses": [x for x in st.STATUSES if _allowed(a, x)],
    }


def _allowed(a: CRMApplication, target: str) -> bool:
    try:
        st.check_transition(a.status, target, a.date_applied is not None or target == "APPLIED")
        return True
    except st.TransitionError:
        return False


def pipeline(s: Session) -> dict[str, Any]:
    apps = CRM(s).applications()
    return {"columns": [{"status": x, "label": st.LABEL_FR.get(x, x)} for x in st.STATUSES],
            "items": [application_row(a) for a in apps]}


_FR_MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
_FR_DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def _fr_day(d: date | datetime) -> str:
    """'mar. 3 nov.' (French short date, no locale dependency)."""
    return f"{_FR_DAYS[d.weekday()]} {d.day} {_FR_MONTHS[d.month - 1]}"


def today_priorities(s: Session, cfg_today: date | None = None) -> list[dict[str, Any]]:
    """The actions that need the user today, each with the screen and button that performs it."""
    today = cfg_today or datetime.now(timezone.utc).date()
    crm = CRM(s)
    items: list[dict[str, Any]] = []
    for a in crm.applications(st.ACTIVE):
        o = a.opportunity
        if a.interview_at and today <= as_utc(a.interview_at).date() <= today + timedelta(days=7):
            items.append({"key": f"interview:{a.id}", "kind": "prepare_interview", "title": f"Préparer l’entretien {o.company_name}",
                          "detail": f"{o.title} · {_fr_day(as_utc(a.interview_at))} {as_utc(a.interview_at):%H:%M}", "href": f"/applications/{a.id}", "rank": 0})
        if a.next_action_date and a.next_action_date <= today:
            items.append({"key": f"next:{a.id}:{a.next_action_date}", "kind": "next_action", "title": a.next_action or "Action prévue",
                          "detail": f"{o.company_name} · {o.title}", "href": f"/applications/{a.id}", "rank": 2})
        if a.deadline and a.status in st.PRE_APPLICATION and 0 <= (a.deadline - today).days <= 7:
            items.append({"key": f"deadline:{a.id}", "kind": "deadline", "title": f"Postuler chez {o.company_name} avant le {_fr_day(a.deadline)}",
                          "detail": o.title, "href": f"/applications/{a.id}", "rank": 1})
    for r in followups.all_suggestions(crm, today):
        if r.kind != "deadline":
            items.append({"key": r.key, "kind": "follow_up", "title": r.text, "detail": "Relance suggérée",
                          "href": f"/applications/{r.application.id}" if r.application else "/networking", "rank": 3})
    for row in networking_queue(s, 10):
        if row["due"]:
            label = {"FOLLOW_UP": "Relancer", "CONTACT_AFTER_APPLICATION": "Écrire à", "CONTACT_BEFORE_APPLICATION": "Écrire à"}
            items.append({"key": f"contact:{row['id']}:{row['opportunity']['id']}", "kind": "contact",
                          "title": f"{label.get(row['timing']['action'], 'Contacter')} {row['name']} ({row['company']})",
                          "detail": row["timing"]["reason"], "href": f"/outreach/{row['id']}?opportunity={row['opportunity']['id']}",
                          "rank": 2})
    apply_items = 0
    for o in Repository(s).top_global(30):
        if o.next_action == "APPLY_NOW" and not crm.application_for(o.id) and apply_items < 8:
            apply_items += 1
            items.append({"key": f"apply:{o.id}", "kind": "apply", "title": f"Postuler : {o.title}",
                          "detail": f"{o.company_name} · priorité {o.global_priority:.0f}", "href": f"/opportunities/{o.id}", "rank": 4})
        elif o.next_action == "VERIFY_COMPENSATION":
            items.append({"key": f"pay:{o.id}", "kind": "verify_compensation", "title": f"Vérifier la paie : {o.title}",
                          "detail": o.company_name, "href": f"/opportunities/{o.id}", "rank": 5})
        elif o.next_action == "VERIFY_ELIGIBILITY":
            items.append({"key": f"elig:{o.id}", "kind": "verify_eligibility", "title": f"Vérifier l’éligibilité : {o.title}",
                          "detail": o.company_name, "href": f"/opportunities/{o.id}", "rank": 5})
    done = {k.removeprefix("action_done:") for (k,) in s.execute(select(KeyValue.key).where(KeyValue.key.like("action_done:%")))}
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["rank"]):
        if it["key"] in seen or it["key"] in done:
            continue
        seen.add(it["key"])
        out.append(it)
    return out[:25]


def command_center(s: Session) -> dict[str, Any]:
    crm = CRM(s)
    apps = crm.applications()
    repo = Repository(s)
    top = [o for o in repo.top_global(60) if o.next_action in {"APPLY_NOW", "PREPARE_APPLICATION", "CONTACT_RECRUITER",
                                                               "VERIFY_ELIGIBILITY", "VERIFY_COMPENSATION"}][:5]
    week = datetime.now(timezone.utc) - timedelta(days=7)
    by_opp = _apps_by_opp(s)
    return {
        "counts": {
            "active": sum(a.status in st.ACTIVE for a in apps),
            "new": s.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.first_discovered_at >= week,
                                                                                Opportunity.family_tier > 0,
                                                                                Opportunity.eligibility_status != "REJECTED")) or 0,
            "awaiting": sum(a.status == "APPLIED" for a in apps),
            "interviews": sum(a.status in st.INTERVIEW_STAGES for a in apps),
            "offers": sum(a.status in st.OFFERS for a in apps),
            "paid": s.scalar(select(func.count()).select_from(Opportunity).where(
                Opportunity.compensation_status == "PAID_CONFIRMED", Opportunity.status == "active", Opportunity.family_tier > 0,
                Opportunity.eligibility_status != "REJECTED", Opportunity.global_priority.is_not(None),
                or_(Opportunity.next_action.is_(None), Opportunity.next_action != "DISCARD"))) or 0,
            "deadlines": sum(1 for a in apps if a.deadline and a.status in st.PRE_APPLICATION
                             and 0 <= (a.deadline - datetime.now(timezone.utc).date()).days <= 14),
        },
        "priorities": today_priorities(s),
        "top": [opp_row(o, by_opp.get(o.id)) for o in top],
        "networking": networking_queue(s, 6),
        "last_run": last_run(s),
    }


def last_run(s: Session) -> dict[str, Any] | None:
    r = s.execute(select(CollectionRun).where(CollectionRun.dry_run.is_(False)).order_by(CollectionRun.id.desc()).limit(1)).scalar_one_or_none()
    return {"status": r.status, "started": _iso(r.started_at), "finished": _iso(r.finished_at)} if r else None


def search(s: Session, q: str, limit: int = 6) -> dict[str, Any]:
    if len(q.strip()) < 2:
        return {"opportunities": [], "applications": [], "contacts": [], "companies": [], "messages": []}
    like = f"%{q.lower()}%"
    opps = list(s.execute(select(Opportunity).where(or_(func.lower(Opportunity.title).like(like), func.lower(Opportunity.company_name).like(like)),
                                                    Opportunity.family_tier > 0).order_by(Opportunity.global_priority.desc().nulls_last())
                          .limit(limit)).scalars())
    apps = [a for a in CRM(s).applications() if q.lower() in f"{a.opportunity.company_name} {a.opportunity.title}".lower()][:limit]
    contacts = list_contacts(s, q)[:limit]
    companies = sorted({o.company_name for o in s.execute(select(Opportunity).where(func.lower(Opportunity.company_name).like(like))
                                                            .limit(50)).scalars()})[:limit]
    msgs = list(s.execute(select(OutreachMessage).where(func.lower(OutreachMessage.text).like(like)).limit(limit)).scalars())
    return {"opportunities": [{"id": o.id, "title": o.title, "company": o.company_name} for o in opps],
            "applications": [{"id": a.id, "code": app_code(a.id), "title": a.opportunity.title, "company": a.opportunity.company_name}
                             for a in apps],
            "contacts": [{"id": c["id"], "name": c["name"], "company": c["company"]} for c in contacts],
            "companies": companies,
            "messages": [{"id": m.id, "contact_id": m.contact_id, "excerpt": m.text[:80]} for m in msgs]}


def activity(s: Session, limit: int = 50) -> list[dict[str, Any]]:
    return [{"at": _iso(e.created_at), "type": e.event_type, "company": e.company, "new": e.new_value, "source": e.source,
             "application_id": e.application_id}
            for e in s.execute(select(CRMEvent).order_by(CRMEvent.id.desc()).limit(limit)).scalars()]
