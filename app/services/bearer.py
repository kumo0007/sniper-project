"""Validate and store client-supplied Minecraft Services bearer tokens."""

from __future__ import annotations

from datetime import timedelta

import httpx

from app.crypto import encrypt_secret
from app.db import session_scope
from app.minecraft.errors import MinecraftError
from app.minecraft.profile import get_name_change, read_profile_or_raise
from app.minecraft.tokens import credential_hint, default_expires_in, normalize_bearer_token
from app.models import Account
from app.services.sanitize import sanitize
from app.services.sessions import apply_profile
from app.timeutil import utcnow


async def validate_minecraft_bearer(http: httpx.AsyncClient, token: str) -> tuple[dict, dict | None]:
    """Confirm the token works against official Minecraft Services endpoints."""
    try:
        profile = await read_profile_or_raise(http, token)
    except MinecraftError as exc:
        if exc.http_status == 401:
            raise MinecraftError(
                "Minecraft rejected this bearer token (expired, revoked, or not a Minecraft Services access token).",
                kind="auth_error",
                http_status=401,
            ) from exc
        raise
    name_status, namechange = await get_name_change(http, token)
    if name_status == 401:
        raise MinecraftError(
            "Minecraft rejected this token while checking rename eligibility.",
            kind="auth_error",
            http_status=401,
        )
    if name_status == 429:
        raise MinecraftError(
            "Minecraft rate limited the name-change eligibility check.",
            kind="rate_limited",
            http_status=429,
        )
    return profile, namechange if name_status == 200 else None


def store_validated_bearer(
    account_id: int,
    token: str,
    profile: dict,
    namechange: dict | None,
) -> None:
    expires_in = default_expires_in(token)
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            return
        account.auth_method = "bearer_token"
        account.credential_hint = credential_hint(token)
        account.encrypted_access_token = encrypt_secret(token)
        account.access_expires_at = utcnow() + timedelta(seconds=max(60, expires_in - 60))
        account.encrypted_refresh_token = None
        if account.device_login is not None:
            db.delete(account.device_login)
        apply_profile(account, profile, namechange)
        account.updated_at = utcnow()


async def connect_with_bearer(http: httpx.AsyncClient, account_id: int, raw_token: str) -> None:
    token = normalize_bearer_token(raw_token)
    try:
        profile, namechange = await validate_minecraft_bearer(http, token)
    except MinecraftError as exc:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is None:
                raise
            account.auth_method = "bearer_token"
            if exc.http_status == 404:
                account.status = "blocked"
            elif exc.kind == "auth_error":
                account.status = "auth_error"
            else:
                account.status = "error"
            account.status_detail = sanitize(str(exc))
            account.credential_hint = None
            account.encrypted_access_token = None
            account.access_expires_at = None
            account.updated_at = utcnow()
        raise
    store_validated_bearer(account_id, token, profile, namechange)
