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
from summer_hunter.engine.geo import (
    EU_EEA_CH,
    SOUTHERN_HEMISPHERE,
    LocationResolution,
    country_name,
)
from summer_hunter.models.candidate import CandidateProfile
from summer_hunter.models.eligibility import (
    CriterionResult,
    Decision,
    EligibilityReport,
    SponsorshipStatus,
)
from summer_hunter.models.eligibility import (
    CriterionStatus as S,
)

ENGINE_VERSION = "2"   # v2 (23 Oct 2026): technical_requirements criterion
FAIL_CONFIDENCE_FLOOR = 0.7          # a FAIL below this confidence only triggers verification
MIN_SCANNABLE_DESCRIPTION = 300      # characters needed before "no restriction found" counts

MONTH_NAMES_FR = {
    1: "janvier",
    2: "février",
    3: "mars",
    4: "avril",
    5: "mai",
    6: "juin",
    7: "juillet",
    8: "août",
    9: "septembre",
    10: "octobre",
    11: "novembre",
    12: "décembre",
}

SEASON_NAMES_FR = {
    "summer": "été",
    "winter": "hiver",
    "fall": "automne",
    "spring": "printemps",
}


@dataclass
class EligibilityContext:
    profile: CandidateProfile
    excluded_countries: set[str]
    safety_excluded: set[str]
    override_allow: set[str]
    advisories: dict[str, dict]           # ISO2 -> {"level", "source_url", "retrieved_at", "summary"}
    exclude_whole_country_advised_against: bool = True
    today: date | None = None
    skills_lexicon: dict | None = None    # recruitability.skills: enables the technical_requirements criterion


def _r(name: str, status: S, explanation: str, evidence: list[str] | None = None, confidence: float = 0.8,
       source_url: str | None = None, material: bool = True) -> CriterionResult:
    evidence = [e for e in (evidence or []) if e][:4]
    return CriterionResult(criterion=name, status=status, explanation=explanation, evidence=evidence,
                           confidence=confidence, source_url=source_url, material=material,
                           verified_at=datetime.now(timezone.utc))


def _scannable(facts: JobFacts) -> bool:
    return facts.description_length >= MIN_SCANNABLE_DESCRIPTION


def _date_fr(value: date, *, day: bool = True, year: bool = True) -> str:
    parts = [f"{value.day:02d}"] if day else []
    parts.append(MONTH_NAMES_FR[value.month])
    if year:
        parts.append(str(value.year))
    return " ".join(parts)


# --- individual criteria -----------------------------------------------------------


def check_internship_status(facts: JobFacts, url: str | None) -> CriterionResult:
    emp = facts.employment
    if emp == "internship":
        return _r("internship_status", S.PASS, "Le titre présente le poste comme un stage.",
                  [facts.employment_evidence], 0.9, url)
    if emp in {"apprenticeship", "working_student", "volunteer", "permanent", "graduate_job"}:
        labels = {"apprenticeship": "un apprentissage", "working_student": "un emploi étudiant",
                  "volunteer": "un poste bénévole", "permanent": "un emploi permanent",
                  "graduate_job": "un poste pour jeunes diplômés sans stage"}
        return _r("internship_status", S.FAIL, f"L’offre concerne {labels[emp]}, ce qui est exclu.",
                  [facts.employment_evidence], 0.85, url)
    if emp == "mixed":
        return _r("internship_status", S.UNKNOWN,
                  "L’offre associe un stage à un autre type de contrat. Confirmez l’option de stage.",
                  [facts.employment_evidence], 0.5, url)
    if emp == "internship_in_text":
        return _r("internship_status", S.UNKNOWN, "Un stage est mentionné dans le texte, mais pas dans le titre.",
                  [facts.employment_evidence], 0.5, url)
    return _r("internship_status", S.UNKNOWN, "Type de contrat non précisé.", [], 0.4, url)


