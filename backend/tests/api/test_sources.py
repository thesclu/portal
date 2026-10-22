"""One happy path per sources endpoint."""

import pytest
from portal.records.actors import Actor
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service

from tests.api.conftest import post_json


@pytest.fixture
def source(db, staff_user):
    return sources_service.register_source(
        Actor.human(staff_user),
        source_type="imported_eml",
        raw_storage_url="s3://test-bucket/msg-1.eml",
        external_ref="test-msg-1",
    )


def test_list_sources(staff_client, source):
    resp = staff_client.get("/api/records/sources", {"unreviewed": True})
    assert resp.status_code == 200
    body = resp.json()
    assert any(s["id"] == str(source.pk) for s in body["items"])


def test_get_source(staff_client, source):
    resp = staff_client.get(f"/api/records/sources/{source.pk}")
    assert resp.status_code == 200
    assert resp.json()["external_ref"] == "test-msg-1"


def test_assign_source(staff_client, staff_user, agency, source, reserved):
    """`also_applies` needs an anchor event to copy `occurred_at` from -- record one on
    `reserved`, then say the same source also answers a second request."""
    anchor = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/events",
        {
            "event_type": "correspondence",
            "channel": "email",
            "direction": "inbound",
            "occurred_at": "2026-03-06T12:00:00Z",
            "display_body": "One letter, two requests.",
            "source_id": str(source.pk),
        },
    ).json()
    second = requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="A second, unrelated request the same letter also answers",
        request_text="Also asking about the same incident from a different angle.",
    )
    resp = post_json(
        staff_client,
        f"/api/records/sources/{source.pk}/assign",
        {"request_id": str(second.pk), "mode": "also_applies", "from_event_id": anchor["id"]},
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["source_id"] == str(source.pk)
    assert resp.json()["request_id"] == str(second.pk)


def test_mark_source_reviewed(staff_client, source):
    resp = post_json(staff_client, f"/api/records/sources/{source.pk}/reviewed", {})
    assert resp.status_code == 200, resp.content
    assert resp.json()["reviewed_at"] is not None
