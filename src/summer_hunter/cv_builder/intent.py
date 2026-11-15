"""Request analysis for the AI CV Builder: what CV does this free text ask for?

The text can be a full posting, a job title, a company, a sector or a free request
("CV pour un stage Robotics Engineer chez une startup à Singapour"). The analysis is
deterministic (no LLM call, no cost) and reuses the hunter's own engines: title families
(TitleRules), mission classification (semantic), requirement extraction (requirements) and
country detection (geo).

Integrity rules:
  - requirements exist only when a posting is supplied; without one, `expected_skills` are what
    the role usually involves, labelled as such, never presented as an employer's demand;
  - a requirement is "covered" only by a verified or candidate-confirmed skill; a skill awaiting
    confirmation in the profile is "to_confirm", never covered;
  - nothing from the request is ever written to the profile.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from summer_hunter.engine import requirements as reqs
from summer_hunter.engine import semantic
from summer_hunter.engine.geo import country_name, find_countries_in_text
from summer_hunter.engine.normalization import TitleRules
from summer_hunter.utils.text import normalize_for_match

OFFER, SPONTANEOUS, TARGET = "offer", "spontaneous", "target"
MAX_QUESTIONS = 3

# French country names geo.py does not carry (it reads postings, mostly in English).
_FR_COUNTRIES = {
    "singapour": "SG", "allemagne": "DE", "royaume uni": "GB", "angleterre": "GB", "londres": "GB", "etats unis": "US",
    "japon": "JP", "chine": "CN", "coree": "KR", "espagne": "ES", "italie": "IT", "pays bas": "NL", "belgique": "BE",
    "suisse": "CH", "suede": "SE", "norvege": "NO", "danemark": "DK", "irlande": "IE", "autriche": "AT", "pologne": "PL",
    "canada": "CA", "australie": "AU", "emirats": "AE", "dubai": "AE", "maroc": "MA", "tunisie": "TN", "egypte": "EG",
    "afrique du sud": "ZA", "bresil": "BR", "mexique": "MX", "hong kong": "HK", "taiwan": "TW", "luxembourg": "LU",
    "portugal": "PT", "finlande": "FI", "republique tcheque": "CZ", "grece": "GR", "inde": "IN",
}
_FRANCOPHONE = {"FR", "BE", "CH", "LU", "MA", "TN", "SN", "CI", "CA"}
_COMPANY_ALIASES = {"aws": "Amazon Web Services", "google cloud": "Google", "gcp": "Google", "azure": "Microsoft",
                    "hpe": "Hewlett Packard Enterprise", "ge aerospace": "GE Aerospace", "afi": "Air France Industries"}
# Known names that are also ordinary words: matched only with their capital letter.
_AMBIGUOUS_NAMES = {"notion", "visa", "wise", "elastic", "scale ai", "intel", "oracle", "box", "square"}

SECTORS = {   # sector id -> cues in the request or posting
    "saas": ["saas", "software as a service", "b2b software"],
    "cloud": ["cloud", "aws", "azure", "gcp", "google cloud", "data center"],
    "ai": ["ai", "artificial intelligence", "intelligence artificielle", "machine learning", "genai", "llm", "ia"],
    "robotics": ["robotics", "robotique", "robot", "cobot", "automation", "automatisation"],
    "aerospace": ["aerospace", "aeronautique", "aviation", "aircraft", "mro", "airline", "spatial", "space"],
    "industry": ["industry", "industrie", "industrial", "industriel", "manufacturing", "usine", "factory", "production"],
    "healthcare": ["healthcare", "sante", "medical", "medtech", "medtech", "hospital", "dental", "dentaire"],
    "fintech": ["fintech", "bank", "banque", "payments", "paiement", "finance"],
    "consulting": ["consulting", "conseil", "consultant", "strategy", "strategie"],
    "energy": ["energy", "energie", "renewable", "grid", "utilities"],
    "startup": ["startup", "start up", "scale up", "scaleup"],
}

# Short labels for the families the hunter knows; used when the request names no exact title.
FAMILY_LABEL = {
    "technical_sales": "Technical Sales / Sales Engineering", "enterprise_sales": "Enterprise Sales",
    "go_to_market": "Business Development / Go-to-Market", "sales_development": "Sales Development",
    "sales_general": "Sales", "product_marketing": "Product Marketing", "product_management": "Product Management",
    "ai_solutions": "AI Solutions", "program_management": "Program Management",
    "commercial_strategy": "Commercial Strategy", "strategy_consulting": "Strategy Consulting",
    "maintenance_reliability": "Maintenance & Reliability Engineering",
    "industrial_project_management": "Industrial Project Management", "industrial_operations": "Industrial Operations",
    "industrial_digital": "Digital Transformation / Industry 4.0", "robotics_raas": "Robotics Deployment",
    "robotics_specialized": "Robotics Engineering", "design_engineering": "Design Engineering",
}
# Requests the title rules miss ("transformation digitale", "cloud") mapped to the closest family.
_EXTRA_FAMILY_CUES = [
    ("industrial_digital", r"transformation (digitale|numerique)|digital transformation|industrie 4 0|industry 4 0|digitali[sz]ation"),
    ("robotics_specialized", r"robotics? engineer|ingenieur robotique|robotique"),
    ("go_to_market", r"business develop|developpement commercial|partenariats?|partnerships?"),
    ("technical_sales", r"tech(nical)? sales|avant vente|presales|pre sales|sales engineer|solutions? engineer"),
    ("ai_solutions", r"\bai\b.*\b(solutions?|deployment|engineer)|intelligence artificielle"),
    ("maintenance_reliability", r"maintenance|fiabilite|reliability"),
    ("industrial_project_management", r"(chef|gestion) de projet|project manag"),
    ("strategy_consulting", r"conseil en strategie|strategy consult|consulting"),
]

_ROLE_PATTERNS = [
    # "CV pour un stage Robotics Engineer chez …", "CV for a Business Development internship at …"
    re.compile(r"(?:cv|resume|curriculum)\s+(?:pour|for|de)\s+(?:(?:un|une|a|an|le|la|du)\s+)?(?:(?:stage|internship|alternance|poste|job|role)\s+)?"
               r"(?:(?:en|in|de|as|comme)\s+|d')?(?P<role>.+?)(?=\s+(?:chez|at|dans|in|à|a|au|aux|pour|for|en|with)\b|[.,;!?]|$)", re.I),
    re.compile(r"(?:stage|internship|poste|role|job)\s+(?:(?:en|in|de|of|as|comme)\s+|d')?(?P<role>.+?)(?=\s+(?:chez|at|dans|in|à|au|aux|pour|for|with)\b|[.,;!?]|$)", re.I),
    re.compile(r"(?:cherche|looking for|vise|target)\s+(?:(?:un|une|a|an)\s+)?(?:(?:stage|internship|poste|role)\s+)?(?:(?:en|in|de|as)\s+)?(?P<role>.+?)(?=\s+(?:chez|at|dans|in|à|au|pour|for)\b|[.,;!?]|$)", re.I),
]
_FILLER_ROLE = re.compile(r"^(?:un|une|a|an|le|la|stage|internship|cv|poste)\s+", re.I)
_NOT_A_ROLE = re.compile(r"^(?:candidature spontan\w*|spontaneous application|cv|stage|internship|job|poste|role)$", re.I)
# Spontaneous application without a stated role: the function the candidate targets in that kind of company.
SECTOR_DEFAULT_FAMILY = {"enterprise_saas": "technical_sales", "enterprise_tech": "technical_sales", "cloud": "technical_sales",
                         "ai": "ai_solutions", "telecom": "technical_sales", "fintech": "go_to_market", "medtech": "technical_sales",
                         "industrial_tech": "industrial_digital", "aerospace_mro": "maintenance_reliability",
                         "logistics": "industrial_operations", "robotics": "robotics_raas"}
_COMPANY_AFTER = re.compile(r"\b(?:chez|at|for|pour|aupr[eè]s de|candidature spontan[ée]e (?:chez|pour|à))\s+(?P<name>[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})")
# A request written by the candidate ("Génère-moi un CV…", "CV pour…"), as opposed to a pasted posting.
_REQUEST_START = re.compile(r"^\W*(?:g[ée]n[èe]re|fais|pr[ée]pare|cr[ée]e|r[ée]dige|je (?:veux|voudrais|cherche|souhaite)|j'aimerais|"
                            r"j'ai besoin|peux-tu|pourrais-tu|write|generate|make|create|prepare|build|i (?:need|want|would like)|"
                            r"could you|can you|cv|resume|candidature|application)\b", re.I)
_REQUEST_CUE = re.compile(r"\b(je postule|je candidate|je souhaite (?:postuler|candidater|une candidature)|ma candidature|"
                          r"my application|i am applying|i'm applying|g[ée]n[èe]re[- ]moi|fais[- ]moi|pr[ée]pare[- ]moi|"
                          r"r[ée]dige[- ]moi|mon cv|my cv|my resume)\b", re.I)
_NOT_OFFER = re.compile(r"candidature spontan[ée]e|spontaneous application|ne s.agit pas d.une (?:candidature [àa] une )?offre|"
                        r"not (?:a|an) (?:specific )?(?:job )?(?:posting|offer|opening)|sans offre|no (?:job )?posting", re.I)
_OFFER_INTRO = re.compile(r"\b(?:voici (?:une|l['’]|cette) offre|here is the (?:job )?(?:offer|posting|description)|offre\s*:|"
                          r"job (?:posting|description)\s*:)", re.I)
_NAME = r"[A-Z0-9][\w&.'’-]*(?:\s+(?:(?:of|in|for|and|de|du|des|et|&|the|von|und)\s+)?[A-Z0-9][\w&.'’-]*){0,7}"
# The recipient named in the request itself: "candidature spontanée à la French Chamber of Commerce in Singapore (FCCS)".
_STRONG_CUE = r"candidature(?:\s+spontan[ée]e)?|spontaneous application|application|postuler|je postule|je candidate|apply(?:ing)?|destin[ée]e?|addressed"
_TARGET = re.compile(r"(?i:(?P<cue>" + _STRONG_CUE + r"|\bcv\b|resume))"
                     r"\s+(?:(?:[\wéèà'’-]+\s+){0,3}?)(?i:aupr[eè]s (?:de|du|des)|à|a|au|aux|chez|pour|to|at|with|for)\s+"
                     r"(?i:la |le |l['’]|les |the )?(?P<name>" + _NAME + r")(?:\s*\((?P<alias>[A-Z][\w&.-]{1,15})\))?")
_GENERIC_NAMES = {"une", "un", "a", "an", "the", "la", "le", "stage", "internship", "startup", "cv", "entreprise", "company",
                  "candidature", "application", "job", "poste"}
_ROLE_FIELD = re.compile(r"^\s*(?:titre du cv|intitul[ée]|poste (?:vis[ée]|cibl[ée])|r[ôo]le (?:vis[ée]|cibl[ée])|target (?:role|title)|"
                         r"job title|cv title)\s*:\s*(?P<role>.*)$", re.I | re.M)
_EMPHASIS_HEADING = re.compile(r"(?:exp[ée]riences?|projets?) [àa] (?:valoriser|mettre en avant)|highlight|to emphasi[sz]e", re.I)
# "chez X" / "auprès de X" without an application word: a recipient unless the words before are about the past.
_RECIPIENT_PREP = re.compile(r"\b(?:chez|aupr[eè]s (?:de|du|des))\s+(?:la |le |l['’]|les )?(?P<name>" + _NAME + r")")
_PAST_BEFORE = re.compile(r"(parcours|exp[ée]rience|apprenti\w*|alternance|stage (?:effectu|r[ée]alis)|travaill\w*|"
                          r"worked|experience|employ\w*|j'ai (?:fait|effectu|men)|i (?:did|worked|interned))[^.\n]{0,40}$", re.I)
_PAST_ANY = re.compile(_PAST_BEFORE.pattern.removesuffix("[^.\\n]{0,40}$"), re.I)
_LABEL_LINE = re.compile(r"^\s*(?:company|entreprise|employer|employeur|soci[ée]t[ée]|organi[sz]ation)\s*[:\-–]\s*(?P<name>.+?)\s*$",
                         re.I | re.M)
_NOT_EMPLOYER_LINE = re.compile(r"partner|partenaire|ecosyst|tools?\b|outils?|stack|clients?\b|customers?|using|technolog|"
                                r"software|logiciel|certif", re.I)
_INTRO = re.compile(r"(?:\babout\b|à propos d[e'’]|\bjoin\b|rejoign\w*|\bat\b|\bchez\b)\s*(?:us\b\s*[:\-–]?\s*)?"
                    r"(?:the |la |le |l['’])?(?P<name>" + _NAME + r")")
_EXPERIENCE_CUE = re.compile(r"\b(mon exp[ée]rience|valoris\w*|ancien\w*|previous|my experience|where i|o[uù] j'ai)\b", re.I)
_PAGES = re.compile(r"\b(?P<n>[12]|une|un|one|two|deux)\s+pages?\b", re.I)
_LANG_FR = re.compile(r"\b(en fran[cç]ais|in french|version fran[cç]aise|cv fran[cç]ais)\b", re.I)
_LANG_EN = re.compile(r"\b(en anglais|in english|english version|version anglaise|cv anglais)\b", re.I)
_EMPHASIS = re.compile(r"(?:mets?|mettre|met|put|highlight|insiste|souligne)\s+(?:davantage|plus|more)?\s*(?:en avant|l'accent sur|the focus on|emphasis on)?\s*"
                       r"(?:sur\s+)?(?P<what>[^.;!?\n]{3,80})", re.I)
_SENIORITY = [("internship", r"\b(stage|stagiaire|intern(ship)?|working student|werkstudent|placement|end of studies|fin d.etudes)\b"),
              ("apprenticeship", r"\b(alternance|apprenti\w*|apprenticeship)\b"),
              ("graduate", r"\b(graduate|new grad|jeune dipl[oô]m\w*|vie\b|v\.i\.e)"),
              ("junior", r"\bjunior\b"), ("senior", r"\b(senior|lead|principal|staff)\b")]
_FRENCH_WORDS = re.compile(r"\b(le|la|les|des|une|vous|nous|pour|avec|dans|sur|est|et|du|au|aux|votre|vos|missions?|profil)\b")
_GERMAN_WORDS = re.compile(r"\b(und|der|die|das|mit|f[üu]r|wir|ihre?|bei|eine?n?|auf|ist|sind|zu|von|praktikum|aufgaben)\b")
_ENGLISH_WORDS = re.compile(r"\b(the|and|you|we|with|for|our|your|will|are|is|of|to|in|role|team|experience)\b")
_REQUIREMENT_HEADINGS = re.compile(r"(requirements|qualifications|what you.ll do|responsibilities|your profile|about the role|"
                                   r"missions|profil recherch|vos missions|ce que vous ferez|who you are|nice to have|must have)", re.I)


@dataclass
class Requirement:
    name: str
    level: str            # mandatory | preferred | exposure
    coverage: str         # covered | partial | to_confirm | missing
    evidence: str = ""


@dataclass
class CVRequest:
    source_text: str
    mode: str                                   # offer | spontaneous | target
    target_role: str | None
    family: str
    role_source: str                            # stated | offer | inferred (family label, the request named no title)
    track: str                                  # business | product | industry
    sector: list[str] = field(default_factory=list)
    company: str | None = None
    company_aliases: list[str] = field(default_factory=list)  # "FCCS" for the French Chamber of Commerce in Singapore
    company_source: str | None = None                         # explicit | header | first_sentence | posting_body
    country_code: str | None = None
    language: str = "en"
    language_reason: str = "default (international)"
    seniority: str = "internship"
    pages: int = 1
    style: str | None = None
    emphasis: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)          # "retire X": items left out of this CV only
    personalization: str = "standard"                         # standard | light (no targeted sentence)
    requirements: list[Requirement] = field(default_factory=list)
    expected_skills: list[str] = field(default_factory=list)   # usual for the role, NOT an employer's demand
    keywords: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    @property
    def location(self) -> str:
        return country_name(self.country_code) if self.country_code else "Not specified"

    def by_coverage(self, coverage: str) -> list[Requirement]:
        return [r for r in self.requirements if r.coverage == coverage]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CVRequest:
        known = {f for f in cls.__dataclass_fields__}
        fields = {k: v for k, v in data.items() if k in known}
        fields["requirements"] = [Requirement(**r) for r in data.get("requirements", [])]
        fields.setdefault("role_source", "stated")
        return cls(**fields)


@dataclass
class AnalyzerContext:
    title_rules: TitleRules
    scoring: dict[str, Any]
    cv_content: dict[str, Any]
    verified_skills: list[str]                  # VERIFIED + USER_CONFIRMED skill names
    pending_skills: list[str] = field(default_factory=list)   # INFERRED profile facts (to confirm)
    partial_skills: list[str] = field(default_factory=list)   # held at a limited level ("SQL (Academic Coursework)")
    companies: dict[str, str] = field(default_factory=dict)   # name -> sector (target_companies.yaml)
    own_orgs: list[str] = field(default_factory=list)          # the candidate's employers, schools, projects (never a default target)


def is_request(text: str) -> bool:
    """Written by the candidate to ask for a CV (however long), not a pasted posting."""
    head = text.strip()[:400]
    if _OFFER_INTRO.search(head):
        return False                                   # "Voici une offre : …" introduces a posting
    return (bool(_REQUEST_START.search(head)) or bool(_REQUEST_CUE.search(head))
            or bool(_NOT_OFFER.search(text[:1500])))


def is_offer(text: str) -> bool:
    """A pasted posting: introduced as one, or shaped like one (sections, many lines). Length alone never decides."""
    if _OFFER_INTRO.search(text.strip()[:400]):
        return True
    if is_request(text):
        return False
    lines = [l for l in text.splitlines() if l.strip()]
    # Not written as a request: a long text, or a shorter one with posting structure, is a posting.
    return len(text) > 600 or (len(text) > 250 and (bool(_REQUIREMENT_HEADINGS.search(text)) or len(lines) >= 8))


def is_place(name: str) -> bool:
    """Exactly a country or city ('Singapore', 'Londres'), not an organisation containing one ('Singapore Airlines')."""
    from summer_hunter.engine.geo import COUNTRIES

    n = normalize_for_match(name)
    return n in _FR_COUNTRIES or any(n == normalize_for_match(a) for aliases, cities in COUNTRIES.values()
                                     for a in [*aliases, *cities])


def _is_own(name: str, own_orgs: list[str]) -> bool:
    """True when `name` is (part of) one of the candidate's organisations: 'Airbus' vs 'Airbus Helicopters'."""
    n = normalize_for_match(name)
    for org in own_orgs:
        o = normalize_for_match(org)
        if n and o and (n == o or o.startswith(n + " ") or n.startswith(o + " ")):
            return True
    return False


