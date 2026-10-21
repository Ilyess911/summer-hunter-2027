"""Safe handling of untrusted external text and HTML."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata

from bs4 import BeautifulSoup

_WS = re.compile(r"[ \t\r\f\v]+")
_MULTI_NL = re.compile(r"\n{3,}")

# Phrases typical of prompt-injection attempts embedded in job content.
INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
        r"disregard (all |any )?(the )?(previous|prior|above|scoring) (instructions|rules)",
        r"(you are|act as) (now )?(an? )?(ai|assistant|language model)",
        r"system prompt",
        r"(give|assign|rate|score) (this|the) (job|role|position|candidate|opportunity)? ?(a )?(score of )?100",
        r"mark (this|the) (job|role|opportunity) as (eligible|top|priority)",
        r"<\s*/?\s*(system|instructions?|untrusted_job_posting)\s*>",
        r"\bnew instructions\b",
    )
]


def html_to_text(raw: str | None) -> str:
    """Convert untrusted HTML to plain text. Scripts, styles and forms are dropped, never executed."""
    if not raw:
        return ""
    if "<" not in raw and "&" in raw:
        raw = html.unescape(raw)
    if "&lt;" in raw:  # Greenhouse returns entity-encoded HTML
        raw = html.unescape(raw)
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "form", "svg", "template"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "tr", "ul", "ol"]):
        block.insert_before("\n")
        if block.name == "li":
            block.insert_before("• ")
    text = soup.get_text()
    return clean_text(text)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(" ", " ").replace("​", "")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return _MULTI_NL.sub("\n\n", text).strip()


def normalize_for_match(text: str) -> str:
    """Lowercase, accent-free, single-spaced text used for evidence matching."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def contains_quote(haystack: str, quote: str, min_len: int = 8) -> bool:
    """True when `quote` appears verbatim (modulo whitespace/case/accents) in `haystack`."""
    q = normalize_for_match(quote).strip(" .\"'")
    if len(q) < min_len:
        return False
    return q in normalize_for_match(haystack)


def detect_injection(text: str) -> list[str]:
    hits = []
    for pattern in INJECTION_PATTERNS:
        match = pattern.search(text or "")
        if match:
            hits.append(match.group(0))
    return hits


def neutralize_for_prompt(text: str, limit: int = 12000) -> str:
    """Prepare untrusted text for an LLM prompt: strip tag look-alikes that could close our delimiters."""
    text = re.sub(r"<\s*/?\s*(untrusted_job_posting|untrusted_post|system|instructions?)[^>]*>", "[removed-tag]", text or "", flags=re.I)
    return text[:limit]


def snippet(text: str, start: int, end: int, radius: int = 90) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    out = text[lo:hi].replace("\n", " ").strip()
    return ("…" if lo > 0 else "") + out + ("…" if hi < len(text) else "")


def sha256(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update((part or "").encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def truncate(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """Split into sentence-like chunks with offsets (bullets, newlines, ., ;, !, ?)."""
    out = []
    for match in re.finditer(r"[^\n.;!?•]+[.;!?]?", text or ""):
        chunk = match.group(0).strip()
        if chunk:
            out.append((match.start(), match.end(), chunk))
    return out
