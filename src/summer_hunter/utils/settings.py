"""Runtime settings: secrets from the environment (.env), behaviour from config/*.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]

SECRET_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "ANTHROPIC_API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "GOOGLE_CSE_API_KEY",
    "IMAP_PASSWORD",
)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


@dataclass
class Settings:
    root: Path = PROJECT_ROOT
    config_dir: Path = field(default=PROJECT_ROOT / "config")
    data_dir: Path = field(default=PROJECT_ROOT / "data")
    database_url: str = ""
    timezone: str = "Europe/Paris"
    night_start: str = "00:00"
    night_end: str = "06:00"
    digest_time: str = "06:00"
    cycle_interval_minutes: int = 90
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    anthropic_api_key: str | None = None
    llm_provider_override: str | None = None
    brave_api_key: str | None = None
    google_cse_api_key: str | None = None
    google_cse_cx: str | None = None
    imap_host: str | None = None
    imap_user: str | None = None
    imap_password: str | None = None
    imap_folder: str = "INBOX"
    linkedin_alerts_dir: Path | None = None
    user_agent: str = "SummerHunter2027/1.0 (personal internship research; contact via repository owner)"
    log_level: str = "INFO"

    @classmethod
    def load(cls, env_file: Path | None = None, root: Path | None = None) -> "Settings":
        root = root or PROJECT_ROOT
        load_dotenv(env_file or root / ".env", override=False)
        data_dir = Path(_env("SH_DATA_DIR", str(root / "data")))
        alerts_dir = _env("LINKEDIN_ALERTS_DIR")
        return cls(
            root=root,
            config_dir=Path(_env("SH_CONFIG_DIR", str(root / "config"))),
            data_dir=data_dir,
            database_url=_env("SH_DATABASE_URL", f"sqlite:///{data_dir / 'summer_hunter.db'}"),
            timezone=_env("SH_TIMEZONE", "Europe/Paris"),
            night_start=_env("SH_NIGHT_START", "00:00"),
            night_end=_env("SH_NIGHT_END", "06:00"),
            digest_time=_env("SH_DIGEST_TIME", "06:00"),
            cycle_interval_minutes=int(_env("SH_CYCLE_INTERVAL_MINUTES", "90")),
            telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            llm_provider_override=_env("SH_LLM_PROVIDER"),
            brave_api_key=_env("BRAVE_SEARCH_API_KEY"),
            google_cse_api_key=_env("GOOGLE_CSE_API_KEY"),
            google_cse_cx=_env("GOOGLE_CSE_CX"),
            imap_host=_env("IMAP_HOST"),
            imap_user=_env("IMAP_USER"),
            imap_password=_env("IMAP_PASSWORD"),
            imap_folder=_env("IMAP_FOLDER", "INBOX"),
            linkedin_alerts_dir=Path(alerts_dir).expanduser() if alerts_dir else None,
            user_agent=_env("SH_USER_AGENT", cls.user_agent),
            log_level=_env("SH_LOG_LEVEL", "INFO"),
        )

    # Config files -------------------------------------------------------------

    def _config(self, name: str) -> dict[str, Any]:
        return load_yaml(self.config_dir / name)

    @cached_property
    def profile_raw(self) -> dict[str, Any]:
        return self._config("candidate_profile.yaml")

    @cached_property
    def preferences(self) -> dict[str, Any]:
        prefs = self._config("search_preferences.yaml")
        geo = prefs.get("geography", {}) or {}
        lists = [geo.get(k) or [] for k in ("excluded_countries", "safety_excluded_countries", "country_overrides_allow")]
        lists += list((geo.get("priority_countries") or {}).values())
        for codes in lists:
            for code in codes:
                if not isinstance(code, str) or len(code) != 2:
                    raise ValueError(f"search_preferences.yaml: invalid country code {code!r} "
                                     "(quote it, e.g. 'NO' for Norway, which YAML reads as false)")
        return prefs

    @cached_property
    def companies(self) -> list[dict[str, Any]]:
        return self._config("target_companies.yaml").get("companies", [])

    @cached_property
    def scoring(self) -> dict[str, Any]:
        return self._config("scoring_rules.yaml")

    @cached_property
    def compensation(self) -> dict[str, Any]:
        path = self.config_dir / "compensation.yaml"
        return load_yaml(path) if path.exists() else {}

    @cached_property
    def llm(self) -> dict[str, Any]:
        cfg = self._config("llm.yaml")
        if self.llm_provider_override:
            cfg["provider"] = self.llm_provider_override
        return cfg

    # Helpers ------------------------------------------------------------------

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def secret_values(self) -> list[str]:
        values = [
            self.telegram_bot_token,
            self.anthropic_api_key,
            self.brave_api_key,
            self.google_cse_api_key,
            self.imap_password,
        ]
        return [v for v in values if v and len(v) >= 6]

    def public_summary(self) -> dict[str, Any]:
        """Non-secret view for /settings and `summer-hunter status`."""
        return {
            "timezone": self.timezone,
            "night_window": f"{self.night_start}-{self.night_end}",
            "digest_time": self.digest_time,
            "cycle_interval_minutes": self.cycle_interval_minutes,
            "telegram": "configured" if self.telegram_configured else "not configured",
            "llm_provider": self.llm.get("provider"),
            "llm_model": self.llm.get("model"),
            "llm_key": "set" if self.anthropic_api_key else "missing",
            "llm_daily_budget_usd": self.llm.get("daily_budget_usd"),
            "search_api": "brave" if self.brave_api_key else ("google_cse" if self.google_cse_api_key else "none"),
            "linkedin_alerts": "imap" if self.imap_host else ("eml_dir" if self.linkedin_alerts_dir else "none"),
            "excluded_countries": self.preferences.get("geography", {}).get("excluded_countries", []),
        }
