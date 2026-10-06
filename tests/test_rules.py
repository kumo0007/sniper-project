from app.crypto import decrypt_secret, encrypt_secret
from app.minecraft.profile import interpret_availability, interpret_claim, profile_has_name
from app.services.sanitize import sanitize
from app.validate import clean_username, resolve_priority


def test_tokens_round_trip_and_are_not_stored_in_plaintext():
    secret = "refresh-token-value"
    stored = encrypt_secret(secret)
    assert stored is not None
    assert secret not in stored
    assert decrypt_secret(stored) == secret


def test_logs_redact_bearer_tokens():
    cleaned = sanitize("Authorization failed Bearer aaa.bbb.ccc and access_token=zzzzzzzzzzzz")
    assert "aaa.bbb.ccc" not in cleaned
    assert "zzzzzzzzzzzz" not in cleaned
    assert "[redacted]" in cleaned


def test_availability_and_claim_need_an_official_signal():
    assert interpret_availability(200, {"status": "AVAILABLE"})[0] == "available"
    assert interpret_availability(200, {"status": "DUPLICATE"})[0] == "unavailable"
    assert interpret_availability(429, {})[0] == "rate_limited"
    assert interpret_claim(200, {"name": "Ace"}, confirmed=False)[0] == "unknown_error"
    assert interpret_claim(200, {"name": "Ace"}, confirmed=True)[0] == "success"
    assert interpret_claim(403, {"details": {"status": "DUPLICATE"}}, confirmed=False)[0] == "unavailable"
    assert profile_has_name({"name": "Ace"}, "ace") is True
    assert profile_has_name({"name": "Other"}, "ace") is False


def test_username_rules():
    assert clean_username(" Ab_1 ") == "Ab_1"
    assert resolve_priority("abc", "auto") == "3c"
    assert resolve_priority("longer", None) == "normal"
    try:
        clean_username("no")
        raise AssertionError("short names should fail")
    except ValueError:
        pass
    try:
        resolve_priority("longer", "3c")
        raise AssertionError("3c priority should require 3 characters")
    except ValueError:
        pass
