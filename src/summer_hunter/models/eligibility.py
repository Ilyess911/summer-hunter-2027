"""Eligibility result models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class CriterionStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Decision(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    REJECTED = "REJECTED"


class SponsorshipStatus(str, Enum):
    AVAILABLE = "sponsorship_explicitly_available"
    UNAVAILABLE = "sponsorship_explicitly_unavailable"
    EXISTING_AUTH_REQUIRED = "existing_work_authorization_required"
    NOT_SPECIFIED = "work_authorization_not_specified"
    UNCLEAR = "immigration_eligibility_unclear"


class CriterionResult(BaseModel):
    criterion: str
    status: CriterionStatus
    explanation: str
    evidence: list[str] = Field(default_factory=list)
    source_url: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    material: bool = True


class EligibilityReport(BaseModel):
    decision: Decision
    criteria: list[CriterionResult]
    sponsorship: SponsorshipStatus = SponsorshipStatus.NOT_SPECIFIED
    hard_exclusion: bool = False      # a confirmed FAIL that nothing may override
    engine_version: str = "1"

    def by_name(self, name: str) -> CriterionResult | None:
        return next((c for c in self.criteria if c.criterion == name), None)

    @property
    def failures(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.status == CriterionStatus.FAIL]

    @property
    def unknowns(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.status == CriterionStatus.UNKNOWN and c.material]
