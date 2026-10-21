"""Normalized records produced by collectors."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PostClass(str, Enum):
    CONFIRMED_INTERNSHIP = "confirmed_internship"
    RECRUITER_WITH_VACANCY = "recruiter_announcement_with_vacancy"
    HIRING_MANAGER_UPCOMING = "hiring_manager_upcoming_opening"
    GENERAL_CAMPAIGN = "general_recruitment_campaign"
    REPOST = "repost"
    CAREER_ADVICE = "career_advice"
    IRRELEVANT = "irrelevant"


ACTIONABLE_POST_CLASSES = {
    PostClass.CONFIRMED_INTERNSHIP,
    PostClass.RECRUITER_WITH_VACANCY,
    PostClass.HIRING_MANAGER_UPCOMING,
    PostClass.GENERAL_CAMPAIGN,
    PostClass.REPOST,
}


class Relationship(str, Enum):
    HIRING_MANAGER_POST = "hiring_manager_post"
    RECRUITER_POST = "recruiter_post"
    TEAM_MEMBER_POST = "team_member_post"
    NAMED_RECRUITER_ON_VACANCY = "named_recruiter_on_vacancy"
    RECRUITING_TEAM = "recruiting_team"
    OTHER_EVIDENCE = "other_evidence"


class ContactCandidate(BaseModel):
    name: str
    role: str | None = None
    company: str | None = None
    profile_url: str | None = None
    source_post_url: str | None = None
    relationship: Relationship
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(max_length=400)

    @field_validator("evidence", mode="before")
    @classmethod
    def _trim(cls, value: str) -> str:
        return (value or "")[:400]


class LinkedInPostInfo(BaseModel):
    post_url: str
    author_name: str | None = None
    author_profile_url: str | None = None
    author_headline: str | None = None
    text: str = ""
    published_at: datetime | None = None
    classification: PostClass = PostClass.IRRELEVANT
    classification_confidence: float = 0.0
    classification_reasons: list[str] = Field(default_factory=list)
    relationship: Relationship | None = None          # author's relation to the opening, from evidence
    relationship_confidence: float = 0.0
    role_hint: str | None = None                       # exact role phrase verified in the post
    discovery_method: str = "manual"


class RawOpportunity(BaseModel):
    """One listing as seen by one source. Unknown values stay None (rendered UNKNOWN)."""

    source: str
    source_id: str
    url: str
    apply_url: str | None = None
    title: str
    company: str
    location_text: str | None = None
    country_hint: str | None = None
    description_text: str = ""
    posted_at: datetime | None = None
    updated_marker: str | None = None       # source-provided change marker (e.g. updated_at)
    employment_type_hint: str | None = None
    remote_hint: bool | None = None
    official_job_id: str | None = None      # ATS requisition id when exposed
    contacts: list[ContactCandidate] = Field(default_factory=list)
    linkedin_post: LinkedInPostInfo | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "company")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("must not be empty")
        return value[:300]


class CollectorResult(BaseModel):
    source: str
    records: list[RawOpportunity] = Field(default_factory=list)
    posts: list[LinkedInPostInfo] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    skipped_unchanged: int = 0
    scanned: int = 0
    status: str = "ok"          # ok | partial | failed | disabled
    # Every id currently listed by the source (not only internships), and whether that listing
    # was complete. Used to close opportunities that disappeared from the source.
    seen_ids: list[str] | None = None
    listing_complete: bool = False
    detail: str | None = None