def _strip_location(name: str) -> str:
    """'AWS Singapore' -> 'AWS'; 'French Chamber of Commerce in Singapore' stays whole (official name)."""
    words = name.split()
    for cut in range(len(words) - 1, 0, -1):
        tail = " ".join(words[cut:])
        only_place = len(words) - cut <= 3 and all(w[:1].isupper() for w in words[cut:])   # 'Singapore', 'Hong Kong'
        if only_place and detect_countries(tail) and normalize_for_match(words[cut - 1]) not in {"in", "of", "de", "du", "for"}:
            return " ".join(words[:cut])
    return name


def _clean_name(raw: str) -> str | None:
    name = _strip_location(raw.strip(" .,;:"))
    flat = normalize_for_match(name)
    if not flat or flat in _GENERIC_NAMES or is_place(name):
        return None
    return _COMPANY_ALIASES.get(flat, name)


def explicit_target(text: str) -> tuple[str | None, list[str]]:
    """The organisation the request applies to, read only from recipient wording anywhere in the request:
    'candidature (spontanée) à/chez/auprès de X', 'je postule chez X', 'CV pour X', 'chez X' (not after past-tense
    words such as 'parcours', 'apprentissage'). A name merely mentioned is never a recipient."""
    request = text.strip()[:3000]
    matches = []
    for m in _TARGET.finditer(request):
        name = _clean_name(m.group("name"))
        if name:
            strong = not re.fullmatch(r"(?i)cv|resume", m.group("cue"))
            matches.append((0 if strong else 1, m.start(), name, [m.group("alias")] if m.group("alias") else []))
    if not matches:
        for m in _RECIPIENT_PREP.finditer(request):
            before = request[max(0, m.start() - 60):m.start()]
            name = _clean_name(m.group("name"))
            if name and not _PAST_BEFORE.search(before):
                matches.append((2, m.start(), name, []))
    if not matches:
        return None, []
    _, _, name, alias = min(matches, key=lambda x: (x[0], x[1]))
    return name, alias


