"""Shared token persistence used by sign-in, refresh, and the monitor."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import session_scope
from app.minecraft.auth import exchange_minecraft, refresh_msa
from app.minecraft.errors import MinecraftError
from app.minecraft.profile import get_name_change, read_profile_or_raise
from app.models import Account, AttemptLog
from app.services.sanitize import sanitize
from app.timeutil import as_utc, utcnow

BEARER_EXPIRED_DETAIL = (
    "This bearer token has expired or is missing. Paste a new Minecraft Services access token."
)
SESSION_MISSING_DETAIL = "This account has no saved Microsoft session. Connect it again."


def expire_stale_credentials(db, now: datetime | None = None) -> int:
    """Mark accounts whose stored credential can no longer authenticate.

    Bearer-token accounts cannot refresh. Without this, they stay status=ready while
    the monitor silently skips them after access_expires_at.
    """
    moment = now or utcnow()
    marked = 0
    for account in db.query(Account).all():
        if not account.enabled or account.status in {
            "auth_error",
            "blocked",
            "needs_login",
            "authenticating",
        }:
            continue
        auth_method = account.auth_method or "device_code"
        expires = as_utc(account.access_expires_at)
        has_access = bool(account.encrypted_access_token)
        has_refresh = bool(account.encrypted_refresh_token)
        expired = expires is not None and expires <= moment

        if auth_method == "bearer_token":
            if has_access and not expired:
                continue
            detail = BEARER_EXPIRED_DETAIL
            account.encrypted_access_token = None
            account.access_expires_at = None
            account.credential_hint = None
        elif has_refresh:
            continue
        elif has_access and not expired:
            continue
        else:
            detail = SESSION_MISSING_DETAIL

        account.status = "auth_error"
        account.status_detail = detail
        account.updated_at = moment
        db.add(
            AttemptLog(
                account_id=account.id,
                timestamp=moment,
                action="auth_refresh",
                result="auth_error",
                detail=sanitize(detail),
            )
        )
        marked += 1
    if marked:
        db.flush()
    return marked


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
    from app.minecraft.tokens import credential_hint

    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            return
        account.auth_method = "device_code" if refresh_token else (account.auth_method or "device_code")
        if refresh_token:
            account.encrypted_refresh_token = encrypt_secret(refresh_token)
            account.auth_method = "device_code"
        account.encrypted_access_token = encrypt_secret(access_token)
        account.credential_hint = credential_hint(access_token)
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
    """Return a usable Minecraft bearer token, refreshing it when the method supports that."""
    refresh: str | None = None
    expired_detail: str | None = None
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise MinecraftError("Account is not available.", kind="auth_error")
        auth_method = account.auth_method or "device_code"
        access = decrypt_secret(account.encrypted_access_token)
        refresh = decrypt_secret(account.encrypted_refresh_token)
        expires = as_utc(account.access_expires_at)
        now = utcnow()
        # Bearer tokens cannot be refreshed. An explicit past expiry must fail closed.
        if access and expires and expires > now + timedelta(minutes=5):
            return access
        if access and expires and expires > now and (auth_method == "bearer_token" or not refresh):
            return access
        if access and expires is None and refresh:
            # Device-code session with unknown expiry: try refresh below.
            pass
        elif access and expires is None and auth_method != "bearer_token":
            return access
        if auth_method == "bearer_token" or not refresh:
            expired_detail = BEARER_EXPIRED_DETAIL if auth_method == "bearer_token" else SESSION_MISSING_DETAIL
            account.status = "auth_error"
            account.status_detail = expired_detail
            if auth_method == "bearer_token":
                account.encrypted_access_token = None
                account.access_expires_at = None
                account.credential_hint = None
            account.updated_at = now
    if expired_detail:
        # Raise after the session commits so auth_error is not rolled back.
        raise MinecraftError(expired_detail, kind="auth_error")

    client_id = get_settings().microsoft_client_id
    if not client_id:
        raise MinecraftError("MICROSOFT_CLIENT_ID is not configured.", kind="auth_error")
    if not refresh:
        raise MinecraftError(SESSION_MISSING_DETAIL, kind="auth_error")
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
            if (account.auth_method or "") == "bearer_token":
                account.encrypted_access_token = None
                account.access_expires_at = None
                account.credential_hint = None
        else:
            account.status = "error"
        account.status_detail = sanitize(str(exc))
        if exc.kind == "rate_limited":
            account.rate_limited_until = utcnow() + timedelta(seconds=60)
        account.updated_at = utcnow()
