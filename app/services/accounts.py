"""Device-code sign-in for accounts the operator owns."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import httpx

from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import session_scope
from app.minecraft.auth import poll_device_token, request_device_code
from app.minecraft.errors import AuthDeclined, AuthExpired, AuthPending, AuthSlowDown, MinecraftError
from app.models import Account, DeviceLogin
from app.services.sanitize import sanitize
from app.services.sessions import establish_session
from app.timeutil import as_utc, utcnow

logger = logging.getLogger("sniper.accounts")
_tasks: set[asyncio.Task] = set()


def _track(task: asyncio.Task) -> None:
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def start_device_login(http: httpx.AsyncClient, account_id: int) -> DeviceLogin:
    client_id = get_settings().microsoft_client_id
    if not client_id:
        raise MinecraftError(
            "Set MICROSOFT_CLIENT_ID in the environment before connecting an account.",
            kind="auth_error",
        )
    code = await request_device_code(http, client_id)
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise MinecraftError("Account disappeared before sign-in started.", kind="error")
        existing = account.device_login
        if existing is not None:
            db.delete(existing)
            db.flush()
        login = DeviceLogin(
            account_id=account.id,
            user_code=code.user_code,
            verification_uri=code.verification_uri,
            message=sanitize(code.message, limit=300),
            interval_seconds=code.interval,
            expires_at=utcnow() + timedelta(seconds=code.expires_in),
            status="pending",
            encrypted_device_code=encrypt_secret(code.device_code),
        )
        account.status = "authenticating"
        account.status_detail = "Waiting for Microsoft sign-in."
        account.updated_at = utcnow()
        db.add(login)
        db.flush()
        db.expunge(login)
    _track(asyncio.create_task(_poll_until_done(http, account_id)))
    return login


async def _poll_until_done(http: httpx.AsyncClient, account_id: int) -> None:
    client_id = get_settings().microsoft_client_id
    try:
        while True:
            with session_scope() as db:
                account = db.get(Account, account_id)
                if account is None or account.device_login is None:
                    return
                login = account.device_login
                if login.status != "pending":
                    return
                if as_utc(login.expires_at) <= utcnow():
                    login.status = "expired"
                    login.encrypted_device_code = None
                    account.status = "needs_login"
                    account.status_detail = "The Microsoft sign-in code expired. Connect again."
                    return
                device_code = decrypt_secret(login.encrypted_device_code)
                interval = max(5, login.interval_seconds)
            if not device_code:
                return
            await asyncio.sleep(interval)
            try:
                tokens = await poll_device_token(http, client_id, device_code)
            except AuthPending:
                continue
            except AuthSlowDown:
                with session_scope() as db:
                    account = db.get(Account, account_id)
                    if account and account.device_login and account.device_login.status == "pending":
                        account.device_login.interval_seconds += 5
                continue
            except AuthDeclined:
                _finish_login_error(account_id, "declined", "Microsoft sign-in was declined.")
                return
            except AuthExpired:
                _finish_login_error(account_id, "expired", "The Microsoft sign-in code expired. Connect again.")
                return
            except MinecraftError as exc:
                _finish_login_error(account_id, "error", str(exc))
                return
            try:
                await establish_session(http, account_id, tokens.access_token, tokens.refresh_token)
            except MinecraftError as exc:
                _finish_login_error(account_id, "error", str(exc), account_status=exc.kind)
                return
            return
    except Exception:
        logger.exception("Device login poll failed for account %s", account_id)
        _finish_login_error(account_id, "error", "Sign-in stopped because of an unexpected error.")


def _finish_login_error(account_id: int, login_status: str, detail: str, account_status: str = "auth_error") -> None:
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            return
        if account.device_login is not None:
            account.device_login.status = login_status
            account.device_login.encrypted_device_code = None
        account.status = "needs_login" if login_status in {"expired", "declined"} else account_status
        if account.status == "rate_limited":
            account.status = "error"
        account.status_detail = sanitize(detail)
        account.updated_at = utcnow()


async def cancel_login_tasks() -> None:
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def resume_pending_logins(http: httpx.AsyncClient) -> None:
    with session_scope() as db:
        pending = db.query(DeviceLogin).filter(DeviceLogin.status == "pending").all()
        ids = [row.account_id for row in pending]
    for account_id in ids:
        _track(asyncio.create_task(_poll_until_done(http, account_id)))
