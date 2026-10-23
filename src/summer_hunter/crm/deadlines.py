"""Explicit application deadlines found in a posting. Nothing is inferred: no year, no deadline."""

from __future__ import annotations

import re
from datetime import date, datetime

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9

_MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
_DATE = (rf"(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH.replace('(', '(?P<m1>', 1)}\s*,?\s+(?P<y1>20\d\d)"
         rf"|{_MONTH.replace('(', '(?P<m2>', 1)}\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+(?P<y2>20\d\d)"
         r"|(?P<iso>20\d\d-\d\d-\d\d)"
         r"|(?P<dmy>\d{1,2}/\d{1,2}/20\d\d)")
_CUE = (r"(?:application\s+deadline|deadline\s+(?:to|for)\s+appl\w*|deadline|apply\s+(?:by|before|until)"
        r"|closing\s+date|applications?\s+(?:close|closes|are\s+open\s+until|must\s+be\s+received\s+by))")
PATTERN = re.compile(rf"{_CUE}\s*(?:is|on|:|-|–)?\s*(?:the\s+)?(?:{_DATE})", re.IGNORECASE)


def _to_date(m: re.Match) -> date | None:
    try:
        if m.group("iso"):
            return date.fromisoformat(m.group("iso"))
        if m.group("dmy"):
            return datetime.strptime(m.group("dmy"), "%d/%m/%Y").date()
        if m.group("m1"):
            return date(int(m.group("y1")), MONTHS[m.group("m1").lower().rstrip(".")], int(m.group("d1")))
        return date(int(m.group("y2")), MONTHS[m.group("m2").lower().rstrip(".")], int(m.group("d2")))
    except (ValueError, KeyError):
        return None


def explicit_deadline(text: str | None) -> date | None:
    """The first deadline the posting states with a full date (day, month and year)."""
    for m in PATTERN.finditer(text or ""):
        found = _to_date(m)
        if found:
            return found
    return None