def stated_role(text: str) -> str | None:
    """'Titre du CV : …' / 'Target role: …' (value on the same line or the next one)."""
    m = _ROLE_FIELD.search(text)
    if not m:
        return None
    value = m.group("role").strip()
    if not value:
        rest = text[m.end():].lstrip("\n").splitlines()
        value = rest[0].strip() if rest else ""
    value = value.strip(" -*•:\"'«»")
    return value if 2 <= len(value) <= 90 else None


def requested_emphasis(text: str, own_orgs: list[str]) -> list[str]:
    """Organisations listed under 'Expériences à valoriser…', in the order given."""
    m = _EMPHASIS_HEADING.search(text)
    if not m:
        return []
    section = text[m.end():].split("\n\n\n")[0][:1500]
    section = re.split(r"\n\s*\n(?=[A-ZÀ-Ý][^\n]{0,40}:)", section)[0]
    flat = re.sub(r"['’]", " ", normalize_for_match(section.replace("’", "'")))
    found: dict[int, str] = {}
    for org in own_orgs:
        short = org.split(" -- ")[0]
        key = re.sub(r"['’]", " ", normalize_for_match(short.replace("’", "'")))
        pos = flat.find(key)
        if pos >= 0 and len(short) > len(found.get(pos, "")):
            found[pos] = short                        # 'Air France Industries' wins over 'Air France'
    return [found[p] for p in sorted(found)][:3]


