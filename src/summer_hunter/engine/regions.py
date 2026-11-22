"""World regions used for filters and statistics (Telegram, Mini App, analytics)."""

from __future__ import annotations

REGIONS = {
    "europe": {"GB", "IE", "CH", "DE", "NL", "SE", "DK", "NO", "FI", "BE", "LU", "ES", "IT", "PT", "AT", "PL", "CZ", "HU",
               "RO", "GR", "EE", "LT", "LV", "BG", "HR", "SK", "SI", "MT", "CY", "IS", "MC"},
    "asia": {"SG", "HK", "TW", "JP", "KR", "CN", "AU", "NZ", "VN", "ID", "TH", "MY", "PH"},
    "middle_east": {"AE", "QA", "SA", "KW", "BH", "OM", "JO", "LB"},
    "africa": {"MA", "TN", "DZ", "EG", "LY", "SN", "CI", "GH", "NG", "BJ", "TG", "BF", "ML", "CM", "GA", "CD", "KE", "RW",
               "TZ", "UG", "ET", "ZA", "BW", "NA", "ZM", "ZW", "MZ", "AO", "MU", "MG"},
    "americas": {"US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "CR"},
}


def region_of(code: str | None) -> str:
    return next((r for r, codes in REGIONS.items() if code in codes), "other")


def region_bonus_map(geography: dict) -> dict[str, tuple[str, float]]:
    """search_preferences.yaml `region_priority` as country -> (tier name, bonus). The first tier listing a country wins."""
    out: dict[str, tuple[str, float]] = {}
    for tier in geography.get("region_priority") or []:
        for code in tier.get("countries") or []:
            out.setdefault(str(code).upper(), (str(tier.get("name", "region")), float(tier.get("bonus", 0))))
    return out


def region_rank(code: str | None, geography: dict) -> int:
    """Position of the country's tier in the user's order (0 = first); unlisted countries come last."""
    for i, tier in enumerate(geography.get("region_priority") or []):
        if code and code.upper() in {str(c).upper() for c in tier.get("countries") or []}:
            return i
    return len(geography.get("region_priority") or [])
