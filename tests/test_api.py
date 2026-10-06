from app.db import session_scope
from app.minecraft.auth import DeviceCode
from app.models import Account, Target
from tests.conftest import login


def test_health_and_login_gate(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/login", json={"password": "wrong-password"}).status_code == 401
    csrf = login(client)
    assert client.post("/api/targets", json={"username": "alpha"}).status_code == 403
    created = client.post(
        "/api/targets",
        json={"username": "alpha", "priority": "auto"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert created.json()["priority"] == "normal"
    rejected = client.post(
        "/api/targets",
        json={"username": "no"},
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 400


def test_three_character_priority_start_stop_and_gap(client):
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    created = client.post("/api/targets", json={"username": "ace"}, headers=headers)
    assert created.status_code == 201
    assert created.json()["priority"] == "3c"
    assert client.post("/api/sniper/start", headers=headers).json()["sniper_running"] is True
    overview = client.get("/api/overview")
    assert overview.json()["sniper_running"] is True
    assert client.post("/api/sniper/stop", headers=headers).json()["sniper_running"] is False
    too_fast = client.put(
        "/api/settings",
        json={
            "per_account_gap_seconds": 1,
            "slow_gap_seconds": 600,
            "fast_window_before_hours": 2,
            "fast_window_after_hours": 6,
        },
        headers=headers,
    )
    assert too_fast.status_code == 400


def test_account_cap_and_secret_free_login_payload(client, monkeypatch):
    async def fake_device_code(_http, _client_id):
        return DeviceCode(
            user_code="ABCD-EFGH",
            device_code="device-secret-value",
            verification_uri="https://www.microsoft.com/link",
            message="Enter the code",
            interval=5,
            expires_in=0,
        )

    monkeypatch.setattr("app.services.accounts.request_device_code", fake_device_code)
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    created = client.post("/api/accounts", json={"label": "Main"}, headers=headers)
    assert created.status_code == 201
    body = created.text
    assert "ABCD-EFGH" in body
    assert "device-secret-value" not in body
    assert "encrypted" not in body

    with session_scope() as db:
        db.add(Account(label="Second", status="needs_login", enabled=True))
    capped = client.post("/api/accounts", json={"label": "Third"}, headers=headers)
    assert capped.status_code == 400

    account_id = created.json()["id"]
    removed = client.delete(f"/api/accounts/{account_id}", headers=headers)
    assert removed.status_code == 200


def test_confirmed_target_stays_disabled(client):
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    created = client.post("/api/targets", json={"username": "kept"}, headers=headers)
    target_id = created.json()["id"]
    with session_scope() as db:
        target = db.get(Target, target_id)
        target.state = "successful"
        target.enabled = False
    resumed = client.patch(f"/api/targets/{target_id}", json={"enabled": True}, headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["enabled"] is False
