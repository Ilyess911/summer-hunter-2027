"""AI CV Builder lot 5: persistent CV preferences (/cv_settings), stored in kv_state as JSON.

A preference is a default: anything the request states explicitly ("en français", "2 pages") wins.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from summer_hunter.cv_builder.document import STYLES
from summer_hunter.cv_builder.intent import CVRequest, detect_pages, detect_style

KEY = "cv_settings"
OPTIONS: dict[str, list[str]] = {
    "language": ["auto", "en", "fr"],
    "pages": ["1", "2"],
    "style": list(STYLES),
    "formats": ["pdf+docx", "pdf", "docx"],
    "personalization": ["standard", "light"],
}
DEFAULTS = {"language": "auto", "pages": "1", "style": "ats_classic", "formats": "pdf+docx", "personalization": "standard"}
LABEL_FR = {
    "language": "Langue par défaut", "pages": "Nombre de pages", "style": "Style", "formats": "Formats envoyés",
    "personalization": "Personnalisation",
}
VALUE_FR = {"auto": "auto (selon le pays ou l'offre)", "en": "anglais", "fr": "français", "standard": "standard",
            "light": "légère (sans phrase ciblée)", "pdf+docx": "PDF + DOCX", "pdf": "PDF seul", "docx": "DOCX seul",
            "ats_classic": "ATS Classic"}


def load(repo) -> dict[str, str]:  # noqa: ANN001 - Repository
    raw = repo.get_kv(KEY)
    try:
        stored = json.loads(raw) if raw else {}
    except ValueError:
        stored = {}
    return {k: (str(stored[k]) if str(stored.get(k)) in OPTIONS[k] else v) for k, v in DEFAULTS.items()}


def cycle(repo, key: str) -> dict[str, str]:  # noqa: ANN001
    if key not in OPTIONS:
        raise KeyError(key)
    prefs = load(repo)
    opts = OPTIONS[key]
    prefs[key] = opts[(opts.index(prefs[key]) + 1) % len(opts)]
    repo.set_kv(KEY, json.dumps(prefs))
    return prefs


def apply(req: CVRequest, prefs: dict[str, Any], text: str) -> CVRequest:
    """Fill what the request did not state with the saved defaults."""
    changes: dict[str, Any] = {}
    if detect_pages(text) is None:
        changes["pages"] = int(prefs["pages"])
    if req.language_reason != "demandé" and prefs["language"] != "auto":
        changes["language"], changes["language_reason"] = prefs["language"], "préférence /cv_settings"
    if detect_style(text) is None:
        changes["style"] = prefs["style"]
    if prefs["personalization"] != "standard":
        changes["personalization"] = prefs["personalization"]
    return replace(req, **changes) if changes else req


def formats(prefs: dict[str, str]) -> set[str]:
    return set(prefs["formats"].split("+"))
