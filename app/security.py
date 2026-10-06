"""Operator session cookie for the web UI. This is not a Minecraft login."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

COOKIE_NAME = "sniper_session"
SESSION_HOURS = 12


def passwords_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def issue_session(secret: str) -> tuple[str, str]:
    expires = int(time.time()) + SESSION_HOURS * 3600
    nonce = secrets.token_urlsafe(16)
    csrf = secrets.token_urlsafe(24)
    payload = f"{expires}.{nonce}.{csrf}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}", csrf


def read_session(secret: str, cookie: str | None) -> dict | None:
    if not cookie or cookie.count(".") != 3:
        return None
    expires_s, nonce, csrf, signature = cookie.split(".", 3)
    payload = f"{expires_s}.{nonce}.{csrf}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        expires = int(expires_s)
    except ValueError:
        return None
    if expires < int(time.time()):
        return None
    return {"csrf": csrf, "expires": expires}
