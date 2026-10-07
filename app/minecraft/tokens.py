"""Helpers for Minecraft Services bearer tokens (login_with_xbox access_token)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone

# Compact JWT-ish shape. Minecraft access tokens are JWTs; we still validate via API.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]+=*\.[A-Za-z0-9_\-]+=*\.[A-Za-z0-9_\-+\/=]*$")


def normalize_bearer_token(raw: str | None) -> str:
    text = (raw or "").strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    text = "".join(text.split())
    if len(text) < 40 or len(text) > 8192:
        raise ValueError(
            "Paste a Minecraft Services access token from the official login_with_xbox flow "
            "(usually a long JWT). Arbitrary strings are rejected."
        )
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(
            "That value does not look like a Minecraft Services access token. "
            "Use the Bearer access_token from api.minecraftservices.com authentication, not a password."
        )
    return text


def credential_hint(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"mc-jwt · {digest[:4]}…{digest[-4:]}"


def jwt_expiry_utc(token: str) -> datetime | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = data.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(int(exp), tz=timezone.utc)


def default_expires_in(token: str) -> int:
    exp = jwt_expiry_utc(token)
    if exp is None:
        return 86400
    remaining = int((exp - datetime.now(timezone.utc)).total_seconds())
    return max(60, remaining)
