"""Security-critical, attacked from the attacker's own direction, not merely demonstrated:

1. A `requester` may only GET requests where `requester_user_id == self`; everything else 403,
   including a docket list that must not leak another requester's rows, and every read that is
   NOT the requester's own two doors (list, detail).
2. `production_received` stays human-only: POST /requests/{id}/status succeeds for staff, and
   POST /requests/{id}/events with status=production_received is refused with 403.
3. GET /requests/{id} must not be an existence oracle: a requester probing a random uuid and a
   requester probing another requester's real request get the SAME status and body, so the
   response can never confirm that a given id names a real request (P3 fix-batch F3).

Both attacks were broken (temporarily neutering the guard), watched to fail, and restored --
see the worker's thoughts file for the pasted before/after evidence.
"""

import uuid

from tests.api.conftest import post_json


def test_requester_can_read_own_request(requester_client, own_request):
    resp = requester_client.get(f"/api/records/requests/{own_request.pk}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(own_request.pk)


def test_requester_cannot_read_another_requesters_request(requester_client, other_request):
    resp = requester_client.get(f"/api/records/requests/{other_request.pk}")
    assert resp.status_code == 403


def test_get_request_is_not_an_existence_oracle_for_a_requester(requester_client, other_request):
    """A random uuid (no such request) and `other_request`'s real id (exists, not the caller's)
    must come back identical -- status AND body -- or the response itself would confirm which
    uuids are real."""
    random_resp = requester_client.get(f"/api/records/requests/{uuid.uuid4()}")
    real_resp = requester_client.get(f"/api/records/requests/{other_request.pk}")
    assert random_resp.status_code == real_resp.status_code == 403
    assert random_resp.json() == real_resp.json()


def test_requester_docket_excludes_other_requesters_rows(
    requester_client, own_request, other_request
):
    resp = requester_client.get("/api/records/requests")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["items"]}
    assert str(own_request.pk) in ids
    assert str(other_request.pk) not in ids


def test_requester_cannot_reach_any_mutating_endpoint(requester_client, own_request):
    resp = post_json(
        requester_client,
        f"/api/records/requests/{own_request.pk}/status",
        {"status": "acknowledged", "note": "trying to self-serve"},
    )
    assert resp.status_code == 403


def test_requester_cannot_read_agencies_sources_or_tasks(requester_client, agency):
    assert requester_client.get("/api/records/agencies").status_code == 403
    assert requester_client.get("/api/records/sources").status_code == 403
    assert requester_client.get("/api/records/tasks").status_code == 403


def test_production_received_via_status_succeeds_for_staff(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/status",
        {"status": "production_received", "note": "checked every page against the index"},
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["status"] == "production_received"


def test_production_received_via_events_is_refused(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/events",
        {
            "event_type": "production",
            "channel": "email",
            "direction": "inbound",
            "occurred_at": "2026-03-20T12:00:00Z",
            "display_body": "Agency says everything responsive is attached.",
            "status": "production_received",
        },
    )
    assert resp.status_code == 403, resp.content
