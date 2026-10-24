"""Internship compensation intelligence: what the posting says about pay, and what it is worth.

Principles (enforced here, tested in tests/integration/test_compensation.py):
- A salary is confirmed only when the text states an amount. No amount is UNKNOWN, never zero,
  never "unpaid". A multinational is not assumed to pay.
- Ranges keep both ends; the conservative end (minimum) drives estimates, never the maximum.
- Gross and net are never mixed: the basis is recorded and an unknown tax treatment stays unknown.
- Hourly pay becomes monthly only with the advertised hours; otherwise a labelled estimate.
- Living costs and pay benchmarks come only from config/compensation.yaml rows with a source.
- Missing inputs give a partial result with its confidence, not a misleading exact figure.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from summer_hunter.engine.fx import FxTable
from summer_hunter.utils.text import normalize_for_match

PAID_CONFIRMED = "PAID_CONFIRMED"
PAID_AMOUNT_UNKNOWN = "PAID_AMOUNT_UNKNOWN"
UNPAID_CONFIRMED = "UNPAID_CONFIRMED"
COMPENSATION_UNKNOWN = "COMPENSATION_UNKNOWN"
MIXED_OR_CONDITIONAL = "MIXED_OR_CONDITIONAL"
OFFICIAL_SOURCES = {"greenhouse", "lever", "ashby", "workday", "career_page", "manual", "link"}

CURRENCY_CODES = ["USD", "EUR", "GBP", "CHF", "SGD", "AUD", "CAD", "HKD", "JPY", "CNY", "KRW", "TWD", "NZD", "SEK", "NOK",
                  "DKK", "PLN", "CZK", "HUF", "AED", "QAR", "SAR", "MAD", "TND", "EGP", "NGN", "KES", "GHS", "XOF", "XAF",
                  "RWF", "TZS", "UGX", "ZAR", "BWP", "NAD", "MUR", "INR", "MXN", "BRL", "THB", "MYR", "IDR", "VND", "PHP"]
SYMBOLS = {"€": "EUR", "£": "GBP", "¥": "JPY", "s$": "SGD", "a$": "AUD", "c$": "CAD", "hk$": "HKD", "nz$": "NZD",
           "chf": "CHF", "fr.": "CHF", "dh": "MAD", "dhs": "MAD", "mad": "MAD", "kr": "SEK", "r": "ZAR", "ksh": "KES",
           "us$": "USD", "ca$": "CAD", "au$": "AUD", "sg$": "SGD",
           "₦": "NGN", "fcfa": "XOF", "cfa": "XOF", "aed": "AED", "e£": "EGP", "dirham": "MAD", "dirhams": "MAD",
           "euro": "EUR", "euros": "EUR", "rand": "ZAR", "naira": "NGN", "cedi": "GHS", "cedis": "GHS"}
COUNTRY_WORD_CURRENCY = {("dollar", "US"): "USD", ("dollars", "US"): "USD", ("shilling", "KE"): "KES", ("shillings", "KE"): "KES",
                         ("shilling", "UG"): "UGX", ("shillings", "UG"): "UGX", ("shilling", "TZ"): "TZS", ("shillings", "TZ"): "TZS",
                         ("dinar", "TN"): "TND", ("dinars", "TN"): "TND"}
DOLLAR_BY_COUNTRY = {"US": "USD", "SG": "SGD", "AU": "AUD", "CA": "CAD", "HK": "HKD", "NZ": "NZD", "TW": "TWD", "MX": "MXN"}
LOCAL_CURRENCY = {"US": "USD", "GB": "GBP", "CH": "CHF", "SG": "SGD", "AU": "AUD", "CA": "CAD", "HK": "HKD", "JP": "JPY",
                  "CN": "CNY", "KR": "KRW", "TW": "TWD", "NZ": "NZD", "SE": "SEK", "NO": "NOK", "DK": "DKK", "PL": "PLN",
                  "CZ": "CZK", "HU": "HUF", "AE": "AED", "QA": "QAR", "SA": "SAR", "MA": "MAD", "TN": "TND", "EG": "EGP",
                  "NG": "NGN", "KE": "KES", "GH": "GHS", "SN": "XOF", "CI": "XOF", "BJ": "XOF", "TG": "XOF", "CM": "XAF",
                  "RW": "RWF", "TZ": "TZS", "UG": "UGX", "ZA": "ZAR", "BW": "BWP", "NA": "NAD", "MU": "MUR", "IN": "INR",
                  "MX": "MXN", "BR": "BRL", "TH": "THB", "MY": "MYR", "ID": "IDR", "VN": "VND", "PH": "PHP"}

_NUM = (r"(?<![\d.,])(?:\d{1,2}(?:,\d{2})+,\d{3}"                       # Indian grouping 1,50,000
        r"|\d{1,3}(?:[ ,.\u202f]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![.,]?\d)")
_CUR_BEFORE = (r"(?<![a-z])(?P<cb>us\$|ca\$|au\$|sg\$|hk\$|nz\$|s\$|a\$|c\$|e£|\$|€|£|¥|₦|"
               + "|".join(c.lower() for c in CURRENCY_CODES) + r")\s?")
_CUR_AFTER = r"\s?(?P<ca>€|£|\$|" + "|".join(c.lower() for c in CURRENCY_CODES) + r"|dirhams?|dhs?|fcfa|chf|ksh|euros?|dollars?|rand|naira|shillings?|cedis?|dinars?)\b"
_AMOUNT = rf"(?P<a1>{_NUM})\s?(?P<k1>k)?"
_AMOUNT2 = rf"(?P<a2>{_NUM})\s?(?P<k2>k)?"
_RANGE_SEP = r"\s?(?:-|–|—|to|and|à|a)\s?"
_PERIOD = (r"(?P<period>per hour|an hour|/\s?h(?:ou)?r\b|/\s?h\b|hourly|de l.heure|par heure|/heure|"
           r"per week|a week|/\s?w(?:ee)?k\b|weekly|par semaine|"
           r"per month|a month|/\s?mo(?:nth)?\b|monthly|par mois|/\s?mois|mensuel\w*|p\.?m\.?\b|"
           r"per year|a year|/\s?y(?:ea)?r\b|annual\w*|yearly|per annum|p\.?a\.?\b|par an|/\s?an\b|"
           r"total|for the (whole |entire )?internship|pour (la duree du |le )?stage)")
PAY_PATTERNS = [
    re.compile(rf"{_CUR_BEFORE}{_AMOUNT}(?:{_RANGE_SEP}(?:{_CUR_BEFORE.replace('cb', 'cb2')})?{_AMOUNT2})?(?:\s?(?:usd|eur|gbp|chf|sgd))?"
               rf"(?:\s?(?:gross|brut|net|before tax|after tax))?\s?(?:{_PERIOD})?"),
    re.compile(rf"{_AMOUNT}(?:{_RANGE_SEP}{_AMOUNT2})?{_CUR_AFTER}(?:\s?(?:gross|brut|net|bruts?))?\s?(?:{_PERIOD})?"),
]
_PAY_CONTEXT = re.compile(r"\b(pay|salary|salaire|compensation|remuneration|rémunération|gratification|stipend|wage|hourly|"
                          r"rate|base|indemnit\w*|allowance|range|per hour|per month|par mois|monthly|annual)\b|/\s?(?:hour|hr|month|mo|year|yr)\b")
_GROSS = re.compile(r"\b(gross|brut|bruts|before tax|pre-tax)\b")
_NET = re.compile(r"\b(net|after tax|take-home)\b")
_HOURS = re.compile(r"\b(\d{2})(?:\s?-\s?(\d{2}))?\s?(?:hours?|hrs?|h)\s?(?:per|a|/|par)\s?(?:week|wk|semaine)\b")
_PAID_NO_AMOUNT = re.compile(r"\b(paid internship|paid (role|position|placement|programme|program)|competitive (pay|salary|"
                             r"stipend|compensation)|stipend (is )?provided|monthly stipend|we offer a stipend|"
                             r"remunerated|stage remunere|stage rémunéré|gratification|compensated)\b")
_CONDITIONAL = re.compile(r"\b(stipend (may|might|could) be|(pay|salary|stipend|compensation) (may|might) (vary|be offered)|"
                          r"eligible for a stipend|(paid|stipend|compensation|pay) depending on (your|the) (university|school|"
                          r"eligibility|visa)|academic credit or (a )?(stipend|pay)|paid or unpaid|unpaid or paid|"
                          r"(stipend|pay) subject to (eligibility|visa))\b")
_UNPAID = re.compile(r"\b(unpaid|no (salary|compensation|stipend|remuneration)|non[- ]remunere|non rémunéré|not paid|"
                     r"voluntary (position|role|basis)|for academic credit only)\b")
_AMOUNT_NO_CURRENCY = re.compile(r"\b(pay|salary|stipend|wage|compensation)\b[^.]{0,25}?\b\d[\d,.]*\s?k?\b")
_UNPAID_NEGATED = re.compile(r"\b(no|not|never|don't|do not)\s+(offer\s+|have\s+)?unpaid\b|\bnot an unpaid\b")
BENEFITS = {
    "housing_provided": r"\b(housing|accommodation|lodging|logement) (is )?(provided|included|offered|fourni|pris en charge)|"
                        r"(free|company|corporate) (housing|accommodation)|we (provide|offer) (housing|accommodation)",
    "housing_allowance": r"\bhousing (allowance|stipend|support|assistance|subsidy)|accommodation (allowance|support|assistance)|"
                         r"aide au logement|prime de logement",
    "relocation": r"\breloca\w* (allowance|assistance|support|package|bonus|stipend)|relocation (is )?(covered|provided)",
    "flight": r"\b(flights?|airfare|travel) (reimburse\w*|covered|paid|provided)|round[- ]trip (flight|ticket)|billet d.avion",
    "transport": r"\b(transport\w*|commut\w*|transit) (allowance|stipend|pass|benefit|subsid\w*)|navigo|titre de transport",
    "meals": r"\b(meal|lunch|food) (allowance|vouchers?|stipend|provided|included)|free (meals|lunch)|tickets? restaurant|"
             r"catered (lunch|meals)",
    "health_insurance": r"\b(health|medical) (insurance|coverage|benefits)|mutuelle",
    "visa_support": r"\bvisa (sponsorship|support|assistance|costs? (covered|reimbursed))|(we|will) sponsor (your )?visa|"
                    r"work permit (support|assistance|sponsorship)",
}
NO_VISA = re.compile(r"\b(no|not|unable to|cannot|can't|do not|does not|will not)\s+(provide\s+|offer\s+)?(visa )?sponsor")


class PayFact(BaseModel):
    amount_min: float
    amount_max: float
    currency: str | None
    period: str | None                 # hour | week | month | year | total | None
    basis: str                         # gross | net | unknown
    wording: str


class CompensationReport(BaseModel):
    status: str
    confidence: str                    # HIGH | MEDIUM | LOW | UNKNOWN
    pay: PayFact | None = None
    hours_per_week: float | None = None
    hours_min: float | None = None
    hours_max: float | None = None
    hours_source: str | None = None    # posting | assumption
    monthly_min: float | None = None   # in the original currency
    monthly_max: float | None = None
    monthly_is_estimate: bool = False
    monthly_min_eur: float | None = None
    monthly_max_eur: float | None = None
    fx_rate: float | None = None
    fx_date: str | None = None
    fx_source: str | None = None
    local_currency: str | None = None
    monthly_min_local: float | None = None
    benefits: dict[str, str] = Field(default_factory=dict)      # benefit -> evidence
    living_cost_eur: float | None = None
    living_cost_source: str | None = None
    living_cost_breakdown: dict[str, float] = Field(default_factory=dict)
    monthly_balance_eur: float | None = None
    total_outcome_eur: float | None = None
    months: float | None = None
    score: int | None = None
    score_components: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    source: str | None = None
    extracted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


def _num(raw: str, k: str | None) -> float | None:
    s = raw.replace(" ", " ").strip()
    if re.fullmatch(r"\d{1,2}(?:,\d{2})+,\d{3}", s):
        return float(s.replace(",", "")) * (1000 if k else 1)
    if re.fullmatch(r"\d{1,3}(?:[ ,.]\d{3})+(?:[.,]\d{1,2})?", s):
        last = s[-3:]
        if last[0] in ".," and not re.fullmatch(r"\d{1,3}(?:[ ,.]\d{3})+", s):    # 1,234.56 / 1.234,56
            s = re.sub(r"[ ,.]", "", s[:-3]) + "." + s[-2:]
        else:
            s = re.sub(r"[ ,.]", "", s)
    else:
        s = s.replace(",", ".")
    try:
        value = float(s)
    except ValueError:
        return None
    return value * 1000 if k else value


def _period(text: str | None) -> str | None:
    if not text:
        return None
    t = text.lower()
    if re.search(r"hour|/\s?h|heure", t):
        return "hour"
    if re.search(r"week|/\s?w|semaine", t):
        return "week"
    if re.search(r"month|/\s?mo|mois|mensuel|p\.?m", t):
        return "month"
    if re.search(r"year|annu|p\.?a|par an|/\s?an|/\s?y", t):
        return "year"
    if re.search(r"total|internship|stage", t):
        return "total"
    return None


_BENEFIT_AMOUNT = re.compile(r"\b(lunch|meal|food|transport\w*|commut\w*|wellness|learning|gym|phone|internet|home office|"
                             r"relocation|sign-on|signing|bonus|referral|equipment|book|education)\b")
WEAK_CURRENCIES = {"JPY", "KRW", "IDR", "VND", "NGN", "UGX", "TZS", "RWF", "XOF", "XAF", "KES", "EGP", "INR", "PHP", "MAD"}


def _plausible(period: str | None, amount: float, before: str, currency: str) -> str | None:
    """Reject impossible readings: "$67,498 (hourly role)" is an annual range, not $67k an hour."""
    if currency in WEAK_CURRENCIES:
        return period
    if period == "hour" and amount > 500:
        return "year" if amount >= 10000 or re.search(r"\bannual|yearly|per year|a year\b", before) else None
    if period == "week" and amount > 5000:
        return "year" if amount >= 10000 else None
    if period == "month" and amount > 30000:
        return "year"
    if period == "year" and amount < 1000:
        return None                 # "annual base salary $35-$58": inconsistent posting, no monthly figure
    if period == "total" and amount < 300:
        return None
    return period


def _lead_period(before: str) -> str | None:
    """A period word right before the amount: "monthly stipend of €1,800", "salaire mensuel de"."""
    m = re.search(r"(monthly|weekly|hourly|annual|yearly|mensuel\w*|hebdomadaire|horaire)\s+(?:\w+\s+){0,3}$", before)
    return m.group(1) if m else None


def _currency(symbol: str | None, country: str | None) -> str | None:
    if not symbol:
        return None
    s = symbol.lower().strip()
    if s == "$":
        return DOLLAR_BY_COUNTRY.get(country or "")        # a bare $ with no known country stays unknown
    if s.upper() in CURRENCY_CODES:
        return s.upper()
    if s == "r" and country != "ZA":
        return None
    if s in {"dollar", "dollars"}:
        return DOLLAR_BY_COUNTRY.get(country or "")
    return COUNTRY_WORD_CURRENCY.get((s, country or "")) or SYMBOLS.get(s)


_SENTENCE = re.compile(r"[^.;\n•]+(?:\.(?=\d)[^.;\n•]*)*")
_BASE_PAY = re.compile(r"\b(salary|salaire|pay|wage|stipend|compensation|remuneration|rémunération|gratification|base|"
                       r"indemnit\w*|allowance)\b")


def sentences(text: str) -> list[str]:
    """Sentence-like chunks; a decimal point inside an amount ("$33.50") does not end a sentence."""
    return [m.group(0).strip() for m in _SENTENCE.finditer(text) if m.group(0).strip()]


def extract_pay(text: str, country: str | None) -> PayFact | None:
    """The base pay stated in one sentence, with its range, currency, period and basis (all from that sentence)."""
    norm = normalize_for_match(text or "")
    candidates: list[tuple[int, PayFact]] = []
    for sent in sentences(norm):
        if not _PAY_CONTEXT.search(sent):
            continue
        for regex in PAY_PATTERNS:
            for m in regex.finditer(sent):
                around = sent[max(0, m.start() - 45): m.start()] + " " + sent[m.end(): m.end() + 25]
                if _BENEFIT_AMOUNT.search(around):
                    continue                    # "$75 weekly lunch stipend", "a $500 relocation stipend": perks, not pay
                g = m.groupdict()
                lo = _num(g["a1"], g.get("k1"))
                hi = _num(g["a2"], g.get("k2")) if g.get("a2") else lo
                if lo is None or hi is None or lo <= 0:
                    continue
                if g.get("k1") is None and g.get("k2") and lo < 1000 <= hi:      # "40-50k": both ends in thousands
                    lo *= 1000
                if hi < lo:
                    lo, hi = hi, lo
                currency = _currency(g.get("cb") or g.get("cb2") or g.get("ca"), country)
                if currency is None:
                    continue
                after = _HOURS.sub(" ", sent[m.end(): m.end() + 40])          # "40 hours per week" is not a pay period
                before = sent[max(0, m.start() - 40): m.start()]
                period = _period(g.get("period")) or _period(_lead_period(before)) or _period(after[:30])
                period = _plausible(period, lo, sent[max(0, m.start() - 120): m.start()], currency)
                if period is None and lo >= 10000 and currency not in WEAK_CURRENCIES:
                    period = "year"                 # US-style pay-transparency bands are annual
                clause = sent[max(0, m.start() - 40): m.end() + 40]
                gross, net = bool(_GROSS.search(clause)), bool(_NET.search(clause))
                basis = "gross" if gross and not net else "net" if net and not gross else "unknown"
                fact = PayFact(amount_min=lo, amount_max=hi, currency=currency, period=period, basis=basis,
                               wording=sent[max(0, m.start() - 60): m.end() + 60].strip())
                rank = (2 if period else 0) + (1 if _BASE_PAY.search(sent[max(0, m.start() - 60): m.start()]) else 0)
                candidates.append((rank, fact))
    if not candidates:
        return None
    best = max(c[0] for c in candidates)
    return next(f for rank, f in candidates if rank == best)      # the first best-ranked, in document order


_SUBJECT = re.compile(r"\b(intern\w*|position|role|placement|stage|stagiaire|programme|program|this (job|opportunity))\b")
_NEGATION = re.compile(r"\b(no|not|never|cannot|can't|unable|without|do not|does not|don't|doesn't|will not|won't|isn't|aren't)\b|n't\b")


def _status(text: str, pay: PayFact | None) -> str:
    """Paid / unpaid / conditional, judged sentence by sentence about the internship itself."""
    sents = sentences(normalize_for_match(text or ""))
    conditional = any(_CONDITIONAL.search(x) for x in sents)
    unpaid = any(_UNPAID.search(x) and _SUBJECT.search(x) and not _UNPAID_NEGATED.search(x) and "overtime" not in x
                 for x in sents)
    paid_words = any(_PAID_NO_AMOUNT.search(x) and not _NEGATION.search(x) for x in sents)
    if pay is not None:
        return MIXED_OR_CONDITIONAL if conditional or unpaid else PAID_CONFIRMED
    if unpaid:
        return MIXED_OR_CONDITIONAL if conditional or paid_words else UNPAID_CONFIRMED
    if conditional:
        return MIXED_OR_CONDITIONAL
    if paid_words or any(_AMOUNT_NO_CURRENCY.search(x) for x in sents):
        return PAID_AMOUNT_UNKNOWN
    return COMPENSATION_UNKNOWN


def _benefits(text: str) -> dict[str, str]:
    """Benefits stated positively; "visa support is not provided" or "no relocation" never count."""
    out = {}
    for sent in sentences(normalize_for_match(text or "")):
        for name, rx in BENEFITS.items():
            m = re.search(rx, sent)
            if not m or name in out or _NEGATION.search(sent) or (name == "visa_support" and NO_VISA.search(sent)):
                continue
            out[name] = sent[max(0, m.start() - 50): m.end() + 50].strip()
    return out


def _living(cfg: dict[str, Any], city: str | None, country: str | None) -> tuple[dict[str, float], str] | None:
    rows = cfg.get("living_costs") or []
    keys = [f"{city}, {country}".lower()] if city and country else []
    keys.append((country or "").lower())
    for key in keys:
        for row in rows:
            if str(row.get("place", "")).lower() == key and row.get("source"):
                src = f"{row['source']} ({row.get('date', 'undated')})"
                if row.get("total") is not None and not any(row.get(k) is not None for k in ("housing", "transport", "food")):
                    return {"total": float(row["total"])}, src
                if any(row.get(k) is None for k in ("housing", "transport", "food")):
                    return None                 # incomplete row: never assume a missing cost is zero
                parts = {k: float(row[k]) for k in ("housing", "transport", "food")}
                parts.update({k: float(row[k]) for k in ("insurance", "other") if row.get(k) is not None})
                return parts, src
    return None


def _benchmark(cfg: dict[str, Any], country: str | None) -> tuple[float, str] | None:
    for row in cfg.get("benchmarks") or []:
        if str(row.get("country", "")).upper() == (country or "") and row.get("source") and row.get("monthly_eur"):
            return float(row["monthly_eur"]), f"{row['source']} ({row.get('date', 'undated')})"
    return None


def _scale(value: float, lo: float, hi: float) -> float:
    if hi <= lo:                       # misconfigured scale: neutral instead of a crash
        return 50.0
    return max(0.0, min(100.0, 100.0 * (value - lo) / (hi - lo)))


def assess(text: str, country: str | None, city: str | None, source: str | None, weeks: float | None,
           cfg: dict[str, Any], fx: FxTable) -> CompensationReport:
    pay = extract_pay(text, country)
    status = _status(text, pay)
    official = (source or "").split(":")[0] in OFFICIAL_SOURCES
    rep = CompensationReport(status=status, confidence="UNKNOWN", pay=pay, source=source, benefits=_benefits(text),
                             local_currency=LOCAL_CURRENCY.get(country or ""))
    if pay:
        rep.confidence = "HIGH" if official else "MEDIUM"
        if pay.basis == "unknown":
            rep.notes.append("Brut ou net non précisé : montants tels qu’annoncés, fiscalité inconnue.")
    if status == UNPAID_CONFIRMED:
        rep.confidence = "HIGH" if official else "MEDIUM"
    hcfg = cfg.get("hours") or {}
    hm = _HOURS.search(normalize_for_match(text or ""))
    hours_lo = hours_hi = None
    if hm:
        hours_lo, hours_hi = float(hm.group(1)), float(hm.group(2) or hm.group(1))
        rep.hours_per_week = hours_lo if hours_lo == hours_hi else None
        rep.hours_min, rep.hours_max = hours_lo, hours_hi
        rep.hours_source = "posting"
    wpm = float(hcfg.get("weeks_per_month") or 4.333)
    wpm = wpm if wpm > 0 else 4.333
    rep.months = round(weeks / wpm, 2) if weeks else None

    # Monthly normalization -----------------------------------------------------------------------
    if pay and pay.period:
        factor = None
        if pay.period == "month":
            factor = 1.0
        elif pay.period == "week":
            factor = wpm
        elif pay.period == "year":
            factor = 1 / 12
        elif pay.period == "hour" and hours_lo is not None:
            # A range of hours gives a range of pay: low hours x low rate .. high hours x high rate.
            rep.monthly_min = round(pay.amount_min * hours_lo * wpm, 2)
            rep.monthly_max = round(pay.amount_max * hours_hi * wpm, 2)
        elif pay.period == "hour":
            hours = None
            if hcfg.get("allow_hourly_estimate", True):
                hours = float(hcfg.get("assumed_hours_per_week_for_estimates", 40))
                rep.hours_source = "assumption"
                rep.monthly_is_estimate = True
                rep.notes.append(f"Paie horaire : montant mensuel estimé sur {hours:g} h/semaine (heures non précisées).")
            factor = hours * wpm if hours else None
        elif pay.period == "total":
            factor = (1 / rep.months) if rep.months else None
            if factor is None:
                rep.notes.append("Gratification totale du stage : montant mensuel inconnu sans la durée.")
        if factor and rep.monthly_min is None:
            rep.monthly_min = round(pay.amount_min * factor, 2)
            rep.monthly_max = round(pay.amount_max * factor, 2)
    elif pay:
        rep.notes.append("Période de paie non précisée : aucun montant mensuel calculé.")
    if rep.monthly_min is not None:
        rep.monthly_min_eur, rate = fx.to_eur(rep.monthly_min, pay.currency)
        rep.monthly_max_eur, _ = fx.to_eur(rep.monthly_max, pay.currency)
        if rate:
            rep.fx_rate, rep.fx_source = rate.per_eur, rate.source
            rep.fx_date = rate.rate_date.isoformat() if rate.rate_date else None
            local = fx.rates.get(rep.local_currency or "")
            if local and rep.local_currency != pay.currency and rep.monthly_min_eur is not None:
                rep.monthly_min_local = round(rep.monthly_min_eur * local.per_eur, 0)
        else:
            rep.notes.append(f"Pas de taux de change pour {pay.currency} : équivalent en euros indisponible.")
        if pay.amount_max > pay.amount_min * 2.5:
            rep.notes.append("Fourchette large (sans doute plusieurs niveaux) : les estimations prennent le minimum.")

    # Living costs and outcome ------------------------------------------------------------------------
    living = _living(cfg, city, country)
    if living:
        parts, src = living
        if "housing_provided" in rep.benefits and "housing" in parts:
            parts["housing"] = 0.0           # provided housing lowers costs; it is not cash income
        rep.living_cost_breakdown = parts
        rep.living_cost_eur = round(sum(parts.values()), 2)
        rep.living_cost_source = src
    else:
        rep.notes.append("Aucun coût de la vie sourcé pour ce lieu : solde disponible inconnu.")
    if rep.monthly_min_eur is not None and rep.living_cost_eur is not None:
        rep.monthly_balance_eur = round(rep.monthly_min_eur - rep.living_cost_eur, 2)
        if rep.months:
            rep.total_outcome_eur = round(rep.monthly_balance_eur * rep.months, 2)
            rep.notes.append("Frais de déménagement et de visa non remboursés : inconnus, non déduits.")
    if status == UNPAID_CONFIRMED and rep.living_cost_eur is not None:
        rep.monthly_balance_eur = -rep.living_cost_eur
    rep.score, rep.score_components = score(rep, cfg, country)
    return rep


def score(rep: CompensationReport, cfg: dict[str, Any], country: str | None) -> tuple[int | None, dict[str, float]]:
    """0-100 attractiveness. None when nothing is known about the amount (never a fake zero)."""
    sc = cfg.get("scoring") or {}
    if rep.status == UNPAID_CONFIRMED:
        return int(sc.get("unpaid_score", 0)), {"unpaid": 0.0}
    if rep.monthly_min_eur is None:
        return None, {}
    w = sc.get("weights") or {}
    comps: dict[str, float] = {}
    if rep.monthly_balance_eur is not None:
        comps["disposable_balance"] = _scale(rep.monthly_balance_eur, *sc.get("balance_scale_eur", [-500, 1500]))
    bench = _benchmark(cfg, country)
    if bench:
        comps["benchmark"] = max(0.0, min(100.0, 50.0 * rep.monthly_min_eur / bench[0]))
    housing = 0.0
    housing += 60 if "housing_provided" in rep.benefits else 35 if "housing_allowance" in rep.benefits else 0
    housing += 25 if "relocation" in rep.benefits else 0
    housing += 15 if "flight" in rep.benefits else 0
    comps["housing_relocation"] = min(100.0, housing)
    if rep.total_outcome_eur is not None:
        comps["total_outcome"] = _scale(rep.total_outcome_eur, *sc.get("total_outcome_scale_eur", [-2000, 8000]))
    comps["confidence"] = float((sc.get("confidence_points") or {}).get(rep.confidence, 0))
    weights = {k: float(w.get(k, 0)) for k in comps}
    if "disposable_balance" not in comps and "benchmark" not in comps:
        # No living costs nor benchmark: nominal pay is the only pay signal (flagged, not cost-adjusted).
        comps["nominal_pay"] = _scale(rep.monthly_min_eur, *sc.get("monthly_scale_eur", [0, 3000]))
        weights["nominal_pay"] = float(w.get("disposable_balance", 0.30)) + float(w.get("benchmark", 0.25))
        rep.notes.append("Score sur la paie nominale : pas de coût de la vie ni de repère sourcé pour ce lieu.")
    total_w = sum(weights.values()) or 1.0
    value = sum(comps[k] * weights[k] for k in comps) / total_w
    if rep.monthly_is_estimate:
        value *= 0.9
    return int(round(value)), {k: round(v, 1) for k, v in comps.items()}
