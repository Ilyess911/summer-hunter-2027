"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from datetime import datetime, timezone
from pathlib import Path

_REDACTION_PATTERNS = [
    re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}"),       # Telegram token inside URLs
    re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"),          # bare Telegram token
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),           # Anthropic keys
]

_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}


class SecretRedactor(logging.Filter):
    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self.secrets = [s for s in (secrets or []) if s]

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        for pattern in _REDACTION_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(str(record.msg))
        if record.args:
            record.args = tuple(self.redact(str(a)) for a in record.args) if isinstance(record.args, tuple) else record.args
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_ATTRS and isinstance(value, str):
                setattr(record, key, self.redact(value))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS and not k.startswith("_")}
        tail = " ".join(f"{k}={v}" for k, v in extras.items())
        base = f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        return f"{base} {tail}".rstrip()


def setup_logging(log_dir: Path, level: str = "INFO", secrets: list[str] | None = None, console: bool = True) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    redactor = SecretRedactor(secrets)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "summer_hunter.jsonl", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(redactor)
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(ConsoleFormatter())
        stream.addFilter(redactor)
        root.addHandler(stream)

    for noisy in ("httpx", "httpcore", "anthropic", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
