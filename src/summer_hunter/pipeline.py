"""Orchestration: collect -> normalize/dedupe -> eligibility -> fit -> networking -> LLM -> priority."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from summer_hunter.collectors.ashby import AshbyCollector
from summer_hunter.collectors.base import CollectContext, Collector
from summer_hunter.collectors.career_pages import CareerPageCollector
from summer_hunter.collectors.greenhouse import GreenhouseCollector
from summer_hunter.collectors.lever import LeverCollector
from summer_hunter.collectors.linkedin_alerts import LinkedInAlertsCollector
from summer_hunter.collectors.linkedin_public import LinkedInSearchCollector, opportunity_from_post, refine_post
from summer_hunter.collectors.workday import WorkdayCollector
from summer_hunter.database.models import CollectionRun, EligibilityEvaluation, FitEvaluation, LinkedInPost, Opportunity, SourceRecord, utcnow
from summer_hunter.database.repository import Repository, as_utc
from summer_hunter.engine import eligibility as elig
from summer_hunter.engine.ai_analysis import Analyzer, blend_fit, enrich_facts
from summer_hunter.engine.deduplication import find_match, make_key
from summer_hunter.engine.extraction import extract_facts
from summer_hunter.engine.feedback import adjustments_for
from summer_hunter.engine.geo import LocationResolution, resolve_location
from summer_hunter.engine.networking import contacts_from_vacancy
from summer_hunter.engine.normalization import TitleRules, normalize_company, normalize_title
from summer_hunter.engine.scoring import ScoringContext, score_fit, score_networking, score_priority
from summer_hunter.integrations.anthropic_client import LLMProvider, NullProvider, build_provider
from summer_hunter.integrations.search_providers import BraveSearch, GoogleCSE
from summer_hunter.models.candidate import CandidateProfile
from summer_hunter.models.eligibility import Decision
from summer_hunter.models.opportunity import CollectorResult, LinkedInPostInfo, RawOpportunity
from summer_hunter.utils.http import HostPolicy, PoliteClient
from summer_hunter.utils.settings import Settings
from summer_hunter.utils.text import detect_injection, sha256

log = logging.getLogger(__name__)

OFFICIAL_API_HOSTS = {"boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com"}
RECHECK_WITHOUT_MARKER = timedelta(days=7)
SOURCE_TIMEOUT_S = 600


@dataclass
class CycleStats:
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    new_opportunities: int = 0
    merged_duplicates: int = 0
    updated: int = 0
    posts: int = 0
    evaluated: int = 0
    llm_analyses: int = 0
    llm_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class Hunter:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session], provider: LLMProvider | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.profile = CandidateProfile.from_yaml(settings.profile_raw)
        self.title_rules = TitleRules(settings.preferences, settings.scoring)
        self.provider = provider if provider is not None else build_provider(settings)
        self.transport = transport
        self.http_min_interval = 1.0     # seconds between requests to one host (politeness)
        self.http_retries = 3
        geo = settings.preferences.get("geography", {})
        self.excluded = {c.upper() for c in geo.get("excluded_countries", [])}
        self.safety_excluded = {c.upper() for c in geo.get("safety_excluded_countries", [])}
        self.override_allow = {c.upper() for c in geo.get("country_overrides_allow", [])} - self.excluded
        self.priority_countries = {c for group in (geo.get("priority_countries") or {}).values() for c in group}
        self.priority_bonus = float(geo.get("priority_country_bonus", 0))
        self.country_penalties = {str(k).upper(): float(v) for k, v in (geo.get("country_penalties") or {}).items()}
        self.exclude_whole_country = bool(geo.get("exclude_whole_country_advised_against", True))
        self.watchlist = {normalize_company(c["name"]): c for c in settings.companies}

    def _display_country(self, countries: list[str]) -> str | None:
        """First country the candidate can actually work in (e.g. 'Paris; Madrid' shows Spain)."""
        return next((c for c in countries if c not in self.excluded), countries[0] if countries else None)

    # Collectors -----------------------------------------------------------------------------

    def build_collectors(self, only: set[str] | None = None) -> list[Collector]:
        out: list[Collector] = []
        for c in self.settings.companies:
            ats = c.get("ats")
            if ats == "greenhouse" and c.get("token"):
                out.append(GreenhouseCollector(c["name"], c["token"]))
            elif ats == "lever" and c.get("token"):
                out.append(LeverCollector(c["name"], c["token"]))
            elif ats == "ashby" and c.get("token"):
                out.append(AshbyCollector(c["name"], c["token"]))
            elif ats == "workday" and c.get("tenant"):
                out.append(WorkdayCollector(c["name"], c["tenant"], c["host"], c["site"]))
            elif ats == "career_page" and c.get("urls"):
                out.append(CareerPageCollector(c["name"], c["urls"]))
        s = self.settings
        imap = {"host": s.imap_host, "user": s.imap_user, "password": s.imap_password, "folder": s.imap_folder} \
            if s.imap_host and s.imap_user and s.imap_password else None
        out.append(LinkedInAlertsCollector(s.linkedin_alerts_dir, imap))
        provider = BraveSearch(s.brave_api_key) if s.brave_api_key else \
            GoogleCSE(s.google_cse_api_key, s.google_cse_cx) if s.google_cse_api_key and s.google_cse_cx else None
        disc = s.preferences.get("discovery", {})
        out.append(LinkedInSearchCollector(provider, disc.get("post_queries", []), disc.get("job_queries", []),
                                           int(disc.get("max_queries_per_run", 15)), int(disc.get("results_per_query", 10))))
        if only:
            out = [c for c in out if c.name in only or c.name.split(":")[0] in only]
        return out

    def _http(self) -> PoliteClient:
        return PoliteClient(
            user_agent=self.settings.user_agent,
            max_retries=self.http_retries,
            default_policy=HostPolicy(min_interval=self.http_min_interval, concurrency=2),
            host_policies={"api.search.brave.com": HostPolicy(min_interval=1.1, concurrency=1)},
            official_api_hosts=OFFICIAL_API_HOSTS,
            transport=self.transport,
        )

    async def collect(self, repo: Repository, collectors: list[Collector], stats: CycleStats, dry_run: bool,
                      should_stop=lambda: False) -> list[CollectorResult]:
        def is_unchanged(source: str, source_id: str, marker: str | None) -> bool:
            rec = repo.source_record(source, source_id)
            if rec is None:
                return False
            fresh = marker is not None and rec.updated_marker == marker
            recent = marker is None and as_utc(rec.last_seen_at) > utcnow() - RECHECK_WITHOUT_MARKER
            if fresh or recent:
                if fresh and not dry_run:
                    rec.last_seen_at = utcnow()
                    rec.opportunity.last_verified_at = utcnow()
                return True
            return False

        results: list[CollectorResult] = []
        sem = asyncio.Semaphore(4)
        async with self._http() as http:
            ctx = CollectContext(http=http, title_rules=self.title_rules, is_unchanged=is_unchanged, dry_run=dry_run)

            async def run_one(col: Collector) -> CollectorResult:
                async with sem:
                    if should_stop():
                        return CollectorResult(source=col.name, status="skipped", detail="stop requested")
                    try:
                        return await asyncio.wait_for(col.collect(ctx), timeout=SOURCE_TIMEOUT_S)
                    except asyncio.TimeoutError:
                        return CollectorResult(source=col.name, status="failed", errors=["timeout"])
                    except Exception as exc:  # noqa: BLE001 - isolate any collector bug
                        log.exception("collector crashed", extra={"source": col.name})
                        return CollectorResult(source=col.name, status="failed", errors=[f"crash: {type(exc).__name__}: {exc}"])

            results = await asyncio.gather(*(run_one(c) for c in collectors))
        for res in results:
            stats.sources[res.source] = {"status": res.status, "records": len(res.records), "posts": len(res.posts),
                                         "unchanged": res.skipped_unchanged, "scanned": res.scanned, "errors": res.errors[:3]}
            if res.errors:
                stats.errors.extend(f"{res.source}: {e}" for e in res.errors[:2])
            if res.status not in {"disabled", "skipped"} and not dry_run:
                repo.update_health(res.source, res.status in {"ok", "partial"}, len(res.records) + len(res.posts), res.status,
                                   "; ".join(res.errors[:2]) if res.errors else None)
        return results

    # Ingestion ---------------------------------------------------------------------------------

    def ingest(self, repo: Repository, raw: RawOpportunity, stats: CycleStats) -> Opportunity:
        loc = resolve_location(raw.location_text, raw.country_hint)
        family = self.title_rules.classify(raw.title)
        ncompany = normalize_company(raw.company)
        key = make_key(raw.company, raw.title, loc.primary_country)
        content_hash = sha256(raw.title, raw.location_text or "", raw.description_text)
        record = repo.source_record(raw.source, raw.source_id)
        now = utcnow()
        if record is not None:
            opp = record.opportunity
            record.last_seen_at = now
            record.updated_marker = raw.updated_marker
            if record.content_hash != content_hash:
                record.content_hash = content_hash
                if len(raw.description_text) >= len(opp.description_text or "") or opp.description_text == "":
                    opp.description_text = raw.description_text
                    opp.content_hash = sha256(opp.title, opp.location_text or "", opp.description_text)
                stats.updated += 1
            opp.last_verified_at = now
        else:
            match_id = find_match(key, raw.official_job_id, repo.dedupe_candidates(ncompany))
            if match_id is None and repo.opportunity_by_key(key.canonical):
                match_id = repo.opportunity_by_key(key.canonical).id
            if match_id is not None:
                opp = repo.opportunity(match_id)
                stats.merged_duplicates += 1
                # Prefer the richer description and official ATS links over aggregator links.
                if len(raw.description_text) > len(opp.description_text or ""):
                    opp.description_text = raw.description_text
                    opp.content_hash = sha256(opp.title, opp.location_text or "", opp.description_text)
                if raw.source.split(":")[0] in {"greenhouse", "lever", "ashby", "workday", "career_page"}:
                    opp.official_url = raw.url
                    opp.apply_url = raw.apply_url or opp.apply_url
                opp.apply_url = opp.apply_url or raw.apply_url
                opp.official_job_id = opp.official_job_id or raw.official_job_id
                if opp.country_code is None and loc.primary_country:
                    opp.country_code, opp.countries = self._display_country(loc.countries), loc.countries
                opp.last_verified_at = now
            else:
                watch = self.watchlist.get(ncompany)
                company = repo.upsert_company(raw.company, ncompany, ats=(watch or {}).get("ats"), sector=(watch or {}).get("sector"),
                                              priority=(watch or {}).get("priority"), watchlist=bool(watch))
                opp = repo.add(Opportunity(
                    canonical_key=key.canonical, company_id=company.id, company_name=raw.company, normalized_company=ncompany,
                    title=raw.title, normalized_title=normalize_title(raw.title), official_job_id=raw.official_job_id,
                    job_family=family.id, family_tier=family.tier, location_text=raw.location_text,
                    country_code=self._display_country(loc.countries), countries=loc.countries,
                    city=loc.city_in(self._display_country(loc.countries)),
                    is_remote=bool(loc.remote or raw.remote_hint), description_text=raw.description_text,
                    content_hash=content_hash, official_url=raw.url, apply_url=raw.apply_url,
                    source_published_at=raw.posted_at, first_discovered_at=now, last_verified_at=now,
                ))
                stats.new_opportunities += 1
            repo.add(SourceRecord(opportunity_id=opp.id, source=raw.source, source_id=raw.source_id, url=raw.url,
                                  apply_url=raw.apply_url, title=raw.title, location_text=raw.location_text,
                                  content_hash=content_hash, updated_marker=raw.updated_marker, posted_at=raw.posted_at))
        if raw.linkedin_post:
            opp.linkedin_post_url = opp.linkedin_post_url or raw.linkedin_post.post_url
            self.save_post(repo, raw.linkedin_post, opp.id)
        contacts = list(raw.contacts) + contacts_from_vacancy(raw.description_text, raw.company, raw.url)
        for c in contacts:
            if repo.add_contact(opp, name=c.name, role=c.role, company=c.company, profile_url=c.profile_url,
                                source_post_url=c.source_post_url, relationship_type=c.relationship.value,
                                confidence=c.confidence, evidence=c.evidence):
                opp.evaluated_hash = None   # networking changed: re-evaluate
        return opp

    def save_post(self, repo: Repository, post: LinkedInPostInfo, opp_id: int | None) -> LinkedInPost:
        row = repo.linkedin_post(post.post_url)
        if row is None:
            row = repo.add(LinkedInPost(post_url=post.post_url, author_name=post.author_name, author_profile_url=post.author_profile_url,
                                        author_headline=post.author_headline, text=post.text[:2000], published_at=post.published_at,
                                        classification=post.classification.value, classification_confidence=post.classification_confidence,
                                        discovery_method=post.discovery_method, opportunity_id=opp_id))
        elif opp_id and not row.opportunity_id:
            row.opportunity_id = opp_id
        return row

    def ingest_post(self, repo: Repository, post: LinkedInPostInfo, stats: CycleStats, company: str | None = None,
                    location: str | None = None, title: str | None = None,
                    analyzer: Analyzer | None = None) -> tuple[LinkedInPost, Opportunity | None]:
        if analyzer is not None and repo.linkedin_post(post.post_url) is None:
            refine_post(post, analyzer)
        raw = opportunity_from_post(post, company=company, location=location, title=title)
        stats.posts += 1
        if raw is None:
            return self.save_post(repo, post, None), None
        opp = self.ingest(repo, raw, stats)
        return repo.linkedin_post(post.post_url), opp

    # Evaluation ----------------------------------------------------------------------------------

    def _elig_ctx(self, repo: Repository) -> elig.EligibilityContext:
        return elig.EligibilityContext(profile=self.profile, excluded_countries=self.excluded, safety_excluded=self.safety_excluded,
                                       override_allow=self.override_allow, advisories=repo.advisories(),
                                       exclude_whole_country_advised_against=self.exclude_whole_country)

    def evaluate(self, repo: Repository, opp: Opportunity, analyzer: Analyzer | None, stats: CycleStats,
                 ctx: elig.EligibilityContext | None = None, prefs: list | None = None) -> None:
        rules = self.settings.scoring
        ctx = ctx or self._elig_ctx(repo)
        facts = extract_facts(opp.title, opp.description_text or "", rules["cold_calling"]["high_volume_phrases"])
        loc = resolve_location(opp.location_text, None)
        if opp.countries:
            loc = LocationResolution(countries=list(opp.countries), cities=loc.cities, remote=loc.remote or opp.is_remote,
                                     evidence=loc.evidence or [f"location '{opp.location_text}'"])
        report = elig.evaluate(facts, loc, ctx, opp.official_url)
        primary = next((c for c in loc.countries if c not in self.excluded), loc.primary_country)
        family = self.title_rules.classify(opp.title)
        watch = self.watchlist.get(opp.normalized_company) or {}
        learned = adjustments_for(prefs if prefs is not None else repo.preference_adjustments(), family.id,
                                  opp.normalized_company, primary,
                                  bool(facts.cold_calling_mentioned or facts.cold_calls_per_day))
        sctx = ScoringContext(profile=self.profile, rules=rules, company_priority=watch.get("priority"),
                              company_sector=watch.get("sector"), priority_countries=self.priority_countries,
                              priority_country_bonus=self.priority_bonus, country_penalties=self.country_penalties,
                              learned=learned)
        fit = score_fit(opp.title, opp.description_text or "", facts, family, sctx, report, primary)

        llm_cfg = self.settings.llm
        if analyzer and analyzer.enabled and report.decision != Decision.REJECTED and fit.score >= llm_cfg.get("min_deterministic_fit_for_llm", 60):
            outcome = analyzer.analyze_job(opp.id, opp.title, opp.company_name, opp.location_text, opp.description_text or "")
            if outcome.status in {"ok", "cached"}:
                stats.llm_analyses += outcome.status == "ok"
                stats.llm_cost_usd += outcome.cost_usd
                notes = enrich_facts(facts, outcome, self.profile.language_names)
                if notes:
                    report = elig.evaluate(facts, loc, ctx, opp.official_url)
                    fit = score_fit(opp.title, opp.description_text or "", facts, family, sctx, report, primary)
                fit = blend_fit(fit, outcome, rules["weights"], llm_cfg.get("blend", {"deterministic_weight": 0.6, "llm_weight": 0.4}))
            elif outcome.injection_flags:
                fit = fit.model_copy(update={"injection_flags": outcome.injection_flags})
        else:
            flags = detect_injection(opp.description_text or "")
            if flags:
                fit = fit.model_copy(update={"injection_flags": flags})

        contacts = [{"name": c.name, "relationship": c.relationship_type, "confidence": c.confidence, "evidence": c.evidence,
                     "profile_url": c.profile_url or c.source_post_url} for c in repo.contacts(opp.id)]
        networking = score_networking(contacts, rules)
        priority = score_priority(fit, report, networking, rules, watch.get("priority"))

        repo.add(EligibilityEvaluation(opportunity_id=opp.id, decision=report.decision.value, sponsorship=report.sponsorship.value,
                                       criteria=[c.model_dump(mode="json") for c in report.criteria], content_hash=opp.content_hash,
                                       engine_version=report.engine_version))
        repo.add(FitEvaluation(opportunity_id=opp.id, score=fit.score, method=fit.method, report=fit.model_dump(mode="json"),
                               networking=networking.model_dump(mode="json"), priority=priority.model_dump(mode="json"),
                               content_hash=opp.content_hash))
        opp.job_family, opp.family_tier = family.id, family.tier
        opp.eligibility_status = report.decision.value
        opp.fit_score = fit.score
        opp.networking_score = networking.score
        opp.priority_score = priority.priority_score
        opp.priority_tier = priority.tier
        opp.notify = priority.notify
        opp.evaluated_hash = opp.content_hash
        opp.evaluated_at = utcnow()
        stats.evaluated += 1

    def analyzer(self, repo: Repository) -> Analyzer | None:
        if isinstance(self.provider, NullProvider):
            return None
        return Analyzer(self.provider, repo, self.profile, self.settings.llm, self.settings.timezone)

    def evaluate_pending(self, repo: Repository, stats: CycleStats, force: bool = False) -> None:
        analyzer = self.analyzer(repo)
        ctx = self._elig_ctx(repo)
        prefs = repo.preference_adjustments()
        pending = repo.opportunities_needing_evaluation(force=force)
        # Deterministic pass first for all; the LLM budget then goes to the best-ranked ones.
        pending.sort(key=lambda o: -(o.family_tier or 0))
        for opp in pending:
            try:
                self.evaluate(repo, opp, analyzer, stats, ctx, prefs)
            except Exception as exc:  # noqa: BLE001 - one bad record must not stop the cycle
                log.exception("evaluation failed", extra={"opportunity_id": opp.id})
                stats.errors.append(f"evaluate {opp.id}: {type(exc).__name__}")

    # Cycle -----------------------------------------------------------------------------------------

    async def run_cycle(self, trigger: str = "manual", dry_run: bool = False, only: set[str] | None = None,
                        should_stop=lambda: False) -> CycleStats:
        stats = CycleStats()
        with self.session_factory() as session:
            repo = Repository(session)
            run = repo.start_run(trigger, dry_run)
            session.commit()
            run_id = run.id
            try:
                collectors = self.build_collectors(only)
                results = await self.collect(repo, collectors, stats, dry_run, should_stop)
                for res in results:
                    for raw in res.records:
                        try:
                            self.ingest(repo, raw, stats)
                        except Exception as exc:  # noqa: BLE001
                            log.exception("ingest failed", extra={"source": raw.source, "source_id": raw.source_id})
                            stats.errors.append(f"ingest {raw.source}/{raw.source_id}: {type(exc).__name__}")
                    for post in res.posts:
                        self.ingest_post(repo, post, stats, analyzer=self.analyzer(repo))
                if not should_stop():
                    self.evaluate_pending(repo, stats)
                failed = [s for s, v in stats.sources.items() if v["status"] == "failed"]
                run.status = "partial" if failed else "ok"
            except Exception as exc:  # noqa: BLE001
                log.exception("cycle failed")
                stats.errors.append(f"cycle: {type(exc).__name__}: {exc}")
                run.status = "failed"
            run.finished_at = utcnow()
            run.stats = {k: v for k, v in stats.as_dict().items() if k != "errors"}
            run.errors = stats.errors[:50]
            if dry_run:
                # Nothing collected or evaluated is persisted; only the run log is kept.
                status, run_stats, run_errors = run.status, run.stats, run.errors
                session.rollback()
                run = session.get(CollectionRun, run_id)
                run.status, run.finished_at, run.stats, run.errors = status, utcnow(), run_stats, run_errors
            session.commit()
        return stats


def since_hours(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)
