"""SQLAlchemy ORM schema (SQLite)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
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
    recruitability_score: Mapped[int | None] = mapped_column(Integer)
    industrial_priority: Mapped[float | None] = mapped_column(Float)
    career_track: Mapped[str | None] = mapped_column(String(30), index=True)       # business_tech | industry_robotics
    career_category: Mapped[str | None] = mapped_column(String(60))
    # V3 (23 Oct 2026): compensation, global priority, strategist, deadlines. NULL = not evaluated yet.
    semantic_family: Mapped[str | None] = mapped_column(String(60))
    compensation_status: Mapped[str | None] = mapped_column(String(30))
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[str | None] = mapped_column(String(12))
    salary_basis: Mapped[str | None] = mapped_column(String(8))           # gross | net | unknown, as stated
    monthly_min_eur: Mapped[float | None] = mapped_column(Float)
    monthly_max_eur: Mapped[float | None] = mapped_column(Float)
    living_cost_eur: Mapped[float | None] = mapped_column(Float)
    monthly_balance_eur: Mapped[float | None] = mapped_column(Float)
    compensation_confidence: Mapped[str | None] = mapped_column(String(10))
    compensation_score: Mapped[int | None] = mapped_column(Integer)
    global_priority: Mapped[float | None] = mapped_column(Float)
    global_provisional: Mapped[bool | None] = mapped_column(Boolean)
    next_action: Mapped[str | None] = mapped_column(String(30))
    strategy_category: Mapped[str | None] = mapped_column(String(12))
    application_deadline: Mapped[date | None] = mapped_column(Date)
    deadline_type: Mapped[str | None] = mapped_column(String(12))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    recruitability: Mapped[dict | None] = mapped_column(JSON)
    compensation: Mapped[dict | None] = mapped_column(JSON)
    strategy: Mapped[dict | None] = mapped_column(JSON)
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


class CRMApplication(Base):
    """One application you chose to track. SQLite is the source of truth; the Excel workbook mirrors it."""
    __tablename__ = "crm_applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    furthest_status: Mapped[str] = mapped_column(String(30))     # deepest pipeline stage ever reached (for funnel metrics)
    deadline: Mapped[date | None] = mapped_column(Date)
    deadline_source: Mapped[str | None] = mapped_column(String(20))   # posting | manual
    date_applied: Mapped[date | None] = mapped_column(Date)
    next_action: Mapped[str | None] = mapped_column(String(300))
    next_action_date: Mapped[date | None] = mapped_column(Date)
    interview_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recruiter_name: Mapped[str | None] = mapped_column(String(160))
    recruiter_linkedin: Mapped[str | None] = mapped_column(String(500))
    compensation: Mapped[str | None] = mapped_column(String(200))
    internship_start: Mapped[date | None] = mapped_column(Date)
    internship_end: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    folder: Mapped[str | None] = mapped_column(String(500))
    cv_path: Mapped[str | None] = mapped_column(String(500))
    cv_hash: Mapped[str | None] = mapped_column(String(64))
    cv_version: Mapped[int] = mapped_column(Integer, default=0)
    cv_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    letter_path: Mapped[str | None] = mapped_column(String(500))
    letter_hash: Mapped[str | None] = mapped_column(String(64))
    letter_version: Mapped[int] = mapped_column(Integer, default=0)
    letter_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_folder: Mapped[str | None] = mapped_column(String(500))    # frozen copy of the documents actually sent
    version: Mapped[int] = mapped_column(Integer, default=1)             # bumped on every change; guards Excel imports
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opportunity: Mapped[Opportunity] = relationship()


class CRMContact(Base):
    """A professional contact you manage. Only public-evidence contacts or ones you entered yourself."""
    __tablename__ = "crm_contacts"
    __table_args__ = (UniqueConstraint("company", "normalized_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(160))
    normalized_name: Mapped[str] = mapped_column(String(160))
    position: Mapped[str | None] = mapped_column(String(200))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    application_id: Mapped[int | None] = mapped_column(ForeignKey("crm_applications.id"), index=True)
    contact_type: Mapped[str] = mapped_column(String(30))
    first_contact_date: Mapped[date | None] = mapped_column(Date)
    last_interaction_date: Mapped[date | None] = mapped_column(Date)
    interaction_status: Mapped[str] = mapped_column(String(30))
    next_followup_date: Mapped[date | None] = mapped_column(Date)
    next_action: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(300))      # manual | public evidence URL
    # V4 networking: role category, verification, dismissal. A contact is one person, linked to many opportunities.
    category: Mapped[str | None] = mapped_column(String(30))       # hiring_manager | talent_acquisition | alumni_esilv ...
    verification: Mapped[str | None] = mapped_column(String(20))   # verified | unverified
    dismissed: Mapped[bool | None] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContactEvidence(Base):
    """Why we believe a contact exists and matters: a public post, a vacancy naming them, a profile, the user."""
    __tablename__ = "contact_evidence"
    __table_args__ = (UniqueConstraint("contact_id", "url", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("crm_contacts.id"), index=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    kind: Mapped[str] = mapped_column(String(30))       # public_post | vacancy | profile | company_site | user | search_result
    url: Mapped[str] = mapped_column(String(1000), default="")
    excerpt: Mapped[str | None] = mapped_column(String(600))      # what was actually retrieved, verbatim
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContactOpportunity(Base):
    """Contact x opportunity link with the ranking and timing computed for that pair."""
    __tablename__ = "contact_opportunities"
    __table_args__ = (UniqueConstraint("contact_id", "opportunity_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("crm_contacts.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    relationship_note: Mapped[str | None] = mapped_column(String(300))
    score: Mapped[int | None] = mapped_column(Integer)
    score_components: Mapped[dict | None] = mapped_column(JSON)
    explanation: Mapped[str | None] = mapped_column(Text)
    timing_action: Mapped[str | None] = mapped_column(String(32))   # CONTACT_BEFORE_APPLICATION | ... | DO_NOT_CONTACT
    timing_date: Mapped[date | None] = mapped_column(Date)
    timing_reason: Mapped[str | None] = mapped_column(String(400))
    timing_confidence: Mapped[str | None] = mapped_column(String(10))
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutreachMessage(Base):
    """A drafted LinkedIn message. Never sent by the system: the user copies it and marks it sent."""
    __tablename__ = "outreach_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("crm_contacts.id"), index=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    kind: Mapped[str] = mapped_column(String(30))       # connection_request | initial | before_applying | after_applying ...
    variant: Mapped[str] = mapped_column(String(20), default="default")   # default | shorter | more_direct | more_formal | more_natural
    text: Mapped[str] = mapped_column(Text)
    generator: Mapped[str] = mapped_column(String(80))   # template | ollama:<model>
    evidence_refs: Mapped[list | None] = mapped_column(JSON)
    validation: Mapped[dict | None] = mapped_column(JSON)
    personalization: Mapped[str] = mapped_column(String(10), default="full")   # full | limited
    status: Mapped[str] = mapped_column(String(12), default="draft")   # draft | sent | dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Interaction(Base):
    """What actually happened with a contact (recorded by the user)."""
    __tablename__ = "interactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("crm_contacts.id"), index=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_messages.id"))
    kind: Mapped[str] = mapped_column(String(20))       # message_sent | reply | call | meeting | note | follow_up_scheduled
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str | None] = mapped_column(Text)
    follow_up_on: Mapped[date | None] = mapped_column(Date)


class SyncChange(Base):
    """Hosted-mode outbox: edits made online, waiting to be pulled and applied by the Mac (the source of truth)."""
    __tablename__ = "sync_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncStaging(Base):
    """Hosted side: snapshot chunks uploaded by the Mac, swapped in atomically by /api/sync/done."""
    __tablename__ = "sync_staging"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    table_name: Mapped[str] = mapped_column(String(60))
    rows: Mapped[str] = mapped_column(Text)                      # JSON list of row dicts


class SyncApplied(Base):
    """Mac side: hosted changes already replayed, so a lost acknowledgement never applies a change twice."""
    __tablename__ = "sync_applied"
    origin: Mapped[str] = mapped_column(String(40), primary_key=True)
    change_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncIdMap(Base):
    """Mac side: id of a row created online (per snapshot generation) -> id of the row created by the replay."""
    __tablename__ = "sync_id_map"
    origin: Mapped[str] = mapped_column(String(40), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    entity: Mapped[str] = mapped_column(String(20), primary_key=True)
    hosted_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    local_id: Mapped[int] = mapped_column(Integer)


class CRMEvent(Base):
    """Append-only activity log. Rows are never updated or deleted."""
    __tablename__ = "crm_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("crm_applications.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("crm_contacts.id"))
    company: Mapped[str | None] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(40))
    previous_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))       # telegram | excel | miniapp | cli | system | migration
    notes: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)


class CRMReminder(Base):
    """One row per reminder ever delivered, so the same reminder is never sent twice."""
    __tablename__ = "crm_reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(30))         # followup_application | followup_contact | deadline
    application_id: Mapped[int | None] = mapped_column(ForeignKey("crm_applications.id"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("crm_contacts.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecruitmentSignal(Base):
    """Public hint that internships may open (never shown as a vacancy until confirmed)."""
    __tablename__ = "recruitment_signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(24), index=True)     # CONFIRMED_OPENING | RECRUITMENT_SIGNAL | COMPANY_WATCH
    company: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(60))
    source_url: Mapped[str] = mapped_column(String(1000), unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class FxRate(Base):
    """Euro reference rates, one row per currency and day, with their source."""
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("currency", "rate_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), index=True)
    per_eur: Mapped[float] = mapped_column(Float)                  # units of currency for 1 EUR
    rate_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(200))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


SCHEMA_VERSION = 5
