"""Write a local .env with fresh secrets. Does not print the password to logs beyond the terminal."""

from __future__ import annotations

import secrets
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def main() -> None:
    if ENV_PATH.exists():
        raise SystemExit(f"{ENV_PATH} already exists. Delete it first if you really want a new one.")
    password = secrets.token_urlsafe(18)
    secret = secrets.token_urlsafe(32)
    key = Fernet.generate_key().decode("ascii")
    ENV_PATH.write_text(
        "\n".join(
            [
                f"APP_PASSWORD={password}",
                f"SECRET_KEY={secret}",
                f"TOKEN_ENCRYPTION_KEY={key}",
                "MICROSOFT_CLIENT_ID=",
                "DATABASE_URL=sqlite:///./data/sniper.db",
                "RUN_WORKER=1",
                "HOST=127.0.0.1",
                "PORT=8000",
                "SECURE_COOKIES=0",
                "MAX_ACCOUNTS=25",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Wrote {ENV_PATH}")
    print("Sign in to the web UI with this password (store it somewhere safe):")
    print(password)
    print("Next: create an Azure app and set MICROSOFT_CLIENT_ID. See README.md.")


if __name__ == "__main__":
    main()
