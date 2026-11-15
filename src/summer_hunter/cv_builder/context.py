"""ApplicationContext: the one description of who an application is for, shared by the CV and the email.

It is a read-only view of the stored request (CVRequest), so the CV, the email and every later version
read the same values; nothing else decides the recipient. The request itself is analyzed from the
current message only (intent.target_company): earlier CVs, conversation state or the candidate's own
experience never supply a target.

`validate_cv` and `validate_email` are blocking checks run before anything is delivered: the recipient
positions (summary target line; email subject, greeting, opening, closing) must name the target and no
other organisation. The candidate's former employers may appear only as experience.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from summer_hunter.cv_builder.intent import CVRequest
from summer_hunter.engine.geo import country_name
from summer_hunter.utils.text import normalize_for_match

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]


class ContextViolation(Exception):
    """A generated document addresses someone other than the application's target. Never delivered."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ApplicationContext:
    target_company: str | None
    company_aliases: tuple[str, ...]
    target_role: str | None
    application_type: str              # spontaneous | offer | target (no organisation named)
    target_country: str | None
    target_country_code: str | None
    availability: str
    recipient: str                     # how the email addresses the reader
    language: str
    company_source: str | None = None

    @property
    def names(self) -> tuple[str, ...]:
        """Every way the target may be written."""
        return tuple(n for n in (self.target_company, *self.company_aliases) if n)


def availability(profile_raw: dict[str, Any]) -> str:
    av = profile_raw.get("availability") or {}
    try:
        start, end = date.fromisoformat(str(av["start"])), date.fromisoformat(str(av["end"]))
    except (KeyError, ValueError):
        return "April–August 2027"
    a, b = MONTHS_EN[start.month - 1], MONTHS_EN[end.month - 1]
    return f"{a}–{b} {end.year}" if start.year == end.year else f"{a} {start.year}–{b} {end.year}"


def from_request(req: CVRequest, profile_raw: dict[str, Any]) -> ApplicationContext:
    alias = req.company_aliases[0] if req.company_aliases else None
    if req.language == "fr":
        recipient = "Madame, Monsieur,"
    else:
        recipient = f"Dear {alias or req.company} team," if req.company else "Dear Hiring Team,"
    return ApplicationContext(
        target_company=req.company, company_aliases=tuple(req.company_aliases), target_role=req.target_role,
        application_type=req.mode if req.company or req.mode == "offer" else "target",
        target_country=country_name(req.country_code) if req.country_code else None,
        target_country_code=req.country_code, availability=availability(profile_raw), recipient=recipient,
        language=req.language, company_source=req.company_source)


# Validation -------------------------------------------------------------------------------------
# Checks read recipient *slots*: the names the generators inserted as recipient, plus every name that the
# text places in a recipient position ("Dear X team", "at X", "join X", "au sein de X"). They fail closed:
# a target with no recipient slot, or any slot that is not exactly the target or a vetted alias, blocks.

_TOKEN = r"[A-Z][\w&'’-]*(?:\.[A-Za-z][\w&'’-]*)*"      # 'S.A.' keeps its dots, a sentence's full stop does not
_RECIPIENT_TEXT = re.compile(
    r"(?:\bDear|\bjoin|\bat|contribute to|bring to|au sein d[e'’u]|au service d[e'’u]|activités d[e'’u]|\bchez|\bde la)"
    r"\s*(?:the |la |le |l['’])?(?P<name>" + _TOKEN + r"(?:\s+(?:(?:of|in|for|and|de|du|des|et|&)\s+)?" + _TOKEN + r"){0,7})")
_NOT_ORG = {"hiring team", "april", "may", "june", "july", "august", "september", "march", "monsieur", "madame"}


def _norm(name: str) -> str:
    return normalize_for_match(re.sub(r"^(?:the|la|le|l['’])\s*", "", name.strip(), flags=re.I))


def _is_target(name: str, ctx: ApplicationContext) -> bool:
    n = _norm(name)
    return any(n == _norm(t) for t in ctx.names)


