"""HANDOFF convention #9: four inputs enforced only by the database (a CHECK or an FK), not by
Python, arrive as django.db.IntegrityError -- the API's global handler (records/api/__init__.py)
maps every one of them to 422 instead of letting them fall through as an unhandled 500.

The two tests below are P3 fix-batch F1/F2: the 422 body must not carry Postgres's own error text
(table/constraint names, and -- for a NOT NULL violation on `requests` -- the tracking number and
inbound alias in the SAME response, which this system deliberately never puts together, see
`records/aliases.py`), and a genuine IntegrityError must still leave a full traceback in the log
even though the client only sees a generic 422."""

import logging
import uuid

from tests.api.conftest import post_json


def test_unknown_event_type_is_422_not_500(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/events",
        {
            "event_type": "not_a_real_event_type",
            "channel": "email",
            "direction": "inbound",
            "occurred_at": "2026-03-05T12:00:00Z",
            "display_body": "x",
        },
    )
    assert resp.status_code == 422, resp.content


def test_unknown_channel_is_422_not_500(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/events",
        {
            "event_type": "correspondence",
            "channel": "carrier_pigeon",
            "direction": "inbound",
            "occurred_at": "2026-03-05T12:00:00Z",
            "display_body": "x",
        },
    )
    assert resp.status_code == 422, resp.content


def test_unknown_direction_is_422_not_500(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/events",
        {
            "event_type": "correspondence",
            "channel": "email",
            "direction": "sideways",
            "occurred_at": "2026-03-05T12:00:00Z",
            "display_body": "x",
        },
    )
    assert resp.status_code == 422, resp.content


def test_unknown_fee_status_is_422_not_500(staff_client, reserved):
    resp = post_json(
        staff_client, f"/api/records/requests/{reserved.pk}/fees", {"status": "not_a_real_status"}
    )
    assert resp.status_code == 422, resp.content


def test_task_for_a_nonexistent_request_is_422_not_500(staff_client):
    resp = post_json(
        staff_client,
        "/api/records/tasks",
        {
            "request_id": str(uuid.uuid4()),
            "task_type": "mail_letter",
            "description": "x",
        },
    )
    assert resp.status_code == 422, resp.content


def _null_out_subject(staff_client, reserved):
    """`subject` is NOT NULL on `requests`; editing it to null on an already-populated row is a
    real IntegrityError reached through the public API (not a monkeypatch) whose Postgres DETAIL
    line names every column of the failing row, including `tracking_number` and
    `inbound_alias_email` -- the exact shape F1 flagged."""
    return post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/edits",
        {
            "entity_type": "requests",
            "entity_id": str(reserved.pk),
            "field": "subject",
            "new_value": None,
            "note": "F1/F2 probe: force a NOT NULL violation on requests.subject",
        },
    )


def test_integrity_error_body_does_not_leak_tracking_number_or_alias(staff_client, reserved):
    resp = _null_out_subject(staff_client, reserved)
    assert resp.status_code == 422, resp.content
    body = resp.json()
    detail = body["detail"]
    assert reserved.tracking_number not in detail
    assert reserved.inbound_alias_email not in detail
    # Not just those two fields -- no Postgres-authored text (table/constraint vocabulary) at all.
    assert "DETAIL" not in detail
    assert "requests" not in detail
    assert detail == (
        "This request could not be completed because it violates a database constraint."
    )


def test_integrity_error_is_logged_with_traceback(staff_client, reserved, caplog):
    with caplog.at_level(logging.INFO, logger="portal.records.api"):
        resp = _null_out_subject(staff_client, reserved)
    assert resp.status_code == 422, resp.content
    integrity_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(integrity_records) == 1, [r.getMessage() for r in caplog.records]
    record = integrity_records[0]
    assert record.exc_info is not None, "no traceback attached to the IntegrityError log record"
    # A genuine fault (this) logs louder than an expected refusal (the 422s in test_error_mapping's
    # other cases): those come through at INFO with no exc_info, this one is ERROR with one.
    assert not any(r.levelno >= logging.ERROR for r in caplog.records if r is not record)
