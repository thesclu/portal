"""One happy path per deadlines endpoint."""

from tests.api.conftest import post_json


def _open(staff_client, request_id, due_at="2026-04-01"):
    return post_json(
        staff_client,
        f"/api/records/requests/{request_id}/deadlines",
        {"deadline_type": "production", "due_at": due_at, "basis": "agency_commitment"},
    )


def test_open_deadline(staff_client, reserved):
    resp = _open(staff_client, reserved.pk)
    assert resp.status_code == 201, resp.content
    assert resp.json()["status"] == "active"


def test_satisfy_deadline(staff_client, reserved):
    deadline_id = _open(staff_client, reserved.pk).json()["id"]
    event = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/events",
        {
            "event_type": "production",
            "channel": "email",
            "direction": "inbound",
            "occurred_at": "2026-03-20T12:00:00Z",
            "display_body": "Records produced.",
        },
    ).json()
    resp = post_json(
        staff_client,
        f"/api/records/deadlines/{deadline_id}/satisfy",
        {"satisfied_by_event_id": event["id"]},
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["status"] == "satisfied"


def test_waive_deadline(staff_client, reserved):
    deadline_id = _open(staff_client, reserved.pk).json()["id"]
    resp = post_json(
        staff_client,
        f"/api/records/deadlines/{deadline_id}/waive",
        {"note": "agency granted extension"},
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["status"] == "waived"


def test_supersede_deadline(director_client, reserved):
    """Superseding moves an existing deadline's date, so it takes a director (P8a)."""
    staff_client = director_client
    old_id = _open(staff_client, reserved.pk, due_at="2026-04-01").json()["id"]
    resp = post_json(
        staff_client,
        f"/api/records/deadlines/{old_id}/supersede",
        {"deadline_type": "production", "due_at": "2026-04-15", "basis": "agency_commitment"},
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["due_at"] == "2026-04-15"
    old = staff_client.get(f"/api/records/requests/{reserved.pk}")
    old_row = next(d for d in old.json()["deadlines"] if d["id"] == old_id)
    assert old_row["status"] == "superseded"
    assert old_row["superseded_by_deadline_id"] == resp.json()["id"]
