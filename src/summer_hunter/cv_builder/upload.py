"""AI CV Builder lot 5: read an existing CV (PDF or DOCX) and propose what the profile does not hold yet.

Nothing is added automatically: each new line becomes a profile fact "to confirm" (Confirm / Reject
buttons). Lines already in the approved CV or already proposed are skipped. The uploaded file is a
temporary copy, deleted as soon as its text is read.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from summer_hunter.cv_builder.document import _norm
from summer_hunter.cv_builder.document import _tokens as _raw_tokens

MAX_PROPOSALS = 8
_BULLET = re.compile(r"^\s*(?:[•●▪◦‣∙·\-–—*]|\d+[.)])\s+")
_CONTACT = re.compile(r"@|\+?\d[\d .]{7,}\d|https?://|linkedin|github\.com", re.I)
_HEADING = re.compile(r"^[A-ZÀ-Ý &'/,-]{4,}$")


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        from docx import Document

        return "\n".join(p.text for p in Document(path).paragraphs)
    if suffix == ".pdf":
        exe = shutil.which("pdftotext")
        if not exe:
            raise RuntimeError("pdftotext absent")
        out = subprocess.run([exe, "-enc", "UTF-8", "-layout", str(path), "-"], capture_output=True, text=True, timeout=30)
        return out.stdout
    raise ValueError("format non pris en charge (PDF ou DOCX)")


def items(text: str) -> list[str]:
    """Bullet points and sentences, with wrapped lines joined back together."""
    out: list[str] = []
    cur = ""
    for raw in text.splitlines():
        line = re.sub(r"\s{3,}.*$", "", raw.strip()) if "   " in raw.strip() else raw.strip()   # drop right-aligned dates
        if not line:
            if cur:
                out.append(cur)
            cur = ""
            continue
        if _BULLET.match(line) or _HEADING.match(line):
            if cur:
                out.append(cur)
            cur = "" if _HEADING.match(line) else _BULLET.sub("", line)
            continue
        if cur and not cur.endswith("."):
            cur = f"{cur} {line}"            # wrapped continuation of the same bullet
        else:
            if cur:
                out.append(cur)
            cur = line
    if cur:
        out.append(cur)
    return [re.sub(r"\s+", " ", i).strip() for i in out]


def _tokens(text: str) -> set[str]:
    return {w.strip(".-") for w in _raw_tokens(text)} - {""}


def _similar(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


def novel_lines(text: str, known: list[str]) -> list[str]:
    """Lines of the uploaded CV that no approved or proposed text already covers."""
    known_tokens = [_tokens(k) for k in known if len(k) > 20]
    known_norm = {_norm(k) for k in known}
    vocab = set().union(*known_tokens, *(_tokens(k) for k in known)) if known else set()
    found: list[str] = []
    for line in items(text):
        if not 30 <= len(line) <= 300 or _CONTACT.search(line) or line.isupper():
            continue
        toks = _tokens(line)
        if _norm(line) in known_norm or len(toks) < 4:
            continue
        if any(_similar(toks, k) >= 0.55 or len(toks & k) / len(toks) >= 0.85 for k in known_tokens):
            continue
        if len(toks & vocab) / len(toks) >= 0.85:     # a merge of known pieces (dates, headers wrapped together)
            continue
        if any(_similar(toks, _tokens(f)) >= 0.7 for f in found):
            continue
        found.append(line)
        if len(found) >= MAX_PROPOSALS:
            break
    return found
