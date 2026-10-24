"""Application strategist: the next best action for each opportunity, and why.

It never estimates a probability of an offer. It combines the separate scores into a decision
the user can act on, with the evidence behind it, and labels each opportunity AMBITIOUS,
TARGETED or SAFETY so application effort can be spread (25 / 50 / 25 by default).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from summer_hunter.engine.compensation import COMPENSATION_UNKNOWN, PAID_AMOUNT_UNKNOWN, CompensationReport
from summer_hunter.engine.priority import GlobalPriority

APPLY_NOW, PREPARE, CONTACT, VERIFY_ELIG, VERIFY_COMP = ("APPLY_NOW", "PREPARE_APPLICATION", "CONTACT_RECRUITER",
                                                        "VERIFY_ELIGIBILITY", "VERIFY_COMPENSATION")
MONITOR, LOW, DISCARD = "MONITOR_OPENING", "LOW_PRIORITY", "DISCARD"
LABEL_FR = {APPLY_NOW: "Postuler maintenant", PREPARE: "Préparer la candidature", CONTACT: "Contacter le recruteur",
            VERIFY_ELIG: "Vérifier l'éligibilité", VERIFY_COMP: "Vérifier la rémunération", MONITOR: "Surveiller",
            LOW: "Priorité basse", DISCARD: "Écarter"}
CRITICAL = {"work_authorization", "dates", "language", "academic", "graduation", "citizenship", "technical_requirements"}


class StrategyReport(BaseModel):
    action: str
    category: str                                   # AMBITIOUS | TARGETED | SAFETY
    why: str
    arguments: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    compensation: str = ""
    urgency: str = ""
    networking: str = ""
    effort: str = ""
    caveat: str | None = None


def category(fit: int, recruit: int, watch_priority: int | None, cfg: dict[str, Any]) -> str:
    if recruit >= int(cfg.get("safety_recruitability_from", 82)):
        return "SAFETY"
    if recruit < int(cfg.get("ambitious_recruitability_below", 60)) or \
            (watch_priority == cfg.get("ambitious_watchlist_priority", 1) and recruit < 75):
        return "AMBITIOUS"
    return "TARGETED"


CRIT_FR = {"work_authorization": "autorisation de travail", "dates": "dates", "language": "langue", "academic": "diplôme",
           "graduation": "date de diplôme", "citizenship": "nationalité", "technical_requirements": "exigences techniques",
           "duration": "durée", "country": "pays", "safety": "sécurité", "internship_status": "type de contrat",
           "qualifications": "licences", "compensation": "rémunération"}


def comp_line(comp: CompensationReport) -> str:
    if comp.monthly_min_eur is not None:
        amount = (f"{comp.monthly_min_eur:,.0f} EUR/mois" if comp.monthly_max_eur in (None, comp.monthly_min_eur)
                  else f"{comp.monthly_min_eur:,.0f}-{comp.monthly_max_eur:,.0f} EUR/mois")
        balance = f", solde estimé {comp.monthly_balance_eur:+,.0f} EUR" if comp.monthly_balance_eur is not None else ""
        est = " (estimation)" if comp.monthly_is_estimate else ""
        return f"{amount}{est}{balance} · confiance {comp.confidence}"
    return {"UNPAID_CONFIRMED": "Non rémunéré (confirmé)", "PAID_AMOUNT_UNKNOWN": "Rémunéré, montant non indiqué",
            "MIXED_OR_CONDITIONAL": "Rémunération conditionnelle"}.get(comp.status, "Rémunération inconnue")


def decide(gp: GlobalPriority, decision: str, criteria: list[dict], comp: CompensationReport, fit: int, recruit: int,
           networking: int, recruit_report: dict, fit_report: dict, contact: dict | None, watch_priority: int | None,
           talent_pool: bool, deadline_days: int | None, rules: dict[str, Any]) -> StrategyReport:
    cfg = rules.get("strategist") or {}
    cat = category(fit, recruit, watch_priority, cfg)
    order = ["work_authorization", "dates", "language", "academic", "graduation", "citizenship", "technical_requirements"]
    unknown_critical = sorted((c for c in criteria if c["status"] == "UNKNOWN" and c["criterion"] in CRITICAL and c.get("material", True)),
                              key=lambda c: order.index(c["criterion"]))     # visa first: it decides most international cases
    weak_fail = [c for c in criteria if c["status"] == "FAIL"]
    score = gp.score
    caveat = None
    if decision == "REJECTED" or gp.filtered:
        action, why = DISCARD, gp.filtered or "un critère d’éligibilité obligatoire n’est pas rempli"
    elif talent_pool:
        action, why = MONITOR, "vivier de talents / postes futurs, pas encore une offre"
    elif score is None or score < float(cfg.get("low_priority_below", 40)):
        action, why = LOW, f"priorité globale {score if score is not None else 'n.d.'}"
    elif weak_fail:
        action, why = VERIFY_ELIG, "une exigence n’est peut-être pas remplie : " + weak_fail[0]["explanation"][:120]
    elif score >= float(cfg.get("apply_now_priority", 70)):
        if networking >= 75 and contact:
            action, why = CONTACT, "très bonne adéquation et une personne pertinente à contacter d’abord"
        elif comp.status in {COMPENSATION_UNKNOWN, PAID_AMOUNT_UNKNOWN} and score < 80:
            action, why = VERIFY_COMP, "très bonne adéquation, paie non précisée"
        else:
            action, why = APPLY_NOW, f"priorité globale {score}"
        if unknown_critical:
            caveat = "sous réserve de vérifier : " + ", ".join(CRIT_FR.get(c["criterion"], c["criterion"]) for c in unknown_critical[:3])
    elif score >= float(cfg.get("prepare_priority", 55)):
        action, why = PREPARE, f"priorité globale {score}"
        if unknown_critical:
            caveat = "à vérifier d’abord : " + ", ".join(CRIT_FR.get(c["criterion"], c["criterion"]) for c in unknown_critical[:3])
    else:
        action, why = LOW, f"priorité globale {score}"
    arguments = list(dict.fromkeys([*(recruit_report.get("reasons") or []), *(fit_report.get("reasons") or [])]))[:3]
    gaps = list(dict.fromkeys([*(recruit_report.get("missing_requirements") or []),
                               *[f"{CRIT_FR.get(c['criterion'], c['criterion'])} : {c['explanation']}" for c in unknown_critical[:3]]]))[:5]
    urgency = (f"date limite dans {deadline_days} j" if deadline_days is not None and deadline_days >= 0
               else "pas de date limite connue")
    net = (f"{contact['name']} ({contact['relationship'].replace('_', ' ')})" if contact else "aucun contact vérifié")
    effort = f"environ {gp.effort_minutes} min" if gp.effort_minutes else ""
    return StrategyReport(action=action, category=cat, why=why, arguments=arguments, gaps=gaps, compensation=comp_line(comp),
                          urgency=urgency, networking=net, effort=effort, caveat=caveat)


def balanced(items: list[Any], n: int, allocation: dict[str, float], key_category, key_score) -> list[Any]:  # noqa: ANN001
    """Top n by score, while keeping the AMBITIOUS / TARGETED / SAFETY mix close to `allocation`."""
    ranked = sorted(items, key=lambda x: -(key_score(x) or 0))
    quota = {c: max(1, round(n * share)) for c, share in allocation.items()}
    picked, rest = [], []
    for item in ranked:
        cat = key_category(item)
        if quota.get(cat, 0) > 0 and len(picked) < n:
            picked.append(item)
            quota[cat] -= 1
        else:
            rest.append(item)
    for item in rest:                                 # fill any unused slots with the next best
        if len(picked) >= n:
            break
        picked.append(item)
    return sorted(picked, key=lambda x: -(key_score(x) or 0))