def check_dates(facts: JobFacts, ctx: EligibilityContext, country: str | None, url: str | None) -> CriterionResult:
    p = ctx.profile
    lo, hi = p.available_from, p.available_to
    explicit = [x for x in facts.periods if x.kind == "explicit_range"]
    if explicit:
        fitting = [x for x in explicit if x.start >= lo and x.end <= hi]
        if fitting:
            x = fitting[0]
            return _r("dates", S.PASS,
                      f"La période indiquée du {_date_fr(x.start)} au {_date_fr(x.end)} respecte les disponibilités.",
                      [x.evidence], 0.9, url)
        x = explicit[0]
        if facts.flexible_dates:
            return _r("dates", S.UNKNOWN,
                      f"La période indiquée de {_date_fr(x.start, day=False)} à {_date_fr(x.end, day=False)} est hors des "
                      "disponibilités, mais l’offre précise que les dates sont flexibles.",
                      [x.evidence, facts.flexible_evidence], 0.6, url)
        return _r("dates", S.FAIL,
                  f"La période indiquée du {_date_fr(x.start)} au {_date_fr(x.end)} ne correspond pas aux disponibilités "
                  f"du {_date_fr(lo, year=False)} au {_date_fr(hi)}.",
                  [x.evidence], 0.9, url)
    starts = [x for x in facts.periods if x.kind == "start_only"]
    if starts:
        x = starts[0]
        if x.start < lo or x.start > hi:
            if facts.flexible_dates:
                return _r("dates", S.UNKNOWN,
                          f"Le début en {_date_fr(x.start, day=False)} est hors des disponibilités, mais les dates sont "
                          "présentées comme flexibles.",
                          [x.evidence, facts.flexible_evidence], 0.6, url)
            return _r("dates", S.FAIL,
                      f"Le début indiqué en {_date_fr(x.start, day=False)} est hors des disponibilités.",
                      [x.evidence], 0.85, url)
        if facts.duration:
            end = date.fromordinal(x.start.toordinal() + int(facts.duration.min_weeks * 7) - 1)
            if end <= hi:
                return _r("dates", S.PASS,
                          f"Avec un début en {_date_fr(x.start, day=False)} et {facts.duration.min_weeks:g} semaines, "
                          f"le stage finit au plus tard le {_date_fr(end)}.",
                          [x.evidence, facts.duration.evidence], 0.8, url)
            return _r("dates", S.FAIL,
                      f"Avec un début en {_date_fr(x.start, day=False)} et {facts.duration.min_weeks:g} semaines, "
                      f"le stage finit après le {_date_fr(hi)}.",
                      [x.evidence, facts.duration.evidence], 0.8, url)
        return _r("dates", S.UNKNOWN,
                  f"Le début en {_date_fr(x.start, day=False)} respecte les disponibilités. La date de fin n’est pas précisée.",
                  [x.evidence], 0.6, url)

    seasons = [x for x in facts.periods if x.kind == "season"]
    target_year = lo.year
    if seasons:
        in_year = [x for x in seasons if x.year == target_year]
        if not in_year:
            years = sorted({x.year for x in seasons})
            return _r("dates", S.FAIL,
                      f"L’offre concerne le cycle {', '.join(map(str, years))}, et non {target_year}.",
                      [seasons[0].evidence], 0.85, url)
        x = in_year[0]
        if x.season == "summer" and country in SOUTHERN_HEMISPHERE:
            return _r("dates", S.UNKNOWN,
                      f"« L’été {x.year} » à {country_name(country)} correspond généralement à décembre-février. "
                      "Confirmez les mois.",
                      [x.evidence], 0.5, url)
        if x.season in {"winter", "fall"}:
            return _r("dates", S.FAIL,
                      f"Un stage à l’{SEASON_NAMES_FR[x.season]} {x.year} est hors de la période avril-août.",
                      [x.evidence], 0.75, url)
        season = SEASON_NAMES_FR.get(x.season, x.season)
        return _r("dates", S.UNKNOWN,
                  f"« {season.title()} {x.year} » est compatible en principe. Les dates exactes ne sont pas précisées.",
                  [x.evidence], 0.6, url)
    if facts.years:
        if target_year not in facts.years and all(y < target_year for y in facts.years):
            return _r("dates", S.FAIL,
                      f"Seuls des cycles passés sont mentionnés ({', '.join(map(str, sorted(facts.years)))}).",
                      [], 0.75, url)
        return _r("dates", S.UNKNOWN, "Dates du stage non précisées.", [], 0.4, url)
    return _r("dates", S.UNKNOWN, "Dates du stage non précisées.", [], 0.4, url)


