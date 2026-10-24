"""Single-user authentication for Summer Hunter OS (local and hosted).

- Password: scrypt hash in SH_OS_PASSWORD_HASH (never the password itself); `summer-hunter os set-password`.
- Session: HMAC-signed, expiring token in an HttpOnly, SameSite=Lax cookie (Secure in hosted mode), keyed by SH_OS_SECRET.
- CSRF: every state-changing request must carry the X-SH-Request header, which a cross-site form cannot set
  and which no CORS policy here allows.
- Login attempts are rate-limited per client and globally. The counters live in the database so they hold across
  serverless instances in hosted mode (an in-memory counter would reset on every cold start).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque

COOKIE = "sh_os_session"
CSRF_HEADER = "x-sh-request"
SESSION_SECONDS = 14 * 24 * 3600
_ATTEMPTS: dict[str, deque] = defaultdict(deque)
MAX_ATTEMPTS, WINDOW = 8, 15 * 60
MAX_GLOBAL_ATTEMPTS = 40          # all clients together: bounds guessing even if client addresses rotate
ATTEMPTS_KEY = "os_login_attempts"
_LOCK = threading.Lock()      # local mode: one process, several worker threads (hosted mode adds a database lock)


def hash_password(password: str, n: int = 2 ** 14, r: int = 8, p: int = 1) -> str:
    if len(password) < 10:
        raise ValueError("use at least 10 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def _secret() -> bytes:
    value = os.environ.get("SH_OS_SECRET")
    if not value or len(value) < 32:
        raise RuntimeError("SH_OS_SECRET must be set (32+ random characters)")
    return value.encode()


def make_session(now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_SECONDS)
    nonce = secrets.token_urlsafe(12)
    payload = f"v1.{expires}.{nonce}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def valid_session(token: str | None, now: float | None = None) -> bool:
    try:
        version, expires, nonce, sig = (token or "").split(".")
    except ValueError:
        return False
    payload = f"{version}.{expires}.{nonce}"
    good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return version == "v1" and hmac.compare_digest(good, sig) and int(expires) > (now or time.time())


def allow_attempt(client: str, now: float | None = None) -> bool:
    now = now or time.time()
    q = _ATTEMPTS[client]
    while q and now - q[0] > WINDOW:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS:
        return False
    q.append(now)
    return True


def allow_attempt_persistent(session, client: str, now: float | None = None) -> bool:  # noqa: ANN001
    """Same policy as allow_attempt, stored in the key-value table and committed before the lock is released."""
    from summer_hunter.database.repository import Repository

    with _LOCK:
        allowed = _allow_persistent(Repository(session), client, now or time.time())
        session.commit()
        return allowed


def _allow_persistent(repo, client: str, now: float) -> bool:  # noqa: ANN001
    try:
        state: dict[str, list[float]] = json.loads(repo.get_kv(ATTEMPTS_KEY) or "{}")
    except ValueError:
        state = {}
    state = {k: [t for t in v if now - t <= WINDOW] for k, v in state.items()}
    state = {k: v for k, v in state.items() if v}
    if len(state.get(client, [])) >= MAX_ATTEMPTS or sum(map(len, state.values())) >= MAX_GLOBAL_ATTEMPTS:
        return False
    state.setdefault(client, []).append(now)
    repo.set_kv(ATTEMPTS_KEY, json.dumps(state))
    return True


def sync_token_ok(token: str | None) -> bool:
    expected = os.environ.get("SH_SYNC_TOKEN")
    return bool(expected) and len(expected) >= 32 and hmac.compare_digest(expected, token or "")
