import os
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

os.environ["TASK_DATABASE_URL"] = f"sqlite+pysqlite:///{Path.cwd() / 'test_task_service.db'}"
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
        assert owner_list_response.json()["total"] == 1
        assert len(owner_list_response.json()["items"]) == 1

        assignee_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {assignee_token}"})
        assert assignee_list_response.status_code == 200
        assert assignee_list_response.json()["total"] == 1
        assert len(assignee_list_response.json()["items"]) == 1

        teamlead_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {teamlead_token}"})
        assert teamlead_list_response.status_code == 200
        assert teamlead_list_response.json()["total"] == 1
        assert len(teamlead_list_response.json()["items"]) == 1

        admin_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_list_response.status_code == 200
        assert admin_list_response.json()["total"] == 1
        assert len(admin_list_response.json()["items"]) == 1

        outsider_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {outsider_token}"})
        assert outsider_list_response.status_code == 200
        assert outsider_list_response.json()["total"] == 0
        assert outsider_list_response.json()["items"] == []

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


def test_task_list_supports_filters_pagination_and_sorting():
    owner_token = make_token("user-1", "owner@example.com", roles=["user"], team_ids=["team-1"])
    teamlead_token = make_token("user-3", "lead@example.com", roles=["teamlead"], team_ids=["team-1"])
    outsider_token = make_token("user-5", "other@example.com", roles=["user"], team_ids=["team-2"])
    admin_token = make_token("user-4", "admin@example.com", roles=["admin"], team_ids=[])

    with TestClient(app) as client:
        high_priority = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "title": "High priority",
                "description": "Visible to teamlead",
                "assignee_id": "user-2",
                "team_id": "team-1",
                "priority": "high",
                "deadline": "2026-04-30T18:00:00Z",
            },
        )
        assert high_priority.status_code == 201
        high_task_id = high_priority.json()["id"]

        low_priority = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "title": "Low priority",
                "description": "No deadline",
                "assignee_id": "user-1",
                "team_id": "team-1",
                "priority": "low"
            },
        )
        assert low_priority.status_code == 201
        low_task_id = low_priority.json()["id"]

        external_task = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {outsider_token}"},
            json={
                "title": "External team",
                "description": "Must stay invisible for teamlead",
                "assignee_id": "user-5",
                "team_id": "team-2",
                "priority": "medium",
                "deadline": "2026-05-05T18:00:00Z",
            },
        )
        assert external_task.status_code == 201

        move_high_task = client.patch(
            f"/tasks/{high_task_id}/status",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"status": "in_progress", "comment": "Started work"},
        )
        assert move_high_task.status_code == 200

        filtered_by_status = client.get(
            "/tasks?status=in_progress",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert filtered_by_status.status_code == 200
        assert filtered_by_status.json()["total"] == 1
        assert filtered_by_status.json()["items"][0]["id"] == high_task_id

        filtered_by_owner = client.get(
            "/tasks?owner_id=user-1",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert filtered_by_owner.status_code == 200
        assert filtered_by_owner.json()["total"] == 2

        paged_sorted = client.get(
            "/tasks?page=1&page_size=1&sort_by=priority&sort_order=desc",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert paged_sorted.status_code == 200
        assert paged_sorted.json()["total"] == 2
        assert paged_sorted.json()["page"] == 1
        assert paged_sorted.json()["page_size"] == 1
        assert paged_sorted.json()["pages"] == 2
        assert len(paged_sorted.json()["items"]) == 1
        assert paged_sorted.json()["items"][0]["id"] == high_task_id

        second_page = client.get(
            "/tasks?page=2&page_size=1&sort_by=priority&sort_order=desc",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["items"]) == 1
        assert second_page.json()["items"][0]["id"] == low_task_id

        deadline_sorted = client.get(
            "/tasks?sort_by=deadline&sort_order=asc",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert deadline_sorted.status_code == 200
        assert deadline_sorted.json()["total"] == 2
        assert deadline_sorted.json()["items"][0]["id"] == high_task_id
        assert deadline_sorted.json()["items"][1]["id"] == low_task_id
        assert deadline_sorted.json()["items"][1]["deadline"] is None

        admin_all_tasks = client.get(
            "/tasks?team_id=team-2",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_all_tasks.status_code == 200
        assert admin_all_tasks.json()["total"] == 1
        assert admin_all_tasks.json()["items"][0]["team_id"] == "team-2"