"""Cross-source deduplication: one canonical opportunity, many source records."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from summer_hunter.engine.normalization import normalize_company, normalize_title
from summer_hunter.utils.text import sha256

TITLE_SIMILARITY = 0.86


@dataclass(frozen=True)
class DedupKey:
    company: str
    title: str
    country: str

    @property
    def canonical(self) -> str:
        return sha256(self.company, self.title, self.country)[:32]


def make_key(company: str, title: str, country: str | None) -> DedupKey:
    return DedupKey(normalize_company(company), normalize_title(title), (country or "XX").upper())


def title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    return max(ratio, jaccard)


@dataclass
class Candidate:
    opportunity_id: int
    company: str        # normalized
    title: str          # normalized
    country: str
    official_job_id: str | None = None


def find_match(key: DedupKey, official_job_id: str | None, candidates: list[Candidate]) -> int | None:
    """Return the id of the existing opportunity this listing duplicates, or None.

    Order: same official requisition id at the same company; exact canonical key; fuzzy title
    match at the same company in a compatible country. Ambiguous fuzzy matches are not merged.
    """
    same_company = [c for c in candidates if c.company == key.company]
    if official_job_id:
        for c in same_company:
            if c.official_job_id and c.official_job_id == official_job_id:
                return c.opportunity_id
    for c in same_company:
        if c.title == key.title and c.country == key.country:
            return c.opportunity_id
    compatible = [c for c in same_company if c.country == key.country or "XX" in (c.country, key.country)]
    scored = sorted(((title_similarity(key.title, c.title), c) for c in compatible), key=lambda x: -x[0])
    if scored and scored[0][0] >= TITLE_SIMILARITY:
        if len(scored) > 1 and scored[1][0] >= TITLE_SIMILARITY and scored[1][1].opportunity_id != scored[0][1].opportunity_id:
            return None
        return scored[0][1].opportunity_id
    return None
