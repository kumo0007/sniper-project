from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import AVAILABILITY_MIN_GAP_SECONDS, get_settings
from app.db import session_scope
from app.minecraft.errors import MinecraftError
from app.models import Account, AttemptLog, SnipingJob, Target
from app.present import account_view, job_view, log_view, target_view
from app.security import COOKIE_NAME, SESSION_HOURS, issue_session, passwords_match, read_session
from app.services.accounts import start_device_login
from app.services.milestone import build_milestone
from app.services.monitor import check_target_now
from app.services.scheduler import clamp_gap_seconds, clamp_slow_gap, clamp_window_hours
from app.services.sessions import refresh_account_state
from app.services.settings_store import runtime_config, set_value
from app.timeutil import as_utc, utcnow
from app.validate import clean_username, parse_hint, resolve_priority

router = APIRouter(prefix="/api")
_login_failures: list[float] = []


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class AccountBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)


class TargetBody(BaseModel):
    username: str
    priority: str | None = None
    release_hint_at: str | None = None
    enabled: bool = True


class TargetPatch(BaseModel):
    priority: str | None = None
    release_hint_at: str | None = None
    enabled: bool | None = None
    clear_release_hint: bool = False


class EnabledBody(BaseModel):
    enabled: bool


class SettingsBody(BaseModel):
    per_account_gap_seconds: int
    slow_gap_seconds: int
    fast_window_before_hours: int
    fast_window_after_hours: int


def _cookie_secure(request: Request) -> bool:
    if get_settings().secure_cookies:
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def require_user(request: Request) -> dict:
    session = read_session(get_settings().secret_key, request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        sent = request.headers.get("x-csrf-token", "")
        if not _csrf_ok(sent, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF check failed. Reload the page and try again.")
    return session


def _csrf_ok(sent: str, expected: str) -> bool:
    if not sent or len(sent) != len(expected):
        return False
    return hmac.compare_digest(sent, expected)


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    now = time.time()
    _login_failures[:] = [stamp for stamp in _login_failures if now - stamp < 60]
    if len(_login_failures) >= 8:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Wait a minute.")
    if not passwords_match(body.password, get_settings().app_password):
        _login_failures.append(now)
        raise HTTPException(status_code=401, detail="Incorrect password.")
    token, csrf = issue_session(get_settings().secret_key)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        max_age=SESSION_HOURS * 3600,
        path="/",
    )
    return {"ok": True, "csrf_token": csrf}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    require_user(request)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    session = require_user(request)
    settings = get_settings()
    return {
        "ok": True,
        "csrf_token": session["csrf"],
        "microsoft_client_configured": bool(settings.microsoft_client_id),
        "max_accounts": settings.max_accounts,
        "availability_min_gap_seconds": AVAILABILITY_MIN_GAP_SECONDS,
    }


@router.get("/overview")
def overview(request: Request) -> dict:
    require_user(request)
    with session_scope() as db:
        accounts = db.query(Account).order_by(Account.id).all()
        targets = db.query(Target).order_by(Target.id).all()
        logs = db.query(AttemptLog).order_by(AttemptLog.id.desc()).limit(80).all()
        jobs = db.query(SnipingJob).order_by(SnipingJob.id.desc()).limit(20).all()
        names = {row.id: row.username for row in targets}
        labels = {row.id: row.label for row in accounts}
        claimed = {row.id: row.mc_name or row.label for row in accounts}
        config = runtime_config(db)
        milestone = build_milestone(db)
        return {
            "sniper_running": config["sniper_running"],
            "settings": {
                "per_account_gap_seconds": config["per_account_gap_seconds"],
                "slow_gap_seconds": config["slow_gap_seconds"],
                "fast_window_before_hours": config["fast_window_before_hours"],
                "fast_window_after_hours": config["fast_window_after_hours"],
                "availability_min_gap_seconds": AVAILABILITY_MIN_GAP_SECONDS,
                "max_accounts": get_settings().max_accounts,
                "microsoft_client_configured": bool(get_settings().microsoft_client_id),
            },
            "worker": {
                "owner": config["worker_owner"],
                "heartbeat": config["worker_heartbeat"],
            },
            "milestone": milestone,
            "accounts": [account_view(row) for row in accounts],
            "targets": [target_view(row, claimed.get(row.claimed_by_account_id or -1)) for row in targets],
            "activity": [log_view(row, names, labels) for row in logs],
            "jobs": [job_view(row) for row in jobs],
        }


@router.put("/settings")
def update_settings(body: SettingsBody, request: Request) -> dict:
    require_user(request)
    try:
        gap = clamp_gap_seconds(body.per_account_gap_seconds)
        slow = clamp_slow_gap(body.slow_gap_seconds)
        before = clamp_window_hours(body.fast_window_before_hours)
        after = clamp_window_hours(body.fast_window_after_hours)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with session_scope() as db:
        set_value(db, "per_account_gap_seconds", str(gap))
        set_value(db, "slow_gap_seconds", str(slow))
        set_value(db, "fast_window_before_hours", str(before))
        set_value(db, "fast_window_after_hours", str(after))
        config = runtime_config(db)
    return {
        "per_account_gap_seconds": config["per_account_gap_seconds"],
        "slow_gap_seconds": config["slow_gap_seconds"],
        "fast_window_before_hours": config["fast_window_before_hours"],
        "fast_window_after_hours": config["fast_window_after_hours"],
    }


@router.post("/sniper/start")
def sniper_start(request: Request) -> dict:
    require_user(request)
    with session_scope() as db:
        set_value(db, "sniper_running", "1")
    return {"sniper_running": True}


@router.post("/sniper/stop")
def sniper_stop(request: Request) -> dict:
    require_user(request)
    with session_scope() as db:
        set_value(db, "sniper_running", "0")
    return {"sniper_running": False}


@router.post("/accounts", status_code=201)
async def create_account(body: AccountBody, request: Request) -> dict:
    require_user(request)
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Give the account a label.")
    with session_scope() as db:
        count = db.query(Account).count()
        if count >= get_settings().max_accounts:
            raise HTTPException(
                status_code=400,
                detail=f"The account cap is {get_settings().max_accounts}. Remove one before adding another.",
            )
        account = Account(label=label, status="needs_login", enabled=True, created_at=utcnow(), updated_at=utcnow())
        db.add(account)
        db.flush()
        account_id = account.id
    try:
        await start_device_login(request.app.state.http, account_id)
    except MinecraftError as exc:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is not None and not account.encrypted_refresh_token:
                db.delete(account)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with session_scope() as db:
        account = db.get(Account, account_id)
        assert account is not None
        return account_view(account)


@router.post("/accounts/{account_id}/relogin")
async def relogin(account_id: int, request: Request) -> dict:
    require_user(request)
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found.")
    try:
        await start_device_login(request.app.state.http, account_id)
    except MinecraftError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with session_scope() as db:
        account = db.get(Account, account_id)
        assert account is not None
        return account_view(account)


@router.post("/accounts/{account_id}/refresh")
async def refresh_account(account_id: int, request: Request) -> dict:
    require_user(request)
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found.")
        last = as_utc(account.last_checked_at)
        if last is not None and (utcnow() - last).total_seconds() < AVAILABILITY_MIN_GAP_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Wait {AVAILABILITY_MIN_GAP_SECONDS} seconds between account refreshes.",
            )
        account.last_checked_at = utcnow()
    try:
        await refresh_account_state(request.app.state.http, account_id)
    except MinecraftError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with session_scope() as db:
        account = db.get(Account, account_id)
        assert account is not None
        return account_view(account)


