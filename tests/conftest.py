import os
import tempfile

from cryptography.fernet import Fernet

_handle, _database = tempfile.mkstemp(prefix="sniper-test-", suffix=".db")
os.close(_handle)
os.environ["APP_PASSWORD"] = "test-password-123"
os.environ["SECRET_KEY"] = "test-secret-key-value"
os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["MICROSOFT_CLIENT_ID"] = "test-client-id"
os.environ["DATABASE_URL"] = "sqlite:///" + _database.replace("\\", "/")
os.environ["RUN_WORKER"] = "0"
os.environ["MAX_ACCOUNTS"] = "2"
os.environ["SECURE_COOKIES"] = "0"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from app.db import get_engine
    from app.main import app
    from app.models import Base

    Base.metadata.drop_all(get_engine())
    with TestClient(app) as test_client:
        yield test_client


def login(client, password="test-password-123"):
    response = client.post("/api/login", json={"password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]