def detect_countries(text: str) -> list[str]:
    found = list(find_countries_in_text(text))
    norm = normalize_for_match(text)
    for name, code in _FR_COUNTRIES.items():
        if re.search(rf"(?<![a-z]){name}(?![a-z])", norm) and code not in found:
            found.append(code)
    return found


def detect_company(text: str, companies: dict[str, str] | list[str], exclude: list[str] | None = None) -> str | None:
    """A known organisation named in `text`. Names in `exclude` (the candidate's own employers) never count."""
    norm = normalize_for_match(text)
    exclude = exclude or []
    for alias, name in _COMPANY_ALIASES.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", norm) and not _is_own(name, exclude):
            return name
    for name in sorted(companies, key=len, reverse=True):
        if _is_own(name, exclude):
            continue
        flat = normalize_for_match(name)
        if flat in _AMBIGUOUS_NAMES or len(flat) <= 3:
            if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text):     # case-sensitive
                return name
        elif re.search(rf"(?<![a-z0-9]){re.escape(flat)}(?![a-z0-9])", norm):
            return name
    m = _COMPANY_AFTER.search(text)
    if m:
        name = m.group("name").strip(" .")
        if normalize_for_match(name) not in _GENERIC_NAMES and not detect_countries(name) and not _is_own(name, exclude):
            return name
    return None