def check_duration(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    p = ctx.profile
    window = p.window_weeks
    d = facts.duration
    if d is None:
        explicit = next((x for x in facts.periods if x.kind == "explicit_range"), None)
        if explicit:
            weeks = ((explicit.end - explicit.start).days + 1) / 7
            if weeks > window + 0.5 and facts.flexible_dates:
                return _r("duration", S.UNKNOWN,
                          f"Les dates indiquées couvrent environ {weeks:.0f} semaines, mais elles sont présentées "
                          "comme flexibles.",
                          [explicit.evidence, facts.flexible_evidence], 0.5, url)
            return _r("duration", S.PASS if weeks <= window + 0.5 else S.FAIL,
                      f"Durée déduite des dates indiquées : environ {weeks:.0f} semaines.",
                      [explicit.evidence], 0.85, url)
        return _r("duration", S.UNKNOWN, "Durée non précisée.", [], 0.4, url)
    if d.min_weeks > window + 0.5:
        return _r("duration", S.FAIL,
                  f"La durée minimale de {d.min_weeks:g} semaines dépasse les {window:.0f} semaines disponibles.",
                  [d.evidence], 0.85, url)
    if d.max_weeks > window + 0.5:
        return _r("duration", S.UNKNOWN,
                  f"Durée de {d.min_weeks:g} à {d.max_weeks:g} semaines : seule l’option la plus courte convient.",
                  [d.evidence], 0.6, url)
    lo, hi = p.acceptable_weeks
    note = "" if lo <= d.min_weeks and d.max_weeks <= hi else (
        " Hors de la durée préférée de 4 à 20 semaines, avec un effet limité au classement."
    )
    return _r("duration", S.PASS,
              f"La durée de {d.min_weeks:g} à {d.max_weeks:g} semaines respecte les disponibilités.{note}",
              [d.evidence], 0.8, url)


def check_country(loc: LocationResolution, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    if not loc.countries:
        if loc.remote:
            return _r("country", S.UNKNOWN,
                      "Poste à distance sans pays précisé. Le lieu de travail doit être confirmé.",
                      loc.evidence, 0.5, url)
        return _r("country", S.UNKNOWN, "Le lieu n’a pas pu être associé à un pays.", loc.evidence, 0.4, url)
    allowed = [c for c in loc.countries if c not in ctx.excluded_countries]
    excluded = [c for c in loc.countries if c in ctx.excluded_countries]
    if not allowed:
        return _r("country", S.FAIL,
                  f"Poste situé dans un pays exclu : {', '.join(country_name(c) for c in excluded)}.",
                  loc.evidence, 0.95, url)
    note = f" Autres lieux exclus ignorés : {', '.join(country_name(c) for c in excluded)}." if excluded else ""
    return _r("country", S.PASS, f"Pays du poste : {', '.join(country_name(c) for c in allowed)}.{note}",
              loc.evidence, 0.9, url)


def check_safety(loc: LocationResolution, ctx: EligibilityContext) -> CriterionResult:
    countries = [c for c in loc.countries if c not in ctx.excluded_countries]
    if not countries:
        return _r("safety", S.NOT_APPLICABLE, "Aucun pays admissible à évaluer.", [], 1.0, material=False)
    results = []
    for code in countries:
        if code in ctx.override_allow:
            results.append((S.PASS, f"{country_name(code)} : autorisé manuellement par l’utilisateur.", None))
            continue
        if code in ctx.safety_excluded:
            results.append((S.FAIL,
                            f"{country_name(code)} figure dans la liste d’exclusion de sécurité de l’utilisateur.",
                            None))
            continue
        adv = ctx.advisories.get(code)
        if not adv:
            results.append((S.UNKNOWN, f"Aucun avis officiel enregistré pour {country_name(code)}.", None))
            continue
        level = adv.get("level")
        src = f"{adv.get('source_url')} (consulté le {adv.get('retrieved_at')})"
        if level == "WHOLE_COUNTRY_ADVISED_AGAINST" and ctx.exclude_whole_country_advised_against:
            results.append((S.FAIL,
                            f"{country_name(code)} : tout le pays est déconseillé selon France Diplomatie, {src}.",
                            adv.get("source_url")))
        elif level == "NO_FORMAL_WARNING_FOUND":
            results.append((S.PASS,
                            f"{country_name(code)} : aucune mention « formellement déconseillé » sur France Diplomatie, "
                            f"{src}.", adv.get("source_url")))
        else:
            results.append((S.UNKNOWN,
                            f"{country_name(code)} : niveau d’avis {level}. Vérifiez la ville ou la zone exacte, {src}.",
                            adv.get("source_url")))
    if all(s == S.FAIL for s, _, _ in results):
        return _r("safety", S.FAIL, " ".join(e for _, e, _ in results), [], 0.9, results[0][2], material=False)
    best = S.PASS if any(s == S.PASS for s, _, _ in results) else S.UNKNOWN
    source_url = next((u for _, _, u in results if u), None)
    return _r("safety", best, " ".join(e for _, e, _ in results), [], 0.6, source_url, material=False)


def check_language(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    known = ctx.profile.language_names
    mandatory_missing = [l for l in facts.languages if l.mandatory and l.language not in known]
    optional_missing = [l for l in facts.languages if not l.mandatory and l.language not in known]
    if mandatory_missing:
        names = ", ".join(l.language.title() for l in mandatory_missing)
        return _r("language", S.FAIL, f"Exigence linguistique obligatoire non satisfaite : {names}.",
                  [l.evidence for l in mandatory_missing], 0.85, url)
    if optional_missing:
        names = ", ".join(l.language.title() for l in optional_missing)
        return _r("language", S.PASS, f"Langue facultative ou appréciée, mais non obligatoire : {names}.",
                  [l.evidence for l in optional_missing], 0.7, url)
    if facts.languages:
        return _r("language", S.PASS,
                  "Les langues requises figurent parmi les langues vérifiées du candidat (French, English).",
                  [l.evidence for l in facts.languages], 0.85, url)
    if _scannable(facts):
        return _r("language", S.PASS, "Aucune exigence linguistique autre que la langue de l’offre.", [], 0.65, url)
    return _r("language", S.UNKNOWN, "Description trop courte pour vérifier les exigences linguistiques.",
              [], 0.3, url)


def check_academic(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    p = ctx.profile
    mandatory = [d for d in facts.degrees if d.mandatory]
    if not mandatory:
        if _scannable(facts):
            return _r("academic", S.PASS,
                      "Aucune restriction de diplôme précise. Le candidat est inscrit en école d’ingénieurs.",
                      [], 0.6, url)
        return _r("academic", S.UNKNOWN, "Description trop courte pour vérifier les exigences académiques.",
                  [], 0.3, url)
    statuses = []
    level_names = {
        "bachelors": "licence",
        "masters": "master",
        "mba": "MBA",
        "phd": "doctorat",
    }
    for d in mandatory:
        levels = d.levels
        if "any_student" in levels:
            statuses.append((S.PASS, "Tout niveau d’études en cours est accepté.", d.evidence))
            continue
        ok = []
        unknown = []
        for level in levels:
            value = {"bachelors": p.bachelors_enrolled, "masters": p.masters_enrolled, "mba": p.mba, "phd": p.phd}[level]
            (ok if value else unknown if value is None else []).append(level)
        if ok:
            names = ", ".join(level_names.get(level, level) for level in ok)
            statuses.append((S.PASS, f"Le niveau accepté ({names}) correspond au profil.", d.evidence))
        elif unknown:
            names = " ou ".join(level_names.get(level, level) for level in sorted(levels))
            statuses.append((S.UNKNOWN,
                             f"Niveau requis : {names}. L’équivalence du diplôme d’ingénieur ESILV n’est pas confirmée "
                             "dans le profil.", d.evidence))
        else:
            names = " ou ".join(level_names.get(level, level) for level in sorted(levels))
            statuses.append((S.FAIL, f"Niveau requis : {names}. Le candidat n’y est pas inscrit.", d.evidence))
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
            return _r("graduation", S.PASS,
                      f"L’obtention du diplôme en {p.graduation_year} respecte la période indiquée.",
                      [passing[0].evidence], 0.85, url)
        if recent_ok:
            return _r("graduation", S.UNKNOWN,
                      "La période d’obtention du diplôme ne correspond pas, mais les jeunes diplômés sont aussi mentionnés.",
                      [windows[0].evidence, recent_ok.evidence], 0.5, url)
        if unknown:
            g = unknown[0]
            return _r("graduation", S.UNKNOWN,
                      f"La période {g.start[1]:02d}/{g.start[0]}-{g.end[1]:02d}/{g.end[0]} ne couvre qu’une partie de "
                      f"{p.graduation_year}. Le mois d’obtention du diplôme est INCONNU dans le profil.",
                      [g.evidence], 0.6, url)
        g = windows[0]
        return _r("graduation", S.FAIL,
                  f"La période d’obtention du diplôme requise, du {g.start[1]:02d}/{g.start[0]} au "
                  f"{g.end[1]:02d}/{g.end[0]}, exclut {p.graduation_year}.",
                  [g.evidence], 0.85, url)
    if penultimate:
        if p.returning_to_studies is True:
            return _r("graduation", S.PASS, "La reprise des études est requise et confirmée par le profil.",
                      [penultimate.evidence], 0.8, url)
        if p.returning_to_studies is False or p.current_study_year == "final_year":
            return _r("graduation", S.FAIL,
                      "La reprise des études après le stage est requise, mais le profil indique une dernière année.",
                      [penultimate.evidence], 0.8, url)
        return _r("graduation", S.UNKNOWN,
                  "La reprise des études après le stage est requise, mais elle n’est pas confirmée dans le profil.",
                  [penultimate.evidence], 0.5, url)
    if _scannable(facts):
        return _r("graduation", S.PASS, "Aucune restriction sur la date d’obtention du diplôme.", [], 0.6, url)
    return _r("graduation", S.UNKNOWN,
              "Description trop courte pour vérifier les exigences liées à l’obtention du diplôme.", [], 0.3, url)


def check_citizenship(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    p = ctx.profile
    if facts.clearance:
        return _r("citizenship", S.FAIL if p.citizenship and "US" not in p.citizenship else S.UNKNOWN,
                  "Habilitation de sécurité ou restriction liée au contrôle des exportations, généralement selon la "
                  "nationalité.",
                  facts.clearance, 0.75, url)
    if facts.citizenship:
        required = facts.citizenship_countries - {"UNKNOWN"}
        if not p.citizenship:
            return _r("citizenship", S.UNKNOWN,
                      "Restriction de citoyenneté ou de résidence permanente. La citoyenneté du candidat est INCONNUE "
                      "dans le profil.",
                      facts.citizenship, 0.6, url)
        if "EU" in required and any(c in EU_EEA_CH for c in p.citizenship):
            return _r("citizenship", S.PASS, "La citoyenneté de l’UE est requise et présente dans le profil.",
                      facts.citizenship, 0.8, url)
        if required and not (required & set(p.citizenship)):
            return _r("citizenship", S.FAIL,
                      f"Offre réservée aux citoyens ou résidents de {', '.join(sorted(required))}.",
                      facts.citizenship, 0.85, url)
        if required & set(p.citizenship):
            return _r("citizenship", S.PASS, "La citoyenneté requise est présente dans le profil.",
                      facts.citizenship, 0.85, url)
        return _r("citizenship", S.UNKNOWN, "La restriction de nationalité n’a pas pu être interprétée.",
                  facts.citizenship, 0.5, url)
    if _scannable(facts):
        return _r("citizenship", S.PASS, "Aucune restriction de citoyenneté détectée.", [], 0.6, url)
    return _r("citizenship", S.UNKNOWN, "Description trop courte pour vérifier les restrictions de nationalité.",
              [], 0.3, url)


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
    evidence = [
        e
        for key in ("unavailable", "existing_auth", "available", "mention")
        for e in facts.sponsorship_flags.get(key, [])
    ][:3]
    if not countries:
        return _r("work_authorization", S.UNKNOWN,
                  "Pays de travail inconnu. L’autorisation de travail ne peut pas être évaluée.",
                  evidence, 0.3, url), status
    authorized = [c for c in countries if p.work_authorization.get(c) is True]
    if authorized:
        return _r("work_authorization", S.PASS,
                  f"Le profil confirme une autorisation de travail pour {', '.join(country_name(c) for c in authorized)}.",
                  evidence, 0.9, url), status
    eu_free = [c for c in countries if c in EU_EEA_CH and any(x in EU_EEA_CH for x in p.citizenship)]
    if eu_free:
        return _r("work_authorization", S.PASS,
                  f"La libre circulation UE/EEE/Suisse s’applique en {', '.join(country_name(c) for c in eu_free)}. "
                  "La citoyenneté est confirmée dans le profil.", evidence, 0.8, url), status
    if status == SponsorshipStatus.AVAILABLE:
        return _r("work_authorization", S.PASS, "L’offre indique qu’un parrainage de visa est disponible.",
                  evidence, 0.8, url), status
    lacks_auth = all(p.work_authorization.get(c) is False for c in countries)
    if status in {SponsorshipStatus.UNAVAILABLE, SponsorshipStatus.EXISTING_AUTH_REQUIRED}:
        if lacks_auth and p.requires_sponsorship is True:
            return _r("work_authorization", S.FAIL,
                      "Une autorisation de travail existante est requise, et le profil n’en confirme aucune pour ce pays.",
                      evidence, 0.85, url), status
        return _r("work_authorization", S.UNKNOWN,
                  "Une autorisation de travail existante est requise, sans parrainage. Le statut du candidat est INCONNU.",
                  evidence, 0.6, url), status
    return _r("work_authorization", S.UNKNOWN,
              "Autorisation de travail et parrainage non précisés. L’absence de restriction ne suffit pas à établir "
              "l’admissibilité.",
              evidence, 0.4, url), status


def check_qualifications(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    held = {q.lower() for q in ctx.profile.certifications + ctx.profile.licenses}
    mandatory = [(n, e) for n, m, e in facts.qualifications if m and n.lower() not in held]
    if mandatory:
        hard = [(n, e) for n, e in mandatory if n != "Driver's license"]
        if hard:
            return _r("qualifications", S.FAIL,
                      f"Qualification obligatoire non détenue : {', '.join(n for n, _ in hard)}.",
                      [e for _, e in hard], 0.8, url)
        return _r("qualifications", S.UNKNOWN,
                  "Un permis de conduire est requis, mais il n’est pas renseigné dans le profil.",
                  [e for _, e in mandatory], 0.5, url)
    if _scannable(facts):
        return _r("qualifications", S.PASS, "Aucune licence professionnelle ni certification obligatoire détectée.",
                  [], 0.65, url)
    return _r("qualifications", S.UNKNOWN, "Description trop courte pour vérifier les qualifications obligatoires.",
              [], 0.3, url)


def check_technical_requirements(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    """Fail only on mandatory specialized expertise the profile lacks; a tool merely mentioned never fails."""
    if not ctx.skills_lexicon:
        return _r("technical_requirements", S.NOT_APPLICABLE, "Vérification des exigences techniques non configurée.",
                  [], 0.5, url, material=False)
    from summer_hunter.engine import requirements as reqs

    found = reqs.extract(facts.text, ctx.skills_lexicon, ctx.profile.verified_skills)
    gaps = reqs.blocking(found)
    if gaps:
        names = ", ".join(r.name for r in gaps)
        core = [r for r in gaps if r.core]
        if core:
            return _r("technical_requirements", S.FAIL,
                      f"Expertise spécialisée obligatoire absente du profil vérifié : {names}.",
                      [r.evidence for r in core], 0.8, url)
        return _r("technical_requirements", S.FAIL,
                  f"Exigence absente du profil vérifié : {names}. Vérifiez son importance pour le poste.",
                  [r.evidence for r in gaps], 0.6, url)
    soft = [r for r in found if r.advanced and not r.verified]
    if soft:
        return _r("technical_requirements", S.PASS, "Outils spécialisés seulement appréciés ou à découvrir : "
                  + ", ".join(f"{r.name} ({r.level})" for r in soft[:3]) + ".", [r.evidence for r in soft], 0.6, url)
    if _scannable(facts):
        return _r("technical_requirements", S.PASS,
                  "Aucune exigence technique spécialisée obligatoire incompatible avec le profil.", [], 0.6, url)
    return _r("technical_requirements", S.UNKNOWN,
              "Description trop courte pour vérifier les exigences techniques.", [], 0.3, url, material=False)


def check_compensation(facts: JobFacts, ctx: EligibilityContext, url: str | None) -> CriterionResult:
    if facts.compensation == "unpaid":
        if ctx.profile.allow_unpaid:
            return _r("compensation", S.PASS, "Stage non rémunéré, explicitement accepté dans le profil.",
                      [facts.compensation_evidence], 0.8, url, material=False)
        return _r("compensation", S.FAIL, "Stage non rémunéré, non accepté dans le profil.",
                  [facts.compensation_evidence], 0.85, url, material=False)
    if facts.compensation == "paid":
        return _r("compensation", S.PASS, "Une rémunération est mentionnée.",
                  [facts.compensation_evidence], 0.75, url, material=False)
    return _r("compensation", S.UNKNOWN, "Rémunération non précisée.", [], 0.4, url, material=False)


# --- aggregate ---------------------------------------------------------------------


def evaluate(facts: JobFacts, loc: LocationResolution, ctx: EligibilityContext,
             source_url: str | None = None) -> EligibilityReport:
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
        check_technical_requirements(facts, ctx, source_url),
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
