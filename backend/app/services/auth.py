"""Local password/session helpers; raw passwords and bearer tokens are never stored."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

_FAILED_LOGINS: dict[str, deque[float]] = defaultdict(deque)
_LOGIN_LOCK = threading.Lock()
_MAX_FAILURES = 8
_WINDOW_SECONDS = 900


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Passwords must contain 12 to 256 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt64, digest64 = stored.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt64.encode())
        expected = base64.urlsafe_b64decode(digest64.encode())
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, MemoryError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def login_allowed(key: str) -> bool:
    now = time.monotonic()
    with _LOGIN_LOCK:
        failures = _FAILED_LOGINS[key]
        while failures and now - failures[0] > _WINDOW_SECONDS:
            failures.popleft()
        return len(failures) < _MAX_FAILURES


def record_login_failure(key: str) -> None:
    now = time.monotonic()
    with _LOGIN_LOCK:
        failures = _FAILED_LOGINS[key]
        while failures and now - failures[0] > _WINDOW_SECONDS:
            failures.popleft()
        failures.append(now)


def clear_login_failures(key: str) -> None:
    with _LOGIN_LOCK:
        _FAILED_LOGINS.pop(key, None)


def retry_after(key: str) -> int:
    with _LOGIN_LOCK:
        failures = _FAILED_LOGINS.get(key, ())
        return max(1, int(_WINDOW_SECONDS - (time.monotonic() - failures[0]))) if failures else _WINDOW_SECONDS
