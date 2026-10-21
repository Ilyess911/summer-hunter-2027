"""Fit, networking and priority result models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Adjustment(BaseModel):
    label: str
    delta: float
    component: str | None = None   # None = applied to the total


class FitReport(BaseModel):
    score: int = Field(ge=0, le=100)
    components: dict[str, float]
    adjustments: list[Adjustment] = Field(default_factory=list)
    job_family: str
    family_tier: int
    reasons: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    relevant_experiences: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    career_value: str = ""
    evidence: list[str] = Field(default_factory=list)
    cold_calls_per_day: int | None = None
    method: str = "deterministic"
    deterministic_score: int | None = None
    llm_score: int | None = None
    injection_flags: list[str] = Field(default_factory=list)


class NetworkingReport(BaseModel):
    score: int = Field(ge=0, le=100)
    rationale: str
    best_contact_name: str | None = None
    best_contact_url: str | None = None
    relationship: str | None = None


class PriorityReport(BaseModel):
    priority_score: float | None       # None when a mandatory criterion failed
    tier: str                          # exceptional | high | review | suppressed | rejected
    notify: bool
    strategic_bypass: bool = False
    reason: str = ""