def detect_role(text: str, drop: tuple[str, ...] = ()) -> str | None:
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    for pattern in _ROLE_PATTERNS:
        m = pattern.search(first)
        if m:
            role = _FILLER_ROLE.sub("", m.group("role").strip(" :-\"'«»"))
            role = re.sub(r"\s+(?:dans|in)\s+(?:une?|a|an|the|l')\s+.*$", "", role, flags=re.I).strip()
            for word in drop:     # the company or country is not part of the job title ("CV pour AWS Singapore")
                role = re.sub(rf"(?i)(?<![\w]){re.escape(word)}(?![\w])", "", role)
            role = " ".join(role.split()).strip(" -,")
            if 2 <= len(role) <= 80 and not _NOT_A_ROLE.match(role) and not re.fullmatch(r"(?:un|une|a|an)", role, re.I):
                return role
    return None


def offer_title(text: str) -> str | None:
    """First short line of a pasted posting that reads like a title."""
    for line in text.strip().splitlines()[:6]:
        line = line.strip(" #*-:\t")
        if 4 <= len(line) <= 90 and not line.endswith(".") and not re.match(r"(?:about|à propos|description|company|entreprise)\b", line, re.I):
            return line
    return None


def _header_location(head: str) -> list[str]:
    from summer_hunter.engine.geo import resolve_location

    for line in head.splitlines():
        for part in re.split(r"\s[-–|·]\s|,", line):
            loc = resolve_location(part.strip())
            if loc.primary_country:
                return [loc.primary_country]
    return []


