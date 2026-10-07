"""Strip tokens from anything that might be logged or shown in the UI."""

from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/=]+", re.IGNORECASE),
    re.compile(
        r"(refresh_token|access_token|device_code|id_token|RpsTicket|identityToken|bearer_token)"
        r"(\"|'|%22)?\s*[:=]\s*(\"|'|%22)?[A-Za-z0-9\-._~+/=]{8,}",
        re.IGNORECASE,
    ),
    # Standalone Minecraft-style JWTs accidentally copied into error text.
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-+/=]{10,}\b"),
)


def sanitize(text: str | None, limit: int = 500) -> str:
    if not text:
        return ""
    cleaned = str(text)
    for pattern in _PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    cleaned = cleaned.replace("\n", " ").strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 1] + "…"
    return cleaned


def safe_api_message(body: object) -> str:
    if not isinstance(body, dict):
        return ""
    parts: list[str] = []
    for key in ("errorMessage", "error_description", "error", "developerMessage"):
        value = body.get(key)
        if isinstance(value, str) and value and value not in parts:
            parts.append(value)
    details = body.get("details")
    if isinstance(details, dict) and details.get("status"):
        parts.append(f"status={details.get('status')}")
    if body.get("XErr"):
        parts.append(f"XErr={body.get('XErr')}")
    return sanitize(" — ".join(parts))
