"""One happy path per requests endpoint (list, reserve, detail, file, status, edits)."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from portal.records.actors import Actor
from portal.records.services import directory
from portal.records.services import requests as requests_service

from tests.api.conftest import post_json


def test_list_requests_docket(staff_client, reserved):
    resp = staff_client.get("/api/records/requests")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(r["id"] == str(reserved.pk) for r in body["items"])


def test_list_requests_pagination_clamps_limit(staff_client, reserved):
    resp = staff_client.get("/api/records/requests", {"limit": 999, "offset": 0})
    assert resp.status_code == 200
    assert resp.json()["limit"] == 200


def test_list_requests_limit_zero_returns_an_empty_page(staff_client, reserved):
    """F6: limit=0 is a valid page size meaning "no rows", not the old `max(limit, 1)` bug that
    silently turned it into 1 row."""
    resp = staff_client.get("/api/records/requests", {"limit": 0})
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["items"] == []
    assert body["limit"] == 0
    assert body["total"] >= 1  # total is still post-scoping, unaffected by the page size


def test_list_requests_rejects_negative_limit_and_offset(staff_client, reserved):
    """F6: the schema declares `minimum: 0` for both -- a negative value is now a 422, not a
    silent clamp."""
    assert staff_client.get("/api/records/requests", {"limit": -1}).status_code == 422
    assert staff_client.get("/api/records/requests", {"offset": -1}).status_code == 422


def test_docket_does_not_n_plus_one_on_agency(staff_client, staff_user):
    """F4: `docket()` must `select_related("agency")` -- without it, `r.agency.name` in
    `list_requests` (api/requests.py) is a fresh query per row. Proven by holding the query count
    for the request across 2 rows against 6 rows: a query count is the only thing that catches an
    N+1 coming back, per the finding, so this compares captured query counts rather than asserting
    "no N+1" by inspection.
    """
    agency_a = directory.create_agency(
        Actor.human(staff_user), name="N+1 probe agency A", jurisdiction="Testland"
    )
    agency_b = directory.create_agency(
        Actor.human(staff_user), name="N+1 probe agency B", jurisdiction="Testland"
    )

    def _reserve(agency):
        requests_service.reserve_request(
            Actor.human(staff_user),
            agency_id=agency.pk,
            requester_user_id=staff_user.pk,
            subject="N+1 probe",
            request_text="N+1 probe",
        )

    for _ in range(2):
        _reserve(agency_a)
    with CaptureQueriesContext(connection) as small:
        resp = staff_client.get("/api/records/requests")
    assert resp.status_code == 200, resp.content

    for _ in range(4):
        _reserve(agency_b)
    with CaptureQueriesContext(connection) as large:
        resp = staff_client.get("/api/records/requests")
    assert resp.status_code == 200, resp.content

    assert len(large.captured_queries) == len(small.captured_queries), (
        f"query count grew with row count ({len(small.captured_queries)} -> "
        f"{len(large.captured_queries)} queries) -- the docket's agency lookup is N+1 again:\n"
        + "\n".join(q["sql"] for q in large.captured_queries)
    )


def test_reserve_request(staff_client, agency, staff_user):
    resp = post_json(
        staff_client,
        "/api/records/requests",
        {
            "agency_id": str(agency.pk),
            "requester_user_id": str(staff_user.pk),
            "subject": "Traffic stop records, Q1 2026",
            "request_text": "All traffic stop records for Q1 2026.",
            "items": [{"item_number": "1", "description": "Dashcam footage"}],
        },
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["current_status"] == "pending_filing"
    assert body["tracking_number"]


def test_get_request_detail_includes_items(staff_client, reserved):
    resp = staff_client.get(f"/api/records/requests/{reserved.pk}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tracking_number"] == reserved.tracking_number
    assert body["status_history"][0]["status"] == "pending_filing"


def test_file_request(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/file",
        {"filed_at": "2026-03-02T18:00:00Z", "channel": "email"},
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["current_status"] == "filed"
    assert any(e["event_type"] == "submission" for e in body["events"])


def test_set_status(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/status",
        {"status": "acknowledged", "note": "phoned the records office"},
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["status"] == "acknowledged"


def test_edit_request_field(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/edits",
        {
            "entity_type": "requests",
            "entity_id": str(reserved.pk),
            "field": "agency_reference_number",
            "new_value": "AG-2026-001",
            "note": "agency confirmed their own file number",
        },
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["new_value"] == "AG-2026-001"
