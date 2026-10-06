from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import AVAILABILITY_MIN_GAP_SECONDS
from app.services.scheduler import (
    choose_account,
    choose_claim_account,
    choose_target,
    clamp_gap_seconds,
)


def _target(**kwargs):
    base = dict(
        id=1,
        enabled=True,
        state="unavailable",
        claim_blocked_until=None,
        last_checked_at=None,
        release_hint_at=None,
        priority="normal",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _account(**kwargs):
    base = dict(
        id=1,
        enabled=True,
        status="ready",
        name_change_allowed=True,
        rate_limited_until=None,
        encrypted_refresh_token="stored",
        encrypted_access_token=None,
        access_expires_at=None,
        last_availability_check_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_gap_cannot_drop_below_documented_limit():
    with pytest.raises(ValueError):
        clamp_gap_seconds(AVAILABILITY_MIN_GAP_SECONDS - 1)
    assert clamp_gap_seconds(AVAILABILITY_MIN_GAP_SECONDS) == AVAILABILITY_MIN_GAP_SECONDS


def test_three_character_names_are_preferred_when_equally_stale():
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    old = now - timedelta(seconds=100)
    chosen = choose_target(
        [
            _target(id=1, priority="normal", last_checked_at=old),
            _target(id=2, priority="3c", last_checked_at=old),
            _target(id=3, priority="og", last_checked_at=old),
        ],
        now,
        slow_gap=600,
        before_hours=2,
        after_hours=6,
    )
    assert chosen.id == 2


def test_lower_priority_name_is_not_starved():
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    chosen = choose_target(
        [
            _target(id=1, priority="3c", last_checked_at=now),
            _target(id=2, priority="normal", last_checked_at=None),
        ],
        now,
        slow_gap=600,
        before_hours=2,
        after_hours=6,
    )
    assert chosen.id == 2


def test_release_hint_uses_the_slow_gap_outside_the_window():
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    hint = now + timedelta(days=20)
    chosen = choose_target(
        [_target(id=1, priority="3c", last_checked_at=now - timedelta(minutes=5), release_hint_at=hint)],
        now,
        slow_gap=600,
        before_hours=2,
        after_hours=6,
    )
    assert chosen is None


def test_account_gap_is_respected_and_claim_can_use_a_ready_account():
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    recent = _account(id=1, last_availability_check_at=now - timedelta(seconds=5))
    ready = _account(id=2, last_availability_check_at=now - timedelta(seconds=30))
    cooling = _account(id=3, name_change_allowed=False, last_availability_check_at=now - timedelta(hours=1))
    assert choose_account([recent, ready], now, AVAILABILITY_MIN_GAP_SECONDS).id == 2
    assert choose_claim_account([recent, cooling], now).id == 1
    assert choose_claim_account([cooling], now) is None
