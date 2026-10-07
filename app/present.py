from __future__ import annotations

from app.models import Account, AttemptLog, DeviceLogin, SnipingJob, Target
from app.timeutil import iso


def device_login_view(login: DeviceLogin | None) -> dict | None:
    if login is None or login.status != "pending":
        return None
    return {
        "user_code": login.user_code,
        "verification_uri": login.verification_uri,
        "message": login.message,
        "status": login.status,
        "expires_at": iso(login.expires_at),
    }


def account_view(account: Account) -> dict:
    auth_method = account.auth_method or "device_code"
    credential_configured = bool(account.encrypted_access_token or account.encrypted_refresh_token)
    return {
        "id": account.id,
        "label": account.label,
        "auth_method": auth_method,
        "credential_configured": credential_configured,
        "credential_hint": account.credential_hint if credential_configured else None,
        "mc_uuid": account.mc_uuid,
        "mc_name": account.mc_name,
        "enabled": account.enabled,
        "status": account.status,
        "status_detail": account.status_detail,
        "name_change_allowed": account.name_change_allowed,
        "name_changed_at": iso(account.name_changed_at),
        "last_checked_at": iso(account.last_checked_at),
        "access_expires_at": iso(account.access_expires_at),
        "rate_limited_until": iso(account.rate_limited_until),
        "created_at": iso(account.created_at),
        "login": device_login_view(account.device_login),
    }


def target_view(target: Target, claimed_name: str | None = None) -> dict:
    return {
        "id": target.id,
        "username": target.username,
        "enabled": target.enabled,
        "priority": target.priority,
        "state": target.state,
        "state_detail": target.state_detail,
        "last_checked_at": iso(target.last_checked_at),
        "release_hint_at": iso(target.release_hint_at),
        "claim_blocked_until": iso(target.claim_blocked_until),
        "claimed_at": iso(target.claimed_at),
        "claimed_by_account_id": target.claimed_by_account_id,
        "claimed_by_name": claimed_name,
        "created_at": iso(target.created_at),
    }


def log_view(row: AttemptLog, names: dict[int, str], accounts: dict[int, str]) -> dict:
    return {
        "id": row.id,
        "target_id": row.target_id,
        "username": names.get(row.target_id or -1),
        "account_id": row.account_id,
        "account_label": accounts.get(row.account_id or -1),
        "job_id": row.job_id,
        "timestamp": iso(row.timestamp),
        "action": row.action,
        "result": row.result,
        "http_status": row.http_status,
        "detail": row.detail,
    }


def job_view(job: SnipingJob) -> dict:
    return {
        "id": job.id,
        "target_id": job.target_id,
        "username": job.username,
        "status": job.status,
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
        "account_id": job.account_id,
        "attempt_count": job.attempt_count,
        "result_detail": job.result_detail,
    }
