"""Cover email sent with a CV (spontaneous application or posting): drafted, never sent.

Built from the CV version's ApplicationContext (the same one the CV used) and its verified content.
Deterministic and instant: the sentences are written for the situation (spontaneous or posting,
business, product or industry profile), every fact about the candidate comes from the CV version
(English quotes its verified bullets as written; French never translates them), and the recipient
positions are checked by context.validate_email before the email is shown.
French wording stays gender-neutral.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from summer_hunter.cv_builder.context import ApplicationContext

COUNTRY_FR = {"Singapore": "Singapour", "Germany": "Allemagne", "United Kingdom": "Royaume-Uni", "Ireland": "Irlande",
              "Netherlands": "Pays-Bas", "Spain": "Espagne", "Italy": "Italie", "Switzerland": "Suisse", "Belgium": "Belgique",
              "Morocco": "Maroc", "United States": "États-Unis", "Japan": "Japon", "Canada": "Canada",
              "United Arab Emirates": "Émirats arabes unis", "Sweden": "Suède", "Denmark": "Danemark", "Norway": "Norvège",
              "Austria": "Autriche", "Poland": "Pologne", "Portugal": "Portugal", "Luxembourg": "Luxembourg",
              "Hong Kong": "Hong Kong", "Australia": "Australie", "China": "Chine"}
PREP_FR = {"Maroc": "au", "Royaume-Uni": "au", "Canada": "au", "Japon": "au", "Danemark": "au", "Portugal": "au",
           "Luxembourg": "au", "États-Unis": "aux", "Pays-Bas": "aux", "Émirats arabes unis": "aux", "Singapour": "à",
           "Hong Kong": "à"}
MONTH_FR = {"January": "janvier", "February": "février", "March": "mars", "April": "avril", "May": "mai", "June": "juin",
            "July": "juillet", "August": "août", "September": "septembre", "October": "octobre", "November": "novembre",
            "December": "décembre"}
FR_TERMS = {"Industry 4.0 & Robotics": "Industrie 4.0 et robotique", "SaaS & AI Solutions": "SaaS et solutions IA",
            "Equipment Engineering": "ingénierie équipements"}
# What the candidate wants to contribute to, by kind of role (an intention, not a claim about the past).
THEME_EN = {
    "business": "business development, partnerships and helping organisations grow in new markets",
    "product": "turning customer and operational needs into data and AI tools that people actually use",
    "industry": "industrial performance, through data, automation and continuous improvement",
}
THEME_FR = {
    "business": "le développement commercial, les partenariats et l'accompagnement d'organisations vers de nouveaux marchés",
    "product": "la transformation de besoins clients et opérationnels en outils data et IA réellement utilisés",
    "industry": "la performance industrielle, par la donnée, l'automatisation et l'amélioration continue",
}
_PRESENT = {"develop", "analyze", "analyse", "automate", "monitor", "build", "lead", "manage", "support", "design",
            "coordinate", "collaborate", "deliver", "drive", "run", "create", "engage", "conduct", "optimize"}
_ARTICLE = re.compile(r"\b(chamber|association|university|institute|foundation|ministry|embassy|agency|federation|"
                      r"council|office|bank of|organisation|organization|commission)\b", re.I)


@dataclass
class Email:
    subject: str
    body: str
    language: str
    parts: dict[str, str] = field(default_factory=dict)   # recipient positions + proof, for validation

    @property
    def text(self) -> str:
        return f"Objet : {self.subject}\n\n{self.body}" if self.language == "fr" else f"Subject: {self.subject}\n\n{self.body}"


def _dash(text: Any) -> str:
    return str(text or "").replace("---", "—").replace("--", "–").strip()


def _first(text: str) -> str:
    return str(text).split(" -- ")[0].split(" – ")[0].strip()


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


def _role(ctx: ApplicationContext) -> str:
    role = re.sub(r"(?i)\b(intern(ship)?|stage|stagiaire)\b", "", ctx.target_role or "")
    return re.sub(r"\s{2,}", " ", role).strip(" -–,&")


def _the(name: str) -> str:
    return f"the {name}" if _ARTICLE.search(name) else name


def _named(ctx: ApplicationContext) -> str:
    """'the French Chamber of Commerce in Singapore (FCCS)' on first mention."""
    if not ctx.target_company:
        return ""
    alias = f" ({ctx.company_aliases[0]})" if ctx.company_aliases else ""
    return _the(ctx.target_company) + alias


def _short(ctx: ApplicationContext) -> str:
    """Later mentions: the acronym when there is one."""
    if ctx.company_aliases:
        return f"the {ctx.company_aliases[0]}" if _ARTICLE.search(ctx.target_company or "") else ctx.company_aliases[0]
    return _the(ctx.target_company) if ctx.target_company else ""


def _de(name: str) -> str:
    """French 'de' before an organisation: 'de la French Chamber…', "d'Amazon Web Services", 'de Salesforce'."""
    if _ARTICLE.search(name) and not re.match(r"[A-Z]{2,}\b", name):
        return f"de la {name}"
    return f"d'{name}" if name[:1].lower() in "aeiouyéèh" else f"de {name}"


def _people(cv: dict[str, Any]) -> tuple[dict, dict | None, dict | None]:
    exps = cv.get("experiences", [])
    current = [e for e in exps if re.search(r"present|aujourd", str(e.get("dates", "")), re.I)]
    apprentice = next((e for e in current if "apprentice" in str(e.get("role", "")).lower()), current[0] if current else {})
    founder = next((e for e in current if "founder" in str(e.get("role", "")).lower()), None)
    school = (cv.get("education") or [None])[0]
    return apprentice, founder, school


def _signature(cv: dict[str, Any], school: dict | None, fr: bool) -> str:
    h = cv.get("header", {})
    degree = _dash(str((school or {}).get("degree", "")).split(" -- ")[-1])
    school_name = _first(str((school or {}).get("school", "")))
    title = (f"Élève ingénieur en dernière année – {school_name} ({FR_TERMS.get(degree, degree)})" if fr
             else f"Final-year Engineering Student – {school_name} ({degree})")
    contact = " | ".join(x for x in (h.get("phone"), h.get("email")) if x)
    linkedin = next((l.get("url") for l in h.get("links", []) if "linkedin" in str(l.get("url", ""))), None)
    return "\n".join(x for x in (h.get("name", ""), title if school_name else "", contact,
                                 f"LinkedIn : {linkedin}" if fr and linkedin else f"LinkedIn: {linkedin}" if linkedin else "") if x)


def compose(cv: dict[str, Any], ctx: ApplicationContext, track: str = "business") -> Email:
    fr = ctx.language == "fr"
    apprentice, founder, school = _people(cv)
    role = _role(ctx)
    spontaneous = ctx.application_type != "offer"
    projects = [p for p in cv.get("projects", []) if p.get("name")]
    theme = (THEME_FR if fr else THEME_EN).get(track, (THEME_FR if fr else THEME_EN)["business"])
    sign = "Bien cordialement," if fr else "Best regards,"
    signature = _signature(cv, school, fr)
    degree = _dash(str((school or {}).get("degree", "")).split(" -- ")[-1])
    school_name = _first(str((school or {}).get("school", "")))
    apprentice_org = _dash(apprentice.get("org", ""))
    founder_org = _first(founder.get("org", "")) if founder else ""
    founder_scope = _dash(str(founder.get("role", "")).split(" -- ")[-1]) if founder else ""

    if fr:
        window = " ".join(MONTH_FR.get(w, w) for w in ctx.availability.replace("–", " – ").split()).replace(" – ", "–")
        months = window.split()
        period = (f"d'{months[0].split('–')[0]} à {months[0].split('–')[1]} {months[1]}" if len(months) == 2 and "–" in months[0]
                  else window)
        country = COUNTRY_FR.get(ctx.target_country or "", ctx.target_country or "")
        in_name = bool(ctx.target_country and ctx.target_country in (ctx.target_company or ""))   # "… in Singapore"
        where = f" {PREP_FR.get(country, 'en')} {country}" if country and not in_name else ""
        stage = (f"un stage en {role}" if role[:1].islower() else f"un stage {role}") if role else "un stage"
        full = ctx.target_company
        alias = ctx.company_aliases[0] if ctx.company_aliases else None
        target = _de(full) + (f" ({alias})" if alias else "") if full else None          # first mention
        later = (_de(full).replace(full, alias) if alias else _de(full)) if full else None  # "de la FCCS"
        what = stage[3:]
        subject = (f"{'Candidature spontanée' if spontaneous else 'Candidature'} – {what[:1].upper() + what[1:]} "
                   f"({window}) – {cv.get('header', {}).get('name', '')}")
        greeting = ctx.recipient
        opening = (f"Je me permets de vous adresser ma candidature{' spontanée' if spontaneous else ''} pour {stage}"
                   f"{f' au sein {target}' if target else ''}{where}, {period}.")
        identity = (f"En dernière année d'école d'ingénieurs à l'{school_name} ({FR_TERMS.get(degree, degree)}), je suis en apprentissage "
                    f"chez {apprentice_org}" + (f" et j'ai cofondé {founder_org} ({FR_TERMS.get(founder_scope, founder_scope)})" if founder else "") + ".")
        skills = [s for items in list((cv.get("skills") or {}).values())[:2] for s in items if "(" not in s][:5]
        proof = identity + (f" Au quotidien, je mobilise notamment : {', '.join(skills)}." if skills else "")
        if projects:
            proof += " J'ai aussi mené des projets comme " + " et ".join(_first(p["name"]) for p in projects[:2]) + "."
        bridge = f"C'est cette double culture, technique et terrain, que je souhaite mettre au service {later or 'de vos équipes'}, notamment sur {theme}."
        closing = ((f"Même en l'absence d'offre publiée, ce serait un plaisir d'échanger avec vous sur la manière dont je pourrais "
                    f"contribuer aux activités {later or 'de votre organisation'}.") if spontaneous
                   else "Ce serait un plaisir d'échanger avec vous sur ce poste.") + " Vous trouverez mon CV en pièce jointe."
        body = "\n\n".join([greeting, opening, proof, bridge, closing, sign, signature])
        slots = [s for s in (full, alias) if s] if full else []
        parts = {"subject": subject, "greeting": greeting, "opening": opening, "closing": bridge + " " + closing, "proof": proof,
                 "slots": slots}
        return Email(subject, body, "fr", parts)

    named, short = _named(ctx), _short(ctx)
    where = f" in {ctx.target_country}" if ctx.target_country and (ctx.target_country not in (ctx.target_company or "")) else ""
    what = f"{role} internship" if role else "internship"
    subject = (f"{'Spontaneous application' if spontaneous else 'Application'} – {what[:1].upper() + what[1:]} "
               f"({ctx.availability}) – {cv.get('header', {}).get('name', '')}")
    greeting = ctx.recipient
    article = "an" if what[:1].lower() in "aeio" else "a"
    opening = (f"I am writing to apply{' spontaneously' if spontaneous else ''} for {article} {what}"
               f"{f' at {named}' if named else ''}{where}, from {ctx.availability.replace('–', ' to ', 1)}.")
    identity = (f"I am a final-year engineering student at {school_name} ({degree}), an engineering apprentice at {apprentice_org}"
                + (f", and the co-founder of {founder_org} ({founder_scope})." if founder else "."))
    facts = []
    for e in cv.get("experiences", [])[:3]:
        clause = next((c for b in e.get("bullets", []) if (c := _clause(b["text"]))), None)
        if clause:
            facts.append(f"At {_first(e.get('org', ''))}, I {clause}.")
        if len(facts) == 2:
            break
    proof = identity + " " + " ".join(facts)
    bridge = (f"That mix of engineering rigour and hands-on work with clients and stakeholders is what I would like to bring to "
              f"{short or 'your team'}, especially in {theme}.")
    closing = ((f"I understand there may be no internship advertised at the moment. If my profile fits your needs, "
                f"I would be glad to discuss how I could contribute to {short or 'your team'}.") if spontaneous
               else f"I would welcome the opportunity to discuss the {role or 'internship'} position with you.") \
        + " My CV is attached."
    body = "\n\n".join([greeting, opening, proof, bridge, closing, sign, signature])
    slots = [n for n in ctx.names] if ctx.target_company else []
    parts = {"subject": subject, "greeting": greeting, "opening": opening, "closing": bridge + " " + closing, "proof": proof,
             "slots": slots}
    return Email(subject, body, "en", parts)
