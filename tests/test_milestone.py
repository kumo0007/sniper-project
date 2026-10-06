from app.crypto import encrypt_secret
from app.db import session_scope
from app.models import Account, AttemptLog, Target
from app.services.milestone import build_milestone
from app.timeutil import utcnow
from tests.conftest import login


def test_milestone_progresses_with_account_target_and_check(client):
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}

    empty = client.get("/api/overview").json()["milestone"]
    assert empty["complete"] is False
    assert empty["summary"]["microsoft_client_configured"] is True
    by_id = {item["id"]: item for item in empty["items"]}
    assert by_id["open_app"]["done"] is True
    assert by_id["no_proxy_farm"]["done"] is True
    assert by_id["own_accounts"]["done"] is False
    assert by_id["add_target"]["done"] is False

    with session_scope() as db:
        account = Account(
            label="Main",
            enabled=True,
            status="ready",
            encrypted_refresh_token=encrypt_secret("refresh"),
            name_change_allowed=True,
            mc_name="Tester",
        )
        db.add(account)
        db.flush()
        target = Target(
            username="ace",
            username_key="ace",
            enabled=True,
            priority="3c",
            state="unavailable",
            last_checked_at=utcnow(),
        )
        db.add(target)
        db.flush()
        db.add(
            AttemptLog(
                target_id=target.id,
                account_id=account.id,
                timestamp=utcnow(),
                action="availability_check",
                result="unavailable",
                http_status=200,
                detail="Name is taken.",
            )
        )

    client.post("/api/sniper/start", headers=headers)
    ready = client.get("/api/overview").json()["milestone"]
    done = {item["id"]: item["done"] for item in ready["items"]}
    assert done["own_accounts"] is True
    assert done["add_target"] is True
    assert done["detect_availability"] is True
    assert done["claim_path"] is True
    assert ready["complete"] is True


def test_build_milestone_without_http():
    from app.db import get_engine
    from app.models import Base

    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    with session_scope() as db:
        milestone = build_milestone(db)
        assert milestone["done_count"] >= 3
        assert any(item["id"] == "open_app" and item["done"] for item in milestone["items"])
