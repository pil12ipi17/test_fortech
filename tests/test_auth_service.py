import os
from pathlib import Path

from fastapi.testclient import TestClient

TEST_DB = Path("test_auth_service.db")
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["AUTH_DATABASE_URL"] = f"sqlite:///{TEST_DB.resolve().as_posix()}"
os.environ["AUTH_JWT_SECRET"] = "test-secret"

from services.auth_service.app.main import app  # noqa: E402


def test_register_login_and_me():
    with TestClient(app) as client:
        register_response = client.post(
            "/auth/register",
            json={"email": "user@example.com", "password": "strongpass123"},
        )
        assert register_response.status_code == 201
        register_payload = register_response.json()
        assert register_payload["user"]["email"] == "user@example.com"
        assert register_payload["access_token"]
        assert register_payload["refresh_token"]

        login_response = client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "strongpass123"},
        )
        assert login_response.status_code == 200
        login_payload = login_response.json()
        token = login_payload["access_token"]
        refresh_token = login_payload["refresh_token"]

        me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_response.status_code == 200
        assert me_response.json()["email"] == "user@example.com"

        refresh_response = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert refresh_response.status_code == 200
        refreshed_payload = refresh_response.json()
        assert refreshed_payload["access_token"]
        assert refreshed_payload["refresh_token"] != refresh_token

        logout_response = client.post("/auth/logout", json={"refresh_token": refreshed_payload["refresh_token"]})
        assert logout_response.status_code == 204

        revoked_refresh_response = client.post(
            "/auth/refresh",
            json={"refresh_token": refreshed_payload["refresh_token"]},
        )
        assert revoked_refresh_response.status_code == 401