def _own(name: str, own_orgs: list[str]) -> bool:
    n = _norm(name)
    return any(n == _norm(o) or _norm(o).startswith(n + " ") or n.startswith(_norm(o) + " ") for o in own_orgs)


def _drop_place(name: str) -> str:
    """'Airbus in Singapore' -> 'Airbus' (the place is not part of the recipient)."""
    from summer_hunter.cv_builder.intent import is_place

    m = re.match(r"^(?P<org>.+?)\s+(?:in|à|en)\s+(?P<place>[A-Z].*)$", name)
    return m.group("org") if m and is_place(m.group("place")) else name


def _recipients_in(text: str) -> list[str]:
    out = []
    for m in _RECIPIENT_TEXT.finditer(text or ""):
        name = re.sub(r"\s+(?:team|équipe)$", "", m.group("name")).strip(" .,")
        if _norm(name) and _norm(name) not in _NOT_ORG and not _norm(name).startswith("hiring"):
            out.append(name)
    return out


def _target_or_place(name: str, ctx: ApplicationContext) -> bool:
    return _is_target(name, ctx) or _is_target(_drop_place(name), ctx)


TRUSTED_SOURCES = {"explicit", "short_request", "posting_label", "posting_header", "posting_intro"}


def check_target(ctx: ApplicationContext, own_orgs: list[str]) -> list[str]:
    """A target must come from recipient evidence in the current request; a former employer needs it too."""
    if ctx.target_company and ctx.company_source not in TRUSTED_SOURCES:
        return [f"cible « {ctx.target_company} » sans preuve dans la demande (source {ctx.company_source})"]
    return []


def validate_cv(cv: dict[str, Any], ctx: ApplicationContext, orgs: list[str], own_orgs: list[str]) -> list[str]:
    problems = check_target(ctx, own_orgs)
    printed = cv.get("recipient")
    if printed and not _is_target(printed, ctx):
        problems.append(f"le CV s'adresse à « {printed} » au lieu de « {ctx.target_company or 'aucune entreprise'} »")
    for sentence in re.split(r"(?<=[.!?])\s+", str(cv.get("summary", ""))):
        targeted = bool(re.match(r"(?i)\s*(seeking|looking for|candidat|ready to|eager to|keen to)", sentence))
        for name in _recipients_in(sentence):
            if _target_or_place(name, ctx):
                continue
            if targeted or re.search(rf"\bjoin\s+(?:the )?{re.escape(name)}", sentence) or not _own(name, own_orgs):
                problems.append(f"le résumé du CV vise « {name} » au lieu de « {ctx.target_company or 'aucune entreprise'} »")
    return problems


def validate_email(parts: dict[str, Any], ctx: ApplicationContext, orgs: list[str], own_orgs: list[str]) -> list[str]:
    """parts: subject, greeting, opening, closing (recipient text), proof (experience), slots (inserted recipients)."""
    problems = check_target(ctx, own_orgs)
    slots = list(parts.get("slots") or [])
    if ctx.target_company and not slots:
        problems.append("le mail n'a aucun destinataire alors qu'une cible est définie")
    for slot in slots:
        if not _is_target(slot, ctx):
            problems.append(f"mail adressé à « {slot} » au lieu de « {ctx.target_company or 'aucune entreprise'} »")
    for key in ("greeting", "opening", "closing"):
        for name in _recipients_in(parts.get(key, "")):
            if not _target_or_place(name, ctx):
                problems.append(f"mail ({key}) adressé à « {name} » au lieu de « {ctx.target_company or 'aucune entreprise'} »")
    # Experience positions ("At X, I…", "chez X", "co-founder of X") may only name the candidate's organisations.
    for m in _EXPERIENCE_SLOT.finditer(parts.get("proof", "")):
        name = m.group("name").strip()
        if not _own(name, own_orgs):
            problems.append(f"le mail présente « {name} » comme une expérience, alors que ce n'est pas ton parcours")
    return list(dict.fromkeys(problems))


_EXPERIENCE_SLOT = re.compile(r"(?:\bAt|apprentice at|co-founder of|chez|cofondé)\s+(?P<name>[A-Z][^,.;()]{1,60}?)(?=\s*(?:[,.;(]|\bet\b|$))")
