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


def make_token(user_id: str, email: str) -> str:
    return jwt.encode(
        {"sub": user_id, "email": email, "iss": "auth-service", "type": "access"},
        "test-secret",
        algorithm="HS256",
    )


def test_task_lifecycle_and_history_are_scoped_by_owner():
    owner_token = make_token("user-1", "owner@example.com")
    another_user_token = make_token("user-2", "other@example.com")

    with TestClient(app) as client:
        create_response = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "title": "Build next level",
                "description": "Extend task-service lifecycle",
                "assignee_id": "user-1",
                "team_id": "team-1",
                "priority": "high",
                "deadline": "2026-04-30T18:00:00Z",
            },
        )
        assert create_response.status_code == 201
        created_task = create_response.json()
        task_id = created_task["id"]
        assert created_task["status"] == "todo"
        assert created_task["priority"] == "high"
        assert created_task["team_id"] == "team-1"
        assert created_task["assignee_id"] == "user-1"

        list_response = client.get("/tasks", headers={"Authorization": f"Bearer {owner_token}"})
        assert list_response.status_code == 200
        assert len(list_response.json()) == 1

        forbidden_read = client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {another_user_token}"})
        assert forbidden_read.status_code == 404

        update_response = client.patch(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "title": "Build lifecycle branch",
                "priority": "medium",
                "deadline": "2026-05-01T18:00:00Z",
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["title"] == "Build lifecycle branch"
        assert update_response.json()["priority"] == "medium"

        invalid_transition = client.patch(
            f"/tasks/{task_id}/status",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"status": "done", "comment": "Skip all workflow"},
        )
        assert invalid_transition.status_code == 409

        in_progress_response = client.patch(
            f"/tasks/{task_id}/status",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"status": "in_progress", "comment": "Work started"},
        )
        assert in_progress_response.status_code == 200
        assert in_progress_response.json()["status"] == "in_progress"

        review_response = client.patch(
            f"/tasks/{task_id}/status",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"status": "review", "comment": "Ready for review"},
        )
        assert review_response.status_code == 200
        assert review_response.json()["status"] == "review"

        done_response = client.patch(
            f"/tasks/{task_id}/status",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"status": "done", "comment": "Done"},
        )
        assert done_response.status_code == 200
        assert done_response.json()["status"] == "done"

        history_response = client.get(
            f"/tasks/{task_id}/history",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert history_response.status_code == 200
        history_items = history_response.json()["items"]
        assert len(history_items) == 3
        assert history_items[0]["from_status"] == "todo"
        assert history_items[0]["to_status"] == "in_progress"
        assert history_items[0]["comment"] == "Work started"
        assert history_items[-1]["to_status"] == "done"

        forbidden_history = client.get(
            f"/tasks/{task_id}/history",
            headers={"Authorization": f"Bearer {another_user_token}"},
        )
        assert forbidden_history.status_code == 404

        delete_response = client.delete(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {owner_token}"})
        assert delete_response.status_code == 204

        deleted_task_response = client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {owner_token}"})
        assert deleted_task_response.status_code == 404