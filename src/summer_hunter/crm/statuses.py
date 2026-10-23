"""Application and contact vocabularies, and the rules for moving between statuses."""

from __future__ import annotations

PIPELINE = [
    "DISCOVERED", "SHORTLISTED", "TO_APPLY", "PREPARING", "APPLIED", "ONLINE_ASSESSMENT",
    "INTERVIEW", "FINAL_INTERVIEW", "OFFER_RECEIVED", "OFFER_ACCEPTED",
]
OUTCOMES = ["REJECTED", "WITHDRAWN", "CLOSED"]
STATUSES = PIPELINE + OUTCOMES
RANK = {s: i for i, s in enumerate(PIPELINE)}

PRE_APPLICATION = {"DISCOVERED", "SHORTLISTED", "TO_APPLY", "PREPARING"}
POST_APPLICATION = {"APPLIED", "ONLINE_ASSESSMENT", "INTERVIEW", "FINAL_INTERVIEW", "OFFER_RECEIVED", "OFFER_ACCEPTED"}
TERMINAL = {"OFFER_ACCEPTED", "REJECTED", "WITHDRAWN", "CLOSED"}
ACTIVE = set(STATUSES) - TERMINAL
INTERVIEW_STAGES = {"INTERVIEW", "FINAL_INTERVIEW"}
OFFERS = {"OFFER_RECEIVED", "OFFER_ACCEPTED"}

LABEL_FR = {
    "DISCOVERED": "Découverte", "SHORTLISTED": "Présélection", "TO_APPLY": "À postuler", "PREPARING": "En préparation",
    "APPLIED": "Postulé", "ONLINE_ASSESSMENT": "Test en ligne", "INTERVIEW": "Entretien", "FINAL_INTERVIEW": "Entretien final",
    "OFFER_RECEIVED": "Offre reçue", "OFFER_ACCEPTED": "Offre acceptée", "REJECTED": "Refus", "WITHDRAWN": "Retirée",
    "CLOSED": "Fermée",
}

CONTACT_TYPES = ["Recruiter", "Hiring Manager", "Employee", "Alumni", "Referral Contact", "Other"]
INTERACTION_STATUSES = ["IDENTIFIED", "TO_CONTACT", "MESSAGE_SENT", "CONNECTED", "REPLIED", "CALL_SCHEDULED",
                        "FOLLOW_UP_REQUIRED", "CLOSED"]

# Networking-engine relationship types -> CRM contact type.
RELATIONSHIP_TO_TYPE = {
    "recruiter_post": "Recruiter", "named_recruiter_on_vacancy": "Recruiter", "recruiting_team": "Recruiter",
    "hiring_manager_post": "Hiring Manager", "team_member_post": "Employee",
}


class TransitionError(ValueError):
    pass


def check_transition(current: str, new: str, has_applied_date: bool, force: bool = False) -> None:
    """Raise TransitionError when `current -> new` is not a legitimate move.

    Forward moves may skip stages (a recruiter can call before the online test). An application
    that was submitted cannot go back to a pre-application stage, a closed outcome cannot be
    reopened, and every post-application status needs a confirmed application date. `force`
    lifts the ordering rules for deliberate corrections, never the vocabulary or date rule.
    """
    if new not in STATUSES:
        raise TransitionError(f"unknown status {new!r}; allowed: {', '.join(STATUSES)}")
    if current == new:
        return
    if new in POST_APPLICATION and not has_applied_date:
        raise TransitionError(f"{new} needs a confirmed application date (mark the application as submitted first)")
    if force:
        return
    if current in TERMINAL:
        raise TransitionError(f"{current} is a final status; it cannot change to {new}")
    if new == "OFFER_ACCEPTED" and current != "OFFER_RECEIVED":
        raise TransitionError("OFFER_ACCEPTED needs OFFER_RECEIVED first")
    if current in RANK and new in RANK and RANK[new] < RANK[current]:
        raise TransitionError(f"{current} cannot go back to {new} (use a forced correction if it was a mistake)")


def deeper(a: str, b: str) -> str:
    """The deeper of two pipeline stages (outcomes never count as depth)."""
    ra, rb = RANK.get(a, -1), RANK.get(b, -1)
    return a if ra >= rb else b
