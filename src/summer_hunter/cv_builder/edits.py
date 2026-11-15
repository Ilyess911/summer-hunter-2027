"""AI CV Builder lot 5: understand an edit message about the last CV, deterministically.

    "Mets davantage en avant mon projet de robotique médicale."   -> emphasis
    "Fais-le en anglais et sur une seule page."                   -> language=en, pages=1
    "Retire Amundi"                                               -> exclude
    "Style moderne" / "Pour un poste de Sales Engineer"           -> style / target role
    "J'ai obtenu une certification IBM"                           -> a profile fact to confirm (never applied directly)

Nothing here writes CV text: an edit only changes which verified items are shown, their order, the
language, the length or the style. A personal fact goes through the profile's Confirm button first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from summer_hunter.cv_builder.intent import _EMPHASIS, detect_pages, detect_style

_LANG_EN = re.compile(r"\b(en anglais|in english|anglais|english version|traduis(?:-le)? en anglais)\b", re.I)
_LANG_FR = re.compile(r"\b(en fran[cç]ais|in french|version fran[cç]aise|traduis(?:-le)? en fran[cç]ais)\b", re.I)
_ONE_PAGE = re.compile(r"\b(une seule page|single page|one page|1 page|tenir sur une page)\b", re.I)
_EXCLUDE = re.compile(r"\b(?:retire|enl[eè]ve|supprime|vire|remove|drop|sans|pas de|plus de mention de)\s+(?:l[ae'] ?|les |mon |ma |mes |the |my )?"
                      r"(?P<what>[^.;,!?\n]{2,60}?)(?=\s+(?:et|and|puis|then)\b|[.;,!?\n]|$)", re.I)
_MORE = re.compile(r"\b(?:plus de|davantage de|insiste sur|focus on|more|accent sur|valorise|mets? l'accent sur)\s+(?:l[ae'] ?|les |mon |ma |mes |the |my )?"
                   r"(?P<what>[^.;,!?\n]{2,60}?)(?=\s+(?:et|and|puis|then)\b|[.;,!?\n]|$)", re.I)
_ROLE = re.compile(r"\b(?:pour un poste de|pour un stage (?:de|en)|plut[oô]t (?:pour|en)|change le poste (?:en|pour)|target(?:ing)? (?:a |an )?)"
                   r"\s*(?P<role>[^.;,!?\n]{3,60})", re.I)
_LIGHT = re.compile(r"\b(moins personnalis\w*|personnalisation l[ée]g[eè]re|version g[ée]n[ée]rique|cv g[ée]n[ée]ral)\b", re.I)
_STRONG = re.compile(r"\b(plus personnalis\w*|personnalisation (?:forte|standard|maximale))\b", re.I)
# A statement about the candidate (a new fact) rather than an instruction about the document.
_FACT = re.compile(r"^\s*(?:j'ai|j’ai|je suis|je parle|i have|i've|i got|i earned|i won|i am|i speak)\b.{6,}", re.I)
_STOP_PARTS = re.compile(r"\s+(?:et|and|puis|then|sur une|en (?:anglais|fran[cç]ais))\b.*$", re.I)


@dataclass
class Edit:
    overrides: dict[str, Any] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)
    understood: list[str] = field(default_factory=list)     # French, one line per change, for the confirmation

    @property
    def empty(self) -> bool:
        return not self.overrides and not self.facts


_ARTICLE = re.compile(r"^(?:la|le|les|l'|l’|mon|ma|mes|the|my|du|des|de)\s*", re.I)


def _clean(what: str) -> str:
    return _ARTICLE.sub("", _STOP_PARTS.sub("", what).strip(" '\"«»")).strip()


def parse(text: str) -> Edit:
    e = Edit()
    t = " ".join((text or "").split())
    if not t:
        return e
    if _FACT.match(t):
        e.facts.append(t)
        e.understood.append("nouveau fait à confirmer pour ton profil")
        return e
    if _LANG_FR.search(t):
        e.overrides["language"] = "fr"
        e.understood.append("langue : français")
    elif _LANG_EN.search(t):
        e.overrides["language"] = "en"
        e.understood.append("langue : anglais")
    pages = 1 if _ONE_PAGE.search(t) else detect_pages(t)
    if pages:
        e.overrides["pages"] = pages
        e.understood.append(f"format : {pages} page{'s' if pages > 1 else ''}")
    style = detect_style(t)
    if style:
        e.overrides["style"] = style
        e.understood.append(f"style : {style}")
    if _LIGHT.search(t):
        e.overrides["personalization"] = "light"
        e.understood.append("personnalisation légère (sans phrase ciblée)")
    elif _STRONG.search(t):
        e.overrides["personalization"] = "standard"
        e.understood.append("personnalisation standard")
    emphasis = [_clean(m.group("what")) for m in _EMPHASIS.finditer(t)] + [_clean(m.group("what")) for m in _MORE.finditer(t)]
    emphasis = [w for w in dict.fromkeys(_clean(w.split(",")[0]) for w in emphasis) if len(w) >= 2]
    if emphasis:
        e.overrides["emphasis"] = emphasis[:3]
        e.understood.append("mettre en avant : " + ", ".join(emphasis[:3]))
    exclude = [w for w in dict.fromkeys(_clean(m.group("what")) for m in _EXCLUDE.finditer(t)) if len(w) >= 2]
    if exclude:
        e.overrides["exclude"] = exclude[:3]
        e.understood.append("retirer : " + ", ".join(exclude[:3]))
    m = _ROLE.search(t)
    if m:
        role = _clean(m.group("role"))
        if role:
            e.overrides["target_role"] = role
            e.understood.append(f"poste visé : {role}")
    return e


_NEW_REQUEST = re.compile(r"\b(cv|curriculum|resume|candidature|application|postuler|je postule)\b", re.I)
_EDIT_START = re.compile(r"^\s*(mets?|mettre|retire|enl[eè]ve|supprime|change|ajoute|traduis|plus|moins|insiste|valorise|"
                         r"version|en (?:anglais|fran[cç]ais)|style|une? page|\d page|pour un poste|pour un stage)", re.I)
_SAME_FOR = re.compile(r"^\s*(?i:(?:re)?fais[- ]le\s+(?:pour|chez|for|at))\s+(?P<name>[A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*)*)")


def is_new_request(text: str, current_company: str | None) -> bool:
    """Another application (another CV or recipient), as opposed to an edit of the active CV.

    Edit wording wins ('mets en avant mon projet pour Airbus' stays an edit). A new recipient counts only
    when written as one ('candidature chez X', 'CV pour X', 'fais-le pour X'), never a name merely mentioned.
    """
    from summer_hunter.cv_builder.intent import explicit_target
    from summer_hunter.utils.text import normalize_for_match

    t = " ".join((text or "").split())
    if _SAME_FOR.match(t):
        return True
    if _EDIT_START.match(t):
        return False
    if _NEW_REQUEST.search(t):
        return True
    name, _ = explicit_target(t)
    return bool(name) and normalize_for_match(name) != normalize_for_match(current_company or "")
