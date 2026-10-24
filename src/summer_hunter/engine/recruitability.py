"""Recruitability: how closely the verified profile matches what the posting actually asks for.

It answers "how well does my demonstrated experience match the requirements?", nothing more.
It is a transparent 0-100 match score built from cited evidence, never a probability of an offer.

Components (weights in config/scoring_rules.yaml, `recruitability.weights`):
    direct_experience 30%   professional experience that is direct evidence for this kind of role
    technical_skills  25%   stated skills, weighted by level (mandatory > preferred > exposure)
    academic          20%   engineering degree and field of study
    transferable      15%   projects, entrepreneurship, leadership
    industry          10%   sector exposure (aerospace / MRO first)
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from summer_hunter.engine import requirements as reqs
from summer_hunter.engine.normalization import FamilyMatch
from summer_hunter.models.candidate import CandidateProfile
from summer_hunter.utils.text import normalize_for_match

_LEVEL_FR = {"mandatory": "obligatoire", "preferred": "souhaité"}
NOT_A_PROBABILITY = "Écart entre l’expérience vérifiée et les exigences de l’offre ; pas une probabilité d’embauche."


class RecruitabilityReport(BaseModel):
    score: int
    components: dict[str, float]
    relevant_experience: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)       # "ROS / ROS2 (mandatory)"
    blocking_requirements: list[str] = Field(default_factory=list)      # mandatory specialized gaps
    technical_requirements_known: bool = True
    reasons: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    note: str = NOT_A_PROBABILITY


def _hits(norm: str, keywords: list[str]) -> list[str]:
    return [k for k in keywords if re.search(r"(?<![a-z0-9])" + re.escape(normalize_for_match(k).strip()) + r"(?![a-z0-9])", norm)]


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def score(title: str, description: str, family: FamilyMatch, profile: CandidateProfile, rules: dict[str, Any],
          found: list[reqs.SkillRequirement] | None = None) -> RecruitabilityReport:
    cfg = rules["recruitability"]
    norm = normalize_for_match(f"{title}. {description}")
    reasons: list[tuple[float, str]] = []
    concerns: list[tuple[float, str]] = []

    # 1. Direct professional experience ---------------------------------------------------
    fam_map = cfg.get("experience_families", {})
    professional = [e for e in profile.experiences if e.type == "professional"]
    direct = 20.0
    relevant: list[str] = []
    for exp in professional:
        hits = _hits(norm, exp.keywords)
        is_direct = family.id in fam_map.get(exp.id, [])
        if not hits and not is_direct:
            continue
        gain = len(hits) * cfg["points_per_professional_hit"] + (cfg["direct_family_bonus"] if is_direct and hits else 0)
        if gain:
            direct += gain
            relevant.append(f"{exp.organization}: {', '.join(hits[:4]) or exp.role}")
            reasons.append((gain, f"{exp.organization} ({exp.role}) correspond : {', '.join(hits[:4])}." if hits
                            else f"{exp.organization} est une expérience directe pour ce type de poste."))
    direct = _clamp(direct)
    if direct < 40:
        concerns.append((70, "Peu de recoupement professionnel direct avec les missions de l’offre."))

    # 2. Technical skills, by requirement level ----------------------------------------------
    found = found if found is not None else reqs.extract(description, cfg["skills"], profile.verified_skills)
    lw = cfg["level_weights"]
    known = bool(found)
    if found:
        total = sum(lw[r.level] for r in found)
        matched = sum(lw[r.level] for r in found if r.verified)
        technical = 100.0 * matched / total if total else cfg["unknown_technical_score"]
        held = [r.name for r in found if r.verified]
        if held:
            reasons.append((30 + 5 * len(held), f"Compétences vérifiées demandées : {', '.join(held[:4])}."))
    else:
        technical = float(cfg["unknown_technical_score"])
        concerns.append((40, "Exigences techniques peu claires : adéquation des compétences inconnue."))
    missing = [f"{r.name} ({_LEVEL_FR.get(r.level, r.level)})" for r in found if not r.verified and r.level != "exposure"]
    blocking = reqs.blocking(found)

    # 3. Academic background --------------------------------------------------------------------
    ac = cfg["academic"]
    academic = float(ac["base"])
    field_hits = _hits(norm, ac["field_keywords"])
    if field_hits:
        academic += ac["field_match_points"]
        reasons.append((35, f"Études d’ingénieur en industrie et robotique : correspondent au domaine demandé ({', '.join(field_hits[:3])})."))
    if re.search(r"\bmaintenance\b", norm) and any("maintenance" in d.lower() for d in _previous_degrees(profile)):
        academic += ac["maintenance_degree_points"]
    if re.search(r"\bph\.?d\b.{0,40}\b(required|must)\b|\b(required|must)\b.{0,40}\bph\.?d\b", norm):
        academic -= ac["phd_required_penalty"]
        concerns.append((80, "Un doctorat est exigé."))
    unrelated = _hits(norm, ac["unrelated_fields"])
    if unrelated and not field_hits:
        academic -= ac["unrelated_field_penalty"]
        concerns.append((50, f"Domaine d’études demandé : {', '.join(unrelated[:2])}."))
    academic = _clamp(academic)

    # 4. Transferable experience (projects, entrepreneurship, leadership) -------------------------
    transferable = 30.0
    for exp in profile.experiences:
        if exp.type == "professional":
            continue
        hits = _hits(norm, exp.keywords)
        if hits:
            transferable += len(hits) * cfg["points_per_transferable_hit"]
            relevant.append(f"{exp.organization}: {', '.join(hits[:3])}")
    transferable = _clamp(transferable)

    # 5. Industry exposure -------------------------------------------------------------------------
    industry = float(cfg["industry"]["default"])
    for level, words in sorted(cfg["industry"]["sectors"].items(), key=lambda kv: -int(kv[0])):
        if _hits(norm, words):
            industry = float(level)
            if int(level) >= 90:
                reasons.append((45, "Secteur aéronautique / MRO : même environnement qu’Air France Industries et Airbus Helicopters."))
            break

    components = {"direct_experience": round(direct, 1), "technical_skills": round(technical, 1),
                  "academic": round(academic, 1), "transferable": round(transferable, 1), "industry": round(industry, 1)}
    total = sum(components[k] * w for k, w in cfg["weights"].items())
    if blocking:
        total -= cfg["advanced_mandatory_penalty"] * len(blocking)
        if len(blocking) >= 2:
            total = min(total, cfg["advanced_mandatory_cap"])
        concerns.append((95, "Expertise spécialisée obligatoire absente du profil vérifié : "
                         + ", ".join(r.name for r in blocking[:3]) + "."))
    for r in found:
        if r.advanced and not r.verified and r.level == "preferred":
            concerns.append((30, f"{r.name} est un plus, pas une exigence."))
            break
    reasons.sort(key=lambda x: -x[0])
    concerns.sort(key=lambda x: -x[0])
    return RecruitabilityReport(
        score=int(round(_clamp(total))), components=components, relevant_experience=relevant[:4],
        missing_requirements=missing[:6], blocking_requirements=[r.name for r in blocking],
        technical_requirements_known=known, reasons=[r for _, r in reasons[:3]], concerns=[c for _, c in concerns[:3]],
    )


def _previous_degrees(profile: CandidateProfile) -> list[str]:
    return [profile.degree_program, *profile.previous_degrees]
