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


def test_task_crud_is_scoped_by_user():
    owner_token = make_token("user-1", "owner@example.com")
    another_user_token = make_token("user-2", "other@example.com")

    with TestClient(app) as client:
        create_response = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"title": "Build MVP", "description": "Finish the test task", "status": "todo"},
        )
        assert create_response.status_code == 201
        task_id = create_response.json()["id"]

        list_response = client.get("/tasks", headers={"Authorization": f"Bearer {owner_token}"})
        assert list_response.status_code == 200
        assert len(list_response.json()) == 1

        forbidden_read = client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {another_user_token}"})
        assert forbidden_read.status_code == 404

        update_response = client.patch(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"status": "done"},
        )
        assert update_response.status_code == 200
        assert update_response.json()["status"] == "done"

        delete_response = client.delete(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {owner_token}"})
        assert delete_response.status_code == 204
