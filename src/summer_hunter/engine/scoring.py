"""Transparent career-fit and priority scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from summer_hunter.engine.extraction import JobFacts
from summer_hunter.engine.normalization import INDUSTRY_ROBOTICS, FamilyMatch
from summer_hunter.models.candidate import CandidateProfile
from summer_hunter.models.eligibility import CriterionStatus, Decision, EligibilityReport
from summer_hunter.models.scoring import Adjustment, FitReport, NetworkingReport, PriorityReport
from summer_hunter.utils.text import normalize_for_match

# Skills commonly required by target roles, used to report "missing skills" honestly.
SKILL_LEXICON = {
    "sql": r"\bsql\b", "salesforce crm": r"\bsalesforce (crm|administration|admin)\b|\bcrm (tools?|experience)\b",
    "tableau / bi tools": r"\b(tableau|power bi|looker)\b", "javascript": r"\bjavascript\b", "java": r"\bjava\b",
    "cloud certification": r"\b(aws|azure|gcp) certif\w*", "networking (tcp/ip)": r"\btcp/ip|network protocols?\b",
    "kubernetes": r"\bkubernetes\b", "financial modeling": r"\bfinancial model\w*", "excel (advanced)": r"\badvanced excel|excel modeling\b",
    "figma": r"\bfigma\b", "a/b testing": r"\ba/b test\w*", "copywriting": r"\bcopywriting\b",
    "machine learning frameworks": r"\b(pytorch|tensorflow)\b", "statistics": r"\bstatistic\w*",
    "linux": r"\blinux\b", "prior sales internship": r"\bprior sales (experience|internship)\b",
}


@dataclass
class ScoringContext:
    profile: CandidateProfile
    rules: dict[str, Any]
    company_priority: int | None = None      # watchlist priority, None if not on watchlist
    company_sector: str | None = None
    priority_countries: set[str] = field(default_factory=set)
    priority_country_bonus: float = 0.0
    country_penalties: dict[str, float] = field(default_factory=dict)
    learned: list[Adjustment] = field(default_factory=list)


def _hits(norm: str, keywords: list[str]) -> list[str]:
    found = []
    for kw in keywords:
        k = normalize_for_match(kw)
        if k.strip() and re.search(r"(?<![a-z0-9])" + re.escape(k.strip()) + r"(?![a-z0-9])", norm):
            found.append(kw.strip())
    return found


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def score_fit(title: str, description: str, facts: JobFacts, family: FamilyMatch, ctx: ScoringContext,
              eligibility: EligibilityReport | None = None, country: str | None = None) -> FitReport:
    rules = ctx.rules
    norm = normalize_for_match(f"{title}. {description}")
    reasons: list[tuple[float, str]] = []
    weaknesses: list[tuple[float, str]] = []
    evidence: list[str] = []
    adjustments: list[Adjustment] = []

    # 1. Career alignment ----------------------------------------------------------
    alignment = float(family.alignment)
    if family.tier >= 1:
        reasons.append((family.alignment, f"Target job family: {family.id.replace('_', ' ')} (tier {family.tier})."))
    else:
        weaknesses.append((100 - family.alignment, f"Role family '{family.id.replace('_', ' ')}' is outside the target career path."))
    ind = rules["industry_signals"]
    ind_hits = _hits(f" {norm} ", ind["keywords"])
    if ind_hits:
        pts = min(len(ind_hits) * ind["points_each"], ind["max_points"])
        alignment += pts
        reasons.append((60 + pts, f"Industry fit: {', '.join(ind_hits[:4])}."))
    pos = (rules.get("track_signals") or {}).get(family.track) or rules["career_positive_signals"]
    pos_hits = _hits(norm, pos["keywords"])
    if pos_hits:
        pts = min(len(pos_hits) * pos["points_each"], pos["max_points"])
        alignment += pts
        label = "Industrial / operations work" if family.track == INDUSTRY_ROBOTICS else "Consultative / technical-commercial work"
        reasons.append((70 + pts, f"{label}: {', '.join(pos_hits[:4])}."))

    # Cold calling
    cc = rules["cold_calling"]
    cold_penalty = 0.0
    if facts.cold_calls_per_day is not None and facts.cold_calls_per_day > cc["threshold_per_day"]:
        cold_penalty = cc["major_penalty"]
        weaknesses.append((95, f"High-volume cold calling: about {facts.cold_calls_per_day} calls per day."))
        evidence.append(facts.cold_call_evidence)
    elif facts.high_volume_calling:
        cold_penalty = cc["major_penalty"]
        weaknesses.append((90, "High-volume outbound calling described."))
        evidence.append(facts.cold_call_evidence)
    elif facts.cold_calling_mentioned:
        cold_penalty = cc["mention_penalty"]
        weaknesses.append((50, "Cold calling is part of the role."))
        evidence.append(facts.cold_call_evidence)
    if cold_penalty and family.id == "sales_development" and ctx.company_priority == 1 and \
            re.search(r"\b(academy|training program|bootcamp|rotation)\b", norm):
        cold_penalty = max(0.0, cold_penalty - cc["structured_academy_relief"])
        reasons.append((60, "Structured sales academy at a priority company (partly offsets the calling load)."))
    if cold_penalty:
        alignment -= cold_penalty
        adjustments.append(Adjustment(label="cold-calling penalty", delta=-cold_penalty, component="career_alignment"))
    alignment = _clamp(alignment)

    # 2. Experience relevance -------------------------------------------------------
    er = rules["experience_relevance"]
    matched_exp: list[tuple[str, list[str]]] = []
    total_hits = 0
    for exp in ctx.profile.experiences:
        hits = _hits(norm, exp.keywords)
        if hits:
            matched_exp.append((exp.id, hits))
            total_hits += len(hits)
    matched_exp.sort(key=lambda item: -len(item[1]))
    experience = _clamp(er["base"] + min(total_hits * er["points_per_keyword"], er["max_points"]))
    if matched_exp:
        top = matched_exp[0]
        org = next(e.organization for e in ctx.profile.experiences if e.id == top[0])
        reasons.append((40 + len(top[1]) * 8, f"Relevant experience: {org} ({', '.join(top[1][:3])})."))
    else:
        weaknesses.append((60, "Little overlap between the listing and verified experience."))

    # 3. Mission quality -------------------------------------------------------------
    ms = rules["mission_signals"]
    m_pos = _hits(norm, ms["positive"])
    m_neg = _hits(norm, ms["negative"])
    mission = ms["base"] + min(len(m_pos) * ms["positive_points"], ms["positive_max"]) \
        - min(len(m_neg) * ms["negative_points"], ms["negative_max"])
    if m_neg:
        weaknesses.append((70, f"Low-value tasks mentioned: {', '.join(m_neg[:3])}."))
    if cold_penalty:
        mission -= cold_penalty
        adjustments.append(Adjustment(label="cold-calling penalty", delta=-cold_penalty, component="mission_quality"))
    mission = _clamp(mission)

    # 4. Skill development -------------------------------------------------------------
    sd = rules["skill_development"]
    tech = _hits(norm, sd["technical"])
    comm = _hits(norm, sd["commercial"])
    skill = sd["base"] + min((len(tech) + len(comm)) * sd["points_each"], sd["max_points"])
    if tech and comm:
        skill += 5
        reasons.append((55 + min(len(tech), len(comm)) * 5, "Combines technical and commercial skill development."))
    skill = _clamp(skill)

    # 5. Long-term value ------------------------------------------------------------------
    lt = rules["long_term_value"]
    long_term = float(lt["base"])
    if ctx.company_priority:
        long_term += lt["watchlist_priority_points"].get(ctx.company_priority, 0)
        reasons.append((50 + 10 * (4 - ctx.company_priority), f"Watchlist company (priority {ctx.company_priority})."))
    if _hits(norm, lt["structured_program_keywords"]):
        long_term += lt["structured_program_points"]
    long_term = _clamp(long_term)

    components = {
        "career_alignment": round(alignment, 1),
        "experience_relevance": round(experience, 1),
        "mission_quality": round(mission, 1),
        "skill_development": round(skill, 1),
        "long_term_value": round(long_term, 1),
    }
    weights = rules["weights"]
    total = sum(components[k] * weights[k] for k in components)

    # Total-level adjustments ----------------------------------------------------------------
    dur = rules["duration"]
    if facts.duration:
        lo_p, hi_p = ctx.profile.preferred_weeks
        lo_a, hi_a = ctx.profile.acceptable_weeks
        if facts.duration.min_weeks < lo_a or facts.duration.max_weeks > hi_a:
            adjustments.append(Adjustment(label="duration outside 4-20 weeks", delta=-dur["outside_acceptable_penalty"]))
        elif facts.duration.min_weeks < lo_p or facts.duration.max_weeks > hi_p:
            adjustments.append(Adjustment(label="duration outside preferred 8-12 weeks", delta=-dur["outside_preferred_penalty"]))
    if country and country in ctx.priority_countries:
        if ctx.priority_country_bonus:
            adjustments.append(Adjustment(label=f"priority country {country}", delta=ctx.priority_country_bonus))
    if country and ctx.country_penalties.get(country):
        adjustments.append(Adjustment(label=f"country preference {country}", delta=float(ctx.country_penalties[country])))
    adjustments.extend(ctx.learned)
    total += sum(a.delta for a in adjustments if a.component is None)
    if family.tier == 0:
        cap = rules.get("tier0_fit_cap_base", 40) + family.alignment / 2
        if total > cap:
            adjustments.append(Adjustment(label=f"outside target families: capped at {cap:.0f}", delta=round(cap - total, 1)))
            total = cap
    score = int(round(_clamp(total)))

    # Explanations -------------------------------------------------------------------------
    if eligibility:
        for crit in eligibility.unknowns[:2]:
            weaknesses.append((65, f"Unverified: {crit.criterion.replace('_', ' ')} ({crit.explanation})"))
    missing = [name for name, rx in SKILL_LEXICON.items() if re.search(rx, norm) and name not in ctx.profile.verified_skills]
    if missing:
        weaknesses.append((40, f"Skills asked that are not in the verified profile: {', '.join(missing[:3])}."))
    reasons.sort(key=lambda r: -r[0])
    weaknesses.sort(key=lambda w: -w[0])
    if family.track == INDUSTRY_ROBOTICS:
        tier_label = {1: "core industry & robotics path (maintenance, industrial projects, robotics deployment)",
                      2: "industrial operations / digitalization path"}.get(family.tier, "limited link to the target career")
    else:
        tier_label = {1: "direct path to technical/enterprise sales", 2: "go-to-market path adjacent to technical sales",
                      3: "product/strategy path with commercial exposure", 4: "general business path"}.get(family.tier, "limited link to the target career")
    return FitReport(
        score=score,
        components=components,
        adjustments=adjustments,
        job_family=family.id,
        family_tier=family.tier,
        reasons=[r for _, r in reasons[:3]],
        weaknesses=[w for _, w in weaknesses[:2]],
        relevant_experiences=[e for e, _ in matched_exp[:4]],
        missing_skills=missing,
        career_value=f"{tier_label.capitalize()}.",
        evidence=[e for e in evidence if e][:4],
        cold_calls_per_day=facts.cold_calls_per_day,
        method="deterministic",
        deterministic_score=score,
    )


# --- networking ------------------------------------------------------------------------


def score_networking(contacts: list[dict[str, Any]], rules: dict[str, Any]) -> NetworkingReport:
    table = rules["networking_scores"]
    if not contacts:
        return NetworkingReport(score=table["none"], rationale="No verified contact identified.")
    best = None
    best_score = -1
    for c in contacts:
        base = table.get(c["relationship"], table["other_evidence"])
        if float(c.get("confidence", 0)) < 0.6:
            base -= table["low_confidence_penalty"]
        if base > best_score:
            best, best_score = c, base
    assert best is not None
    rationale = f"{best['relationship'].replace('_', ' ')} (confidence {float(best.get('confidence', 0)):.2f}): {best.get('evidence', '')[:160]}"
    return NetworkingReport(score=int(max(0, min(100, best_score))), rationale=rationale, best_contact_name=best.get("name"),
                            best_contact_url=best.get("profile_url"), relationship=best["relationship"])


# --- priority --------------------------------------------------------------------------


def industrial_priority(fit: int, recruit: int, networking: int, decision: Decision, rules: dict[str, Any]) -> float | None:
    """40% recruitability + 35% career fit + 25% networking (weights configurable). None once rejected."""
    if decision == Decision.REJECTED:
        return None
    w = (rules.get("industrial_priority") or {}).get("weights", {"recruitability": 0.40, "career_fit": 0.35, "networking": 0.25})
    return round(w["recruitability"] * recruit + w["career_fit"] * fit + w["networking"] * networking, 1)


def rank_with(priority: PriorityReport, value: float, rules: dict[str, Any], label: str) -> PriorityReport:
    """Re-rank an eligible opportunity on another score, with the same tiers and notify threshold."""
    if priority.priority_score is None:
        return priority
    pr = rules["priority"]
    tiers = pr["tiers"]
    tier = next((t for t in ("exceptional", "high", "review") if value >= tiers[t]), "suppressed")
    notify = value >= pr["notify_threshold"] or priority.strategic_bypass
    reason = f"{label}: {value}" + ("; a low-confidence conflict needs checking" if "conflict" in priority.reason else "")
    return priority.model_copy(update={"priority_score": value, "tier": tier, "notify": notify, "reason": reason})



def score_priority(fit: FitReport, eligibility: EligibilityReport, networking: NetworkingReport, rules: dict[str, Any],
                   company_priority: int | None) -> PriorityReport:
    pr = rules["priority"]
    if eligibility.decision == Decision.REJECTED:
        fails = "; ".join(c.explanation for c in eligibility.failures[:2])
        return PriorityReport(priority_score=None, tier="rejected", notify=False, reason=fails)
    value = round(pr["fit_weight"] * fit.score + pr["networking_weight"] * networking.score, 1)
    tiers = pr["tiers"]
    if value >= tiers["exceptional"]:
        tier = "exceptional"
    elif value >= tiers["high"]:
        tier = "high"
    elif value >= tiers["review"]:
        tier = "review"
    else:
        tier = "suppressed"
    notify = value >= pr["notify_threshold"]
    bypass = False
    sb = rules["strategic_bypass"]
    if not notify and fit.family_tier == 1 and company_priority == 1 and fit.score >= sb["min_fit"]:
        bypass = notify = True
    weak_fail = any(c.status == CriterionStatus.FAIL for c in eligibility.criteria)
    reason = "strategic bypass (tier-1 role at a priority-1 company)" if bypass else f"priority {value}"
    if weak_fail:
        reason += "; a low-confidence conflict needs checking"
    return PriorityReport(priority_score=value, tier=tier, notify=notify, strategic_bypass=bypass, reason=reason)
