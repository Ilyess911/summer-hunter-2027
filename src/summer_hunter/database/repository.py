"""Data access layer. All SQL goes through SQLAlchemy (parameterized, no string-built SQL)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from summer_hunter.database.models import (
    AIAnalysis,
    Application,
    CollectionRun,
    Company,
    Contact,
    EligibilityEvaluation,
    FitEvaluation,
    KeyValue,
    LinkedInPost,
    LLMUsage,
    Notification,
    Opportunity,
    PreferenceAdjustment,
    SourceHealth,
    SourceRecord,
    TravelAdvisory,
    UserFeedback,
    utcnow,
)
from summer_hunter.engine.deduplication import Candidate


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class Repository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def commit(self) -> None:
        self.s.commit()

    # Companies -------------------------------------------------------------------------
    def upsert_company(self, name: str, normalized: str, **fields: Any) -> Company:
        company = self.s.execute(select(Company).where(Company.normalized_name == normalized)).scalar_one_or_none()
        if company is None:
            company = Company(name=name, normalized_name=normalized, **fields)
            self.s.add(company)
            self.s.flush()
        else:
            for key, value in fields.items():
                if value is not None:
                    setattr(company, key, value)
        return company

    def company_by_normalized(self, normalized: str) -> Company | None:
        return self.s.execute(select(Company).where(Company.normalized_name == normalized)).scalar_one_or_none()

    # Opportunities ---------------------------------------------------------------------
    def source_record(self, source: str, source_id: str) -> SourceRecord | None:
        return self.s.execute(
            select(SourceRecord).where(SourceRecord.source == source, SourceRecord.source_id == source_id)
        ).scalar_one_or_none()

    def records_for_source(self, source: str) -> list[SourceRecord]:
        return list(self.s.execute(select(SourceRecord).where(SourceRecord.source == source)).scalars())

    def dedupe_candidates(self, normalized_company: str) -> list[Candidate]:
        rows = self.s.execute(
            select(Opportunity.id, Opportunity.normalized_company, Opportunity.normalized_title,
                   Opportunity.country_code, Opportunity.official_job_id)
            .where(Opportunity.normalized_company == normalized_company)
        ).all()
        return [Candidate(r[0], r[1], r[2], r[3] or "XX", r[4]) for r in rows]

    def opportunity(self, opp_id: int) -> Opportunity | None:
        return self.s.get(Opportunity, opp_id)

    def opportunity_by_key(self, key: str) -> Opportunity | None:
        return self.s.execute(select(Opportunity).where(Opportunity.canonical_key == key)).scalar_one_or_none()

    def add(self, obj: Any) -> Any:
        self.s.add(obj)
        self.s.flush()
        return obj

    def add_contact(self, opp: Opportunity, **fields: Any) -> Contact | None:
        exists = self.s.execute(
            select(Contact).where(Contact.opportunity_id == opp.id, Contact.name == fields["name"],
                                  Contact.relationship_type == fields["relationship_type"])
        ).scalar_one_or_none()
        if exists:
            return None
        contact = Contact(opportunity_id=opp.id, **fields)
        self.s.add(contact)
        self.s.flush()
        return contact

    def contacts(self, opp_id: int) -> list[Contact]:
        return list(self.s.execute(select(Contact).where(Contact.opportunity_id == opp_id)).scalars())

    def opportunities_needing_evaluation(self, force: bool = False) -> list[Opportunity]:
        stmt = select(Opportunity).where(Opportunity.status == "active")
        if not force:
            stmt = stmt.where((Opportunity.evaluated_hash.is_(None)) | (Opportunity.evaluated_hash != Opportunity.content_hash))
        return list(self.s.execute(stmt).scalars())

    def all_active(self) -> list[Opportunity]:
        return list(self.s.execute(select(Opportunity).where(Opportunity.status == "active")).scalars())

    def top(self, limit: int = 10, include_dismissed: bool = False) -> list[Opportunity]:
        stmt = select(Opportunity).where(Opportunity.status == "active", Opportunity.eligibility_status != "REJECTED",
                                         Opportunity.priority_score.is_not(None))
        if not include_dismissed:
            stmt = stmt.where(Opportunity.user_status.not_in(("dismissed", "applied")))
        return list(self.s.execute(stmt.order_by(Opportunity.priority_score.desc()).limit(limit)).scalars())

    def discovered_since(self, since: datetime) -> list[Opportunity]:
        return list(self.s.execute(select(Opportunity).where(Opportunity.first_discovered_at >= since)
                                   .order_by(Opportunity.priority_score.desc().nulls_last())).scalars())

    def by_user_status(self, status: str, limit: int = 20) -> list[Opportunity]:
        return list(self.s.execute(select(Opportunity).where(Opportunity.user_status == status)
                                   .order_by(Opportunity.priority_score.desc().nulls_last()).limit(limit)).scalars())

    def by_eligibility(self, decision: str, limit: int = 20, since: datetime | None = None) -> list[Opportunity]:
        stmt = select(Opportunity).where(Opportunity.eligibility_status == decision)
        if since:
            stmt = stmt.where(Opportunity.first_discovered_at >= since)
        return list(self.s.execute(stmt.order_by(Opportunity.first_discovered_at.desc()).limit(limit)).scalars())

    def counts(self) -> dict[str, int]:
        rows = self.s.execute(select(Opportunity.eligibility_status, func.count()).group_by(Opportunity.eligibility_status)).all()
        out = {str(k): v for k, v in rows}
        out["total"] = sum(out.values())
        out["saved"] = self.s.scalar(select(func.count()).where(Opportunity.user_status == "saved")) or 0
        out["applied"] = self.s.scalar(select(func.count()).where(Opportunity.user_status == "applied")) or 0
        out["dismissed"] = self.s.scalar(select(func.count()).where(Opportunity.user_status == "dismissed")) or 0
        out["linkedin_posts"] = self.s.scalar(select(func.count()).select_from(LinkedInPost)) or 0
        out["source_records"] = self.s.scalar(select(func.count()).select_from(SourceRecord)) or 0
        return out

    # Evaluations -------------------------------------------------------------------------
    def latest_eligibility(self, opp_id: int) -> EligibilityEvaluation | None:
        return self.s.execute(select(EligibilityEvaluation).where(EligibilityEvaluation.opportunity_id == opp_id)
                              .order_by(EligibilityEvaluation.id.desc()).limit(1)).scalar_one_or_none()

    def latest_fit(self, opp_id: int) -> FitEvaluation | None:
        return self.s.execute(select(FitEvaluation).where(FitEvaluation.opportunity_id == opp_id)
                              .order_by(FitEvaluation.id.desc()).limit(1)).scalar_one_or_none()

    # LinkedIn posts -------------------------------------------------------------------------
    def linkedin_post(self, url: str) -> LinkedInPost | None:
        return self.s.execute(select(LinkedInPost).where(LinkedInPost.post_url == url)).scalar_one_or_none()

    def unnotified_post_leads(self, classes: Iterable[str]) -> list[LinkedInPost]:
        return list(self.s.execute(select(LinkedInPost).where(LinkedInPost.notified.is_(False),
                                                              LinkedInPost.classification.in_(list(classes)))
                                   .order_by(LinkedInPost.discovered_at.desc())).scalars())

    # AI cache / usage ----------------------------------------------------------------------
    def cached_analysis(self, content_hash: str, model: str, prompt_version: str, purpose: str) -> AIAnalysis | None:
        return self.s.execute(select(AIAnalysis).where(AIAnalysis.content_hash == content_hash, AIAnalysis.model == model,
                                                       AIAnalysis.prompt_version == prompt_version,
                                                       AIAnalysis.purpose == purpose)).scalar_one_or_none()

    def spend_on(self, local_date: str) -> float:
        return float(self.s.scalar(select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(LLMUsage.local_date == local_date)) or 0.0)

    def usage_summary(self, days: int = 7) -> list[tuple[str, int, float]]:
        rows = self.s.execute(select(LLMUsage.local_date, func.count(), func.sum(LLMUsage.cost_usd))
                              .group_by(LLMUsage.local_date).order_by(LLMUsage.local_date.desc()).limit(days)).all()
        return [(r[0], r[1], float(r[2] or 0)) for r in rows]

    # Feedback ---------------------------------------------------------------------------------
    def record_feedback(self, opp: Opportunity | None, action: str, reason: str | None = None) -> UserFeedback:
        fb = UserFeedback(opportunity_id=opp.id if opp else None, action=action, reason=reason,
                          job_family=opp.job_family if opp else None, company=opp.normalized_company if opp else None,
                          country=opp.country_code if opp else None)
        self.s.add(fb)
        if opp and action in {"save", "dismiss", "applied"}:
            opp.user_status = {"save": "saved", "dismiss": "dismissed", "applied": "applied"}[action]
        if opp and action == "applied":
            if not self.s.execute(select(Application).where(Application.opportunity_id == opp.id)).scalar_one_or_none():
                self.s.add(Application(opportunity_id=opp.id))
        self.s.flush()
        return fb

    def feedback_rows(self) -> list[UserFeedback]:
        return list(self.s.execute(select(UserFeedback)).scalars())

    def preference_adjustments(self) -> list[PreferenceAdjustment]:
        return list(self.s.execute(select(PreferenceAdjustment)).scalars())

    def replace_preference_adjustments(self, rows: list[dict[str, Any]]) -> None:
        for row in self.preference_adjustments():
            self.s.delete(row)
        self.s.flush()
        for row in rows:
            self.s.add(PreferenceAdjustment(**row))
        self.s.flush()

    def reset_preferences(self) -> int:
        rows = self.preference_adjustments()
        for row in rows:
            self.s.delete(row)
        self.set_kv("preferences_reset_at", utcnow().isoformat())
        self.s.flush()
        return len(rows)

    # Notifications ---------------------------------------------------------------------------
    def notification_sent(self, dedupe_key: str) -> bool:
        row = self.s.execute(select(Notification).where(Notification.dedupe_key == dedupe_key)).scalar_one_or_none()
        return row is not None and row.status == "sent"

    def record_notification(self, dedupe_key: str, kind: str, opportunity_id: int | None, message_id: int | None, status: str) -> None:
        row = self.s.execute(select(Notification).where(Notification.dedupe_key == dedupe_key)).scalar_one_or_none()
        if row is None:
            self.s.add(Notification(dedupe_key=dedupe_key, kind=kind, opportunity_id=opportunity_id,
                                    telegram_message_id=message_id, status=status))
        else:
            row.status, row.telegram_message_id, row.sent_at = status, message_id, utcnow()
        self.s.flush()

    # Runs & health ---------------------------------------------------------------------------
    def start_run(self, trigger: str, dry_run: bool) -> CollectionRun:
        run = CollectionRun(trigger=trigger, dry_run=dry_run, stats={}, errors=[])
        self.s.add(run)
        self.s.flush()
        return run

    def last_run(self, successful_only: bool = True) -> CollectionRun | None:
        stmt = select(CollectionRun).where(CollectionRun.dry_run.is_(False))
        if successful_only:
            stmt = stmt.where(CollectionRun.status.in_(["ok", "partial"]))
        return self.s.execute(stmt.order_by(CollectionRun.id.desc()).limit(1)).scalar_one_or_none()

    def runs_since(self, since: datetime) -> list[CollectionRun]:
        return list(self.s.execute(select(CollectionRun).where(CollectionRun.started_at >= since, CollectionRun.dry_run.is_(False))).scalars())

    def update_health(self, source: str, ok: bool, items: int, status: str, error: str | None = None) -> SourceHealth:
        h = self.s.get(SourceHealth, source)
        if h is None:
            h = SourceHealth(source=source, consecutive_failures=0, total_runs=0, last_item_count=0)
            self.s.add(h)
        h.total_runs = (h.total_runs or 0) + 1
        h.last_status = status
        h.last_item_count = items
        if ok:
            h.last_success_at = utcnow()
            h.consecutive_failures = 0
        else:
            h.last_failure_at = utcnow()
            h.consecutive_failures = (h.consecutive_failures or 0) + 1
            h.last_error = (error or "")[:500]
        self.s.flush()
        return h

    def health(self) -> list[SourceHealth]:
        return list(self.s.execute(select(SourceHealth).order_by(SourceHealth.source)).scalars())

    # Advisories --------------------------------------------------------------------------------
    def advisories(self) -> dict[str, dict]:
        return {a.country_code: {"level": a.level, "source_url": a.source_url, "summary": a.summary,
                                 "retrieved_at": as_utc(a.retrieved_at).date().isoformat(), "manual": a.manual}
                for a in self.s.execute(select(TravelAdvisory)).scalars()}

    def upsert_advisory(self, code: str, level: str, summary: str, source_url: str, manual: bool = False) -> None:
        row = self.s.get(TravelAdvisory, code)
        if row is None:
            row = TravelAdvisory(country_code=code, level=level, summary=summary, source_url=source_url,
                                 retrieved_at=utcnow(), manual=manual)
            self.s.add(row)
        elif row.manual and not manual:
            return  # never let an automatic refresh overwrite the user's manual decision
        else:
            row.level, row.summary, row.source_url, row.retrieved_at, row.manual = level, summary, source_url, utcnow(), manual
        self.s.flush()

    # Key/value -----------------------------------------------------------------------------------
    def get_kv(self, key: str, default: str | None = None) -> str | None:
        row = self.s.get(KeyValue, key)
        return row.value if row else default

    def set_kv(self, key: str, value: str) -> None:
        row = self.s.get(KeyValue, key)
        if row is None:
            self.s.add(KeyValue(key=key, value=value))
        else:
            row.value = value
        self.s.flush()

    def stale_records(self, older_than: timedelta) -> list[SourceRecord]:
        cutoff = utcnow() - older_than
        return list(self.s.execute(select(SourceRecord).where(SourceRecord.last_seen_at < cutoff)).scalars())
