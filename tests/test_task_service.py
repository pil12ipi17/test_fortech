import os
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

TEST_DB = Path("test_task_service.db")
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["TASK_DATABASE_URL"] = f"sqlite:///{TEST_DB.resolve().as_posix()}"
os.environ["TASK_JWT_SECRET"] = "test-secret"

from services.task_service.app.main import app  # noqa: E402


def make_token(
    user_id: str,
    email: str,
    *,
    roles: list[str] | None = None,
    team_ids: list[str] | None = None,
) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "iss": "auth-service",
        "type": "access",
        "roles": roles or [],
        "team_ids": team_ids or [],
    }
    return jwt.encode(payload, "test-secret", algorithm="HS256")


def test_task_rbac_across_owner_assignee_teamlead_and_admin():
    owner_token = make_token("user-1", "owner@example.com", roles=["user"], team_ids=["team-1"])
    assignee_token = make_token("user-2", "assignee@example.com", roles=["user"], team_ids=["team-1"])
    teamlead_token = make_token("user-3", "lead@example.com", roles=["teamlead"], team_ids=["team-1"])
    admin_token = make_token("user-4", "admin@example.com", roles=["admin"], team_ids=[])
    outsider_token = make_token("user-5", "other@example.com", roles=["user"], team_ids=["team-2"])

    with TestClient(app) as client:
        create_response = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "title": "Build RBAC branch",
                "description": "Extend task-service access control",
                "assignee_id": "user-2",
                "team_id": "team-1",
                "priority": "high",
                "deadline": "2026-04-30T18:00:00Z",
            },
        )
        assert create_response.status_code == 201
        task_id = create_response.json()["id"]

        owner_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {owner_token}"})
        assert owner_list_response.status_code == 200
        assert len(owner_list_response.json()) == 1

        assignee_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {assignee_token}"})
        assert assignee_list_response.status_code == 200
        assert len(assignee_list_response.json()) == 1

        teamlead_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {teamlead_token}"})
        assert teamlead_list_response.status_code == 200
        assert len(teamlead_list_response.json()) == 1

        admin_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_list_response.status_code == 200
        assert len(admin_list_response.json()) == 1

        outsider_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {outsider_token}"})
        assert outsider_list_response.status_code == 200
        assert outsider_list_response.json() == []

        assignee_read = client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {assignee_token}"})
        assert assignee_read.status_code == 200

        teamlead_update = client.patch(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {teamlead_token}"},
            json={"priority": "medium"},
        )
        assert teamlead_update.status_code == 200
        assert teamlead_update.json()["priority"] == "medium"

        assignee_status = client.patch(
            f"/tasks/{task_id}/status",
            headers={"Authorization": f"Bearer {assignee_token}"},
            json={"status": "in_progress", "comment": "Assignee started work"},
        )
        assert assignee_status.status_code == 200
        assert assignee_status.json()["status"] == "in_progress"

        teamlead_history = client.get(
            f"/tasks/{task_id}/history",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert teamlead_history.status_code == 200
        assert len(teamlead_history.json()["items"]) == 1

        assignee_delete = client.delete(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {assignee_token}"})
        assert assignee_delete.status_code == 403

        outsider_read = client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {outsider_token}"})
        assert outsider_read.status_code == 404

        admin_delete = client.delete(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_delete.status_code == 204

        deleted_task_response = client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {owner_token}"})
        assert deleted_task_response.status_code == 404