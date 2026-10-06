from __future__ import annotations

from app.models import Setting

DEFAULTS = {
    "sniper_running": "0",
    "per_account_gap_seconds": "16",
    "slow_gap_seconds": "600",
    "fast_window_before_hours": "2",
    "fast_window_after_hours": "6",
    "worker_owner": "",
    "worker_heartbeat": "",
}


def seed_settings(db) -> None:
    for key, value in DEFAULTS.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value))


def get_value(db, key: str) -> str:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, "")
    return row.value


def set_value(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def runtime_config(db) -> dict:
    return {
        "sniper_running": get_value(db, "sniper_running") == "1",
        "per_account_gap_seconds": int(get_value(db, "per_account_gap_seconds") or "16"),
        "slow_gap_seconds": int(get_value(db, "slow_gap_seconds") or "600"),
        "fast_window_before_hours": int(get_value(db, "fast_window_before_hours") or "2"),
        "fast_window_after_hours": int(get_value(db, "fast_window_after_hours") or "6"),
        "worker_owner": get_value(db, "worker_owner"),
        "worker_heartbeat": get_value(db, "worker_heartbeat"),
    }
