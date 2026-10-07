"""Environment configuration. Secrets stay in the environment, not in code."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Stay under the documented 20 availability requests / 5 minutes / account.
AVAILABILITY_MIN_GAP_SECONDS = 16
CLAIM_MAX_PER_MINUTE = 3
CLAIM_ACCOUNTS_PER_BURST = 3
CLAIM_BURST_COOLDOWN_SECONDS = 30
ACCOUNT_CAP_CEILING = 50

_WEAK_PASSWORDS = {
    "change-me",
    "changeme",
    "password",
    "replace-with-a-long-password",
}


@dataclass(frozen=True)
class Settings:
    app_password: str
    secret_key: str
    token_encryption_key: str
    microsoft_client_id: str
    database_url: str
    run_worker: bool
    host: str
    port: int
    secure_cookies: bool
    max_accounts: int


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable {name}. See .env.example.")
    return value


def _database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        path = (ROOT / "data" / "sniper.db").resolve()
        return "sqlite:///" + path.as_posix()
    if raw.startswith("sqlite:///./") or raw.startswith("sqlite:///.."):
        relative = raw.removeprefix("sqlite:///")
        path = (ROOT / relative).resolve()
        return "sqlite:///" + path.as_posix()
    return raw


def _max_accounts() -> int:
    try:
        raw = int(os.environ.get("MAX_ACCOUNTS", "25"))
    except ValueError:
        raw = 25
    return max(1, min(ACCOUNT_CAP_CEILING, raw))


@lru_cache
def get_settings() -> Settings:
    password = _required("APP_PASSWORD")
    if len(password) < 10 or password.lower() in _WEAK_PASSWORDS:
        raise SystemExit("APP_PASSWORD must be a unique value of at least 10 characters.")
    secret = _required("SECRET_KEY")
    if len(secret) < 16:
        raise SystemExit("SECRET_KEY must be at least 16 characters.")
    encryption_key = _required("TOKEN_ENCRYPTION_KEY")
    return Settings(
        app_password=password,
        secret_key=secret,
        token_encryption_key=encryption_key,
        microsoft_client_id=os.environ.get("MICROSOFT_CLIENT_ID", "").strip(),
        database_url=_database_url(),
        run_worker=os.environ.get("RUN_WORKER", "1").strip() not in {"0", "false", "False"},
        host=os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1").strip()
        or "127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        secure_cookies=os.environ.get("SECURE_COOKIES", "0").strip() in {"1", "true", "True"},
        max_accounts=_max_accounts(),
    )
