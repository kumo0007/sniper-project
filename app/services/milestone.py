"""First-milestone readiness mapped to the requirements acceptance criteria."""

from __future__ import annotations

from datetime import datetime

from app.config import ACCOUNT_CAP_CEILING, get_settings
from app.models import Account, AttemptLog, Target
from app.services.settings_store import runtime_config
from app.timeutil import as_utc, utcnow

HEARTBEAT_FRESH_SECONDS = 25


def _worker_fresh(config: dict, now: datetime) -> bool:
    raw = config.get("worker_heartbeat") or ""
    if not raw:
        return False
    try:
        beat = as_utc(datetime.fromisoformat(raw))
    except ValueError:
        return False
    return beat is not None and (now - beat).total_seconds() < HEARTBEAT_FRESH_SECONDS


def build_milestone(db) -> dict:
    """Return a checklist the client can use to prove the first milestone."""
    settings = get_settings()
    config = runtime_config(db)
    now = utcnow()
    accounts = db.query(Account).all()
    targets = db.query(Target).all()
    logs = db.query(AttemptLog).order_by(AttemptLog.id.desc()).limit(200).all()

    azure_ok = bool(settings.microsoft_client_id)
    connected = [
        row
        for row in accounts
        if row.enabled and row.encrypted_refresh_token and row.status not in {"needs_login", "authenticating"}
    ]
    ready_to_check = [
        row for row in connected if row.status in {"ready", "cooldown", "error"}
    ]
    can_rename = [row for row in connected if row.name_change_allowed is True]
    enabled_targets = [row for row in targets if row.enabled]
    checked_targets = [row for row in targets if row.last_checked_at is not None]
    successful = [row for row in targets if row.state == "successful"]
    availability_logs = [row for row in logs if row.action == "availability_check"]
    claim_logs = [row for row in logs if row.action == "claim"]
    auth_errors = [
        row
        for row in accounts
        if row.status in {"auth_error", "blocked", "needs_login"} and row.status_detail
    ]
    auth_errors += [row for row in logs if row.result in {"authentication_error", "auth_error"}]

    worker_ok = _worker_fresh(config, now)
    monitoring_active = bool(config["sniper_running"] and worker_ok)

    items = [
        {
            "id": "open_app",
            "label": "Open the web application",
            "done": True,
            "detail": "You are signed in to the operator UI.",
        },
        {
            "id": "azure_client",
            "label": "Azure client id configured for Microsoft sign-in",
            "done": azure_ok,
            "detail": (
                "MICROSOFT_CLIENT_ID is set."
                if azure_ok
                else "Set MICROSOFT_CLIENT_ID in .env and restart. See docs/FIRST_MILESTONE.md."
            ),
        },
        {
            "id": "own_accounts",
            "label": "Configure your own Minecraft accounts without sending passwords",
            "done": bool(connected),
            "detail": (
                f"{len(connected)} account(s) connected with encrypted Microsoft sessions."
                if connected
                else "Connect an account on the Accounts page. Sign-in happens on Microsoft's site."
            ),
        },
        {
            "id": "add_target",
            "label": "Add a target username",
            "done": bool(targets),
            "detail": (
                f"{len(targets)} target(s) on the list."
                if targets
                else "Add a username on the Targets page. Use a disposable name for the first test."
            ),
        },
        {
            "id": "monitor_target",
            "label": "Monitor the target",
            "done": monitoring_active or bool(checked_targets) or bool(availability_logs),
            "detail": (
                "Monitoring is running."
                if monitoring_active
                else (
                    "At least one check has already run. Press Start to keep monitoring."
                    if checked_targets or availability_logs
                    else "Press Start after an account and target are ready."
                )
            ),
        },
        {
            "id": "detect_availability",
            "label": "Detect availability with the official Minecraft endpoint",
            "done": bool(availability_logs) or bool(checked_targets),
            "detail": (
                f"{len(availability_logs)} availability check(s) logged."
                if availability_logs
                else (
                    "A target has been checked."
                    if checked_targets
                    else "After Start, each check uses GET /minecraft/profile/name/<name>/available."
                )
            ),
        },
        {
            "id": "claim_path",
            "label": "Legitimate username-change path is ready",
            "done": bool(can_rename) or bool(claim_logs) or bool(successful),
            "detail": (
                "A confirmed claim already exists."
                if successful
                else (
                    f"{len(claim_logs)} claim attempt(s) logged."
                    if claim_logs
                    else (
                        f"{len(can_rename)} account(s) allowed to rename right now."
                        if can_rename
                        else "Connect an account that is outside the 30-day rename cooldown."
                    )
                )
            ),
        },
        {
            "id": "results_visible",
            "label": "Results and errors are visible in the UI",
            "done": True,
            "detail": (
                f"{len(auth_errors)} authentication issue(s) currently visible."
                if auth_errors
                else "Account state, target state, activity, and claim jobs are shown on this page."
            ),
        },
        {
            "id": "no_proxy_farm",
            "label": "Does not depend on thousands of accounts or proxies",
            "done": True,
            "detail": (
                f"Account cap is {settings.max_accounts} (hard ceiling {ACCOUNT_CAP_CEILING}). "
                "No proxy support is built in."
            ),
        },
    ]

    done_count = sum(1 for item in items if item["done"])
    complete = done_count == len(items)
    return {
        "title": "First milestone",
        "complete": complete,
        "done_count": done_count,
        "total": len(items),
        "items": items,
        "summary": {
            "accounts_connected": len(connected),
            "accounts_ready_to_check": len(ready_to_check),
            "accounts_can_rename": len(can_rename),
            "targets": len(targets),
            "targets_enabled": len(enabled_targets),
            "targets_checked": len(checked_targets),
            "confirmed_claims": len(successful),
            "availability_checks": len(availability_logs),
            "claim_attempts": len(claim_logs),
            "sniper_running": bool(config["sniper_running"]),
            "worker_heartbeating": worker_ok,
            "microsoft_client_configured": azure_ok,
            "max_accounts": settings.max_accounts,
        },
        "next_step": _next_step(items, monitoring_active, bool(can_rename), bool(successful)),
    }


def _next_step(items, monitoring_active: bool, can_rename: bool, successful: bool) -> str:
    if successful:
        return "A confirmed claim already proves the core workflow. Keep monitoring other targets if you want."
    for item in items:
        if not item["done"]:
            return item["detail"]
    if monitoring_active and can_rename:
        return (
            "Core path is ready. Leave Start on with a disposable target, or wait until a name returns AVAILABLE. "
            "Success is shown only after a profile read confirms the new name."
        )
    return "First-milestone checklist is complete."