def detect_language(text: str, country: str | None, offer: bool) -> tuple[str, str]:
    if _LANG_FR.search(text):
        return "fr", "demandé"
    if _LANG_EN.search(text):
        return "en", "demandé"
    if offer:
        low = text.lower()
        fr, en, de = len(_FRENCH_WORDS.findall(low)), len(_ENGLISH_WORDS.findall(low)), len(_GERMAN_WORDS.findall(low))
        if de > max(fr, en):
            return "en", "offre en allemand : CV en anglais (français et anglais seulement)"
        if fr > en * 1.3:
            return "fr", "offre rédigée en français"
        return "en", "offre rédigée en anglais"
    if country in _FRANCOPHONE:
        return "fr", f"pays francophone ({country_name(country)})"
    return "en", "candidature internationale"


def detect_pages(text: str) -> int | None:
    m = _PAGES.search(text)
    if not m:
        return None
    return 2 if m.group("n").lower() in {"2", "two", "deux"} else 1


def detect_style(text: str) -> str | None:
    low = normalize_for_match(text)
    if re.search(r"\b(consulting|conseil|business style|style business)\b.*\b(style|cv|template|format)\b|\bstyle (consulting|business)\b", low):
        return "consulting"
    if re.search(r"\b(modern|moderne)\b", low):
        return "modern"
    if re.search(r"\b(ats classic|classique|ats)\b", low):
        return "ats_classic"
    return None


def detect_sectors(text: str) -> list[str]:
    norm = normalize_for_match(text)
    return [sid for sid, cues in SECTORS.items()
            if any(re.search(rf"(?<![a-z0-9]){re.escape(normalize_for_match(c))}(?![a-z0-9])", norm) for c in cues)]


def detect_seniority(text: str) -> str:
    norm = normalize_for_match(text)
    for level, pattern in _SENIORITY:
        if re.search(pattern, norm):
            return level
    return "internship"           # the candidate's search: April-August 2027 internship


def classify_family(ctx: AnalyzerContext, role: str | None, text: str, offer: bool) -> str:
    fam = ctx.title_rules.classify(role or "").id if role else ctx.title_rules.default.id
    if offer:
        sem = semantic.classify(role or "", text, ctx.scoring, fam)
        if sem.applied and sem.family:
            fam = sem.family
    if fam in {ctx.title_rules.default.id, "out_of_scope", "marketing_general"}:
        norm = normalize_for_match(f"{role or ''} {text if not offer else ''}")
        for family, pattern in _EXTRA_FAMILY_CUES:
            if re.search(pattern, norm):
                return family
        if not role:
            whole = ctx.title_rules.classify(text[:300]).id
            if whole != ctx.title_rules.default.id:
                return whole
    return fam


