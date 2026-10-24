"""Orchestration: collect -> normalize/dedupe -> eligibility -> fit -> networking -> LLM -> priority."""

from __future__ import annotations

import asyncio
import logging
import re
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
from summer_hunter.database.models import (
    CollectionRun,
    EligibilityEvaluation,
    FitEvaluation,
    LinkedInPost,
    Opportunity,
    SourceRecord,
    utcnow,
)
from summer_hunter.database.repository import Repository, as_utc
from summer_hunter.engine import eligibility as elig
from summer_hunter.crm.deadlines import explicit_deadline
from summer_hunter.engine import compensation, modes, networking_reco, recruitability, semantic, signals, strategist
from summer_hunter.engine import priority as gpriority
from summer_hunter.engine.fx import FxTable
from summer_hunter.engine.fx import load as fx_load
from summer_hunter.engine.fx import refresh as fx_refresh
from summer_hunter.engine.fx import stale as fx_stale
from summer_hunter.engine.ai_analysis import Analyzer, blend_fit, enrich_facts
from summer_hunter.engine.deduplication import find_match, make_key
from summer_hunter.engine.discovery import plan as discovery_plan
from summer_hunter.engine.extraction import extract_facts
from summer_hunter.engine.feedback import adjustments_for
from summer_hunter.engine.geo import LocationResolution, resolve_location
from summer_hunter.engine.networking import contacts_from_vacancy
from summer_hunter.engine.normalization import INDUSTRY_ROBOTICS, TitleRules, normalize_company, normalize_title
from summer_hunter.engine.scoring import (
    ScoringContext,
    industrial_priority,
    rank_with,
    score_fit,
    score_networking,
    score_priority,
)
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
    closed: int = 0
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
        self._fx: FxTable | None = None
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
        rotation = int(datetime.now(timezone.utc).timestamp() // 3600)   # new slice of each track's phrases every hour
        out.append(LinkedInSearchCollector(provider, disc.get("post_queries", []), disc.get("job_queries", []),
                                           int(disc.get("max_queries_per_run", 15)), int(disc.get("results_per_query", 10)),
                                           planned=discovery_plan(disc, rotation, self.current_mode())))
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
        resting = {h.source: h for h in repo.health() if in_backoff(h)}
        async with self._http() as http:
            ctx = CollectContext(http=http, title_rules=self.title_rules, is_unchanged=is_unchanged, dry_run=dry_run)

            async def run_one(col: Collector) -> CollectorResult:
                async with sem:
                    if should_stop():
                        return CollectorResult(source=col.name, status="skipped", detail="stop requested")
                    if col.name in resting:
                        h = resting[col.name]
                        return CollectorResult(source=col.name, status="skipped",
                                               detail=f"backoff after {h.consecutive_failures} consecutive failures")
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

    async def verify_shortlist(self, repo: Repository, stats: CycleStats, limit: int = 120) -> None:
        """Check that offers the user can see are still open on the real career site."""
        from summer_hunter.collectors.liveness import is_gone

        # Evaluated offers the user can see in the app (target families, not rejected).
        targets = [o for o in repo.all_active()
                   if (o.notify or (o.family_tier or 0) > 0) and o.eligibility_status not in (None, "REJECTED")]
        targets.sort(key=lambda o: -(o.priority_score or 0))
        checks = [(o, r) for o in targets[:limit] for r in o.sources
                  if r.closed_at is None and r.source.split(":")[0] in {"lever", "workday", "greenhouse"}]
        if not checks:
            return
        sem = asyncio.Semaphore(4)
        async with self._http() as http:
            async def one(rec):
                async with sem:
                    return await is_gone(http, rec.source, rec.source_id, rec.url)
            verdicts = await asyncio.gather(*(one(r) for _, r in checks))
        now = utcnow()
        for (opp, rec), gone in zip(checks, verdicts, strict=True):
            if gone is True:
                rec.closed_at = now
                ats = [s for s in opp.sources if s.source.split(":")[0] in {"greenhouse", "lever", "ashby", "workday"}]
                if ats and all(s.closed_at is not None for s in ats) and opp.status != "closed":
                    opp.status, opp.notify = "closed", False
                    stats.closed += 1

    def close_missing(self, repo: Repository, res: CollectorResult, stats: CycleStats) -> None:
        """Close source records absent from a complete listing; close an opportunity once all of its
        verifiable (ATS) sources are closed. A record that reappears is reopened."""
        if res.seen_ids is None or not res.listing_complete or res.status not in {"ok", "partial"}:
            return
        seen = set(res.seen_ids)
        records = repo.records_for_source(res.source)
        if not seen and records:
            return  # an empty listing where offers existed is more likely a glitch than a mass closure
        now = utcnow()
        for rec in records:
            opp = rec.opportunity
            if rec.source_id in seen:
                if rec.closed_at is not None:
                    rec.closed_at = None
                    if opp.status == "closed":
                        opp.status = "active"
                        opp.evaluated_hash = None
                continue
            if rec.closed_at is None:
                rec.closed_at = now
            ats = [s for s in opp.sources if s.source.split(":")[0] in {"greenhouse", "lever", "ashby", "workday"}]
            if ats and all(s.closed_at is not None for s in ats) and opp.status != "closed":
                opp.status = "closed"
                opp.notify = False
                stats.closed += 1

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
                                       exclude_whole_country_advised_against=self.exclude_whole_country,
                                       skills_lexicon=self.settings.scoring.get("recruitability", {}).get("skills"))

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
        sem = semantic.classify(opp.title, opp.description_text or "", rules, family.id)
        if sem.applied:
            family = self.title_rules.by_id(sem.family, "responsibilities: " + ", ".join(sem.evidence[:3]))
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
        recruit = recruitability.score(opp.title, opp.description_text or "", family, self.profile, rules)
        industrial = industrial_priority(fit.score, recruit.score, networking.score, report.decision, rules)
        if industrial is not None and family.track == INDUSTRY_ROBOTICS and rules.get("industrial_priority", {}).get("enabled"):
            priority = rank_with(priority, industrial, rules, "industrial priority (40% recruitability, 35% fit, 25% networking)")
        v3 = self._v3(repo, opp, facts, report, family, fit, recruit, networking, contacts, watch, primary)
        if v3["priority"].filtered:
            priority = priority.model_copy(update={"notify": False, "reason": f"{priority.reason}; {v3['priority'].filtered}"})

        repo.add(EligibilityEvaluation(opportunity_id=opp.id, decision=report.decision.value, sponsorship=report.sponsorship.value,
                                       criteria=[c.model_dump(mode="json") for c in report.criteria], content_hash=opp.content_hash,
                                       engine_version=report.engine_version))
        repo.add(FitEvaluation(opportunity_id=opp.id, score=fit.score, method=fit.method, report=fit.model_dump(mode="json"),
                               networking=networking.model_dump(mode="json"), priority=priority.model_dump(mode="json"),
                               recruitability=recruit.model_dump(mode="json"), compensation=v3["comp"].model_dump(mode="json"),
                               strategy=v3["strategy"].model_dump(mode="json") | {"global": v3["priority"].__dict__,
                                                                                   "semantic": sem.__dict__,
                                                                                   "networking": v3["networking"]},
                               content_hash=opp.content_hash))
        opp.job_family, opp.family_tier = family.id, family.tier
        opp.eligibility_status = report.decision.value
        opp.fit_score = fit.score
        opp.networking_score = networking.score
        opp.priority_score = priority.priority_score
        opp.priority_tier = priority.tier
        opp.notify = priority.notify
        opp.recruitability_score, opp.industrial_priority = recruit.score, industrial
        opp.career_track, opp.career_category = family.track, family.category
        self._store_v3(opp, v3, sem)
        opp.evaluated_hash = opp.content_hash
        opp.evaluated_at = utcnow()
        stats.evaluated += 1

    # V3: compensation, deadlines, global priority, strategist ----------------------------------------

    def current_mode(self) -> str:
        with self.session_factory() as session:
            return modes.get(Repository(session), self.settings.scoring)

    def fx_table(self, repo: Repository) -> FxTable:
        if self._fx is None:
            self._fx = fx_load(repo.s, self.settings.compensation)
        return self._fx

    def refresh_fx(self) -> dict[str, Any]:
        """Fetch exchange rates when the stored ones are old (nightly run, never in tests)."""
        with self.session_factory() as session:
            if not fx_stale(session, self.settings.compensation):
                return {"stored": 0, "skipped": "fresh"}
            out = fx_refresh(session, self.settings.compensation)
            session.commit()
        self._fx = None
        return out

    def _v3(self, repo, opp, facts, report, family, fit, recruit, networking, contacts, watch, primary) -> dict[str, Any]:  # noqa: ANN001
        rules, comp_cfg = self.settings.scoring, self.settings.compensation
        desc = opp.description_text or ""
        names = [s.source for s in opp.sources] or ["unknown"]
        source = next((n for n in names if n.split(":")[0] in {"greenhouse", "lever", "ashby", "workday", "career_page"}), names[0])
        weeks = facts.duration.max_weeks if facts.duration else None
        comp = compensation.assess(desc, primary, opp.city, source, weeks, comp_cfg, self.fx_table(repo))
        deadline = explicit_deadline(desc)
        rolling = bool(re.search(r"\brolling (basis|admissions?|applications?|recruitment)\b|\bau fil de l.eau\b", desc, re.I))
        today = datetime.now(timezone.utc).date()
        crit = [c.model_dump(mode="json") for c in report.criteria]
        dates_pass = any(c["criterion"] == "dates" and c["status"] == "PASS" for c in crit)
        lang_unknown = any(c["criterion"] == "language" and c["status"] == "UNKNOWN" for c in crit)
        missing_mandatory = sum("(obligatoire)" in m for m in recruit.missing_requirements)
        docs_ready = (self.settings.data_dir / "cv" / str(opp.id)).exists()
        ease_score, minutes = gpriority.ease(desc, missing_mandatory, lang_unknown, docs_ready)
        mode = modes.get(repo, rules)
        gp = gpriority.compute(fit.score, recruit.score, networking.score, comp, report.decision == Decision.REJECTED,
                               gpriority.quality(names, desc, watch.get("priority"), dates_pass, comp.status == "PAID_CONFIRMED"),
                               gpriority.urgency(deadline, rolling, as_utc(opp.source_published_at), today),
                               ease_score, minutes, mode, rules, comp_cfg)
        best = min((c for c in contacts if float(c.get("confidence", 0)) >= 0.6),
                   key=lambda c: networking_reco.RANK.get(c["relationship"], 9), default=None)
        strat = strategist.decide(gp, report.decision.value, crit, comp, fit.score, recruit.score, networking.score,
                                  recruit.model_dump(), fit.model_dump(), best, watch.get("priority"),
                                  bool(signals.TALENT_POOL.search(opp.title.lower())),
                                  (deadline - today).days if deadline else None, rules)
        reco = networking_reco.recommend(opp.company_name, opp.title, contacts, recruit.model_dump(), family.track)
        return {"comp": comp, "priority": gp, "strategy": strat, "deadline": deadline, "rolling": rolling, "networking": reco}

    @staticmethod
    def _store_v3(opp: Opportunity, v3: dict[str, Any], sem) -> None:  # noqa: ANN001
        comp, gp, strat = v3["comp"], v3["priority"], v3["strategy"]
        opp.semantic_family = sem.family
        opp.compensation_status, opp.compensation_confidence, opp.compensation_score = comp.status, comp.confidence, comp.score
        opp.salary_min = comp.pay.amount_min if comp.pay else None
        opp.salary_max = comp.pay.amount_max if comp.pay else None
        opp.salary_currency = comp.pay.currency if comp.pay else None
        opp.salary_period = comp.pay.period if comp.pay else None
        opp.salary_basis = comp.pay.basis if comp.pay else None
        opp.monthly_min_eur, opp.monthly_max_eur = comp.monthly_min_eur, comp.monthly_max_eur
        opp.living_cost_eur, opp.monthly_balance_eur = comp.living_cost_eur, comp.monthly_balance_eur
        opp.global_priority, opp.global_provisional = gp.score, gp.provisional
        opp.next_action, opp.strategy_category = strat.action, strat.category
        opp.application_deadline = v3["deadline"]
        opp.deadline_type = "fixed" if v3["deadline"] else "rolling" if v3["rolling"] else "unknown"

    def analyzer(self, repo: Repository) -> Analyzer | None:
        if isinstance(self.provider, NullProvider):
            return None
        return Analyzer(self.provider, repo, self.profile, self.settings.llm, self.settings.timezone)

    def evaluate_pending(self, repo: Repository, stats: CycleStats, force: bool = False) -> None:
        self._fx = None                      # pick up rates refreshed since the last cycle
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
                    self.close_missing(repo, res, stats)
                if not should_stop() and not only:
                    await self.verify_shortlist(repo, stats)
                if not should_stop():
                    self.evaluate_pending(repo, stats)
                    if not dry_run:
                        signals.sync(repo)
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


BACKOFF_AFTER = 3          # consecutive failures before a source is rested
BACKOFF_MAX_HOURS = 48


def in_backoff(health, now: datetime | None = None) -> bool:  # noqa: ANN001
    """A failing source rests 1 h, 2 h, 4 h... (capped at 48 h) after its last failure, then is retried."""
    n = health.consecutive_failures or 0
    if n < BACKOFF_AFTER or health.last_failure_at is None:
        return False
    hours = min(BACKOFF_MAX_HOURS, 2 ** (n - BACKOFF_AFTER))
    return (now or datetime.now(timezone.utc)) - as_utc(health.last_failure_at) < timedelta(hours=hours)


def since_hours(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)
