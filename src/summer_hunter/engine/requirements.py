"""Technical requirements stated in a posting, each tagged mandatory, preferred or mere exposure.

A tool name alone proves nothing: "exposure to PLCs" is not "strong PLC programming required".
Levels come from the sentence and from the section heading it sits under (Requirements vs
Nice to have), so a skill is only treated as a hard requirement when the posting says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from summer_hunter.utils.text import normalize_for_match, split_sentences

_PREFERRED = re.compile(r"\b(preferred|a plus|is a plus|plus\b|nice to have|ideally|bonus|desirable|advantage|advantageous|"
                        r"would be great|good to have|optional)\b")
_MANDATORY = re.compile(r"\b(required|requires?|requirement|must|mandatory|essential|need to have|you have|you bring|"
                        r"strong|proven|solid|proficien\w*|expert\w*|advanced|extensive|in-depth|deep (knowledge|understanding|experience)|"
                        r"\d+\+? years?)\b")
_CORE = re.compile(r"\b(strong|proven|solid|proficien\w*|expert\w*|advanced|extensive|in-depth|deep (knowledge|understanding|experience)|"
                   r"\d+\+? years?|hands-on (experience )?(developing|programming|designing))\b")
_EXPOSURE = re.compile(r"\b(exposure to|familiar\w*|interest in|learn|you will (use|work with|support)|introduction to|awareness)\b")
_HEADING_PREFERRED = re.compile(r"^(preferred|bonus|nice to have|desired|desirable|additional|plus)\b.{0,40}$")
_HEADING_REQUIRED = re.compile(r"^(requirements?|required|basic|minimum|must[- ]haves?|qualifications|what you (bring|need|have)|"
                               r"who you are|your profile|you have|profile)\b.{0,40}$")


@dataclass(frozen=True)
class SkillRequirement:
    name: str
    level: str            # mandatory | preferred | exposure
    advanced: bool        # specialized engineering expertise
    core: bool            # phrased as deep expertise (strong, proven, N years...)
    verified: bool        # the profile lists this skill
    evidence: str


def _compile(skills_cfg: dict[str, Any]) -> list[tuple[str, list[re.Pattern], bool]]:
    return [(name, [re.compile(a, re.I) for a in cfg.get("aliases", [name])], bool(cfg.get("advanced")))
            for name, cfg in skills_cfg.items()]


def verified_names(skills_cfg: dict[str, Any], verified: list[str]) -> set[str]:
    """Lexicon skills the profile holds: the skill name, or one of its aliases, matches a verified skill."""
    held = [normalize_for_match(v) for v in verified]
    out = set()
    for name, patterns, _ in _compile(skills_cfg):
        if any(normalize_for_match(name) == h or any(p.search(h) for p in patterns) for h in held):
            out.add(name)
    return out


_HEADING_NEUTRAL = re.compile(r"^(responsibilit\w*|what you.ll (do|work on)|your (role|mission|tasks)|the role|about\b|"
                              r"key tasks|tasks|job description|description|what we offer|benefits|why join|your impact)\b.{0,40}$")
_CLAUSE = re.compile(r"[,;]\s+|\s+(?:but|while|whereas)\s+")


def _clauses(sentence: str) -> list[str]:
    return [c for c in _CLAUSE.split(sentence) if c and c.strip()]


def _level(clause: str) -> str | None:
    """Level stated by this clause, or None when the clause states none (a list item continuing the previous one)."""
    if _PREFERRED.search(clause):
        return "preferred"
    if _EXPOSURE.search(clause) and not _CORE.search(clause):
        return "exposure"
    if _MANDATORY.search(clause):
        return "mandatory"
    return None


def extract(description: str, skills_cfg: dict[str, Any], verified: list[str]) -> list[SkillRequirement]:
    lexicon = _compile(skills_cfg)
    held = verified_names(skills_cfg, verified)
    found: dict[str, SkillRequirement] = {}
    rank = {"exposure": 0, "preferred": 1, "mandatory": 2}
    section = None
    # Split on the raw text first: line breaks carry the structure (headings, bullets) that
    # normalization would flatten into one sentence.
    for _, _, raw in split_sentences(description or ""):
        sentence = normalize_for_match(raw)
        head = sentence.split(":", 1)[0].strip(" -*")
        if len(head) < 60 and _HEADING_PREFERRED.search(head):
            section = "preferred"
        elif len(head) < 60 and _HEADING_REQUIRED.search(head):
            section = "mandatory"
        elif len(head) < 60 and _HEADING_NEUTRAL.search(head):
            section = "duties"      # what the intern will do: describes the work, not a requirement
        # Qualifiers apply to their own clause ("C++ required, Python is a plus"); a clause without
        # one continues the previous clause ("strong C++, Java" keeps Java strong).
        level, core = None, False
        for clause in _clauses(sentence):
            stated = _level(clause)
            if section == "duties" and stated == "mandatory":
                stated = "exposure"
            if stated is not None:
                level, core = stated, bool(_CORE.search(clause))
            current = level or (section if section in {"mandatory", "preferred"} else "exposure")
            for name, patterns, advanced in lexicon:
                match = next((m for p in patterns if (m := p.search(clause))), None)
                if not match:
                    continue
                lvl = current
                in_sentence = next((m for p in patterns if (m := p.search(sentence))), match)
                if lvl == "mandatory" and advanced and _alternative(sentence, in_sentence, name, lexicon, held):
                    lvl = "preferred"     # "C++, Java or Python": a held alternative satisfies the line
                req = SkillRequirement(name, lvl, advanced, core and lvl == "mandatory", name in held, sentence[:300])
                if name not in found or rank[lvl] > rank[found[name].level]:
                    found[name] = req
    return list(found.values())


_LANGUAGES = re.compile(r"\b(python|java|javascript|typescript|go|rust|c#|matlab|sql)\b")


def _alternative(clause: str, match: re.Match, name: str, lexicon: list, held: set[str]) -> bool:
    """True when `name` sits in an "X, Y or Z" group with a skill the profile holds ("and" breaks the group)."""
    others = [m for n, pats, _ in lexicon if n != name and n in held for p in pats for m in p.finditer(clause)]
    if "python" in held:
        others += [m for m in _LANGUAGES.finditer(clause) if m.group(0) == "python"]
    for other in others:
        lo, hi = sorted((match.end(), other.start())) if other.start() > match.start() else (other.end(), match.start())
        between = clause[lo:hi]
        if re.search(r"\band\b", between):
            continue
        # "X or Y", or a list "A, B, C, or D" whose "or" comes after both items.
        tail = clause[max(match.end(), other.end()):][:40]
        if re.search(r"\bor\b|/", between) or ("," in between and re.match(r"[^.;]*?\bor\b", tail)):
            return True
    return False


def blocking(reqs: list[SkillRequirement]) -> list[SkillRequirement]:
    """Mandatory specialized expertise the profile does not hold."""
    return [r for r in reqs if r.advanced and r.level == "mandatory" and not r.verified]
