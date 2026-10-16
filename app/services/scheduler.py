"""Pure scheduling rules. HTTP and the database stay outside this module."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.config import AVAILABILITY_MIN_GAP_SECONDS, HIGH_PRIORITY
from app.timeutil import as_utc

PRIORITY_WEIGHT = {"3c": 8, "og": 4, "normal": 1}
BLOCKED_TARGET_STATES = {"successful", "attempting"}
UNUSABLE_ACCOUNT_STATUSES = {
    "needs_login",
    "authenticating",
    "disabled",
    "auth_error",
    "blocked",
}


def clamp_gap_seconds(value: int) -> int:
    if value < AVAILABILITY_MIN_GAP_SECONDS or value > 3600:
        raise ValueError(
            f"Per-account gap must be between {AVAILABILITY_MIN_GAP_SECONDS} and 3600 seconds."
        )
    return value


def clamp_slow_gap(value: int) -> int:
    if value < 60 or value > 86400:
        raise ValueError("Slow check interval must be between 60 and 86400 seconds.")
    return value


def clamp_window_hours(value: int) -> int:
    if value < 0 or value > 168:
        raise ValueError("Release window must be between 0 and 168 hours.")
    return value


def is_high_priority(target) -> bool:
    return (getattr(target, "priority", None) or "normal") in HIGH_PRIORITY


def focus_high_priority(targets) -> bool:
    """True when any enabled 3c/OG target should monopolize check bandwidth."""
    return any(
        target.enabled and target.state not in BLOCKED_TARGET_STATES and is_high_priority(target)
        for target in targets
    )


def effective_check_interval(account_count: int, min_gap: int = AVAILABILITY_MIN_GAP_SECONDS) -> float:
    """Best-case seconds between checks when accounts are staggered."""
    usable = max(1, account_count)
    return max(0.5, min_gap / usable)


def _release_bounds(target, before_hours: int, after_hours: int) -> tuple[datetime, datetime] | None:
    hint = as_utc(getattr(target, "release_hint_at", None))
    if hint is None:
        return None
    end = as_utc(getattr(target, "release_hint_end_at", None))
    if end is None:
        end = hint + timedelta(hours=after_hours)
    start = hint - timedelta(hours=before_hours)
    if end < start:
        start, end = end, start
    return start, end


def gap_for_target(target, now: datetime, slow_gap: int, before_hours: int, after_hours: int) -> int:
    bounds = _release_bounds(target, before_hours, after_hours)
    if bounds is None:
        return 0
    start, end = bounds
    if start <= now <= end:
        return 0
    return slow_gap


def in_release_window(target, now: datetime, before_hours: int, after_hours: int) -> bool:
    bounds = _release_bounds(target, before_hours, after_hours)
    if bounds is None:
        return False
    start, end = bounds
    return start <= now <= end


def stagger_account_checks(accounts, now: datetime, min_gap: int) -> int:
    """Spread last-check times so ready accounts fire evenly across the gap.

    Returns how many accounts were staggered. Call when Start is pressed so a
    multi-account 3c race does not clump all checks into the first second.
    """
    usable = [row for row in accounts if _account_usable(row, now, require_name_change=False)]
    count = len(usable)
    if count == 0:
        return 0
    step = min_gap / count
    for index, account in enumerate(usable):
        account.last_availability_check_at = now - timedelta(seconds=min_gap) + timedelta(seconds=step * index)
        account.updated_at = now
    return count


def _eligible_target(target, now: datetime, slow_gap: int, before_hours: int, after_hours: int) -> bool:
    if not target.enabled or target.state in BLOCKED_TARGET_STATES:
        return False
    blocked = as_utc(getattr(target, "claim_blocked_until", None))
    if blocked and blocked > now:
        return False
    last = as_utc(target.last_checked_at)
    gap = gap_for_target(target, now, slow_gap, before_hours, after_hours)
    if last is None:
        return True
    return now >= last + timedelta(seconds=gap)


def choose_target(targets, now: datetime, slow_gap: int, before_hours: int, after_hours: int):
    """Pick the next name to check.

    When any enabled 3c/OG target exists, normal-priority names are skipped so all
    account bandwidth goes to the race. Higher-priority names still outrank peers.
    """
    pool = list(targets)
    if focus_high_priority(pool):
        pool = [row for row in pool if is_high_priority(row)]
    best = None
    best_score = None
    for target in pool:
        if not _eligible_target(target, now, slow_gap, before_hours, after_hours):
            continue
        last = as_utc(target.last_checked_at)
        age = 10**9 if last is None else max(0.0, (now - last).total_seconds())
        weight = PRIORITY_WEIGHT.get(target.priority, 1)
        # Prefer targets inside their release-hint window.
        if in_release_window(target, now, before_hours, after_hours):
            weight *= 4
        score = age * weight
        if best is None or score > best_score:
            best = target
            best_score = score
    return best


def account_can_authenticate(account, now: datetime) -> bool:
    if getattr(account, "encrypted_refresh_token", None):
        return True
    expires = as_utc(getattr(account, "access_expires_at", None))
    return bool(getattr(account, "encrypted_access_token", None) and expires and expires > now)


def _account_usable(account, now: datetime, *, require_name_change: bool) -> bool:
    if not account.enabled or account.status in UNUSABLE_ACCOUNT_STATUSES:
        return False
    if require_name_change and account.name_change_allowed is not True:
        return False
    limited = as_utc(getattr(account, "rate_limited_until", None))
    if limited and limited > now:
        return False
    return account_can_authenticate(account, now)


def choose_account(accounts, now: datetime, min_gap: int):
    """Oldest eligible account whose availability gap has elapsed."""
    best = None
    best_last = None
    for account in accounts:
        if not _account_usable(account, now, require_name_change=False):
            continue
        last = as_utc(account.last_availability_check_at)
        if last is not None and now < last + timedelta(seconds=min_gap):
            continue
        sort_last = last or datetime.min.replace(tzinfo=now.tzinfo)
        if best is None or sort_last < best_last:
            best = account
            best_last = sort_last
    return best


def list_claim_accounts(accounts, now: datetime, limit: int) -> list:
    """Rename-ready accounts, oldest-check first, up to limit."""
    ready = []
    for account in accounts:
        if not _account_usable(account, now, require_name_change=True):
            continue
        last = as_utc(account.last_availability_check_at)
        sort_last = last or datetime.min.replace(tzinfo=now.tzinfo)
        ready.append((sort_last, account))
    ready.sort(key=lambda item: item[0])
    return [account for _, account in ready[: max(0, limit)]]


def choose_claim_account(accounts, now: datetime):
    """An account that is allowed to rename right now. The check gap does not apply."""
    rows = list_claim_accounts(accounts, now, 1)
    return rows[0] if rows else None


def soonest_account_wait(accounts, now: datetime, min_gap: int) -> float | None:
    """Seconds until some account may check again. None if no account can ever check."""
    waits: list[float] = []
    for account in accounts:
        if not _account_usable(account, now, require_name_change=False):
            continue
        last = as_utc(account.last_availability_check_at)
        if last is None:
            return 0.0
        waits.append(max(0.0, (last + timedelta(seconds=min_gap) - now).total_seconds()))
    if not waits:
        return None
    return min(waits)


def count_usable_accounts(accounts, now: datetime) -> int:
    return sum(1 for account in accounts if _account_usable(account, now, require_name_change=False))