def _skill_held(name: str, skills: list[str]) -> bool:
    n = normalize_for_match(name)
    return any(n == normalize_for_match(s) or re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", normalize_for_match(s))
               for s in skills)


def analyze(text: str, ctx: AnalyzerContext) -> CVRequest:
    from summer_hunter.web.cv import FAMILY_KEYWORDS, track_for

    text = (text or "").strip()
    offer = is_offer(text)
    head = "\n".join(text.splitlines()[:3])
    company, aliases, source = target_company(text, offer, ctx)
    if offer:   # the header states the location; the body often cites HQ or other offices, so only a lone country counts
        body = detect_countries(text[:3000])
        countries = detect_countries(head) or _header_location(head) or (body if len(body) == 1 else [])
    else:
        countries = detect_countries(text)
    country = countries[0] if countries else None
    drop = tuple(w for w in [company or ""] + [a for a, n in _COMPANY_ALIASES.items() if n == company]
                 + [n for n, c in _FR_COUNTRIES.items() if c == country] + [country_name(country) if country else ""] if w)
    role = offer_title(text) if offer else (stated_role(text) or detect_role(text, drop))
    family = classify_family(ctx, role, text, offer)
    if company and not role and family == ctx.title_rules.default.id:
        family = SECTOR_DEFAULT_FAMILY.get(ctx.companies.get(company, "") if isinstance(ctx.companies, dict) else "",
                                           "technical_sales")
    role_source = "offer" if offer and role else "stated" if role else "inferred"
    if not role and family in FAMILY_LABEL:
        role = FAMILY_LABEL[family]
    mode = OFFER if offer else (SPONTANEOUS if company else TARGET)
    language, why = detect_language(text, country, offer)
    req = CVRequest(source_text=text, mode=mode, target_role=role, family=family, role_source=role_source,
                    track=(track_for(ctx.cv_content, family, text) if offer
                           else "industry" if family in set(ctx.cv_content.get("industry_families", []))
                           else track_for(ctx.cv_content, family, f"{role or ''} {text}")),
                    sector=detect_sectors(text if not offer else text[:3000]), company=company,
                    company_aliases=aliases, company_source=source, country_code=country,
                    language=language, language_reason=why, seniority=detect_seniority(text),
                    pages=detect_pages(text) or 1, style=detect_style(text) if not offer else None,
                    emphasis=(requested_emphasis(text, ctx.own_orgs)
                              or [m.group("what").strip() for m in _EMPHASIS.finditer(text)][:3]) if not offer else [])
    lexicon = (ctx.scoring.get("recruitability") or {}).get("skills") or {}
    if offer and lexicon:
        for r in reqs.extract(text, lexicon, ctx.verified_skills):
            coverage = ("covered" if r.verified or _skill_held(r.name, ctx.verified_skills)
                        else "partial" if _skill_held(r.name, ctx.partial_skills)
                        else "to_confirm" if _skill_held(r.name, ctx.pending_skills) else "missing")
            req.requirements.append(Requirement(r.name, r.level, coverage, r.evidence[:200]))
        rank = {"mandatory": 0, "preferred": 1, "exposure": 2}
        req.requirements.sort(key=lambda r: (rank[r.level], r.name))
    family_words = FAMILY_KEYWORDS.get(family, "").split()
    if not offer:
        req.expected_skills = family_words[:8]
    norm = normalize_for_match(text)
    keywords = [r.name for r in req.requirements if r.level != "exposure"]
    keywords += [w for w in family_words if (not offer or re.search(rf"(?<![a-z]){w}", norm)) and w not in keywords]
    req.keywords = keywords[:15]
    req.questions = questions_for(req)
    return req


