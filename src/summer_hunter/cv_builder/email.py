"""Cover email to send with a CV (spontaneous application or posting), drafted, never sent.

Deterministic and instant (no LLM): the connecting sentences are fixed, every fact comes from the
CV version it accompanies (the tailored, verified content) and from the profile's availability.
English emails quote the CV's own verified bullets; French ones never translate them and name
organisations, projects and skills instead. French wording stays gender-neutral.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from summer_hunter.cv_builder.intent import CVRequest

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]
COUNTRY_FR = {"Singapore": "Singapour", "Germany": "Allemagne", "United Kingdom": "Royaume-Uni", "Ireland": "Irlande",
              "Netherlands": "Pays-Bas", "Spain": "Espagne", "Italy": "Italie", "Switzerland": "Suisse", "Belgium": "Belgique",
              "Morocco": "Maroc", "United States": "États-Unis", "Japan": "Japon", "Canada": "Canada",
              "United Arab Emirates": "Émirats arabes unis", "Sweden": "Suède", "Denmark": "Danemark", "Norway": "Norvège",
              "Austria": "Autriche", "Poland": "Pologne", "Portugal": "Portugal", "Luxembourg": "Luxembourg"}
# Country names in French take au / aux / en / à; cities and islands take à.
PREP_FR = {"Maroc": "au", "Royaume-Uni": "au", "Canada": "au", "Japon": "au", "Danemark": "au", "Portugal": "au",
           "Luxembourg": "au", "États-Unis": "aux", "Pays-Bas": "aux", "Émirats arabes unis": "aux", "Singapour": "à",
           "Hong Kong": "à"}
DEGREE_FR = {"Industry 4.0 & Robotics": "Industrie 4.0 et robotique"}
# Verbs that can follow "I" as written on the CV (present for current roles, past for finished ones).
_PRESENT = {"develop", "analyze", "analyse", "automate", "monitor", "build", "lead", "manage", "support", "design",
            "coordinate", "collaborate", "deliver", "drive", "run", "create", "engage", "conduct", "optimize"}


@dataclass
class Email:
    subject: str
    body: str
    language: str

    @property
    def text(self) -> str:
        label = "Objet" if self.language == "fr" else "Subject"
        return f"{label} : {self.subject}\n\n{self.body}" if self.language == "fr" else f"{label}: {self.subject}\n\n{self.body}"


def _dash(text: str) -> str:
    return str(text or "").replace("---", "—").replace("--", "–").strip()


def _first(text: str) -> str:
    return text.split(" -- ")[0].split(" – ")[0].strip()


def _clause(bullet: str) -> str | None:
    """A CV bullet usable after 'I' ('Automate the …' -> 'automate the …'), or None."""
    words = bullet.strip().split()
    if not words:
        return None
    verb = words[0].lower()
    if not (verb.endswith("ed") or verb in _PRESENT or verb in {"built", "led", "ran", "drove", "won", "made"}):
        return None
    head = words[0] if len(words[0]) > 1 and words[0][1].isupper() else words[0][:1].lower() + words[0][1:]
    return " ".join([head, *words[1:]]).rstrip(".")


def _period(profile_raw: dict[str, Any], fr: bool) -> str:
    av = profile_raw.get("availability") or {}
    try:
        start, end = date.fromisoformat(str(av["start"])), date.fromisoformat(str(av["end"]))
    except (KeyError, ValueError):
        return "d'avril à août 2027" if fr else "from April to August 2027"
    months = MONTHS_FR if fr else MONTHS_EN
    a, b = months[start.month - 1], months[end.month - 1]
    if fr:
        de = "d'" if a[0] in "aeiouéè" else "de "
        return f"{de}{a} à {b} {end.year}" if start.year == end.year else f"{de}{a} {start.year} à {b} {end.year}"
    return f"from {a} to {b} {end.year}" if start.year == end.year else f"from {a} {start.year} to {b} {end.year}"


def _current(cv: dict[str, Any]) -> tuple[dict, dict | None]:
    exps = cv.get("experiences", [])
    current = [e for e in exps if re.search(r"present|aujourd", str(e.get("dates", "")), re.I)]
    return (current[0] if current else (exps[0] if exps else {})), (cv.get("education") or [None])[0]


def _role(req: CVRequest) -> str:
    return re.sub(r"\s{2,}", " ", re.sub(r"(?i)\b(intern(ship)?|stage|stagiaire)\b", "", req.target_role or "")).strip(" -–,")


def _role_phrase(req: CVRequest, fr: bool) -> str:
    role = _role(req)
    if fr:
        if not role:
            return "un stage"
        return f"un stage en {role}" if role[:1].islower() else f"un stage {role}"   # "en maintenance" / "Tech Sales"
    if not role:
        return "an internship"
    return f"{'an' if role[:1].lower() in 'aeio' else 'a'} {role} internship"


def _window(profile_raw: dict[str, Any], fr: bool) -> str:
    """'April–August 2027' / 'avril–août 2027' for a subject line."""
    period = _period(profile_raw, fr)
    m = re.search(r"(\w+)(?: \d{4})? (?:à|to) (\w+) (\d{4})", period.replace("d'", "de "))
    return f"{m.group(1)}–{m.group(2)} {m.group(3)}" if m else period


def _signature(cv: dict[str, Any]) -> str:
    h = cv.get("header", {})
    lines = [h.get("name", ""), h.get("phone", ""), h.get("email", "")]
    linkedin = next((l.get("url") for l in h.get("links", []) if "linkedin" in str(l.get("url", ""))), None)
    if linkedin:
        lines.append(linkedin)
    return "\n".join(l for l in lines if l)


def compose(cv: dict[str, Any], req: CVRequest, profile_raw: dict[str, Any], language: str | None = None) -> Email:
    fr = (language or req.language) == "fr"
    company = req.company
    place = req.location if req.country_code else ""
    current, school = _current(cv)
    name = cv.get("header", {}).get("name", "")
    role = _role_phrase(req, fr)
    period = _period(profile_raw, fr)
    spontaneous = req.mode != "offer"
    projects = [p for p in cv.get("projects", []) if p.get("name")][:2]
    org = _dash(current.get("org", ""))
    degree = _dash(str((school or {}).get("degree", "")).split(" -- ")[-1])
    school_name = _first(str((school or {}).get("school", "")))

    if fr:
        country = COUNTRY_FR.get(place, place)
        where = f" {PREP_FR.get(country, 'en')} {country}" if place else ""
        what = role[3:]                       # "stage en maintenance…" / "stage Tech Sales"
        subject = (f"Candidature{' spontanée' if spontaneous else ''} – {what[:1].upper() + what[1:]} "
                   f"({_window(profile_raw, True)}) – {name}")
        hello = "Madame, Monsieur,"
        intro = (f"En dernière année d'école d'ingénieurs à l'{school_name} ({DEGREE_FR.get(degree, degree)}) et en "
                 f"apprentissage chez {org}, je vous adresse ma candidature{' spontanée' if spontaneous else ''} pour {role}"
                 f"{f' au sein de {company}' if company else ''}{where}, {period}.")
        others = [_dash(e.get("org", "")) for e in cv.get("experiences", []) if e is not current][:2]
        path = []
        if others:
            path.append("Mon parcours compte aussi " + " et ".join(others))
        if projects:
            path.append(("ainsi que " if others else "Mon parcours compte aussi ") + "des projets comme "
                        + " et ".join(_first(p["name"]) for p in projects))
        skills = [s for items in list((cv.get("skills") or {}).values())[:2] for s in items
                  if "(" not in s and "Language" not in s][:6]
        middle = (", ".join(path) + "." if path else "")
        if skills:
            middle += f" Compétences clés : {', '.join(skills)}."
        close = (f"Vous trouverez mon CV en pièce jointe. Ce serait un plaisir d'échanger avec vous pour voir comment je pourrais "
                 f"contribuer à {company or 'vos équipes'}.")
        body = "\n\n".join([hello, intro, middle.strip(), close, "Bien cordialement,", _signature(cv)])
        return Email(subject, re.sub(r"\n{3,}", "\n\n", body), "fr")

    where = f" in {place}" if place else ""
    what = f"{_role(req)} internship".strip() if _role(req) else "Internship"
    subject = (f"{'Spontaneous application' if spontaneous else 'Application'} – {what[:1].upper() + what[1:]} "
               f"({_window(profile_raw, False)}) – {name}")
    hello = f"Dear {company} team," if company else "Dear Hiring Team,"
    intro = (f"I am a final-year engineering student at {school_name} ({degree}) and an "
             f"{_dash(current.get('role', '')).split(' – ')[0].lower()} at {org}. "
             f"I am writing to apply{' spontaneously' if spontaneous else ''} for {role}"
             f"{f' at {company}' if company else ''}{where}, {period}.")
    facts = []
    for e in cv.get("experiences", [])[:3]:
        clause = next((c for b in e.get("bullets", []) if (c := _clause(b["text"]))), None)
        if clause:
            facts.append(f"At {_first(e.get('org', ''))}, I {clause}.")
        if len(facts) == 2:
            break
    for p in projects[:1]:
        clause = next((c for b in p.get("bullets", []) if (c := _clause(b["text"]))), None)
        facts.append(f"I also {clause}." if clause else f"I also worked on {_first(p['name'])}.")
    close = (f"My CV is attached. I would welcome a short call to discuss how I could contribute to "
             f"{company or 'your team'}.")
    body = "\n\n".join([hello, intro, " ".join(facts), close, "Kind regards,", _signature(cv)])
    return Email(subject, re.sub(r"\n{3,}", "\n\n", body), "en")
