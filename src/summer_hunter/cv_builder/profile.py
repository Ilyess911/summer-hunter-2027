"""Permanent candidate profile for the AI CV Builder.

Source of truth, in order:
  1. `config/cv_content.yaml` and `config/candidate_profile.yaml`: approved, VERIFIED facts (read-only here).
  2. The `profile_facts` table: facts stated outside those files. They start INFERRED ("to confirm") and
     only become usable (USER_CONFIRMED) after the candidate presses Confirm. REJECTED rows are kept so the
     same claim is never proposed again; MISSING rows name information the profile does not have.

Nothing is added without an explicit confirmation, and an offer's requirement is never written here
(the CV builder only ever calls `propose` with text the candidate wrote about themself).
Fact texts are personal data: they are never logged.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from summer_hunter.database.models import ProfileFact, utcnow
from summer_hunter.engine import knowledge

INFERRED, USER_CONFIRMED, REJECTED, MISSING = "INFERRED", "USER_CONFIRMED", "REJECTED", "MISSING"
STATUSES = {INFERRED, USER_CONFIRMED, REJECTED, MISSING}
KINDS = ("skill", "experience", "project", "education", "certification", "language", "achievement", "other")
KIND_FR = {"skill": "Compétence", "experience": "Expérience", "project": "Projet", "education": "Formation",
           "certification": "Certification", "language": "Langue", "achievement": "Distinction", "other": "Autre"}
MAX_TEXT = 500

# Facts from the AI CV Builder brief (15 Nov 2026) that the approved CV and profile do not show.
# All start INFERRED: the brief itself marks several as "to verify", and none may appear as a fact before
# the candidate confirms it.
SEED_SOURCE = "brief AI CV Builder (15/11/2026)"
SEED: list[tuple[str, str, str | None]] = [
    ("skill", "MATLAB / Simulink", None),
    ("skill", "RoboDK", None),
    ("skill", "C++", None),
    ("skill", "PLM", None),
    ("skill", "Git / GitHub", "niveau à confirmer"),
    ("skill", "Industrial Automation", None),
    ("project", "Cobotized Intra-Oral Dental Scan using AI: academic collaborative robotics project applied to healthcare", None),
    ("project", "DevFinOps AI Agent for PLM: AI, automation and PLM environment", None),
    ("achievement", "AI Innovation Hackathon: university hackathon won by the team", "victoire à confirmer"),
    ("experience", "Air France Industries: equipment reliability analysis", "absent des puces du CV approuvé"),
    ("experience", "Airbus Helicopters: 2022 -- 2024", "le CV approuvé indique 09/2023 - 07/2024"),
    ("education", "ESILV specialization: Industry & Robotics", "le CV approuvé indique « Industry 4.0 & Robotics »"),
]

_KIND_PATTERNS = [   # first match wins; the candidate can still change nothing but confirm or reject
    ("certification", r"\b(certif\w*|certified|badge|accr[ée]dit\w*)"),
    ("language", r"\b(toeic|toefl|ielts|cambridge|delf|dele|hsk|langue|language|anglais|english|espagnol|spanish|"
                 r"allemand|german|chinois|mandarin|arabe|arabic|n[ée]erlandais|dutch)\b"),
    ("achievement", r"\b(prix|award|laur[ée]at|gagn\w*|remport\w*|won|winner|distinction|hackathon)"),
    ("education", r"\b(dipl[ôo]m\w*|degree|master|bachelor|licence|mba|semestre|exchange|[ée]change universitaire|cours|course)"),
    ("project", r"\b(projet|project|prototype|github\.com|side project)"),
    ("experience", r"\b(stage|internship|alternance|apprenti\w*|cdd|cdi|freelance|mission|job|poste|worked|travaill\w*)"),
    ("skill", r"\b(ma[iî]tris\w*|comp[ée]tence|skill|outil|tool|logiciel|software|langage|python|sql|excel|cad|catia|"
              r"solidworks|matlab|ros|c\+\+)"),
]


def fact_key(kind: str, text: str) -> str:
    """Accent, case and punctuation insensitive identity, so one claim is never stored twice."""
    flat = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    flat = re.sub(r"[^a-z0-9+#]+", " ", flat).strip()
    return f"{kind}:{flat}"[:160]


def guess_kind(text: str) -> str:
    low = text.lower()
    for kind, pattern in _KIND_PATTERNS:
        if re.search(pattern, low):
            return kind
    return "other"


def clean_statement(text: str) -> str:
    """The candidate's own wording, trimmed of chat filler ("j'ai", "ajoute"), never rewritten."""
    t = " ".join(text.split())
    t = re.sub(r"^(?:ajoute(?:r)?|add|note|retiens?)\s*(?:que|that|:)?\s*", "", t, flags=re.I)
    return t[:MAX_TEXT].strip(" .")