@router.post("/accounts/{account_id}/enabled")
def set_account_enabled(account_id: int, body: EnabledBody, request: Request) -> dict:
    require_user(request)
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found.")
        account.enabled = body.enabled
        account.updated_at = utcnow()
        return account_view(account)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, request: Request) -> dict:
    require_user(request)
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found.")
        db.delete(account)
    return {"ok": True}


@router.post("/targets", status_code=201)
def create_target(body: TargetBody, request: Request) -> dict:
    require_user(request)
    try:
        username = clean_username(body.username)
        priority = resolve_priority(username, body.priority)
        hint = parse_hint(body.release_hint_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with session_scope() as db:
        existing = db.query(Target).filter(Target.username_key == username.lower()).one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="That username is already on the list.")
        target = Target(
            username=username,
            username_key=username.lower(),
            enabled=body.enabled,
            priority=priority,
            state="unavailable",
            state_detail="Waiting for the monitor.",
            release_hint_at=hint,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(target)
        db.flush()
        return target_view(target)


@router.patch("/targets/{target_id}")
def patch_target(target_id: int, body: TargetPatch, request: Request) -> dict:
    require_user(request)
    with session_scope() as db:
        target = db.get(Target, target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Target not found.")
        try:
            if body.priority is not None:
                target.priority = resolve_priority(target.username, body.priority)
            if body.clear_release_hint:
                target.release_hint_at = None
            elif body.release_hint_at is not None:
                target.release_hint_at = parse_hint(body.release_hint_at)
            if body.enabled is not None:
                target.enabled = body.enabled
                if body.enabled and target.state == "successful":
                    target.state_detail = "Already confirmed. Monitoring stays off for a claimed name."
                    target.enabled = False
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target.updated_at = utcnow()
        return target_view(target)


@router.delete("/targets/{target_id}")
def delete_target(target_id: int, request: Request) -> dict:
    require_user(request)
    with session_scope() as db:
        target = db.get(Target, target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Target not found.")
        db.delete(target)
    return {"ok": True}


@router.post("/targets/{target_id}/check")
async def check_target(target_id: int, request: Request) -> dict:
    """One official availability check for the first-milestone demo."""
    require_user(request)
    try:
        return await check_target_now(request.app.state.http, target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
