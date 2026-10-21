"""France Diplomatie travel advisories (Conseils aux voyageurs).

The refresh reads each country's public page and records whether the official
"formellement déconseillé" wording appears. That wording is often zone-specific, so the
automatic level is at most PARTIAL_ZONES_ADVISED_AGAINST; only the user can set
WHOLE_COUNTRY_ADVISED_AGAINST (`summer-hunter safety set`). Nothing is excluded automatically.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from summer_hunter.utils.http import HttpError, PoliteClient, RobotsDisallowed
from summer_hunter.utils.text import clean_text

PAGES = (
    "https://www.diplomatie.gouv.fr/fr/information-par-pays/{slug}/dernieres-minutes-et-alertes",
    "https://www.diplomatie.gouv.fr/fr/information-par-pays/{slug}/conseils-aux-voyageurs-securite",
)

SLUGS = {
    "SG": "singapour", "HK": "hong-kong", "TW": "taiwan", "JP": "japon", "KR": "coree-du-sud", "AU": "australie",
    "CN": "chine", "VN": "vietnam", "ID": "indonesie", "TH": "thailande", "AE": "emirats-arabes-unis", "QA": "qatar",
    "IE": "irlande", "GB": "royaume-uni", "CH": "suisse", "DE": "allemagne", "NL": "pays-bas", "SE": "suede",
    "DK": "danemark", "NO": "norvege", "FI": "finlande", "BE": "belgique", "LU": "luxembourg", "ES": "espagne",
    "IT": "italie", "US": "etats-unis", "CA": "canada", "IN": "inde", "MY": "malaisie", "PH": "philippines",
    "SA": "arabie-saoudite", "BR": "bresil", "MX": "mexique", "ZA": "afrique-du-sud", "MA": "maroc", "EG": "egypte",
}

LEVELS = ("NO_FORMAL_WARNING_FOUND", "PARTIAL_ZONES_ADVISED_AGAINST", "WHOLE_COUNTRY_ADVISED_AGAINST", "UNKNOWN")
_FORMAL = re.compile(r"formellement d[ée]conseill[ée]e?s?", re.I)
_IMPERATIVE = re.compile(r"d[ée]conseill[ée]e?s? sauf raison imp[ée]rative", re.I)


def classify_advisory_html(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = clean_text(soup.get_text("\n"))
    hits = []
    for rx in (_FORMAL, _IMPERATIVE):
        for m in rx.finditer(text):
            hits.append(text[max(0, m.start() - 120): m.end() + 120].replace("\n", " "))
    if hits:
        return "PARTIAL_ZONES_ADVISED_AGAINST", " | ".join(hits[:2])[:580]
    return "NO_FORMAL_WARNING_FOUND", "No 'formellement déconseillé' or 'sauf raison impérative' wording on the page."


async def fetch_advisory(http: PoliteClient, code: str) -> tuple[str, str, str]:
    """Read the 'latest alerts' and 'security' pages; returns (level, summary, source_url)."""
    slug = SLUGS.get(code)
    if not slug:
        return "UNKNOWN", "No France Diplomatie page mapped for this country.", ""
    html_parts, errors, first_url = [], [], PAGES[1].format(slug=slug)
    for page in PAGES:
        url = page.format(slug=slug)
        try:
            resp = await http.request("GET", url)
            html_parts.append(resp.text)
        except (HttpError, RobotsDisallowed) as exc:
            errors.append(str(exc))
    if not html_parts:
        return "UNKNOWN", f"Fetch failed: {'; '.join(errors)[:300]}", first_url
    level, summary = classify_advisory_html("\n".join(html_parts))
    if errors:
        summary += f" (one page unavailable: {errors[0][:80]})"
    return level, summary[:600], first_url