def target_company(text: str, offer: bool, ctx: AnalyzerContext) -> tuple[str | None, list[str], str | None]:
    """The recipient organisation, from the current text only, with the evidence it came from.

    Free request: recipient wording only (explicit_target); a short request that is just a name ('AWS',
    'Airbus Business Development à Singapour') may name it directly. Posting: a labelled employer line, then the
    header lines that are not about partners or tools, then 'About X' / 'Join X'. Never a name found elsewhere,
    and never a choice between several candidates: then no target, and the analysis asks.
    """
    if not offer:
        name, aliases = explicit_target(text)
        if name:
            return name, _vet_aliases(aliases, ctx), "explicit"
        one_line = text.strip()
        if len(one_line) <= 100 and "\n" not in one_line and not _EXPERIENCE_CUE.search(one_line):
            past = _PAST_ANY.search(one_line)          # 'j'ai fait mon apprentissage chez Airbus…': not a recipient
            name = detect_company(one_line, ctx.companies, ctx.own_orgs if past else None)
            if name:
                return name, [], "short_request"
        return None, [], None
    m = _LABEL_LINE.search("\n".join(text.splitlines()[:15]))
    if m and (name := _clean_name(m.group("name"))):
        return name, [], "posting_label"
    found = []
    for line in text.splitlines()[:3]:
        if _NOT_EMPLOYER_LINE.search(line):
            continue
        for name in sorted(ctx.companies, key=len, reverse=True):
            if _mentions_name(line, name) and not any(normalize_for_match(name) in normalize_for_match(f) for f in found):
                found.append(name)
    if len(found) == 1:
        return found[0], [], "posting_header"
    if len(found) > 1:
        return None, [], None                      # ambiguous header: ask instead of guessing
    for m in _INTRO.finditer(text[:3000]):
        name = m.group("name")
        known = next((c for c in sorted(ctx.companies, key=len, reverse=True) if _mentions_name(name, c)), None)
        if known:
            return known, [], "posting_intro"
    for line in text.splitlines()[:40]:          # "About the employer" heading, then "Airbus builds…"
        if re.match(r"(?i)\s*(about|à propos)\b.{0,30}$", line):
            nxt = text[text.index(line) + len(line):].lstrip().split("\n")[0][:80]
            known = next((c for c in sorted(ctx.companies, key=len, reverse=True) if _mentions_name(nxt[:40], c)), None)
            if known:
                return known, [], "posting_intro"
    return None, [], None


def _mentions_name(text: str, name: str) -> bool:
    flat = normalize_for_match(name)
    if flat in _AMBIGUOUS_NAMES or len(flat) <= 3:
        return re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text) is not None
    return re.search(rf"(?<![a-z0-9]){re.escape(flat)}(?![a-z0-9])", normalize_for_match(text)) is not None


def _vet_aliases(aliases: list[str], ctx: AnalyzerContext) -> list[str]:
    """An acronym in brackets is kept only if it is not another organisation's name ('Amazon Robotics (SAP)')."""
    taken = {normalize_for_match(n) for n in [*ctx.companies, *ctx.own_orgs, *_COMPANY_ALIASES, *_COMPANY_ALIASES.values()]}
    return [a for a in aliases if normalize_for_match(a) not in taken]


def questions_for(req: CVRequest) -> list[str]:
    """At most three targeted questions, only when the answer would change the CV."""
    out: list[str] = []
    if req.mode == "offer" and not req.company:
        out.append("Quelle entreprise publie cette offre ? (je n'en choisis jamais une au hasard dans le texte)")
    if not req.target_role and req.family == "unclassified":
        out.append("Quel poste vises-tu (intitulé ou domaine) ?")
    for r in req.by_coverage("to_confirm"):
        if r.level == "mandatory":
            out.append(f"L'offre exige {r.name}, qui attend ta confirmation dans ton profil : le confirmes-tu (/cv_profile) ?")
    return out[:MAX_QUESTIONS]


def context_from(settings, session) -> AnalyzerContext:  # noqa: ANN001
    """Build the analyzer context from the live config and profile store."""
    from summer_hunter.cv_builder.profile import INFERRED, ProfileStore
    from summer_hunter.utils.settings import load_yaml
    from summer_hunter.web.cv import load_content

    store = ProfileStore(session)
    skills = settings.profile_raw.get("skills") or {}
    verified = list(skills.get("verified") or []) + list(skills.get("user_confirmed") or [])
    cv = load_content(settings.config_dir)
    partial: list[str] = []
    for group in (cv.get("skills") or {}).values():
        for s in group:
            if isinstance(s, str):
                (partial if "academic" in s.lower() or "beginner" in s.lower() else verified).append(s)
    partial += [s for s in verified if "academic" in s.lower()]
    verified = [s for s in verified if "academic" not in s.lower()]
    verified += [f.text for f in store.confirmed("skill")]
    pending = [f.text for f in store.facts(INFERRED) if f.kind == "skill"]
    try:
        companies = {c["name"]: c.get("sector", "") for c in load_yaml(settings.config_dir / "target_companies.yaml").get("companies", [])}
    except FileNotFoundError:
        companies = {}
    own = [str(e.get("org", "")).split(" -- ")[0] for e in cv.get("experiences", []) or []]
    own += [str(e.get("school", "")).split(" -- ")[0] for e in cv.get("education", []) or []]
    own += [str(p.get("name", "")).split(" -- ")[0] for p in cv.get("projects", []) or []]
    own += ["Air France Industries", "Air France", "KLM Engineering & Maintenance"]
    return AnalyzerContext(TitleRules(settings.preferences, settings.scoring), settings.scoring, cv, verified, pending, partial,
                           companies, [o for o in dict.fromkeys(own) if o])
