"""Profile, availability, and username change calls.

interpret_* functions are the claim decision. A rename is successful only
when a later profile read shows the requested name.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.minecraft.errors import MinecraftError
from app.services.sanitize import safe_api_message

PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
NAMECHANGE_URL = "https://api.minecraftservices.com/minecraft/profile/namechange"


@dataclass
class ApiResult:
    http_status: int
    body: dict
    result: str
    detail: str


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def _body(response: httpx.Response) -> dict:
    try:
        parsed = response.json()
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def interpret_availability(status_code: int, body: dict | None) -> tuple[str, str]:
    payload = body or {}
    if status_code == 200:
        status = str(payload.get("status") or "")
        if status == "AVAILABLE":
            return "available", "Official availability check returned AVAILABLE."
        if status == "DUPLICATE":
            return "unavailable", "Name is taken."
        if status == "NOT_ALLOWED":
            return "not_allowed", "Name is not allowed by Minecraft's rules."
        return "unknown_error", f"Unexpected availability status {status or 'empty'}."
    if status_code == 401:
        return "authentication_error", "Minecraft rejected the access token during an availability check."
    if status_code == 429:
        return "rate_limited", "Availability rate limit reached for this account."
    if status_code >= 500:
        return "unknown_error", "Minecraft availability service returned a server error."
    message = safe_api_message(payload)
    return "unknown_error", message or f"Availability check returned HTTP {status_code}."


def interpret_claim(status_code: int, body: dict | None, *, confirmed: bool) -> tuple[str, str]:
    payload = body or {}
    if status_code == 200 and confirmed:
        return "success", "Profile read confirms this account now has the target name."
    if status_code == 200 and not confirmed:
        return "unknown_error", "Rename was accepted but the profile name does not match yet."
    if status_code == 400:
        return "failed", safe_api_message(payload) or "Minecraft rejected the name as invalid."
    if status_code == 401:
        return "authentication_error", "Minecraft rejected the access token during the rename."
    if status_code == 403:
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        if str(details.get("status") or "") == "DUPLICATE":
            return "unavailable", "Name is not available to claim."
        return "failed", safe_api_message(payload) or "Minecraft refused the username change."
    if status_code == 429:
        return "rate_limited", "Username change rate limit reached."
    if status_code >= 500:
        return "unknown_error", "Minecraft username service returned a server error."
    return "unknown_error", safe_api_message(payload) or f"Username change returned HTTP {status_code}."


def profile_has_name(profile: dict | None, username: str) -> bool:
    if not isinstance(profile, dict):
        return False
    current = profile.get("name")
    return isinstance(current, str) and current.lower() == username.lower()


async def get_profile(http: httpx.AsyncClient, access_token: str) -> tuple[int, dict]:
    response = await http.get(PROFILE_URL, headers=_auth_headers(access_token))
    return response.status_code, _body(response)


async def get_name_change(http: httpx.AsyncClient, access_token: str) -> tuple[int, dict]:
    response = await http.get(NAMECHANGE_URL, headers=_auth_headers(access_token))
    return response.status_code, _body(response)


async def check_availability(http: httpx.AsyncClient, access_token: str, username: str) -> ApiResult:
    response = await http.get(
        f"https://api.minecraftservices.com/minecraft/profile/name/{username}/available",
        headers=_auth_headers(access_token),
    )
    body = _body(response)
    result, detail = interpret_availability(response.status_code, body)
    return ApiResult(response.status_code, body, result, detail)


async def change_name(http: httpx.AsyncClient, access_token: str, username: str) -> tuple[int, dict]:
    response = await http.put(
        f"https://api.minecraftservices.com/minecraft/profile/name/{username}",
        headers=_auth_headers(access_token),
    )
    return response.status_code, _body(response)


async def read_profile_or_raise(http: httpx.AsyncClient, access_token: str) -> dict:
    status, body = await get_profile(http, access_token)
    if status == 200 and body.get("name"):
        return body
    if status == 404:
        raise MinecraftError(
            "This Microsoft account has no Minecraft Java profile yet. "
            "It needs to own Minecraft and have created a username once.",
            kind="error",
            http_status=404,
        )
    if status == 401:
        raise MinecraftError(
            "Minecraft rejected the access token while reading the profile.",
            kind="auth_error",
            http_status=401,
        )
    if status == 429:
        raise MinecraftError(
            "Minecraft rate limited a profile read.",
            kind="rate_limited",
            http_status=429,
        )
    raise MinecraftError(
        safe_api_message(body) or f"Profile read returned HTTP {status}.",
        kind="error",
        http_status=status,
    )
