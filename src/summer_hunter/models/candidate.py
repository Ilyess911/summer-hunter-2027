"""Candidate profile with explicit UNKNOWN handling."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

UNKNOWN = "UNKNOWN"


def tri(value: Any) -> bool | None:
    """YAML tri-state: true/false, or None for UNKNOWN/missing."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "yes"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "no"}:
        return False
    return None


def maybe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Experience(BaseModel):
    id: str
    organization: str
    role: str
    type: str
    summary: str
    keywords: list[str] = Field(default_factory=list)


class LanguageSkill(BaseModel):
    language: str
    level: str
    evidence: str | None = None


class CandidateProfile(BaseModel):
    school: str
    school_country: str
    degree_program: str
    graduation_year: int
    graduation_month: int | None
    current_study_year: str | None
    returning_to_studies: bool | None
    bachelors_enrolled: bool | None
    masters_enrolled: bool | None
    mba: bool
    phd: bool
    languages: list[LanguageSkill]
    citizenship: list[str]
    work_authorization: dict[str, bool]
    requires_sponsorship: bool | None
    willing_internship_visa: bool | None
    available_from: date
    available_to: date
    preferred_weeks: tuple[int, int]
    acceptable_weeks: tuple[int, int]
    allow_unpaid: bool
    certifications: list[str]
    licenses: list[str]
    experiences: list[Experience]
    verified_skills: list[str]
    achievements: list[str]

    @property
    def language_names(self) -> set[str]:
        return {lang.language.lower() for lang in self.languages}

    @property
    def window_weeks(self) -> float:
        return ((self.available_to - self.available_from).days + 1) / 7.0

    @classmethod
    def from_yaml(cls, raw: dict[str, Any]) -> "CandidateProfile":
        ident = raw.get("identity", {})
        equiv = ident.get("degree_equivalence", {}) or {}
        imm = raw.get("immigration", {}) or {}
        avail = raw.get("availability", {}) or {}
        study_year = ident.get("current_study_year")
        return cls(
            school=ident.get("school", UNKNOWN),
            school_country=ident.get("school_country", UNKNOWN),
            degree_program=ident.get("degree_program", UNKNOWN),
            graduation_year=int(ident["expected_graduation_year"]),
            graduation_month=maybe_int(ident.get("expected_graduation_month")),
            current_study_year=None if study_year in (None, UNKNOWN) else str(study_year),
            returning_to_studies=tri(ident.get("returning_to_studies_after_internship")),
            bachelors_enrolled=tri(equiv.get("bachelors_enrolled")),
            masters_enrolled=tri(equiv.get("masters_enrolled")),
            mba=bool(tri(equiv.get("mba"))),
            phd=bool(tri(equiv.get("phd"))),
            languages=[LanguageSkill(**item) for item in raw.get("languages", [])],
            citizenship=[c.upper() for c in imm.get("citizenship", []) or []],
            work_authorization={k.upper(): bool(v) for k, v in (imm.get("work_authorization") or {}).items() if tri(v) is not None},
            requires_sponsorship=tri(imm.get("requires_visa_sponsorship")),
            willing_internship_visa=tri(imm.get("willing_to_apply_internship_visa")),
            available_from=avail.get("start", date(2027, 4, 1)),
            available_to=avail.get("end", date(2027, 8, 31)),
            preferred_weeks=tuple(avail.get("preferred_weeks", [8, 12])),
            acceptable_weeks=tuple(avail.get("acceptable_weeks", [4, 20])),
            allow_unpaid=bool(tri((raw.get("compensation") or {}).get("allow_unpaid"))),
            certifications=raw.get("certifications", []) or [],
            licenses=raw.get("professional_licenses", []) or [],
            experiences=[Experience(**e) for e in raw.get("experiences", [])],
            verified_skills=[s.lower() for s in (raw.get("skills", {}) or {}).get("verified", [])],
            achievements=raw.get("achievements", []) or [],
        )

    def prompt_summary(self) -> str:
        """Verified-only candidate summary for LLM prompts."""
        lines = [
            f"School: {self.school} ({self.degree_program}); expected graduation {self.graduation_year}"
            f" (month: {self.graduation_month or UNKNOWN}).",
            "Languages: " + ", ".join(f"{l.language} ({l.level}{', ' + l.evidence if l.evidence else ''})" for l in self.languages)
            + ". No other languages.",
            f"Citizenship: {', '.join(self.citizenship) or UNKNOWN}. Work authorization outside France: "
            f"{self.work_authorization or UNKNOWN}.",
            "Verified skills: " + ", ".join(self.verified_skills) + ".",
            "Experiences (cite by id only):",
        ]
        for exp in self.experiences:
            lines.append(f"- id={exp.id}: {exp.organization}, {exp.role}. {exp.summary.strip()}")
        lines.append("Achievements: " + "; ".join(self.achievements))
        lines.append("No sales quota, closed deals, revenue figures or certifications are claimed.")
        return "\n".join(lines)
