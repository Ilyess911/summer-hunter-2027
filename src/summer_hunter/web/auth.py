"""Telegram Mini App authentication and signed download links.

Every API call carries the Mini App `initData`, verified with the bot token as Telegram
documents (HMAC-SHA256 with key HMAC("WebAppData", token)). Only the configured user is
accepted. File downloads use short-lived HMAC-signed URLs, since a download link cannot
carry custom headers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_S = 24 * 3600
LINK_TTL_S = 15 * 60


class AuthError(Exception):
    pass


def _secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Build a valid initData string (used by tests)."""
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    digest = hmac.new(_secret(bot_token), check.encode(), hashlib.sha256).hexdigest()
    from urllib.parse import urlencode

    return urlencode({**fields, "hash": digest})


def verify_init_data(init_data: str, bot_token: str, allowed_user_id: str, now: float | None = None) -> dict:
    if not init_data:
        raise AuthError("missing initData")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received = pairs.pop("hash", None)
    if not received:
        raise AuthError("missing hash")
    # The data-check-string covers every field except `hash` (including `signature` on Bot API 8+).
    # Older clients send no `signature`; both forms are HMAC-checked with the bot token.
    candidates = [pairs, {k: v for k, v in pairs.items() if k != "signature"}]
    ok = False
    for fields in candidates:
        check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
        expected = hmac.new(_secret(bot_token), check.encode(), hashlib.sha256).hexdigest()
        ok = ok or hmac.compare_digest(expected, received)
    if not ok:
        raise AuthError("bad signature")
    if (now or time.time()) - int(pairs.get("auth_date", "0")) > MAX_AGE_S:
        raise AuthError("initData expired")
    user = json.loads(pairs.get("user", "{}") or "{}")
    if str(user.get("id")) != str(allowed_user_id):
        raise AuthError("user not allowed")
    return user


def _link_key(bot_token: str) -> bytes:
    return hashlib.sha256(b"summer-hunter-download:" + bot_token.encode()).digest()


def sign_link(path: str, bot_token: str, now: float | None = None) -> str:
    exp = int((now or time.time()) + LINK_TTL_S)
    sig = hmac.new(_link_key(bot_token), f"{path}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{path}?exp={exp}&sig={sig}"


def verify_link(path: str, exp: str, sig: str, bot_token: str, now: float | None = None) -> bool:
    if not exp.isdigit() or int(exp) < (now or time.time()):
        return False
    expected = hmac.new(_link_key(bot_token), f"{path}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, sig or "")
