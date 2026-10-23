"""Input validation shared by Telegram, the CLI and the Excel import."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

MAX_NOTE = 4000
MAX_SHORT = 300
APP_ID = re.compile(r"^\s*(?:APP-?)?0*(\d{1,7})\s*$", re.IGNORECASE)
CONTACT_ID = re.compile(r"^\s*(?:C-?)?0*(\d{1,7})\s*$", re.IGNORECASE)
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y")


class ValidationError(ValueError):
    pass


def app_code(app_id: int) -> str:
    return f"APP-{app_id:04d}"


def contact_code(contact_id: int) -> str:
    return f"C-{contact_id:04d}"


def parse_app_id(value: object) -> int:
    m = APP_ID.match(str(value or ""))
    if not m:
        raise ValidationError(f"invalid application id {value!r} (expected APP-0001)")
    return int(m.group(1))


def parse_contact_id(value: object) -> int:
    m = CONTACT_ID.match(str(value or ""))
    if not m:
        raise ValidationError(f"invalid contact id {value!r} (expected C-0001)")
    return int(m.group(1))


def parse_date(value: object, field: str = "date") -> date | None:
    """Accepts a date, a datetime (Excel cells) or ISO / day-first text. Empty means None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValidationError(f"{field}: {text!r} is not a date (use YYYY-MM-DD)")


def parse_datetime(value: object, tz: str, field: str = "date") -> datetime | None:
    """A local date-time (Excel cell or 'YYYY-MM-DD HH:MM'); a bare date means 09:00 local."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    zone = ZoneInfo(tz)
    if isinstance(value, datetime):
        local = value if value.tzinfo else value.replace(tzinfo=zone)
        return local.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time(9, 0), zone).astimezone(timezone.utc)
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=zone).astimezone(timezone.utc)
        except ValueError:
            continue
    d = parse_date(text, field)
    return datetime.combine(d, time(9, 0), zone).astimezone(timezone.utc) if d else None


def check_url(value: object, field: str = "url") -> str | None:
    if value is None or not str(value).strip():
        return None
    url = str(value).strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or len(url) > 1000:
        raise ValidationError(f"{field}: only http(s) links are accepted")
    return url


def clean_text(value: object, field: str, limit: int = MAX_SHORT) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\r\n", "\n").strip()
    if len(text) > limit:
        raise ValidationError(f"{field}: {len(text)} characters, the limit is {limit}")
    return text or None


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


# Excel formula injection: a cell whose text starts with one of these is evaluated by spreadsheet apps.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")


def excel_safe(value: object) -> object:
    """Neutralize untrusted text before it reaches a cell (OWASP CSV/formula injection guidance)."""
    if isinstance(value, str) and value.startswith(FORMULA_TRIGGERS):
        return "'" + value
    return value


def excel_unescape(value: object) -> object:
    """Undo excel_safe on the way back in, so a round trip never alters a note."""
    if isinstance(value, str) and len(value) > 1 and value[0] == "'" and value[1:].startswith(FORMULA_TRIGGERS):
        return value[1:]
    return value
