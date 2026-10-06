"""Background monitor. One availability check per loop, then a bounded claim."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

import httpx

from app.config import CLAIM_ACCOUNTS_PER_BURST, CLAIM_BURST_COOLDOWN_SECONDS, CLAIM_MAX_PER_MINUTE
from app.db import session_scope
from app.minecraft.errors import MinecraftError
from app.minecraft.profile import (
    change_name,
    check_availability,
    get_name_change,
    get_profile,
    interpret_claim,
    profile_has_name,
)
from app.models import Account, AttemptLog, SnipingJob, Target
from app.services.sanitize import sanitize
from app.services.scheduler import choose_account, choose_claim_account, choose_target, soonest_account_wait
from app.services.sessions import apply_profile, ensure_access_token
from app.services.settings_store import get_value, runtime_config, set_value
from app.timeutil import as_utc, utcnow

logger = logging.getLogger("sniper.monitor")
WORKER_ID = uuid.uuid4().hex
HEARTBEAT_STALE_SECONDS = 20


def _log(db, *, action: str, result: str, detail: str, target_id: int | None = None, account_id: int | None = None, job_id: int | None = None, http_status: int | None = None) -> None:
    db.add(
        AttemptLog(
            target_id=target_id,
            account_id=account_id,
            job_id=job_id,
            timestamp=utcnow(),
            action=action,
            result=result,
            http_status=http_status,
            detail=sanitize(detail),
        )
    )


def _take_worker_slot(db, now: datetime) -> bool:
    owner = get_value(db, "worker_owner")
    raw_beat = get_value(db, "worker_heartbeat")
    beat = None
    if raw_beat:
        try:
            beat = as_utc(datetime.fromisoformat(raw_beat))
        except ValueError:
            beat = None
    fresh = beat is not None and (now - beat).total_seconds() < HEARTBEAT_STALE_SECONDS
    if owner and owner != WORKER_ID and fresh:
        return False
    set_value(db, "worker_owner", WORKER_ID)
    set_value(db, "worker_heartbeat", now.isoformat())
    return True


def _claims_last_minute(db, now: datetime) -> int:
    cutoff = now - timedelta(seconds=60)
    return (
        db.query(AttemptLog)
        .filter(AttemptLog.action == "claim", AttemptLog.timestamp >= cutoff)
        .count()
    )


def _mark_account_backoff(db, account_id: int, seconds: int, detail: str, status: str = "error") -> None:
    account = db.get(Account, account_id)
    if account is None:
        return
    account.rate_limited_until = utcnow() + timedelta(seconds=seconds)
    if status == "auth_error":
        account.status = "auth_error"
    account.status_detail = sanitize(detail)
    account.updated_at = utcnow()


async def run_once(http: httpx.AsyncClient) -> float:
    now = utcnow()
    with session_scope() as db:
        if not _take_worker_slot(db, now):
            return 5.0
        config = runtime_config(db)
        if not config["sniper_running"]:
            return 2.0
        targets = db.query(Target).all()
        accounts = db.query(Account).all()
        target = choose_target(
            targets,
            now,
            config["slow_gap_seconds"],
            config["fast_window_before_hours"],
            config["fast_window_after_hours"],
        )
        if target is None:
            return 5.0
        account = choose_account(accounts, now, config["per_account_gap_seconds"])
        if account is None:
            wait = soonest_account_wait(accounts, now, config["per_account_gap_seconds"])
            return 5.0 if wait is None else max(0.5, min(5.0, wait))
        target_id = target.id
        account_id = account.id

    await _check_and_maybe_claim(http, target_id, account_id)
    return 0.5


async def check_target_now(http: httpx.AsyncClient, target_id: int) -> dict:
    """One official availability check for the demo. Respects the same account gap."""
    now = utcnow()
    with session_scope() as db:
        target = db.get(Target, target_id)
        if target is None:
            raise ValueError("Target not found.")
        if target.state == "successful":
            return {
                "ok": True,
                "state": target.state,
                "detail": target.state_detail or "Already confirmed from a profile read.",
            }
        if target.state == "attempting":
            raise ValueError("A claim is already in progress for this name.")
        config = runtime_config(db)
        accounts = db.query(Account).all()
        account = choose_account(accounts, now, config["per_account_gap_seconds"])
        if account is None:
            wait = soonest_account_wait(accounts, now, config["per_account_gap_seconds"])
            if wait is None:
                raise ValueError("No connected account can check availability right now.")
            raise ValueError(
                f"Every usable account is inside the {config['per_account_gap_seconds']}s check gap. "
                f"Try again in about {max(1, int(wait))} second(s)."
            )
        account_id = account.id
        username = target.username

    result = await _check_and_maybe_claim(http, target_id, account_id)
    with session_scope() as db:
        target = db.get(Target, target_id)
        return {
            "ok": True,
            "username": username,
            "state": target.state if target else result,
            "detail": (target.state_detail if target else None) or result,
        }


async def _check_and_maybe_claim(http: httpx.AsyncClient, target_id: int, account_id: int) -> str:
    now = utcnow()
    with session_scope() as db:
        target = db.get(Target, target_id)
        account = db.get(Account, account_id)
        if target is None or account is None:
            return "missing"
        if target.state in {"successful", "attempting"}:
            return target.state
        username = target.username
        target.state = "checking"
        target.state_detail = "Checking the official Minecraft availability endpoint."
        target.updated_at = now
        account.last_availability_check_at = now
        account.last_checked_at = now

    try:
        token = await ensure_access_token(http, account_id)
    except MinecraftError as exc:
        with session_scope() as db:
            _log(
                db,
                action="auth_refresh",
                result=exc.kind,
                detail=str(exc),
                target_id=target_id,
                account_id=account_id,
                http_status=exc.http_status,
            )
            target = db.get(Target, target_id)
            if target is not None and target.state == "checking":
                target.state = "authentication_error" if exc.kind == "auth_error" else "unknown_error"
                target.state_detail = sanitize(str(exc))
                target.last_checked_at = utcnow()
        return "authentication_error" if exc.kind == "auth_error" else "unknown_error"

    try:
        checked = await check_availability(http, token, username)
    except httpx.HTTPError as exc:
        with session_scope() as db:
            _mark_account_backoff(db, account_id, 30, "Network error during an availability check.")
            _log(
                db,
                action="availability_check",
                result="unknown_error",
                detail=f"Network error: {exc.__class__.__name__}",
                target_id=target_id,
                account_id=account_id,
            )
            _set_target(db, target_id, "unknown_error", "Network error while checking availability.")
        return "unknown_error"

    with session_scope() as db:
        _log(
            db,
            action="availability_check",
            result=checked.result,
            detail=checked.detail,
            target_id=target_id,
            account_id=account_id,
            http_status=checked.http_status,
        )
        if checked.result == "rate_limited":
            _mark_account_backoff(db, account_id, 90, checked.detail, status="error")
            _set_target(db, target_id, "rate_limited", checked.detail)
            return "rate_limited"
        if checked.result == "authentication_error":
            _mark_account_backoff(db, account_id, 0, checked.detail, status="auth_error")
            account = db.get(Account, account_id)
            if account is not None:
                account.rate_limited_until = None
            _set_target(db, target_id, "authentication_error", checked.detail)
            return "authentication_error"
        if checked.result == "not_allowed":
            _set_target(db, target_id, "failed", checked.detail, disable=True)
            return "failed"
        if checked.result != "available":
            state = "unavailable" if checked.result == "unavailable" else "unknown_error"
            _set_target(db, target_id, state, checked.detail)
            return state

    await _claim_burst(http, target_id, username)
    with session_scope() as db:
        target = db.get(Target, target_id)
        return target.state if target else "attempting"


def _set_target(db, target_id: int, state: str, detail: str, *, disable: bool = False) -> None:
    target = db.get(Target, target_id)
    if target is None or target.state == "successful":
        return
    target.state = state
    target.state_detail = sanitize(detail)
    target.last_checked_at = utcnow()
    target.updated_at = utcnow()
    if disable:
        target.enabled = False


async def _claim_burst(http: httpx.AsyncClient, target_id: int, username: str) -> None:
    now = utcnow()
    with session_scope() as db:
        target = db.get(Target, target_id)
        if target is None or target.state == "successful" or not target.enabled:
            return
        if _claims_last_minute(db, now) >= CLAIM_MAX_PER_MINUTE:
            target.state = "rate_limited"
            target.state_detail = "Claim paused because the rename budget for this minute is already used."
            target.claim_blocked_until = now + timedelta(seconds=CLAIM_BURST_COOLDOWN_SECONDS)
            target.last_checked_at = now
            return
        updated = (
            db.query(Target)
            .filter(Target.id == target_id, Target.state != "successful", Target.state != "attempting")
            .update(
                {
                    Target.state: "attempting",
                    Target.state_detail: "Availability was confirmed. Starting a username change.",
                    Target.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            return
        job = SnipingJob(target_id=target_id, username=username, status="running", started_at=now, attempt_count=0)
        db.add(job)
        db.flush()
        job_id = job.id

    attempts = 0
    final_result = "failed"
    final_detail = "No account was able to change its name."
    used_accounts: set[int] = set()

    while attempts < CLAIM_ACCOUNTS_PER_BURST:
        with session_scope() as db:
            if _claims_last_minute(db, utcnow()) >= CLAIM_MAX_PER_MINUTE:
                final_result = "rate_limited"
                final_detail = "Stopped this claim burst at the per-minute rename limit."
                break
            accounts = [row for row in db.query(Account).all() if row.id not in used_accounts]
            account = choose_claim_account(accounts, utcnow())
            if account is None:
                final_result = "failed"
                final_detail = "No connected account is currently allowed to change its username."
                break
            account_id = account.id
        used_accounts.add(account_id)
        attempts += 1
        outcome, detail = await _claim_with_account(http, target_id, username, account_id, job_id)
        final_result, final_detail = outcome, detail
        if outcome == "success":
            break
        if outcome in {"rate_limited", "unavailable", "failed"}:
            break
        if outcome in {"cooldown", "authentication_error", "auth_error"}:
            continue
        if outcome == "unknown_error":
            await asyncio.sleep(2)
            continue
        break

    with session_scope() as db:
        target = db.get(Target, target_id)
        job = db.get(SnipingJob, job_id)
        if job is not None:
            job.attempt_count = attempts
            job.finished_at = utcnow()
            job.status = "succeeded" if final_result == "success" else "failed"
            job.result_detail = sanitize(final_detail)
        if target is None or target.state == "successful":
            return
        if final_result == "success":
            return
        state = {
            "rate_limited": "rate_limited",
            "unavailable": "unavailable",
            "authentication_error": "authentication_error",
            "auth_error": "authentication_error",
            "unknown_error": "unknown_error",
            "failed": "failed",
            "cooldown": "failed",
        }.get(final_result, "unknown_error")
        target.state = state
        target.state_detail = sanitize(final_detail)
        target.last_checked_at = utcnow()
        target.claim_blocked_until = utcnow() + timedelta(seconds=CLAIM_BURST_COOLDOWN_SECONDS)
        target.updated_at = utcnow()


async def _claim_with_account(http: httpx.AsyncClient, target_id: int, username: str, account_id: int, job_id: int) -> tuple[str, str]:
    try:
        token = await ensure_access_token(http, account_id)
    except MinecraftError as exc:
        with session_scope() as db:
            _log(
                db,
                action="claim",
                result=exc.kind,
                detail=str(exc),
                target_id=target_id,
                account_id=account_id,
                job_id=job_id,
                http_status=exc.http_status,
            )
        if exc.kind == "rate_limited":
            return "rate_limited", str(exc)
        if exc.kind == "auth_error":
            return "auth_error", str(exc)
        return "unknown_error", str(exc)

    try:
        profile_status, profile = await get_profile(http, token)
    except httpx.HTTPError:
        with session_scope() as db:
            _log(db, action="profile_confirm", result="unknown_error", detail="Network error before rename.", target_id=target_id, account_id=account_id, job_id=job_id)
            _mark_account_backoff(db, account_id, 30, "Network error before a username change.")
        return "unknown_error", "Network error before the username change."

    if profile_status == 200 and profile_has_name(profile, username):
        _mark_success(target_id, account_id, job_id, profile)
        return "success", "This account already had the target name, confirmed from its profile."

    try:
        eligible_status, namechange = await get_name_change(http, token)
    except httpx.HTTPError:
        return "unknown_error", "Network error while checking whether the account can rename."

    if eligible_status == 429:
        with session_scope() as db:
            _mark_account_backoff(db, account_id, 90, "Rate limited while checking name-change eligibility.")
            _log(db, action="namechange_check", result="rate_limited", detail="Name-change eligibility was rate limited.", target_id=target_id, account_id=account_id, job_id=job_id, http_status=429)
        return "rate_limited", "Rate limited while checking name-change eligibility."
    if eligible_status == 200 and namechange.get("nameChangeAllowed") is False:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is not None:
                apply_profile(account, profile if profile_status == 200 else {"id": account.mc_uuid, "name": account.mc_name}, namechange)
                account.status = "cooldown"
            _log(db, action="namechange_check", result="cooldown", detail="Account is inside the 30-day rename cooldown.", target_id=target_id, account_id=account_id, job_id=job_id, http_status=200)
        return "cooldown", "Account is inside the 30-day rename cooldown."

    try:
        status, body = await change_name(http, token, username)
    except httpx.HTTPError:
        with session_scope() as db:
            _log(db, action="claim", result="unknown_error", detail="Network error during rename.", target_id=target_id, account_id=account_id, job_id=job_id)
            _mark_account_backoff(db, account_id, 30, "Network error during a username change.")
        return "unknown_error", "Network error during the username change."

    confirmed = False
    confirm_profile = None
    if status == 200:
        try:
            confirm_status, confirm_profile = await get_profile(http, token)
            confirmed = confirm_status == 200 and profile_has_name(confirm_profile, username)
        except httpx.HTTPError:
            confirmed = False
            confirm_profile = None
    result, detail = interpret_claim(status, body, confirmed=confirmed)
    with session_scope() as db:
        _log(db, action="claim", result=result, detail=detail, target_id=target_id, account_id=account_id, job_id=job_id, http_status=status)
        if result == "rate_limited":
            _mark_account_backoff(db, account_id, 90, detail)
        elif result == "authentication_error":
            _mark_account_backoff(db, account_id, 0, detail, status="auth_error")
            account = db.get(Account, account_id)
            if account is not None:
                account.rate_limited_until = None
                account.status = "auth_error"
    if result == "success" and confirm_profile is not None:
        _mark_success(target_id, account_id, job_id, confirm_profile)
    elif result == "success":
        return "unknown_error", "Rename looked successful but the confirming profile read did not complete."
    return result, detail


def _mark_success(target_id: int, account_id: int, job_id: int | None, profile: dict) -> None:
    with session_scope() as db:
        target = db.get(Target, target_id)
        account = db.get(Account, account_id)
        if target is not None:
            target.state = "successful"
            target.state_detail = "Profile read confirms the username change."
            target.claimed_at = utcnow()
            target.claimed_by_account_id = account_id
            target.enabled = False
            target.claim_blocked_until = None
            target.last_checked_at = utcnow()
            target.updated_at = utcnow()
        if account is not None:
            account.mc_name = str(profile.get("name") or account.mc_name or "")
            account.mc_uuid = str(profile.get("id") or account.mc_uuid or "")
            account.name_change_allowed = False
            account.status = "cooldown"
            account.status_detail = "Name change succeeded. This account is now in the 30-day cooldown."
            account.updated_at = utcnow()
        if job_id is not None:
            job = db.get(SnipingJob, job_id)
            if job is not None:
                job.status = "succeeded"
                job.account_id = account_id
                job.finished_at = utcnow()
                job.result_detail = "Profile read confirms the username change."
        _log(
            db,
            action="profile_confirm",
            result="success",
            detail="Profile name matches the target.",
            target_id=target_id,
            account_id=account_id,
            job_id=job_id,
            http_status=200,
        )


async def monitor_loop(stop: asyncio.Event, http: httpx.AsyncClient) -> None:
    logger.info("Monitor started")
    while not stop.is_set():
        try:
            delay = await run_once(http)
        except Exception:
            logger.exception("Monitor iteration failed")
            delay = 5.0
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.2, min(5.0, delay)))
            return
        except asyncio.TimeoutError:
            continue
    logger.info("Monitor stopped")
