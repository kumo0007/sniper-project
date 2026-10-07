import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.crypto import decrypt_secret
from app.db import session_scope
from app.minecraft.errors import MinecraftError
from app.minecraft.tokens import credential_hint, normalize_bearer_token
from app.models import Account
from app.services.sanitize import sanitize
from app.services.sessions import ensure_access_token
from tests.conftest import login


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.signaturepart1234567890"


def test_normalize_rejects_passwords_and_masks_hint():
    with pytest.raises(ValueError):
        normalize_bearer_token("not-a-token")
    with pytest.raises(ValueError):
        normalize_bearer_token("password12345")
    token = _fake_jwt({"exp": int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())})
    assert normalize_bearer_token(f"Bearer {token}") == token
    hint = credential_hint(token)
    assert token not in hint
    assert "mc-jwt" in hint


def test_sanitize_redacts_jwt_and_bearer():
    token = _fake_jwt({"exp": 9999999999})
    cleaned = sanitize(f"failed Bearer {token} and leftover {token}")
    assert token not in cleaned
    assert "[redacted]" in cleaned


def test_bearer_valid_invalid_expired_replace_delete(client, monkeypatch):
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    good = _fake_jwt({"exp": int((datetime.now(timezone.utc) + timedelta(hours=6)).timestamp())})
    bad = _fake_jwt({"exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())})

    async def fake_profile(_http, token):
        if token == good:
            return {"id": "abc", "name": "BearerUser"}
        raise MinecraftError("rejected", kind="auth_error", http_status=401)

    async def fake_namechange(_http, token):
        if token == good:
            return 200, {"nameChangeAllowed": True, "changedAt": "2020-01-01T00:00:00Z"}
        return 401, {}

    monkeypatch.setattr("app.services.bearer.read_profile_or_raise", fake_profile)
    monkeypatch.setattr("app.services.bearer.get_name_change", fake_namechange)

    rejected = client.post(
        "/api/accounts",
        json={"label": "Bad", "auth_method": "bearer_token", "bearer_token": bad},
        headers=headers,
    )
    assert rejected.status_code == 400
    assert bad not in rejected.text
    assert "token" in rejected.json()["detail"].lower() or "rejected" in rejected.json()["detail"].lower()

    created = client.post(
        "/api/accounts",
        json={"label": "Tok", "auth_method": "bearer_token", "bearer_token": good},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["auth_method"] == "bearer_token"
    assert body["status"] in {"ready", "cooldown"}
    assert body["mc_name"] == "BearerUser"
    assert body["credential_configured"] is True
    assert good not in created.text
    assert body["credential_hint"]
    assert good not in body["credential_hint"]

    account_id = body["id"]
    with session_scope() as db:
        account = db.get(Account, account_id)
        assert decrypt_secret(account.encrypted_access_token) == good
        assert account.encrypted_refresh_token is None

    with session_scope() as db:
        account = db.get(Account, account_id)
        account.access_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    with pytest.raises(MinecraftError):
        asyncio.run(ensure_access_token(httpx.AsyncClient(), account_id))

    replacement = _fake_jwt({"exp": int((datetime.now(timezone.utc) + timedelta(hours=8)).timestamp())})

    async def fake_profile2(_http, token):
        if token == replacement:
            return {"id": "abc", "name": "BearerUser"}
        raise MinecraftError("rejected", kind="auth_error", http_status=401)

    async def fake_namechange2(_http, _token):
        return 200, {"nameChangeAllowed": True}

    monkeypatch.setattr("app.services.bearer.read_profile_or_raise", fake_profile2)
    monkeypatch.setattr("app.services.bearer.get_name_change", fake_namechange2)

    replaced = client.post(
        f"/api/accounts/{account_id}/bearer-token",
        json={"bearer_token": replacement},
        headers=headers,
    )
    assert replaced.status_code == 200
    assert replacement not in replaced.text
    assert replaced.json()["credential_configured"] is True

    deleted = client.delete(f"/api/accounts/{account_id}", headers=headers)
    assert deleted.status_code == 200
    with session_scope() as db:
        assert db.get(Account, account_id) is None


def test_missing_bearer_token_rejected(client):
    csrf = login(client)
    response = client.post(
        "/api/accounts",
        json={"label": "Empty", "auth_method": "bearer_token"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400


def test_bearer_endpoints_require_login(client):
    token = _fake_jwt({"exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())})
    create = client.post(
        "/api/accounts",
        json={"label": "NoAuth", "auth_method": "bearer_token", "bearer_token": token},
    )
    assert create.status_code == 401
    assert token not in create.text

    replace = client.post(
        "/api/accounts/1/bearer-token",
        json={"bearer_token": token},
    )
    assert replace.status_code == 401
    assert token not in replace.text
