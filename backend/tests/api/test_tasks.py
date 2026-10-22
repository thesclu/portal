"""One happy path per tasks endpoint."""

from tests.api.conftest import post_json


def test_create_and_list_tasks(staff_client, reserved):
    created = post_json(
        staff_client,
        "/api/records/tasks",
        {
            "request_id": str(reserved.pk),
            "task_type": "mail_letter",
            "description": "Print and mail the request letter.",
        },
    )
    assert created.status_code == 201, created.content
    resp = staff_client.get("/api/records/tasks", {"open": True, "request_id": str(reserved.pk)})
    assert resp.status_code == 200
    assert any(t["id"] == created.json()["id"] for t in resp.json())


def test_complete_task(staff_client, reserved):
    task_id = post_json(
        staff_client,
        "/api/records/tasks",
        {
            "request_id": str(reserved.pk),
            "task_type": "confirm_filed",
            "description": "Call the records office to confirm filing.",
        },
    ).json()["id"]
    resp = post_json(staff_client, f"/api/records/tasks/{task_id}/complete", {})
    assert resp.status_code == 200, resp.content
    assert resp.json()["completed_at"] is not None
