"""Deterministic eligibility engine.

Two kinds of criteria:
- positive-evidence criteria (internship status, dates, duration, country, work authorization)
  are PASS only when the listing states something that satisfies them; silence is UNKNOWN.
- restriction-scan criteria (language, academic, graduation, citizenship, qualifications)
  are FAIL when an explicit restriction conflicts with the profile, UNKNOWN when a restriction
  depends on missing profile data, and PASS (confidence <= 0.7) when a full description
  was scanned and no restriction was found. A short or missing description is UNKNOWN.

Decision: any confident FAIL -> REJECTED; any material UNKNOWN (or low-confidence FAIL)
-> NEEDS_VERIFICATION; otherwise ELIGIBLE. UNKNOWN is never treated as PASS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from summer_hunter.engine.extraction import JobFacts
from summer_hunter.engine.geo import EU_EEA_CH, SOUTHERN_HEMISPHERE, LocationResolution, country_name
from summer_hunter.models.candidate import CandidateProfile
from summer_hunter.models.eligibility import (
    CriterionResult,
    CriterionStatus as S,
    Decision,
    EligibilityReport,
    SponsorshipStatus,
)

ENGINE_VERSION = "1"
FAIL_CONFIDENCE_FLOOR = 0.7          # a FAIL below this confidence only triggers verification
MIN_SCANNABLE_DESCRIPTION = 300      # characters needed before "no restriction found" counts


@dataclass
class EligibilityContext:
    profile: CandidateProfile
    excluded_countries: set[str]
    safety_excluded: set[str]
    override_allow: set[str]
    advisories: dict[str, dict]           # ISO2 -> {"level", "source_url", "retrieved_at", "summary"}
    exclude_whole_country_advised_against: bool = True
    today: date | None = None


def _r(name: str, status: S, explanation: str, evidence: list[str] | None = None, confidence: float = 0.8,
       source_url: str | None = None, material: bool = True) -> CriterionResult:
    return CriterionResult(criterion=name, status=status, explanation=explanation, evidence=[e for e in (evidence or []) if e][:4],
                           confidence=confidence, source_url=source_url, material=material,
                           verified_at=datetime.now(timezone.utc))


def _scannable(facts: JobFacts) -> bool:
    return facts.description_length >= MIN_SCANNABLE_DESCRIPTION


# --- individual criteria -----------------------------------------------------------


def check_internship_status(facts: JobFacts, url: str | None) -> CriterionResult:
    emp = facts.employment
    if emp == "internship":
        return _r("internship_status", S.PASS, "The title identifies the role as an internship.", [facts.employment_evidence], 0.9, url)
    if emp in {"apprenticeship", "working_student", "volunteer", "permanent", "graduate_job"}:
        labels = {"apprenticeship": "an apprenticeship", "working_student": "a working-student job",
                  "volunteer": "a volunteer position", "permanent": "permanent employment",
                  "graduate_job": "a graduate job without an internship component"}
        return _r("internship_status", S.FAIL, f"The listing is {labels[emp]}, which is excluded.", [facts.employment_evidence], 0.85, url)
    if emp == "mixed":
        return _r("internship_status", S.UNKNOWN, "The listing mixes internship and another contract type; confirm the internship option.",
                  [facts.employment_evidence], 0.5, url)
    if emp == "internship_in_text":
        return _r("internship_status", S.UNKNOWN, "Internship mentioned in the text but not in the title.", [facts.employment_evidence], 0.5, url)
    return _r("internship_status", S.UNKNOWN, "Contract type not stated.", [], 0.4, url)


def check_dates(facts: JobFacts, ctx: EligibilityContext, country: str | None, url: str | None) -> CriterionResult:
    p = ctx.profile
    lo, hi = p.available_from, p.available_to
    explicit = [x for x in facts.periods if x.kind == "explicit_range"]
    if explicit:
        fitting = [x for x in explicit if x.start >= lo and x.end <= hi]
        if fitting:
            x = fitting[0]
            return _r("dates", S.PASS, f"Stated period {x.start:%d %b %Y} to {x.end:%d %b %Y} fits inside the availability window.",
                      [x.evidence], 0.9, url)
        x = explicit[0]
        if facts.flexible_dates:
            return _r("dates", S.UNKNOWN, f"Stated period {x.start:%b %Y}-{x.end:%b %Y} is outside the window, but the listing says dates are flexible.",
                      [x.evidence, facts.flexible_evidence], 0.6, url)
        return _r("dates", S.FAIL, f"Stated period {x.start:%d %b %Y} to {x.end:%d %b %Y} does not fit {lo:%d %b}-{hi:%d %b %Y}.",
                  [x.evidence], 0.9, url)
    starts = [x for x in facts.periods if x.kind == "start_only"]
    if starts:
        x = starts[0]
        if x.start < lo or x.start > hi:
            if facts.flexible_dates:
                return _r("dates", S.UNKNOWN, f"Start {x.start:%b %Y} is outside the window but dates are described as flexible.",
                          [x.evidence, facts.flexible_evidence], 0.6, url)
            return _r("dates", S.FAIL, f"Stated start {x.start:%b %Y} is outside the availability window.", [x.evidence], 0.85, url)
        if facts.duration:
            end = date.fromordinal(x.start.toordinal() + int(facts.duration.min_weeks * 7) - 1)
            if end <= hi:
                return _r("dates", S.PASS, f"Start {x.start:%b %Y} plus {facts.duration.min_weeks:g} weeks ends by {end:%d %b %Y}.",
                          [x.evidence, facts.duration.evidence], 0.8, url)
            return _r("dates", S.FAIL, f"Start {x.start:%b %Y} plus {facts.duration.min_weeks:g} weeks ends after {hi:%d %b %Y}.",
                      [x.evidence, facts.duration.evidence], 0.8, url)
        return _r("dates", S.UNKNOWN, f"Start {x.start:%b %Y} is inside the window; end date not stated.", [x.evidence], 0.6, url)

    seasons = [x for x in facts.periods if x.kind == "season"]
    target_year = lo.year
    if seasons:
        in_year = [x for x in seasons if x.year == target_year]
        if not in_year:
            years = sorted({x.year for x in seasons})
            return _r("dates", S.FAIL, f"Listing targets the {', '.join(map(str, years))} cycle, not {target_year}.",
                      [seasons[0].evidence], 0.85, url)
        x = in_year[0]
        if x.season == "summer" and country in SOUTHERN_HEMISPHERE:
            return _r("dates", S.UNKNOWN, f"'Summer {x.year}' in {country_name(country)} usually means December-February; confirm the months.",
                      [x.evidence], 0.5, url)
        if x.season in {"winter", "fall"}:
            return _r("dates", S.FAIL, f"A {x.season} {x.year} internship falls outside April-August.", [x.evidence], 0.75, url)
        return _r("dates", S.UNKNOWN, f"'{x.season.title()} {x.year}' is compatible in principle; exact dates are not stated.",
                  [x.evidence], 0.6, url)
    if facts.years:
        if target_year not in facts.years and all(y < target_year for y in facts.years):
            return _r("dates", S.FAIL, f"Only past cycles are mentioned ({', '.join(map(str, sorted(facts.years)))}).", [], 0.75, url)
        return _r("dates", S.UNKNOWN, "Internship dates are not stated.", [], 0.4, url)
    return _r("dates", S.UNKNOWN, "Internship dates are not stated.", [], 0.4, url)


def check_duration(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    p = ctx.profile
    window = p.window_weeks
    d = facts.duration
    if d is None:
        explicit = next((x for x in facts.periods if x.kind == "explicit_range"), None)
        if explicit:
            weeks = ((explicit.end - explicit.start).days + 1) / 7
            if weeks > window + 0.5 and facts.flexible_dates:
                return _r("duration", S.UNKNOWN, f"Stated dates span about {weeks:.0f} weeks, but dates are described as flexible.",
                          [explicit.evidence, facts.flexible_evidence], 0.5, url)
            return _r("duration", S.PASS if weeks <= window + 0.5 else S.FAIL, f"Derived from stated dates: about {weeks:.0f} weeks.",
                      [explicit.evidence], 0.85, url)
        return _r("duration", S.UNKNOWN, "Duration not stated.", [], 0.4, url)
    if d.min_weeks > window + 0.5:
        return _r("duration", S.FAIL, f"Minimum duration {d.min_weeks:g} weeks exceeds the {window:.0f}-week availability window.",
                  [d.evidence], 0.85, url)
    if d.max_weeks > window + 0.5:
        return _r("duration", S.UNKNOWN, f"Duration range {d.min_weeks:g}-{d.max_weeks:g} weeks: only the shorter option fits.",
                  [d.evidence], 0.6, url)
    lo, hi = p.acceptable_weeks
    note = "" if lo <= d.min_weeks and d.max_weeks <= hi else " Outside the preferred 4-20 week range (ranking penalty only)."
    return _r("duration", S.PASS, f"Duration {d.min_weeks:g}-{d.max_weeks:g} weeks fits the window.{note}", [d.evidence], 0.8, url)


def check_country(loc: LocationResolution, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    if not loc.countries:
        if loc.remote:
            return _r("country", S.UNKNOWN, "Remote role with no stated country; the work location must be confirmed.", loc.evidence, 0.5, url)
        return _r("country", S.UNKNOWN, "Location could not be resolved to a country.", loc.evidence, 0.4, url)
    allowed = [c for c in loc.countries if c not in ctx.excluded_countries]
    excluded = [c for c in loc.countries if c in ctx.excluded_countries]
    if not allowed:
        return _r("country", S.FAIL, f"Located in an excluded country: {', '.join(country_name(c) for c in excluded)}.",
                  loc.evidence, 0.95, url)
    note = f" Excluded alternative(s) ignored: {', '.join(country_name(c) for c in excluded)}." if excluded else ""
    return _r("country", S.PASS, f"Located in {', '.join(country_name(c) for c in allowed)}.{note}", loc.evidence, 0.9, url)


def check_safety(loc: LocationResolution, ctx: EligibilityContext) -> CriterionResult:
    countries = [c for c in loc.countries if c not in ctx.excluded_countries]
    if not countries:
        return _r("safety", S.NOT_APPLICABLE, "No eligible country to assess.", [], 1.0, material=False)
    results = []
    for code in countries:
        if code in ctx.override_allow:
            results.append((S.PASS, f"{country_name(code)}: manually allowed by the user.", None))
            continue
        if code in ctx.safety_excluded:
            results.append((S.FAIL, f"{country_name(code)} is on the user's safety exclusion list.", None))
            continue
        adv = ctx.advisories.get(code)
        if not adv:
            results.append((S.UNKNOWN, f"No stored official advisory for {country_name(code)}.", None))
            continue
        level = adv.get("level")
        src = f"{adv.get('source_url')} (retrieved {adv.get('retrieved_at')})"
        if level == "WHOLE_COUNTRY_ADVISED_AGAINST" and ctx.exclude_whole_country_advised_against:
            results.append((S.FAIL, f"{country_name(code)}: whole country advised against per France Diplomatie, {src}.", adv.get("source_url")))
        elif level == "NO_FORMAL_WARNING_FOUND":
            results.append((S.PASS, f"{country_name(code)}: no 'formellement déconseillé' wording on France Diplomatie, {src}.", adv.get("source_url")))
        else:
            results.append((S.UNKNOWN, f"{country_name(code)}: advisory level {level}; check the exact city/zone, {src}.", adv.get("source_url")))
    if all(s == S.FAIL for s, _, _ in results):
        return _r("safety", S.FAIL, " ".join(e for _, e, _ in results), [], 0.9, results[0][2], material=False)
    best = S.PASS if any(s == S.PASS for s, _, _ in results) else S.UNKNOWN
    return _r("safety", best, " ".join(e for _, e, _ in results), [], 0.6, next((u for _, _, u in results if u), None), material=False)


def check_language(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    known = ctx.profile.language_names
    mandatory_missing = [l for l in facts.languages if l.mandatory and l.language not in known]
    optional_missing = [l for l in facts.languages if not l.mandatory and l.language not in known]
    if mandatory_missing:
        names = ", ".join(l.language.title() for l in mandatory_missing)
        return _r("language", S.FAIL, f"Mandatory language requirement the candidate does not meet: {names}.",
                  [l.evidence for l in mandatory_missing], 0.85, url)
    if optional_missing:
        names = ", ".join(l.language.title() for l in optional_missing)
        return _r("language", S.PASS, f"{names} is listed as optional or a plus (not mandatory).",
                  [l.evidence for l in optional_missing], 0.7, url)
    if facts.languages:
        return _r("language", S.PASS, "Required languages are within the candidate's verified languages (French, English).",
                  [l.evidence for l in facts.languages], 0.85, url)
    if _scannable(facts):
        return _r("language", S.PASS, "No language requirement beyond the listing language was found.", [], 0.65, url)
    return _r("language", S.UNKNOWN, "Description too short to check language requirements.", [], 0.3, url)


def check_academic(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    p = ctx.profile
    mandatory = [d for d in facts.degrees if d.mandatory]
    if not mandatory:
        if _scannable(facts):
            return _r("academic", S.PASS, "No specific degree restriction found; the candidate is an enrolled engineering student.", [], 0.6, url)
        return _r("academic", S.UNKNOWN, "Description too short to check academic requirements.", [], 0.3, url)
    statuses = []
    for d in mandatory:
        levels = d.levels
        if "any_student" in levels:
            statuses.append((S.PASS, "Any enrolled student level accepted.", d.evidence))
            continue
        ok = []
        unknown = []
        for level in levels:
            value = {"bachelors": p.bachelors_enrolled, "masters": p.masters_enrolled, "mba": p.mba, "phd": p.phd}[level]
            (ok if value else unknown if value is None else []).append(level)
        if ok:
            statuses.append((S.PASS, f"Accepted level ({', '.join(ok)}) matches the profile.", d.evidence))
        elif unknown:
            statuses.append((S.UNKNOWN, f"Requires {' or '.join(sorted(levels))}; equivalence of the ESILV engineering degree is not confirmed in the profile.", d.evidence))
        else:
            statuses.append((S.FAIL, f"Requires {' or '.join(sorted(levels))}, which the candidate is not enrolled in.", d.evidence))
    if any(s == S.FAIL for s, _, _ in statuses):
        fail = next(x for x in statuses if x[0] == S.FAIL)
        return _r("academic", S.FAIL, fail[1], [fail[2]], 0.8, url)
    if any(s == S.UNKNOWN for s, _, _ in statuses):
        unk = next(x for x in statuses if x[0] == S.UNKNOWN)
        return _r("academic", S.UNKNOWN, unk[1], [unk[2]], 0.5, url)
    return _r("academic", S.PASS, statuses[0][1], [statuses[0][2]], 0.75, url)


def _window_status(req_start: tuple[int, int], req_end: tuple[int, int], year: int, month: int | None) -> S:
    if month:
        return S.PASS if req_start <= (year, month) <= req_end else S.FAIL
    year_start, year_end = (year, 1), (year, 12)
    if req_start <= year_start and year_end <= req_end:
        return S.PASS
    if req_end < year_start or req_start > year_end:
        return S.FAIL
    return S.UNKNOWN


def check_graduation(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    p = ctx.profile
    windows = [g for g in facts.graduation if g.kind == "window"]
    penultimate = next((g for g in facts.graduation if g.kind == "penultimate"), None)
    recent_ok = next((g for g in facts.graduation if g.kind == "recent_grad_ok"), None)
    if windows:
        statuses = [(_window_status(g.start, g.end, p.graduation_year, p.graduation_month), g) for g in windows]
        passing = [g for s, g in statuses if s == S.PASS]
        unknown = [g for s, g in statuses if s == S.UNKNOWN]
        if passing:
            return _r("graduation", S.PASS, f"Graduation {p.graduation_year} falls inside a stated window.", [passing[0].evidence], 0.85, url)
        if recent_ok:
            return _r("graduation", S.UNKNOWN, "Graduation window does not match, but recent graduates are also mentioned.",
                      [windows[0].evidence, recent_ok.evidence], 0.5, url)
        if unknown:
            g = unknown[0]
            return _r("graduation", S.UNKNOWN,
                      f"Window {g.start[1]:02d}/{g.start[0]}-{g.end[1]:02d}/{g.end[0]} only partly overlaps {p.graduation_year}; "
                      "graduation month is UNKNOWN in the profile.", [g.evidence], 0.6, url)
        g = windows[0]
        return _r("graduation", S.FAIL, f"Required graduation window {g.start[1]:02d}/{g.start[0]}-{g.end[1]:02d}/{g.end[0]} excludes {p.graduation_year}.",
                  [g.evidence], 0.85, url)
    if penultimate:
        if p.returning_to_studies is True:
            return _r("graduation", S.PASS, "Requires returning to studies; the profile confirms this.", [penultimate.evidence], 0.8, url)
        if p.returning_to_studies is False or p.current_study_year == "final_year":
            return _r("graduation", S.FAIL, "Requires returning to studies after the internship; the profile says this is the final year.",
                      [penultimate.evidence], 0.8, url)
        return _r("graduation", S.UNKNOWN, "Requires returning to studies after the internship; not confirmed in the profile.",
                  [penultimate.evidence], 0.5, url)
    if _scannable(facts):
        return _r("graduation", S.PASS, "No graduation-date restriction found.", [], 0.6, url)
    return _r("graduation", S.UNKNOWN, "Description too short to check graduation requirements.", [], 0.3, url)


def check_citizenship(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    p = ctx.profile
    if facts.clearance:
        return _r("citizenship", S.FAIL if p.citizenship and "US" not in p.citizenship else S.UNKNOWN,
                  "Security clearance or export-control restriction (typically nationality-restricted).", facts.clearance, 0.75, url)
    if facts.citizenship:
        required = facts.citizenship_countries - {"UNKNOWN"}
        if not p.citizenship:
            return _r("citizenship", S.UNKNOWN, "Citizenship/permanent residency restriction; candidate citizenship is UNKNOWN in the profile.",
                      facts.citizenship, 0.6, url)
        if "EU" in required and any(c in EU_EEA_CH for c in p.citizenship):
            return _r("citizenship", S.PASS, "EU citizenship required and present in the profile.", facts.citizenship, 0.8, url)
        if required and not (required & set(p.citizenship)):
            return _r("citizenship", S.FAIL, f"Restricted to {', '.join(sorted(required))} citizens/residents.", facts.citizenship, 0.85, url)
        if required & set(p.citizenship):
            return _r("citizenship", S.PASS, "Required citizenship present in the profile.", facts.citizenship, 0.85, url)
        return _r("citizenship", S.UNKNOWN, "Nationality restriction could not be interpreted.", facts.citizenship, 0.5, url)
    if _scannable(facts):
        return _r("citizenship", S.PASS, "No citizenship restriction found.", [], 0.6, url)
    return _r("citizenship", S.UNKNOWN, "Description too short to check nationality restrictions.", [], 0.3, url)


def sponsorship_status(facts: JobFacts) -> SponsorshipStatus:
    flags = facts.sponsorship_flags
    available = bool(flags.get("available"))
    unavailable = bool(flags.get("unavailable"))
    existing = bool(flags.get("existing_auth"))
    if available and (unavailable or existing):
        return SponsorshipStatus.UNCLEAR
    if unavailable:
        return SponsorshipStatus.UNAVAILABLE
    if existing:
        return SponsorshipStatus.EXISTING_AUTH_REQUIRED
    if available:
        return SponsorshipStatus.AVAILABLE
    if flags.get("mention"):
        return SponsorshipStatus.UNCLEAR
    return SponsorshipStatus.NOT_SPECIFIED


def check_work_authorization(facts: JobFacts, loc: LocationResolution, ctx: EligibilityContext, url: str | None
                             ) -> tuple[CriterionResult, SponsorshipStatus]:
    p = ctx.profile
    status = sponsorship_status(facts)
    countries = [c for c in loc.countries if c not in ctx.excluded_countries]
    evidence = [e for key in ("unavailable", "existing_auth", "available", "mention") for e in facts.sponsorship_flags.get(key, [])][:3]
    if not countries:
        return _r("work_authorization", S.UNKNOWN, "Work country unknown, so authorization cannot be assessed.", evidence, 0.3, url), status
    authorized = [c for c in countries if p.work_authorization.get(c) is True]
    if authorized:
        return _r("work_authorization", S.PASS, f"Profile confirms existing work authorization for {', '.join(country_name(c) for c in authorized)}.",
                  evidence, 0.9, url), status
    eu_free = [c for c in countries if c in EU_EEA_CH and any(x in EU_EEA_CH for x in p.citizenship)]
    if eu_free:
        return _r("work_authorization", S.PASS, f"EU/EEA/Swiss freedom of movement applies in {', '.join(country_name(c) for c in eu_free)} "
                  "(citizenship confirmed in the profile).", evidence, 0.8, url), status
    if status == SponsorshipStatus.AVAILABLE:
        return _r("work_authorization", S.PASS, "The listing states that visa sponsorship is available.", evidence, 0.8, url), status
    lacks_auth = all(p.work_authorization.get(c) is False for c in countries)
    if status in {SponsorshipStatus.UNAVAILABLE, SponsorshipStatus.EXISTING_AUTH_REQUIRED}:
        if lacks_auth and p.requires_sponsorship is True:
            return _r("work_authorization", S.FAIL, "Existing work authorization required and the profile confirms none for this country.",
                      evidence, 0.85, url), status
        return _r("work_authorization", S.UNKNOWN, "Existing work authorization required (no sponsorship); the candidate's status is UNKNOWN.",
                  evidence, 0.6, url), status
    return _r("work_authorization", S.UNKNOWN, "Work authorization and sponsorship are not specified; absence of a restriction is not eligibility.",
              evidence, 0.4, url), status


def check_qualifications(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    held = {q.lower() for q in ctx.profile.certifications + ctx.profile.licenses}
    mandatory = [(n, e) for n, m, e in facts.qualifications if m and n.lower() not in held]
    if mandatory:
        hard = [(n, e) for n, e in mandatory if n != "Driver's license"]
        if hard:
            return _r("qualifications", S.FAIL, f"Mandatory qualification not held: {', '.join(n for n, _ in hard)}.", [e for _, e in hard], 0.8, url)
        return _r("qualifications", S.UNKNOWN, "A driver's license is required; not recorded in the profile.", [e for _, e in mandatory], 0.5, url)
    if _scannable(facts):
        return _r("qualifications", S.PASS, "No mandatory professional license or certification found.", [], 0.65, url)
    return _r("qualifications", S.UNKNOWN, "Description too short to check mandatory qualifications.", [], 0.3, url)


def check_compensation(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    if facts.compensation == "unpaid":
        if ctx.profile.allow_unpaid:
            return _r("compensation", S.PASS, "Unpaid, explicitly allowed in the profile.", [facts.compensation_evidence], 0.8, url, material=False)
        return _r("compensation", S.FAIL, "Unpaid internship (not approved in the profile).", [facts.compensation_evidence], 0.85, url, material=False)
    if facts.compensation == "paid":
        return _r("compensation", S.PASS, "Compensation is mentioned.", [facts.compensation_evidence], 0.75, url, material=False)
    return _r("compensation", S.UNKNOWN, "Compensation not stated.", [], 0.4, url, material=False)


# --- aggregate ---------------------------------------------------------------------


def evaluate(facts: JobFacts, loc: LocationResolution, ctx: EligibilityContext, source_url: str | None = None) -> EligibilityReport:
    primary = next((c for c in loc.countries if c not in ctx.excluded_countries), loc.primary_country)
    work_auth, sponsorship = check_work_authorization(facts, loc, ctx, source_url)
    criteria = [
        check_internship_status(facts, source_url),
        check_dates(facts, ctx, primary, source_url),
        check_duration(facts, ctx, source_url),
        check_country(loc, ctx, source_url),
        check_safety(loc, ctx),
        check_language(facts, ctx, source_url),
        check_academic(facts, ctx, source_url),
        check_graduation(facts, ctx, source_url),
        check_citizenship(facts, ctx, source_url),
        work_auth,
        check_qualifications(facts, ctx, source_url),
        check_compensation(facts, ctx, source_url),
    ]
    return aggregate(criteria, sponsorship)


def aggregate(criteria: list[CriterionResult], sponsorship: SponsorshipStatus) -> EligibilityReport:
    confident_fail = [c for c in criteria if c.status == S.FAIL and c.confidence >= FAIL_CONFIDENCE_FLOOR]
    weak_fail = [c for c in criteria if c.status == S.FAIL and c.confidence < FAIL_CONFIDENCE_FLOOR]
    material_unknown = [c for c in criteria if c.status == S.UNKNOWN and c.material]
    if confident_fail:
        decision = Decision.REJECTED
    elif weak_fail or material_unknown:
        decision = Decision.NEEDS_VERIFICATION
    else:
        decision = Decision.ELIGIBLE
    return EligibilityReport(decision=decision, criteria=criteria, sponsorship=sponsorship,
                             hard_exclusion=bool(confident_fail), engine_version=ENGINE_VERSION)
