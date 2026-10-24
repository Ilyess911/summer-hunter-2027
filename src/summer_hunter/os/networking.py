"""Networking intelligence: who to contact for an opportunity, why, and when.

Contacts come only from evidence: people the networking engine found in public posts or named on
a vacancy, people the user adds, and (with a search API key) public profile search results, which
stay UNVERIFIED until the user confirms them. Nothing is guessed: no email addresses, no invented
alumni links, no shared connections. One person is one contact, linked to as many opportunities
as the evidence supports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from summer_hunter.crm.followups import add_business_days
from summer_hunter.crm.service import CRM
from summer_hunter.crm.statuses import POST_APPLICATION, TERMINAL
from summer_hunter.crm.validation import ValidationError, check_url, normalize_name
from summer_hunter.database.models import (
    Contact,
    ContactEvidence,
    ContactOpportunity,
    CRMApplication,
    CRMContact,
    Interaction,
    Opportunity,
    utcnow,
)
from summer_hunter.database.repository import as_utc

CATEGORIES = ["hiring_manager", "team_manager", "talent_acquisition", "university_recruiter", "referral", "alumni_esilv",
              "alumni_af_airbus", "team_member", "program_coordinator", "other"]
CATEGORY_LABEL = {"hiring_manager": "Manager qui recrute", "team_manager": "Manager de l’équipe", "talent_acquisition": "Recrutement",
                  "university_recruiter": "Recruteur campus", "referral": "Contact pour recommandation", "alumni_esilv": "Ancien de l’ESILV",
                  "alumni_af_airbus": "Ancien d’Air France / Airbus", "team_member": "Membre de l’équipe",
                  "program_coordinator": "Coordinateur du programme de stages", "other": "Autre"}
_STATUS_FR = {"REJECTED": "refusée", "WITHDRAWN": "retirée", "CLOSED": "clôturée"}
EVIDENCE_LABEL = {"public_post": "post public", "vacancy": "cité dans l’offre", "user": "ajouté par toi"}
CRM_TYPE = {"hiring_manager": "Hiring Manager", "team_manager": "Hiring Manager", "talent_acquisition": "Recruiter",
            "university_recruiter": "Recruiter", "referral": "Referral Contact", "alumni_esilv": "Alumni",
            "alumni_af_airbus": "Alumni", "team_member": "Employee", "program_coordinator": "Recruiter", "other": "Other"}
SENIOR = re.compile(r"\b(vp|vice president|chief|cxo|ceo|cto|coo|cfo|president|head of|director|svp|evp)\b", re.I)
UNIVERSITY = re.compile(r"\b(university|campus|early careers?|graduate|student|intern(ship)? program)\b", re.I)
RECRUITING = re.compile(r"\b(recruit\w*|talent|sourcer|hr business partner|people partner)\b", re.I)
MANAGER = re.compile(r"\b(manager|lead|head)\b", re.I)
LINKEDIN_PROFILE = re.compile(r"^https://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?$")


def categorize(position: str | None, relationship: str | None = None) -> str:
    pos = position or ""
    if relationship == "hiring_manager_post":
        return "hiring_manager"
    if RECRUITING.search(pos) or relationship in {"recruiter_post", "named_recruiter_on_vacancy", "recruiting_team"}:
        return "university_recruiter" if UNIVERSITY.search(pos) else "talent_acquisition"
    if re.search(r"intern(ship)? program|campus program|programme coordinator", pos, re.I):
        return "program_coordinator"
    if relationship == "team_member_post":
        return "team_member"
    if MANAGER.search(pos):
        return "team_manager"
    return "other"


def _link(session: Session, contact: CRMContact, opp_id: int, note: str | None) -> ContactOpportunity:
    row = session.execute(select(ContactOpportunity).where(ContactOpportunity.contact_id == contact.id,
                                                           ContactOpportunity.opportunity_id == opp_id)).scalar_one_or_none()
    if row is None:
        row = ContactOpportunity(contact_id=contact.id, opportunity_id=opp_id, relationship_note=note)
        session.add(row)
        session.flush()
    return row


def _evidence(session: Session, contact: CRMContact, kind: str, url: str, excerpt: str | None, verified: bool,
              opp_id: int | None) -> None:
    exists = session.execute(select(ContactEvidence.id).where(ContactEvidence.contact_id == contact.id,
                                                              ContactEvidence.url == url, ContactEvidence.kind == kind)).first()
    if not exists:
        session.add(ContactEvidence(contact_id=contact.id, opportunity_id=opp_id, kind=kind, url=url,
                                    excerpt=(excerpt or "")[:600] or None, verified=verified))


def _get_or_create(session: Session, company: str, name: str, position: str | None, url: str | None, category: str,
                   verification: str, source: str) -> tuple[CRMContact, bool]:
    norm = normalize_name(name)
    row = session.execute(select(CRMContact).where(CRMContact.company == company, CRMContact.normalized_name == norm)).scalar_one_or_none()
    if row:
        changed = False
        if url and not row.linkedin_url:
            row.linkedin_url, changed = url, True
        if position and not row.position:
            row.position, changed = position, True
        if verification == "verified" and row.verification != "verified":
            row.verification, changed = "verified", True
        if not row.category or row.category == "other":
            row.category = category
        if changed:
            row.updated_at = utcnow()
        return row, False
    row = CRMContact(company=company, name=name, normalized_name=norm, position=position, linkedin_url=url,
                     contact_type=CRM_TYPE.get(category, "Other"), interaction_status="IDENTIFIED", source=source[:300],
                     category=category, verification=verification, dismissed=False)
    session.add(row)
    session.flush()
    return row, True


def import_public_contacts(session: Session) -> dict[str, int]:
    """Turn the contacts found by the discovery engine (public posts, named recruiters) into managed contacts."""
    created = linked = 0
    for c in session.execute(select(Contact)).scalars():
        evidence_url = c.source_post_url or c.profile_url
        if not evidence_url or c.confidence < 0.6:
            continue
        opp = session.get(Opportunity, c.opportunity_id)
        if opp is None:
            continue
        url = c.profile_url if c.profile_url and LINKEDIN_PROFILE.match(c.profile_url) else None
        contact, new = _get_or_create(session, opp.company_name, c.name, c.role, url, categorize(c.role, c.relationship_type),
                                      "verified", evidence_url)
        created += new
        kind = "public_post" if c.source_post_url else "vacancy" if c.relationship_type == "named_recruiter_on_vacancy" else "profile"
        _evidence(session, contact, kind, evidence_url, c.evidence, True, opp.id)
        link = _link(session, contact, opp.id, c.relationship_type.replace("_", " "))
        linked += link.computed_at is None
    session.flush()
    return {"created": created, "linked": linked}


def add_user_contact(session: Session, company: str, name: str, position: str | None, linkedin_url: str | None,
                     category: str, opportunity_id: int | None, note: str | None = None) -> CRMContact:
    """A contact the user knows exists. The LinkedIn URL must be a public profile URL."""
    if category not in CATEGORIES:
        raise ValidationError(f"category must be one of: {', '.join(CATEGORIES)}")
    url = check_url(linkedin_url, "LinkedIn profile")
    if url and not LINKEDIN_PROFILE.match(url):
        raise ValidationError("L’URL du profil LinkedIn doit ressembler à https://www.linkedin.com/in/<nom>")
    if not (name or "").strip() or not (company or "").strip():
        raise ValidationError("a contact needs a name and a company")
    contact, _ = _get_or_create(session, company.strip(), name.strip(), position, url, category, "verified", "manual")
    _evidence(session, contact, "user", url or "", note or "added by the user", True, opportunity_id)
    if opportunity_id:
        _link(session, contact, opportunity_id, note or "added by the user")
    CRM(session).log_event("contact_added", "web", contact=contact, new=f"{contact.name} ({category})", notes="entered manually")
    session.flush()
    return contact


def add_search_candidates(session: Session, opp: Opportunity, results: list[dict[str, str]]) -> int:
    """Public profile search results: stored as UNVERIFIED candidates until the user confirms them."""
    added = 0
    for r in results:
        url = (r.get("url") or "").split("?")[0]
        title = r.get("title") or ""
        m = re.match(r"^([^\-|–]+?)\s*[-–|]\s*(.+?)(?:\s*[-–|]\s*LinkedIn.*)?$", title)
        if not LINKEDIN_PROFILE.match(url) or not m:
            continue
        name, position = m.group(1).strip(), m.group(2).strip()
        if opp.company_name.lower().split()[0] not in f"{position} {r.get('snippet', '')}".lower():
            continue                         # the result must mention the company
        contact, new = _get_or_create(session, opp.company_name, name, position, url, categorize(position), "unverified", url)
        _evidence(session, contact, "search_result", url, f"{title} · {r.get('snippet', '')}", False, opp.id)
        _link(session, contact, opp.id, "public profile search result (unverified)")
        added += new
    session.flush()
    return added


def confirm_contact(session: Session, contact: CRMContact) -> None:
    contact.verification = "verified"
    contact.updated_at = utcnow()
    session.add(ContactEvidence(contact_id=contact.id, kind="user", url=contact.linkedin_url or "",
                                excerpt="profile checked and confirmed by the user", verified=True))
    session.flush()


# Ranking ---------------------------------------------------------------------------------------------
@dataclass
class ContactScore:
    score: int
    components: dict[str, float]
    explanation: list[str]


def _track_words(opp: Opportunity) -> set[str]:
    base = {"maintenance", "reliability", "robotics", "operations", "industrial", "manufacturing", "automation", "project"} \
        if opp.career_track == "industry_robotics" else {"sales", "solutions", "presales", "partnerships", "product", "growth",
                                                         "business development", "customer"}
    return base | {w for w in re.findall(r"[a-z]{4,}", (opp.title or "").lower())}


def score_contact(contact: CRMContact, opp: Opportunity, evidence: list[ContactEvidence], cfg: dict[str, Any],
                  today: date | None = None) -> ContactScore:
    sc = cfg.get("contact_score") or {}
    today = today or datetime.now(timezone.utc).date()
    cat = contact.category or "other"
    linked = [e for e in evidence if e.opportunity_id == opp.id and e.kind in {"public_post", "vacancy", "user"}]
    why: list[str] = []

    involvement = float((sc.get("involvement") or {}).get(cat, 25))
    if linked and cat in {"talent_acquisition", "university_recruiter", "hiring_manager", "team_manager"}:
        involvement = min(100.0, involvement + float(sc.get("linked_to_this_opening_bonus", 15)))
        why.append(f"{CATEGORY_LABEL[cat]}, lié à cette offre ({EVIDENCE_LABEL.get(linked[0].kind, linked[0].kind)}).")
    else:
        why.append(f"{CATEGORY_LABEL[cat]}.")
    if SENIOR.search(contact.position or "") and not linked:
        involvement = min(involvement, float(sc.get("senior_without_link_cap", 40)))
        why.append("Poste senior sans preuve d’implication dans ce recrutement : pas un premier contact.")

    words = _track_words(opp)
    pos = (contact.position or "").lower()
    if linked:
        relevance = 100.0
    elif any(w in pos for w in words):
        relevance = 70.0
        why.append("Travaille dans le domaine du poste.")
    else:
        relevance = 40.0

    connection = 20.0
    if cat == "alumni_esilv":
        connection = 100.0
        why.append("Ancien de l’ESILV (indiqué par toi ou par une preuve publique).")
    elif cat == "alumni_af_airbus":
        connection = 90.0
        why.append("Ancien d’Air France / Airbus : parcours professionnel commun.")
    elif any(w in pos for w in ("maintenance", "mro", "robotics", "industrial")) and opp.career_track == "industry_robotics":
        connection = 50.0

    recent_days = int(sc.get("recent_activity_days", 60))
    dated = [as_utc(e.retrieved_at).date() for e in evidence if e.kind in {"public_post", "vacancy"} and e.retrieved_at]
    if any((today - d).days <= recent_days for d in dated):
        activity = 100.0 if any(e.kind == "public_post" for e in evidence) else 80.0
        why.append("Activité de recrutement publique récente.")
    elif dated:
        activity = 40.0
    else:
        activity = 10.0

    if contact.linkedin_url and contact.verification == "verified":
        access = 100.0
    elif contact.linkedin_url:
        access = 60.0
        why.append("Profil pas encore confirmé : vérifie-le avant d’écrire.")
    else:
        access = 20.0
        why.append("Aucune URL de profil public enregistrée.")

    comps = {"involvement": involvement, "relevance": relevance, "connection": connection,
             "recruiting_activity": activity, "accessibility": access}
    weights = sc.get("weights") or {"involvement": .30, "relevance": .25, "connection": .20, "recruiting_activity": .15, "accessibility": .10}
    total = sum(comps[k] * float(w) for k, w in weights.items()) / (sum(float(w) for w in weights.values()) or 1.0)
    return ContactScore(int(round(total)), {k: round(v, 1) for k, v in comps.items()}, why)


# Timing ----------------------------------------------------------------------------------------------
@dataclass
class Timing:
    action: str              # CONTACT_BEFORE_APPLICATION | CONTACT_AFTER_APPLICATION | FOLLOW_UP | WAIT | DO_NOT_CONTACT
    on: date | None
    reason: str
    confidence: str          # high | medium | low
    message_kind: str | None


def _next_workday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def timing(contact: CRMContact, opp: Opportunity, app: CRMApplication | None, interactions: list[Interaction],
           cfg: dict[str, Any], today: date | None = None) -> Timing:
    tc = cfg.get("timing") or {}
    today = today or datetime.now(timezone.utc).date()
    cat = contact.category or "other"
    low = contact.verification != "verified"
    conf = "low" if low else "high"
    if contact.dismissed:
        return Timing("DO_NOT_CONTACT", None, "Écarté par toi.", "high", None)
    if app and app.status in TERMINAL and app.status != "OFFER_ACCEPTED":
        return Timing("DO_NOT_CONTACT", None, f"Candidature close ({_STATUS_FR.get(app.status, app.status.lower())}) : pas de relance de routine.", "high", None)
    sent = sorted((i for i in interactions if i.kind == "message_sent"), key=lambda i: as_utc(i.occurred_at))
    replies = [i for i in interactions if i.kind == "reply"]
    if replies and (not sent or as_utc(replies[-1].occurred_at) >= as_utc(sent[-1].occurred_at)):
        return Timing("WAIT", None, "Cette personne a répondu : poursuis la conversation toi-même.", "high", "thank_you")
    if sent:
        if len(sent) > int(tc.get("max_follow_ups", 1)):
            return Timing("DO_NOT_CONTACT", None, "Une relance déjà envoyée sans réponse : on s’arrête là.", "high", None)
        due = add_business_days(as_utc(sent[-1].occurred_at).date(), int(tc.get("follow_up_after_business_days", 5)))
        if today < due:
            return Timing("WAIT", due, f"Message envoyé le {as_utc(sent[-1].occurred_at):%d/%m} : laisse du temps jusqu’au {due:%d/%m}.", "high", None)
        return Timing("FOLLOW_UP", _next_workday(max(today, due)), "Pas de réponse après 5 jours ouvrés : une relance courte.",
                      "high", "follow_up")
    if app and app.status in {"INTERVIEW", "FINAL_INTERVIEW"} and app.interview_at:
        after = as_utc(app.interview_at).date() + timedelta(days=int(tc.get("post_interview_follow_up_days", 1)))
        return Timing("FOLLOW_UP", _next_workday(max(today, after)), "Après l’entretien : un court message de remerciement.", conf,
                      "post_interview")
    applied = app is not None and app.status in POST_APPLICATION and app.date_applied is not None
    deadline_soon = opp.application_deadline is not None and (opp.application_deadline - today).days <= 7
    if cat in {"alumni_esilv", "alumni_af_airbus", "referral"}:
        kind = "alumni" if cat.startswith("alumni") else "referral_request"
        return Timing("CONTACT_BEFORE_APPLICATION" if not applied else "CONTACT_AFTER_APPLICATION", _next_workday(today),
                      "Demande un conseil sur l’équipe et le poste, pas une recommandation dans un premier message."
                      + (" Postule d’abord : la date limite approche." if deadline_soon and not applied else ""), conf, kind)
    if cat in {"talent_acquisition", "university_recruiter", "program_coordinator"}:
        if applied:
            when = add_business_days(app.date_applied, int(tc.get("recruiter_after_application_business_days", 2)))
            return Timing("CONTACT_AFTER_APPLICATION", _next_workday(max(today, when)),
                          "Les recruteurs agissent sur les candidatures envoyées : mentionne la tienne.", conf, "after_applying")
        return Timing("CONTACT_AFTER_APPLICATION", None,
                      "Postule d’abord, puis envoie un mot court qui mentionne ta candidature. Une demande de connexion dès maintenant convient.",
                      "low" if low else "medium", "connection_request")
    if cat in {"hiring_manager", "team_manager", "team_member"}:
        if applied:
            return Timing("CONTACT_AFTER_APPLICATION", _next_workday(today), "Candidature envoyée : un mot concis à l’équipe.", conf,
                          "after_applying")
        return Timing("CONTACT_BEFORE_APPLICATION", _next_workday(today),
                      "Une vraie raison d’écrire avant de postuler ; postule quand même, sans attendre de réponse."
                      + (" La date limite approche : postule aujourd’hui." if deadline_soon else ""), conf, "before_applying")
    return Timing("WAIT", None, "Pas encore de raison claire d’écrire : garde ce contact en référence.", "low", None)


# Recompute -------------------------------------------------------------------------------------------------
def recompute(session: Session, cfg: dict[str, Any], opportunity_id: int | None = None, today: date | None = None) -> int:
    """Refresh score and timing of every contact-opportunity link (or one opportunity's links)."""
    stmt = select(ContactOpportunity)
    if opportunity_id is not None:
        stmt = stmt.where(ContactOpportunity.opportunity_id == opportunity_id)
    links = list(session.execute(stmt).scalars())
    if not links:
        return 0
    contact_ids = {link.contact_id for link in links}
    contacts = {c.id: c for c in session.execute(select(CRMContact).where(CRMContact.id.in_(contact_ids))).scalars()}
    evidence: dict[int, list[ContactEvidence]] = {}
    for e in session.execute(select(ContactEvidence).where(ContactEvidence.contact_id.in_(contact_ids))).scalars():
        evidence.setdefault(e.contact_id, []).append(e)
    interactions: dict[tuple[int, int | None], list[Interaction]] = {}
    for i in session.execute(select(Interaction).where(Interaction.contact_id.in_(contact_ids))).scalars():
        interactions.setdefault((i.contact_id, i.opportunity_id), []).append(i)
    apps = {a.opportunity_id: a for a in session.execute(select(CRMApplication)).scalars()}
    for link in links:
        contact, opp = contacts.get(link.contact_id), session.get(Opportunity, link.opportunity_id)
        if contact is None or opp is None:
            continue
        ev = evidence.get(contact.id, [])
        sc = score_contact(contact, opp, ev, cfg, today)
        hist = interactions.get((contact.id, opp.id), []) + interactions.get((contact.id, None), [])
        t = timing(contact, opp, apps.get(opp.id), hist, cfg, today)
        link.score, link.score_components, link.explanation = sc.score, sc.components, " ".join(sc.explanation)
        link.timing_action, link.timing_date, link.timing_reason, link.timing_confidence = t.action, t.on, t.reason, t.confidence
        link.computed_at = utcnow()
    session.flush()
    return len(links)


def record_interaction(session: Session, contact: CRMContact, kind: str, opportunity_id: int | None, note: str | None = None,
                       message_id: int | None = None, follow_up_on: date | None = None) -> Interaction:
    if kind not in {"message_sent", "reply", "call", "meeting", "note", "follow_up_scheduled"}:
        raise ValidationError("unknown interaction kind")
    row = Interaction(contact_id=contact.id, opportunity_id=opportunity_id, kind=kind, note=note, message_id=message_id,
                      follow_up_on=follow_up_on)
    session.add(row)
    status = {"message_sent": "MESSAGE_SENT", "reply": "REPLIED", "call": "CALL_SCHEDULED", "meeting": "CALL_SCHEDULED"}.get(kind)
    crm = CRM(session)
    if status:
        crm.set_contact_status(contact, status, "web")
    if kind == "follow_up_scheduled" and follow_up_on:
        crm.update_contact_fields(contact, {"next_followup_date": follow_up_on}, "web")
    session.flush()
    return row
