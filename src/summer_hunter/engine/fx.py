"""Exchange rates to EUR, with their source and date.

Primary source: ECB euro foreign exchange reference rates (official, daily, ~30 currencies).
Fallback: open.er-api.com (ExchangeRate-API open endpoint) for currencies the ECB does not
publish (MAD, TND, EGP, NGN, KES, AED...). Fixed pegs (CFA francs) are exact. A converted amount
always carries the rate, its date and its source; an unknown currency is never guessed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from summer_hunter.database.models import FxRate

log = logging.getLogger(__name__)
ECB_NAME = "ECB euro reference rates"
ER_NAME = "open.er-api.com (ExchangeRate-API)"


@dataclass(frozen=True)
class Rate:
    currency: str
    per_eur: float          # units of currency for 1 EUR
    rate_date: date | None
    source: str


@dataclass
class FxTable:
    rates: dict[str, Rate] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: list[FxRate], pegs: dict[str, float] | None = None) -> "FxTable":
        table = cls({"EUR": Rate("EUR", 1.0, None, "identity")})
        for row in sorted(rows, key=lambda r: r.rate_date):
            table.rates[row.currency] = Rate(row.currency, row.per_eur, row.rate_date, row.source)
        for code, value in (pegs or {}).items():          # a treaty peg is exact: it wins over market quotes
            table.rates[code.upper()] = Rate(code.upper(), float(value), None, "fixed peg")
        return table

    def to_eur(self, amount: float | None, currency: str | None) -> tuple[float | None, Rate | None]:
        if amount is None or not currency:
            return None, None
        rate = self.rates.get(currency.upper())
        if rate is None or rate.per_eur <= 0:
            return None, None
        return round(amount / rate.per_eur, 2), rate


def load(session: Session, cfg: dict[str, Any]) -> FxTable:
    return FxTable.from_rows(list(session.execute(select(FxRate)).scalars()), (cfg.get("fx") or {}).get("pegs"))


def parse_ecb(xml: str) -> tuple[date | None, dict[str, float]]:
    day = re.search(r"time=['\"](\d{4}-\d{2}-\d{2})['\"]", xml)
    rates = {c: float(v) for c, v in re.findall(r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9.]+)['\"]", xml)}
    return (date.fromisoformat(day.group(1)) if day else None), rates


def parse_er(data: dict[str, Any]) -> tuple[date | None, dict[str, float]]:
    if data.get("result") != "success":
        return None, {}
    stamp = data.get("time_last_update_unix")
    day = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date() if stamp else None
    return day, {k: float(v) for k, v in (data.get("rates") or {}).items() if isinstance(v, (int, float)) and v > 0}


def refresh(session: Session, cfg: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch today's rates (ECB first, fallback for the rest) and store them. Never raises on network errors."""
    fx = cfg.get("fx") or {}
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"User-Agent": "SummerHunter2027/1.0"})
    stored, errors = 0, []
    try:
        merged: dict[str, tuple[float, date | None, str]] = {}
        try:
            day, rates = parse_ecb(client.get(fx.get("primary_url", "")).text)
            merged.update({c: (v, day, ECB_NAME) for c, v in rates.items()})
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"ECB: {type(exc).__name__}")
        try:
            day, rates = parse_er(client.get(fx.get("fallback_url", "")).json())
            for c, v in rates.items():
                merged.setdefault(c, (v, day, ER_NAME))
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"fallback: {type(exc).__name__}")
        for code, (value, day, source) in merged.items():
            if code == "EUR" or day is None:
                continue
            row = session.execute(select(FxRate).where(FxRate.currency == code, FxRate.rate_date == day)).scalar_one_or_none()
            if row is None:
                session.add(FxRate(currency=code, per_eur=value, rate_date=day, source=source))
                stored += 1
        session.flush()
    finally:
        if own:
            client.close()
    log.info("fx refreshed", extra={"stored": stored, "errors": errors})
    return {"stored": stored, "currencies": len(merged), "errors": errors}


def stale(session: Session, cfg: dict[str, Any], today: date | None = None) -> bool:
    latest = session.execute(select(FxRate.rate_date).order_by(FxRate.rate_date.desc()).limit(1)).scalar_one_or_none()
    max_age = int((cfg.get("fx") or {}).get("max_age_days", 7))
    return latest is None or (today or date.today()) - latest > timedelta(days=max_age)
