"""SQLAlchemy ORM schema (SQLite)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    ats: Mapped[str | None] = mapped_column(String(40))
    sector: Mapped[str | None] = mapped_column(String(60))
    priority: Mapped[int | None] = mapped_column(Integer)
    watchlist: Mapped[bool] = mapped_column(Boolean, default=False)


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    company_name: Mapped[str] = mapped_column(String(300))
    normalized_company: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(300))
    normalized_title: Mapped[str] = mapped_column(String(300))
    official_job_id: Mapped[str | None] = mapped_column(String(120))
    job_family: Mapped[str | None] = mapped_column(String(60))
    family_tier: Mapped[int | None] = mapped_column(Integer)
    location_text: Mapped[str | None] = mapped_column(String(400))
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    countries: Mapped[list | None] = mapped_column(JSON)
    city: Mapped[str | None] = mapped_column(String(120))
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    description_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    official_url: Mapped[str] = mapped_column(String(1000))
    apply_url: Mapped[str | None] = mapped_column(String(1000))
    linkedin_post_url: Mapped[str | None] = mapped_column(String(1000))
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="active")        # active | closed
    user_status: Mapped[str] = mapped_column(String(20), default="new")      # new | saved | dismissed | applied
    eligibility_status: Mapped[str | None] = mapped_column(String(30), index=True)
    fit_score: Mapped[int | None] = mapped_column(Integer)
    networking_score: Mapped[int | None] = mapped_column(Integer)
    priority_score: Mapped[float | None] = mapped_column(Float)
    priority_tier: Mapped[str | None] = mapped_column(String(20))
    notify: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluated_hash: Mapped[str | None] = mapped_column(String(64))
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sources: Mapped[list["SourceRecord"]] = relationship(back_populates="opportunity", cascade="all, delete-orphan")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="opportunity", cascade="all, delete-orphan")


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("source", "source_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    source: Mapped[str] = mapped_column(String(60))
    source_id: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(1000))
    apply_url: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(300))
    location_text: Mapped[str | None] = mapped_column(String(400))
    content_hash: Mapped[str] = mapped_column(String(64))
    updated_marker: Mapped[str | None] = mapped_column(String(80))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opportunity: Mapped[Opportunity] = relationship(back_populates="sources")


class LinkedInPost(Base):
    __tablename__ = "linkedin_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_url: Mapped[str] = mapped_column(String(1000), unique=True)
    author_name: Mapped[str | None] = mapped_column(String(160))
    author_profile_url: Mapped[str | None] = mapped_column(String(500))
    author_headline: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, default="")          # truncated to 2000 chars
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification: Mapped[str] = mapped_column(String(60))
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    discovery_method: Mapped[str] = mapped_column(String(40))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class Contact(Base):
    __tablename__ = "recruiter_contacts"
    __table_args__ = (UniqueConstraint("opportunity_id", "name", "relationship_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str | None] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))
    profile_url: Mapped[str | None] = mapped_column(String(500))
    source_post_url: Mapped[str | None] = mapped_column(String(1000))
    relationship_type: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[str] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opportunity: Mapped[Opportunity] = relationship(back_populates="contacts")


class EligibilityEvaluation(Base):
    __tablename__ = "eligibility_evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    decision: Mapped[str] = mapped_column(String(30))
    sponsorship: Mapped[str] = mapped_column(String(60))
    criteria: Mapped[list] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(10))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FitEvaluation(Base):
    __tablename__ = "fit_evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(30))
    report: Mapped[dict] = mapped_column(JSON)
    networking: Mapped[dict] = mapped_column(JSON)
    priority: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIAnalysis(Base):
    """Cache: one validated LLM analysis per (content hash, model, prompt version)."""
    __tablename__ = "ai_analyses"
    __table_args__ = (UniqueConstraint("content_hash", "model", "prompt_version", "purpose"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    purpose: Mapped[str] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserFeedback(Base):
    __tablename__ = "user_feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))          # save | dismiss | applied | reason
    reason: Mapped[str | None] = mapped_column(String(60))
    job_family: Mapped[str | None] = mapped_column(String(60))
    company: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="applied")
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notification_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(30))             # card | digest | post_lead | alert
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="sent")   # sent | dry_run | failed
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(30))          # manual | nightly | catch_up
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)


class SourceHealth(Base):
    __tablename__ = "source_health"
    source: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_item_count: Mapped[int] = mapped_column(Integer, default=0)
    last_status: Mapped[str | None] = mapped_column(String(20))
    total_runs: Mapped[int] = mapped_column(Integer, default=0)


class LLMUsage(Base):
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    local_date: Mapped[str] = mapped_column(String(10), index=True)   # Europe/Paris date, budget key
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(40))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    opportunity_id: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(30), default="ok")


class PreferenceAdjustment(Base):
    __tablename__ = "preference_adjustments"
    __table_args__ = (UniqueConstraint("dimension", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dimension: Mapped[str] = mapped_column(String(30))       # job_family | company | country | pattern
    key: Mapped[str] = mapped_column(String(200))
    delta: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TravelAdvisory(Base):
    __tablename__ = "travel_advisories"
    country_code: Mapped[str] = mapped_column(String(2), primary_key=True)
    level: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(String(600))
    source_url: Mapped[str] = mapped_column(String(500))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    manual: Mapped[bool] = mapped_column(Boolean, default=False)


class KeyValue(Base):
    __tablename__ = "kv_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


SCHEMA_VERSION = 1
