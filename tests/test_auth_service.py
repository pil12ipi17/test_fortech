import os
from pathlib import Path

from fastapi.testclient import TestClient

TEST_DB = Path("test_auth_service.db")
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["AUTH_DATABASE_URL"] = f"sqlite:///{TEST_DB.resolve().as_posix()}"
os.environ["AUTH_JWT_SECRET"] = "test-secret"

from services.auth_service.app.main import app  # noqa: E402


def test_auth_roles_teams_and_refresh_flow():
    with TestClient(app) as client:
        register_response = client.post(
            "/auth/register",
            json={"email": "admin@example.com", "password": "strongpass123"},
        )
        assert register_response.status_code == 201
        register_payload = register_response.json()
        assert register_payload["user"]["email"] == "admin@example.com"
        assert register_payload["user"]["roles"] == ["user", "admin"]
        assert register_payload["user"]["team_ids"] == []

        admin_access_token = register_payload["access_token"]
        admin_refresh_token = register_payload["refresh_token"]
        admin_headers = {"Authorization": f"Bearer {admin_access_token}"}

        me_response = client.get("/auth/me", headers=admin_headers)
        assert me_response.status_code == 200
        assert me_response.json()["roles"] == ["user", "admin"]

        create_team_response = client.post(
            "/teams",
            headers=admin_headers,
            json={"name": "Backend"},
        )
        assert create_team_response.status_code == 201
        team_payload = create_team_response.json()
        team_id = team_payload["id"]
        assert team_payload["name"] == "Backend"
        assert team_payload["member_count"] == 0

        create_user_response = client.post(
            "/users",
            headers=admin_headers,
            json={
                "email": "member@example.com",
                "password": "strongpass123",
                "roles": ["user"],
                "team_ids": [team_id],
            },
        )
        assert create_user_response.status_code == 201
        user_payload = create_user_response.json()
        user_id = user_payload["id"]
        assert user_payload["roles"] == ["user"]
        assert user_payload["team_ids"] == [team_id]

        plain_user_register_response = client.post(
            "/auth/register",
            json={"email": "plain.user@example.com", "password": "strongpass123"},
        )
        assert plain_user_register_response.status_code == 201
        plain_headers = {"Authorization": f"Bearer {plain_user_register_response.json()['access_token']}"}

        forbidden_create_user = client.post(
            "/users",
            headers=plain_headers,
            json={
                "email": "forbidden@example.com",
                "password": "strongpass123",
                "roles": ["user"],
                "team_ids": [],
            },
        )
        assert forbidden_create_user.status_code == 403

        forbidden_create_team = client.post(
            "/teams",
            headers=plain_headers,
            json={"name": "Frontend"},
        )
        assert forbidden_create_team.status_code == 403

        list_users_response = client.get("/users", headers=admin_headers)
        assert list_users_response.status_code == 200
        assert list_users_response.json()["total"] == 3

        role_update_response = client.patch(
            f"/users/{user_id}/roles",
            headers=admin_headers,
            json={"roles": ["teamlead"]},
        )
        assert role_update_response.status_code == 200
        assert role_update_response.json()["roles"] == ["teamlead"]

        member_login_response = client.post(
            "/auth/login",
            json={"email": "member@example.com", "password": "strongpass123"},
        )
        assert member_login_response.status_code == 200
        member_payload = member_login_response.json()
        member_headers = {"Authorization": f"Bearer {member_payload['access_token']}"}

        member_me_response = client.get("/auth/me", headers=member_headers)
        assert member_me_response.status_code == 200
        assert member_me_response.json()["roles"] == ["teamlead"]
        assert member_me_response.json()["team_ids"] == [team_id]

        teams_response = client.get("/teams", headers=member_headers)
        assert teams_response.status_code == 200
        assert teams_response.json()["total"] == 1
        assert teams_response.json()["items"][0]["id"] == team_id

        filtered_users_response = client.get("/users?role=teamlead", headers=admin_headers)
        assert filtered_users_response.status_code == 200
        assert filtered_users_response.json()["total"] == 1
        assert filtered_users_response.json()["items"][0]["id"] == user_id

        refresh_response = client.post("/auth/refresh", json={"refresh_token": admin_refresh_token})
        assert refresh_response.status_code == 200
        refreshed_payload = refresh_response.json()
        assert refreshed_payload["access_token"]
        assert refreshed_payload["refresh_token"] != admin_refresh_token
        assert refreshed_payload["user"]["roles"] == ["user", "admin"]

        logout_response = client.post("/auth/logout", json={"refresh_token": refreshed_payload["refresh_token"]})
        assert logout_response.status_code == 204

        revoked_refresh_response = client.post(
            "/auth/refresh",
            json={"refresh_token": refreshed_payload["refresh_token"]},
        )
        assert revoked_refresh_response.status_code == 401