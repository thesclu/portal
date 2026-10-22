"""One happy path per events endpoint, plus GET /definitions."""

from tests.api.conftest import post_json


def test_record_event(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/events",
        {
            "event_type": "correspondence",
            "channel": "email",
            "direction": "inbound",
            "occurred_at": "2026-03-05T12:00:00Z",
            "display_body": "Agency confirmed receipt.",
        },
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["event_type"] == "correspondence"


def test_void_event(staff_client, reserved):
    created = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/events",
        {
            "event_type": "note",
            "channel": "system",
            "direction": "internal",
            "occurred_at": "2026-03-05T12:00:00Z",
            "display_body": "Wrong request -- logged in error.",
        },
    )
    event_id = created.json()["id"]
    resp = post_json(
        staff_client, f"/api/records/events/{event_id}/void", {"reason": "logged in error"}
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["voided_at"] is not None
    assert resp.json()["void_reason"] == "logged in error"


def test_definitions(staff_client):
    resp = staff_client.get("/api/records/definitions")
    assert resp.status_code == 200
    body = resp.json()
    codes = {s["code"] for s in body["statuses"]}
    assert "production_received" in codes
    assert any(e["code"] == "submission" for e in body["event_types"])


def test_definitions_are_ordered_by_sort_order(staff_client):
    """F7: `status_definitions.sort_order` and `event_definitions.sort_order` exist for display
    order, and every sibling list endpoint in this API orders explicitly -- this one now does too.
    Each row's `sort_order` must be non-decreasing across the response."""
    resp = staff_client.get("/api/records/definitions")
    assert resp.status_code == 200
    body = resp.json()
    status_orders = [s["sort_order"] for s in body["statuses"]]
    event_orders = [e["sort_order"] for e in body["event_types"]]
    assert len(status_orders) > 1, "need >1 row for an ordering assertion to mean anything"
    assert len(event_orders) > 1, "need >1 row for an ordering assertion to mean anything"
    assert status_orders == sorted(status_orders)
    assert event_orders == sorted(event_orders)
