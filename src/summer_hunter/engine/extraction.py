"""Deterministic requirement extraction from job titles and descriptions.

Every extracted fact carries the evidence snippet it came from. Nothing is inferred
beyond what the text states: absent facts stay None.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

from summer_hunter.utils.text import normalize_for_match, snippet, split_sentences

MONTHS = {
    "january": 1, "jan": 1, "janvier": 1, "januar": 1,
    "february": 2, "feb": 2, "fevrier": 2, "februar": 2,
    "march": 3, "mar": 3, "mars": 3, "marz": 3,
    "april": 4, "apr": 4, "avril": 4,
    "may": 5, "mai": 5,
    "june": 6, "jun": 6, "juin": 6, "juni": 6,
    "july": 7, "jul": 7, "juillet": 7, "juli": 7,
    "august": 8, "aug": 8, "aout": 8,
    "september": 9, "sept": 9, "sep": 9, "septembre": 9,
    "october": 10, "oct": 10, "octobre": 10, "oktober": 10,
    "november": 11, "nov": 11, "novembre": 11,
    "december": 12, "dec": 12, "decembre": 12, "dezember": 12,
}
_MONTH_RE = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b\.?"
_YEAR = r"20[2-3]\d"
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "sixteen": 16, "twenty": 20,
              "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six ": 6}

LANGUAGES = {
    "thai": ["thai"], "mandarin": ["mandarin", "putonghua"], "chinese": ["chinese"], "cantonese": ["cantonese"],
    "japanese": ["japanese", "jlpt"], "korean": ["korean", "topik"], "german": ["german", "deutsch"],
    "dutch": ["dutch"], "swedish": ["swedish"], "danish": ["danish"], "norwegian": ["norwegian"],
    "finnish": ["finnish"], "spanish": ["spanish", "espanol"], "italian": ["italian"],
    "portuguese": ["portuguese"], "arabic": ["arabic"], "hebrew": ["hebrew"], "vietnamese": ["vietnamese"],
    "indonesian": ["indonesian", "bahasa indonesia", "bahasa"], "malay": ["malay", "bahasa melayu"],
    "russian": ["russian"], "polish": ["polish"], "turkish": ["turkish"], "hindi": ["hindi"],
    "czech": ["czech"], "greek": ["greek"], "hungarian": ["hungarian"], "romanian": ["romanian"],
    "french": ["french", "francais"], "english": ["english", "anglais"],
}
_LANG_REQUIRED = re.compile(
    r"\b(fluen\w*|native|mother tongue|proficien\w*|business[- ]level|professional (working )?(level|proficiency)|"
    r"required|requirement|must|mandatory|essential|excellent (command|knowledge)|written and (spoken|verbal)|"
    r"spoken and written|full professional|c1|c2|jlpt\s*n[12]|hsk\s*[4-6]|topik\s*[4-6]|courant|bilingu\w*|"
    r"maitrise|need to (speak|be))\b"
)
_LANG_OPTIONAL = re.compile(
    r"\b(plus|preferred|preferably|advantage\w*|nice[- ]to[- ]have|desirable|desired|bonus|beneficial|ideally|"
    r"a plus|is an asset|asset|appreciated|welcome|helpful|optional|not required|souhaite|apprecie|atout)\b"
)

_DURATION_CUES = re.compile(
    r"\b(intern\w*|program\w*|duration|stage|placement|period|assignment|position|role|summer|weeks?|months?|"
    r"mois|semaines|contract|duree|engagement)\b"
)


@dataclass
class Evidence:
    text: str


@dataclass
class PeriodFact:
    kind: str                          # explicit_range | start_only | season | year_only
    start: date | None = None
    end: date | None = None
    season: str | None = None
    year: int | None = None
    evidence: str = ""


@dataclass
class DurationFact:
    min_weeks: float
    max_weeks: float
    evidence: str


@dataclass
class LanguageReq:
    language: str
    mandatory: bool
    evidence: str


@dataclass
class GradReq:
    kind: str                     # window | penultimate | final_year | recent_grad_ok
    start: tuple[int, int] | None = None   # (year, month)
    end: tuple[int, int] | None = None
    evidence: str = ""


@dataclass
class DegreeReq:
    levels: set[str]              # any of: bachelors, masters, mba, phd, any_student
    mandatory: bool
    evidence: str


@dataclass
class JobFacts:
    title: str
    text: str
    years: set[int] = field(default_factory=set)
    periods: list[PeriodFact] = field(default_factory=list)
    flexible_dates: bool = False
    flexible_evidence: str = ""
    duration: DurationFact | None = None
    languages: list[LanguageReq] = field(default_factory=list)
    graduation: list[GradReq] = field(default_factory=list)
    degrees: list[DegreeReq] = field(default_factory=list)
    citizenship: list[str] = field(default_factory=list)          # evidence snippets
    citizenship_countries: set[str] = field(default_factory=set)
    clearance: list[str] = field(default_factory=list)
    sponsorship_flags: dict[str, list[str]] = field(default_factory=dict)
    compensation: str | None = None                              # paid | unpaid
    compensation_evidence: str = ""
    cold_calls_per_day: int | None = None
    cold_call_evidence: str = ""
    cold_calling_mentioned: bool = False
    high_volume_calling: bool = False
    qualifications: list[tuple[str, bool, str]] = field(default_factory=list)  # (name, mandatory, evidence)
    employment: str | None = None
    employment_evidence: str = ""

    @property
    def description_length(self) -> int:
        return len(self.text)


def _date_from(year: int, month: int, day: int | None, end: bool) -> date:
    if day:
        last = calendar.monthrange(year, month)[1]
        return date(year, month, min(day, last))
    return date(year, month, calendar.monthrange(year, month)[1] if end else 1)


def _month(token: str | None) -> int | None:
    if not token:
        return None
    return MONTHS.get(token.strip(". ").lower())


def _int(token: str | None) -> int | None:
    if not token:
        return None
    digits = re.sub(r"\D", "", token)
    return int(digits) if digits else None


# --- dates ---------------------------------------------------------------------

_RANGE = re.compile(
    rf"(?P<d1>\b\d{{1,2}})?(?:st|nd|rd|th|er)?\s*(?P<m1>{_MONTH_RE})(?:\s*(?P<d1b>\d{{1,2}})(?:st|nd|rd|th)?\b)?,?"
    rf"(?:\s*(?P<y1>{_YEAR}))?\s*(?:-|to|through|until|till|thru|au|a|jusqu'?a|bis)\s*"
    rf"(?P<d2>\b\d{{1,2}})?(?:st|nd|rd|th|er)?\s*(?P<m2>{_MONTH_RE})(?:\s*(?P<d2b>\d{{1,2}})(?:st|nd|rd|th)?\b)?,?"
    rf"(?:\s*(?P<y2>{_YEAR}))?"
)
_ISO_RANGE = re.compile(rf"(?P<a>{_YEAR}-\d{{2}}-\d{{2}})\s*(?:-|to|through|until|au|a)\s*(?P<b>{_YEAR}-\d{{2}}-\d{{2}})")
_START = re.compile(
    rf"\b(?:start(?:ing|s)?(?: date)?|begin(?:ning|s)?|commenc\w*|a partir d[eu]|debut(?:ant)?|des)\s*:?\s*"
    rf"(?:in|on|from|of|le|en|du|the)?\s*(?:early|mid|late|debut|fin|mi)?[\s-]*(?P<m>{_MONTH_RE})"
    rf"(?:\s*\d{{1,2}}(?:st|nd|rd|th)?)?,?\s*(?P<y>{_YEAR})"
)
_SEASON_A = re.compile(rf"\b(?P<s>summer|spring|fall|autumn|winter|off[- ]?cycle|ete|printemps|automne|hiver)\s+(?:intern\w*\s+|stage\s+|program\w*\s+)?(?P<y>{_YEAR})\b")
_SEASON_B = re.compile(rf"\b(?P<y>{_YEAR})\s*(?:/\d{{2}}\s*)?(?P<s>summer|spring|fall|autumn|winter|off[- ]?cycle)\b")
_FLEX = re.compile(r"\b(flexible (start|dates?|timing|period)|start dates? (are|is|can be) flexible|dates? (are|is) flexible|dates flexibles|can start (any ?time|at any time)|start date negotiable)\b")
_YEARS = re.compile(rf"\b({_YEAR})\b")

SEASON_NORMAL = {"ete": "summer", "printemps": "spring", "automne": "fall", "autumn": "fall", "hiver": "winter",
                 "offcycle": "off-cycle", "off-cycle": "off-cycle", "off cycle": "off-cycle"}


_NOT_INTERNSHIP_DATE = re.compile(r"(graduat\w*|degree|diploma|class of|completion|enrolled|semester|exam\w*|deadline|apply by|applications? (close|due))[^.;\n]{0,40}$")


def _about_internship(norm: str, start: int) -> bool:
    """False when the date phrase describes graduation, a deadline or studies rather than the internship."""
    return not _NOT_INTERNSHIP_DATE.search(norm[max(0, start - 60): start])


def _extract_periods(norm: str, facts: JobFacts) -> None:
    single_year = next(iter(facts.years)) if len(facts.years) == 1 else None
    for m in _ISO_RANGE.finditer(norm):
        if not _about_internship(norm, m.start()):
            continue
        try:
            a, b = date.fromisoformat(m.group("a")), date.fromisoformat(m.group("b"))
        except ValueError:
            continue
        facts.periods.append(PeriodFact("explicit_range", a, b, evidence=snippet(norm, m.start(), m.end())))
    for m in _RANGE.finditer(norm):
        m1, m2 = _month(m.group("m1")), _month(m.group("m2"))
        if not m1 or not m2 or not _about_internship(norm, m.start()):
            continue
        y1, y2 = _int(m.group("y1")), _int(m.group("y2"))
        if y2 is None and y1 is None:
            if single_year is None:
                continue
            y1 = y2 = single_year
        if y2 is None:
            y2 = y1 if m2 >= m1 else y1 + 1
        if y1 is None:
            y1 = y2 if m1 <= m2 else y2 - 1
        d1 = _int(m.group("d1")) or _int(m.group("d1b"))
        d2 = _int(m.group("d2")) or _int(m.group("d2b"))
        if d1 and d1 > 31:
            d1 = None
        if d2 and d2 > 31:
            d2 = None
        start, end = _date_from(y1, m1, d1, False), _date_from(y2, m2, d2, True)
        if end <= start or (end - start).days > 400:
            continue
        facts.periods.append(PeriodFact("explicit_range", start, end, evidence=snippet(norm, m.start(), m.end())))
    if not any(p.kind == "explicit_range" for p in facts.periods):
        for m in _START.finditer(norm):
            month, year = _month(m.group("m")), int(m.group("y"))
            if month and _about_internship(norm, m.start()):
                facts.periods.append(PeriodFact("start_only", _date_from(year, month, None, False), year=year,
                                                evidence=snippet(norm, m.start(), m.end())))
    for regex in (_SEASON_A, _SEASON_B):
        for m in regex.finditer(norm):
            if not _about_internship(norm, m.start()):
                continue
            season = m.group("s").replace(" ", "-")
            season = SEASON_NORMAL.get(season, season)
            facts.periods.append(PeriodFact("season", season=season, year=int(m.group("y")),
                                            evidence=snippet(norm, m.start(), m.end())))
    flex = _FLEX.search(norm)
    if flex:
        facts.flexible_dates = True
        facts.flexible_evidence = snippet(norm, flex.start(), flex.end())


# --- duration ------------------------------------------------------------------

_DURATION = re.compile(
    r"\b(?P<a>\d{1,2}|" + "|".join(k.strip() for k in _NUM_WORDS) + r")"
    r"(?:\s*(?:-|to|a|or)\s*(?P<b>\d{1,2}|" + "|".join(k.strip() for k in _NUM_WORDS) + r"))?"
    r"\s*\+?\s*-?\s*(?P<unit>weeks?|wks?|months?|mois|semaines?)\b(?P<after>[^.;\n]{0,25})"
)


def _num(token: str | None) -> int | None:
    if token is None:
        return None
    token = token.strip()
    return int(token) if token.isdigit() else _NUM_WORDS.get(token)


def _extract_duration(norm: str, facts: JobFacts) -> None:
    for _, _, sentence in split_sentences(norm):
        if not _DURATION_CUES.search(sentence):
            continue
        for m in _DURATION.finditer(sentence):
            after = m.group("after")
            if re.search(r"\b(of )?(experience|experiences|exp\b|notice|after|ago|prior)", after):
                continue
            if re.search(r"(experience|every|within|after|least|up to)\s*$", sentence[: m.start()][-20:]):
                continue
            a, b = _num(m.group("a")), _num(m.group("b"))
            if not a:
                continue
            b = b or a
            factor = 1.0 if m.group("unit").startswith(("week", "wk", "semaine")) else 4.345
            lo, hi = sorted((a * factor, b * factor))
            if hi > 80 or lo < 1:
                continue
            facts.duration = DurationFact(round(lo, 1), round(hi, 1), snippet(sentence, m.start(), m.end(), 70))
            return


# --- languages -----------------------------------------------------------------


def _extract_languages(norm: str, facts: JobFacts) -> None:
    seen: dict[str, LanguageReq] = {}
    for _, _, sentence in split_sentences(norm):
        for lang, aliases in LANGUAGES.items():
            for alias in aliases:
                m = re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", sentence)
                if not m:
                    continue
                # "Chinese market", "German customers": a language name used as an adjective.
                context = sentence[m.end(): m.end() + 30]
                if re.match(r"\s+(market|markets|customers?|clients?|companies|company|team|office|government|economy|"
                            r"region|speaking (market|region)|partners?|subsidiar\w+|entity|law|startups?)", context) \
                        and not _LANG_REQUIRED.search(sentence):
                    continue
                required = bool(_LANG_REQUIRED.search(sentence))
                optional = bool(_LANG_OPTIONAL.search(sentence))
                if not required and not optional:
                    if not re.search(r"\b(speak|speaking|language|languages|langue|written|spoken|communicat\w*)\b", sentence):
                        continue
                    required = False
                    optional = True
                mandatory = required and not optional
                # A sentence listing several languages with "or" ("Japanese or Korean") is an alternative,
                # each one alone is not mandatory.
                if mandatory and re.search(rf"{re.escape(alias)}\s+(or|ou)\s+\w+|\w+\s+(or|ou)\s+{re.escape(alias)}", sentence):
                    others = [a for l2, al in LANGUAGES.items() if l2 != lang for a in al if a in sentence]
                    if others:
                        mandatory = False
                req = LanguageReq(lang, mandatory, sentence[:300])
                if lang not in seen or (mandatory and not seen[lang].mandatory):
                    seen[lang] = req
                break
    facts.languages = list(seen.values())


# --- graduation & degree -------------------------------------------------------

_GRAD_WINDOW = re.compile(
    rf"graduat\w*(?:\s+date)?\s+(?:between|from)\s+(?P<m1>{_MONTH_RE})?[\s/.,-]*(?P<y1>{_YEAR})\s*"
    rf"(?:and|to|-|through|until)\s*(?P<m2>{_MONTH_RE})?[\s/.,-]*(?P<y2>{_YEAR})"
)
_GRAD_SINGLE = re.compile(
    rf"(?:graduat\w*|graduation(?:\s+date)?|degree completion|complete (?:your|their) degree)\s+"
    rf"(?:date\s+)?(?:(?P<prep>in|by|before|after|no later than|no earlier than|of|during|between)\s+)?"
    rf"(?:(?:the\s+)?(?:winter|spring|summer|fall|autumn)\s+(?:of\s+)?)?(?P<m>{_MONTH_RE})?[\s/.,-]*(?P<y>{_YEAR})"
    rf"(?P<more>(?:\s*(?:,|or|and|/)\s*(?:{_MONTH_RE}\s*)?{_YEAR})*)"
)
_CLASS_OF = re.compile(rf"\b(?:class of|graduating class(?: of)?|promo(?:tion)?)\s+(?P<y>{_YEAR})\b|\b(?P<y2>{_YEAR})\s+graduates\b")
_PENULTIMATE = re.compile(r"\b(penultimate|pre-?final|second[- ]to[- ]last|returning to (school|university|college|your studies|their studies)|"
                          r"return to (school|university|college|your studies|full-time studies)|continu\w* (your |their )?(studies|degree) after|"
                          r"at least one (semester|term|year) (of study )?remaining|enrolled (in|for) (the )?(following|fall|autumn) (semester|term))\b")
_FINAL_YEAR = re.compile(r"\b(final[- ]year|last year of (your|their) (studies|degree)|fin d'?etudes|graduating students?)\b")
_RECENT_GRAD = re.compile(r"\b(recent graduates?|recently graduated|graduated within the (last|past))\b")

_DEGREE_LEVELS = {
    "phd": r"\b(ph\.?\s?d|doctoral|doctorate)\b",
    "mba": r"\bmba\b",
    "masters": r"\b(master'?s?|msc|m\.s\.|ms degree|graduate (degree|program|student)|postgraduate)\b",
    "bachelors": r"\b(bachelor'?s?|undergraduate|ba/bs|bs/ba|b\.s\.|bsc|licence)\b",
}
_DEGREE_CUE = re.compile(r"\b(currently (enrolled|pursuing|studying)|enrolled|pursuing|working towards|candidate for|"
                         r"student|must be|requires?|required|eligible|program|degree)\b")
_OPTIONAL_CUE = re.compile(r"\b(preferred|plus|nice to have|ideally|bonus|desirable|or equivalent experience|advantage)\b")


def _ym(month_token: str | None, year: str, end: bool) -> tuple[int, int]:
    m = _month(month_token)
    return int(year), (m if m else (12 if end else 1))


def _extract_graduation(norm: str, facts: JobFacts) -> None:
    covered: list[tuple[int, int]] = []
    for m in _GRAD_WINDOW.finditer(norm):
        facts.graduation.append(GradReq("window", _ym(m.group("m1"), m.group("y1"), False),
                                        _ym(m.group("m2"), m.group("y2"), True), snippet(norm, m.start(), m.end())))
        covered.append((m.start(), m.end()))
    for m in _GRAD_SINGLE.finditer(norm):
        if any(a <= m.start() < b for a, b in covered):
            continue
        prep = (m.group("prep") or "in").strip()
        ev = snippet(norm, m.start(), m.end())
        years = [m.group("y")] + _YEARS.findall(m.group("more") or "")
        for y in years:
            if prep in {"by", "before", "no later than"}:
                facts.graduation.append(GradReq("window", (2000, 1), _ym(m.group("m"), y, True), ev))
            elif prep in {"after", "no earlier than"}:
                facts.graduation.append(GradReq("window", _ym(m.group("m"), y, False), (2100, 12), ev))
            else:
                facts.graduation.append(GradReq("window", _ym(m.group("m"), y, False), _ym(m.group("m"), y, True), ev))
    for m in _CLASS_OF.finditer(norm):
        y = m.group("y") or m.group("y2")
        facts.graduation.append(GradReq("window", (int(y), 1), (int(y), 12), snippet(norm, m.start(), m.end())))
    for regex, kind in ((_PENULTIMATE, "penultimate"), (_FINAL_YEAR, "final_year"), (_RECENT_GRAD, "recent_grad_ok")):
        m = regex.search(norm)
        if m:
            facts.graduation.append(GradReq(kind, evidence=snippet(norm, m.start(), m.end())))


def _extract_degrees(norm: str, facts: JobFacts) -> None:
    for _, _, sentence in split_sentences(norm):
        if not _DEGREE_CUE.search(sentence):
            continue
        levels = {lvl for lvl, rx in _DEGREE_LEVELS.items() if re.search(rx, sentence)}
        if not levels:
            continue
        if re.search(r"\b(or (higher|above|equivalent)|and above|any (degree|level))\b", sentence):
            levels.add("any_student")
        mandatory = not _OPTIONAL_CUE.search(sentence)
        facts.degrees.append(DegreeReq(levels, mandatory, sentence[:300]))


# --- authorization, compensation, calls, qualifications --------------------------

_CITIZEN = re.compile(
    r"\b(?:must be|required to be|requires?|only open to|open only to|restricted to|limited to|applicants must be|eligible:?)\s+"
    r"(?:an?\s+)?(?P<who>u\.?s\.?|us|american|singapore(?:an)?|uk|british|canadian|australian|japanese|korean|chinese|"
    r"emirati|uae|qatari|swiss|eu|european union|indian|new zealand)\s+(?:citizens?|nationals?|citizenship|permanent residents?|pr\b)"
)
_CITIZEN_ONLY = re.compile(r"\b(singapore(an)? citizens? (or|and|/) (permanent residents?|prs?) only|citizens? only|"
                           r"citizenship (is )?required|nationality (is )?required|u\.?s\.? persons?)\b")
_CLEARANCE = re.compile(r"\b(security clearance|government clearance|ts/sci|secret clearance|clearance eligib\w*|"
                        r"itar|export control(led)? (regulations?|laws?))\b")
_CITIZEN_COUNTRY = {"u.s.": "US", "us": "US", "u.s": "US", "american": "US", "singapore": "SG", "singaporean": "SG",
                    "uk": "GB", "british": "GB", "canadian": "CA", "australian": "AU", "japanese": "JP",
                    "korean": "KR", "chinese": "CN", "emirati": "AE", "uae": "AE", "qatari": "QA", "swiss": "CH",
                    "eu": "EU", "european union": "EU", "indian": "IN", "new zealand": "NZ"}

_SPONSOR_PATTERNS = {
    "unavailable": [
        r"\bno (visa |immigration |work permit )?sponsorship\b",
        r"\b(not|unable to|cannot|can't|won't|will not|do not|does not|don't|are not able to)\s+(currently\s+)?(be able to\s+)?"
        r"(provide|offer|sponsor|support)\b[^.;]{0,50}\b(sponsor\w*|visas?|work permits?)",
        r"\bsponsorship (is |will )?not (be )?(available|provided|offered|possible)\b",
        r"\bwithout (the need for |requiring |needing )?(current or future |future )?(visa |employment visa )?sponsorship\b",
        r"\bnot eligible for (visa )?sponsorship\b",
    ],
    "existing_auth": [
        r"\b(must|need to|required to|should)\s+(already\s+)?(have|hold|possess|be|obtain)\s+(a\s+|the\s+|valid\s+|current\s+|full\s+|existing\s+|legal\s+)*"
        r"(right to work|work (authori[sz]ation|permit|rights?|visa)|authori[sz]ed to work|eligible to work|legally (authori[sz]ed|eligible))",
        r"\b(legally )?(authori[sz]ed|eligible|entitled) to work in [^.;]{1,40}\b(is )?(required|without)\b",
        r"\bright to work in [^.;]{1,40}\b(is )?required\b",
        r"\bvalid work (permit|authori[sz]ation|visa) (is )?required\b",
    ],
    "available": [
        r"\b(visa |immigration )?sponsorship (is |will be |can be )?(available|provided|offered|possible)\b",
        r"\bwe (will|can|do|are able to) sponsor\b",
        r"\b(will|can) (provide|offer|support) (visa |work permit |immigration )?(sponsorship|visa)\b",
        r"\b(visa|immigration|relocation and visa) (support|assistance) (is |will be )?(available|provided|offered)\b",
    ],
    "mention": [r"\b(right to work|work authori[sz]ation|work permit|visa|sponsorship)\b"],
}

_UNPAID = re.compile(r"\b(unpaid|no (salary|compensation|stipend|remuneration)|non[- ]remunere|not paid|voluntary (position|role|basis))\b")
_UNPAID_NEGATED = re.compile(r"\b(no|not|never|don't|do not)\s+(offer\s+|have\s+)?unpaid\b|\bnot an unpaid\b")
_PAID = re.compile(
    r"\b(paid (internship|position|role)|stipend|salary|hourly (rate|wage|pay)|per hour|base pay|pay range|"
    r"compensation (range|package)|gratification|remuneration|remunere|competitive (pay|compensation))\b|"
    r"(\$|€|£|¥)\s?\d|\b\d[\d,.]*\s?(k\s)?(usd|eur|gbp|sgd|chf|aud|cad|hkd|jpy|aed)\b"
)
_COLD_COUNT = [
    re.compile(r"(?P<n>\d{2,3})\s*\+?\s*(?:or more\s+)?(?:outbound\s+|cold\s+|daily\s+|sales\s+|phone\s+|outreach\s+)*"
               r"(?:calls|dials|phone calls)\s*(?:per|a|each|every|/)\s*(?:day|business day|working day|shift)"),
    re.compile(r"(?P<n>\d{2,3})\s*\+?\s*(?:outbound\s+|cold\s+)?(?:calls|dials)\s+(?:daily|a day|per day)"),
    re.compile(r"(?:per day|daily|each day)[^.;]{0,30}?(?P<n>\d{2,3})\s*\+?\s*(?:outbound\s+|cold\s+)?(?:calls|dials)"),
]
_COLD_MENTION = re.compile(r"\bcold[- ]?call\w*|\bcold outreach\b|\bdialing\b")

QUALIFICATIONS = {
    "CPA": r"\bcpa\b", "ACCA": r"\bacca\b", "CFA charter": r"\bcfa charter\w*",
    "FINRA Series license": r"\bseries (7|63|65|79)\b", "Professional Engineer license": r"\b(pe|professional engineer(ing)?) licen[cs]e\b",
    "Bar admission": r"\bbar admission|admitted to the bar\b", "Medical/nursing license": r"\b(nursing|medical) licen[cs]e\b",
    "Driver's license": r"\bdriv(er'?s|ing) licen[cs]e\b",
}

_EMPLOYMENT = [
    ("apprenticeship", re.compile(r"\b(apprentice(ship)?|alternan(ce|t)|contrat de professionnalisation|duales? studium)\b")),
    ("working_student", re.compile(r"\b(working student|werkstudent(in)?)\b")),
    ("volunteer", re.compile(r"\bvolunteer(ing)? (position|role|opportunity)\b|\bbenevol\w*")),
    ("permanent", re.compile(r"\b(permanent (position|role|contract|employment|full[- ]time)|full[- ]time permanent|\bcdi\b|unbefristet)\b")),
    ("graduate_job", re.compile(r"\b(new grad(uate)? (role|position|hire)|graduate (scheme|programme|program) (role|position)|full[- ]time new grad)\b")),
]
_INTERN = re.compile(r"\b(intern(ship)?s?|stagiaire|stage|praktikum|praktikant\w*|co-?op|placement)\b")


def extract_facts(title: str, description: str, high_volume_phrases: list[str] | None = None) -> JobFacts:
    norm_title = normalize_for_match(title)
    norm_desc = normalize_for_match(description)
    norm = f"{norm_title}. {norm_desc}" if norm_desc else norm_title
    facts = JobFacts(title=title, text=description or "")
    facts.years = {int(y) for y in _YEARS.findall(norm) if 2024 <= int(y) <= 2030}

    _extract_periods(norm, facts)
    _extract_duration(norm, facts)
    _extract_languages(norm_desc, facts)
    _extract_graduation(norm, facts)
    _extract_degrees(norm_desc, facts)

    for m in _CITIZEN.finditer(norm):
        facts.citizenship.append(snippet(norm, m.start(), m.end()))
        facts.citizenship_countries.add(_CITIZEN_COUNTRY.get(m.group("who").strip(), "UNKNOWN"))
    for m in _CITIZEN_ONLY.finditer(norm):
        facts.citizenship.append(snippet(norm, m.start(), m.end()))
        if "singapore" in m.group(0):
            facts.citizenship_countries.add("SG")
        elif "u.s" in m.group(0) or "us person" in m.group(0):
            facts.citizenship_countries.add("US")
    for m in _CLEARANCE.finditer(norm):
        facts.clearance.append(snippet(norm, m.start(), m.end()))

    for key, patterns in _SPONSOR_PATTERNS.items():
        for pattern in patterns:
            for m in re.finditer(pattern, norm):
                facts.sponsorship_flags.setdefault(key, []).append(snippet(norm, m.start(), m.end()))

    unpaid = _UNPAID.search(norm)
    if unpaid and not _UNPAID_NEGATED.search(norm):
        facts.compensation, facts.compensation_evidence = "unpaid", snippet(norm, unpaid.start(), unpaid.end())
    else:
        paid = _PAID.search(norm)
        if paid:
            facts.compensation, facts.compensation_evidence = "paid", snippet(norm, paid.start(), paid.end())

    for regex in _COLD_COUNT:
        m = regex.search(norm)
        if m:
            facts.cold_calls_per_day = int(m.group("n"))
            facts.cold_call_evidence = snippet(norm, m.start(), m.end())
            break
    if _COLD_MENTION.search(norm):
        facts.cold_calling_mentioned = True
        if not facts.cold_call_evidence:
            mm = _COLD_MENTION.search(norm)
            facts.cold_call_evidence = snippet(norm, mm.start(), mm.end())
    for phrase in high_volume_phrases or []:
        if phrase.lower() in norm:
            facts.high_volume_calling = True
            facts.cold_call_evidence = facts.cold_call_evidence or phrase

    for name, rx in QUALIFICATIONS.items():
        for _, _, sentence in split_sentences(norm_desc):
            if re.search(rx, sentence):
                mandatory = bool(re.search(r"\b(must|required|requires|mandatory|need to hold|valid)\b", sentence)) \
                    and not _OPTIONAL_CUE.search(sentence)
                facts.qualifications.append((name, mandatory, sentence[:300]))
                break

    title_hits = [label for label, rx in _EMPLOYMENT if rx.search(norm_title)]
    if title_hits:
        facts.employment, facts.employment_evidence = title_hits[0], title
        if _INTERN.search(norm_title):
            facts.employment = "mixed"
    elif _INTERN.search(norm_title):
        facts.employment, facts.employment_evidence = "internship", title
        for label, rx in _EMPLOYMENT:
            m = rx.search(norm_desc)
            if m and label in {"permanent", "apprenticeship"}:
                facts.employment = "mixed"
                facts.employment_evidence = snippet(norm_desc, m.start(), m.end())
                break
    else:
        for label, rx in _EMPLOYMENT:
            m = rx.search(norm_desc)
            if m:
                facts.employment, facts.employment_evidence = label, snippet(norm_desc, m.start(), m.end())
                break
        else:
            m = _INTERN.search(norm_desc)
            if m:
                facts.employment, facts.employment_evidence = "internship_in_text", snippet(norm_desc, m.start(), m.end())
    return facts
