"""Shared token persistence used by sign-in, refresh, and the monitor."""

from __future__ import annotations

from datetime import timedelta

import httpx

from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import session_scope
from app.minecraft.auth import exchange_minecraft, refresh_msa
from app.minecraft.errors import MinecraftError
from app.minecraft.profile import get_name_change, read_profile_or_raise
from app.models import Account
from app.services.sanitize import sanitize
from app.timeutil import as_utc, utcnow


def apply_profile(account: Account, profile: dict, namechange: dict | None) -> None:
    account.mc_uuid = str(profile.get("id") or "") or None
    account.mc_name = str(profile.get("name") or "") or None
    account.last_auth_at = utcnow()
    if isinstance(namechange, dict) and "nameChangeAllowed" in namechange:
        account.name_change_allowed = bool(namechange.get("nameChangeAllowed"))
        changed = namechange.get("changedAt")
        account.name_changed_at = _parse_optional(changed)
        account.status = "ready" if account.name_change_allowed else "cooldown"
        account.status_detail = (
            None
            if account.name_change_allowed
            else "This account cannot change its name until the 30-day cooldown ends."
        )
    else:
        account.status = "ready"
        account.status_detail = None
    account.updated_at = utcnow()


def _parse_optional(value):
    if not isinstance(value, str) or not value:
        return None
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return as_utc(parsed)


def save_new_session(account_id: int, refresh_token: str | None, access_token: str, expires_in: int, profile: dict, namechange: dict | None) -> None:
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            return
        if refresh_token:
            account.encrypted_refresh_token = encrypt_secret(refresh_token)
        account.encrypted_access_token = encrypt_secret(access_token)
        account.access_expires_at = utcnow() + timedelta(seconds=max(60, expires_in - 60))
        apply_profile(account, profile, namechange)
        login = account.device_login
        if login is not None and login.status == "pending":
            login.status = "completed"
            login.encrypted_device_code = None


async def establish_session(http: httpx.AsyncClient, account_id: int, msa_access: str, refresh_token: str | None) -> None:
    mc = await exchange_minecraft(http, msa_access)
    profile = await read_profile_or_raise(http, mc.access_token)
    status, namechange = await get_name_change(http, mc.access_token)
    save_new_session(
        account_id,
        refresh_token,
        mc.access_token,
        mc.expires_in,
        profile,
        namechange if status == 200 else None,
    )


async def refresh_account_state(http: httpx.AsyncClient, account_id: int) -> None:
    """Re-read profile and rename eligibility. Used by the manual refresh button."""
    token = await ensure_access_token(http, account_id)
    profile = await read_profile_or_raise(http, token)
    status, namechange = await get_name_change(http, token)
    if status == 429:
        raise MinecraftError("Minecraft rate limited the name-change check.", kind="rate_limited", http_status=429)
    if status == 401:
        raise MinecraftError("Minecraft rejected the access token.", kind="auth_error", http_status=401)
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            return
        apply_profile(account, profile, namechange if status == 200 else None)
        account.last_checked_at = utcnow()


async def ensure_access_token(http: httpx.AsyncClient, account_id: int) -> str:
    """Return a usable Minecraft bearer token, refreshing it when it is near expiry."""
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise MinecraftError("Account is not available.", kind="auth_error")
        access = decrypt_secret(account.encrypted_access_token)
        refresh = decrypt_secret(account.encrypted_refresh_token)
        expires = as_utc(account.access_expires_at)
        if access and expires and expires > utcnow() + timedelta(minutes=5):
            return access
        if not refresh:
            raise MinecraftError("This account has no saved Microsoft session. Connect it again.", kind="auth_error")

    client_id = get_settings().microsoft_client_id
    if not client_id:
        raise MinecraftError("MICROSOFT_CLIENT_ID is not configured.", kind="auth_error")
    try:
        msa = await refresh_msa(http, client_id, refresh)
        mc = await exchange_minecraft(http, msa.access_token)
        profile = await read_profile_or_raise(http, mc.access_token)
        status, namechange = await get_name_change(http, mc.access_token)
    except MinecraftError as exc:
        _record_auth_failure(account_id, exc)
        raise
    save_new_session(
        account_id,
        msa.refresh_token,
        mc.access_token,
        mc.expires_in,
        profile,
        namechange if status == 200 else None,
    )
    return mc.access_token


def _record_auth_failure(account_id: int, exc: MinecraftError) -> None:
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            return
        if exc.http_status == 404:
            account.status = "blocked"
        elif exc.kind == "auth_error":
            account.status = "auth_error"
        else:
            account.status = "error"
        account.status_detail = sanitize(str(exc))
        if exc.kind == "rate_limited":
            account.rate_limited_until = utcnow() + timedelta(seconds=60)
        account.updated_at = utcnow()