@dataclass(frozen=True)
class Proposal:
    fact: ProfileFact
    created: bool


class ProfileStore:
    def __init__(self, session: Session) -> None:
        self.s = session

    def seed(self) -> int:
        """Insert the brief's unconfirmed facts once (idempotent by key). Returns the number added."""
        added = 0
        for kind, text, note in SEED:
            key = fact_key(kind, text)
            if self._by_key(key) is None:
                self.s.add(ProfileFact(key=key, kind=kind, text=text, status=INFERRED, source=SEED_SOURCE, note=note))
                added += 1
        self.s.flush()
        return added

    def _by_key(self, key: str) -> ProfileFact | None:
        return self.s.execute(select(ProfileFact).where(ProfileFact.key == key)).scalar_one_or_none()

    def get(self, fact_id: int) -> ProfileFact | None:
        return self.s.get(ProfileFact, fact_id)

    def facts(self, *statuses: str) -> list[ProfileFact]:
        q = select(ProfileFact).order_by(ProfileFact.kind, ProfileFact.id)
        if statuses:
            q = q.where(ProfileFact.status.in_(statuses))
        return list(self.s.execute(q).scalars())

    def propose(self, text: str, kind: str | None = None, source: str = "message Telegram") -> Proposal:
        """Record a statement as INFERRED, awaiting confirmation. An existing claim is returned unchanged."""
        statement = clean_statement(text)
        if not statement:
            raise ValueError("empty statement")
        kind = kind if kind in KINDS else guess_kind(statement)
        key = fact_key(kind, statement)
        if (existing := self._by_key(key)) is not None:
            return Proposal(existing, False)
        fact = ProfileFact(key=key, kind=kind, text=statement, status=INFERRED,
                           source=f"{source} ({utcnow():%d/%m/%Y})")
        self.s.add(fact)
        self.s.flush()
        return Proposal(fact, True)

    def decide(self, fact_id: int, status: str) -> ProfileFact | None:
        if status not in {USER_CONFIRMED, REJECTED, INFERRED}:
            raise ValueError(status)
        fact = self.get(fact_id)
        if fact is None:
            return None
        fact.status = status
        fact.decided_at = None if status == INFERRED else utcnow()
        self.s.flush()
        return fact

    def claims(self, profile_raw: dict[str, Any], cv: dict[str, Any]) -> list[knowledge.Claim]:
        """The full knowledge base: approved YAML claims plus the stored facts, each with its status."""
        out = knowledge.build(profile_raw, cv)
        status_map = {USER_CONFIRMED: knowledge.USER_CONFIRMED, INFERRED: knowledge.INFERRED,
                      MISSING: knowledge.UNKNOWN}
        for f in self.facts(USER_CONFIRMED, INFERRED, MISSING):
            out.append(knowledge.Claim(f.kind, f.text, f.source, status_map[f.status], ref=f"fact:{f.id}"))
        return out

    def confirmed(self, kind: str | None = None) -> list[ProfileFact]:
        return [f for f in self.facts(USER_CONFIRMED) if kind is None or f.kind == kind]
