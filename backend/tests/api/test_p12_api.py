"""P12 over HTTP: the review sheet, recording a review, the Records listing and set download,
and request links. Scope is the census's business; this is shapes and outcomes."""

import io
import zipfile

import pytest
from portal.records.actors import Actor
from portal.records.services import classify
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service

from tests.api.conftest import post_json
from tests.p8b_helpers import attach, make_source

BASE = "/api/records"


@pytest.fixture
def actor(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def with_items(db, actor, staff_user, agency):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Reviewable",
        request_text="Body.",
        items=[
            requests_service.NewItem("1", "Contracts", sort_order=1),
            requests_service.NewItem("2", "Policies", sort_order=2),
        ],
    )


@pytest.fixture
def production(actor, with_items, media):
    """A production whose files are really in storage, so a set can be zipped."""
    added = sources_service.add_document(
        actor,
        with_items.pk,
        source_type="scanned_mail",
        received_at=requests_service.timezone.now(),
        files=[
            sources_service.IncomingFile("contract.pdf", [b"contract bytes"]),
            sources_service.IncomingFile("policy.pdf", [b"policy bytes"]),
        ],
        display_body="Two files.",
    )
    classify.classify(actor, added.event.pk, "production_partial", note="Partial.")
    return added


def _ids(request):
    return {i.item_number: str(i.pk) for i in request.items.all()}


def test_the_sheet_the_review_and_the_detail_agree(staff_client, with_items, production):
    items = _ids(with_items)
    sheet = staff_client.get(f"{BASE}/events/{production.event.pk}/review-sheet")
    assert sheet.status_code == 200, sheet.content
    body = sheet.json()
    assert [f["filename"] for f in body["files"]] == ["contract.pdf", "policy.pdf"]
    assert [i["disposition"] for i in body["items"]] == [None, None]

    contract = body["files"][0]["id"]
    made = post_json(
        staff_client,
        f"{BASE}/requests/{with_items.pk}/reviews",
        {
            "event_id": str(production.event.pk),
            "rows": [
                {"request_item_id": items["1"], "disposition": "produced"},
                {
                    "request_item_id": items["2"],
                    "disposition": "withheld",
                    "exemption_cited": "cited",
                    "cited_where": "cover letter",
                },
            ],
            "files": [{"attachment_id": contract, "request_item_id": items["1"]}],
            "note": "First look.",
        },
    )
    assert made.status_code == 201, made.content
    review = made.json()
    assert review["outcome"] == "partial_production"
    assert [r["disposition"] for r in review["rows"]] == ["produced", "withheld"]

    detail = staff_client.get(f"{BASE}/requests/{with_items.pk}").json()
    assert [i["disposition"] for i in detail["items"]] == ["produced", "withheld"]
    assert detail["current_status"] == "partial_production"
    assert [f["attachment_id"] for f in detail["file_links"]] == [contract]
    listed = staff_client.get(f"{BASE}/requests/{with_items.pk}/reviews").json()
    assert [r["id"] for r in listed] == [review["id"]]
    again = staff_client.get(f"{BASE}/events/{production.event.pk}/review-sheet").json()
    assert again["linked_files"] == {contract: items["1"]}
    assert again["current"][items["2"]]["exemption_cited"] == "cited"


def test_the_records_listing_and_a_set(staff_client, with_items, production):
    items = _ids(with_items)
    sheet = staff_client.get(f"{BASE}/events/{production.event.pk}/review-sheet").json()
    contract, policy = (f["id"] for f in sheet["files"])
    post_json(
        staff_client,
        f"{BASE}/requests/{with_items.pk}/reviews",
        {
            "event_id": str(production.event.pk),
            "rows": [
                {"request_item_id": items["1"], "disposition": "produced"},
                {"request_item_id": items["2"], "disposition": "produced"},
            ],
            "files": [
                {"attachment_id": contract, "request_item_id": items["1"]},
                {"attachment_id": policy},
            ],
        },
    )
    listing = staff_client.get(f"{BASE}/produced").json()
    assert listing["total"] == 2
    by_name = {row["attachment"]["filename"]: row for row in listing["items"]}
    assert by_name["contract.pdf"]["item_number"] == "1"
    assert by_name["policy.pdf"]["item_number"] is None  # the whole request
    assert by_name["contract.pdf"]["tracking_number"] == with_items.tracking_number
    assert staff_client.get(f"{BASE}/produced?q=policy").json()["total"] == 1
    assert (
        staff_client.get(f"{BASE}/produced?agency_id={with_items.agency_id}").json()["total"] == 2
    )

    zipped = staff_client.get(f"{BASE}/produced/set?ids={contract},{policy}")
    assert zipped.status_code == 200, zipped.content
    assert zipped["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(b"".join(zipped.streaming_content)))
    assert sorted(archive.namelist()) == ["contract.pdf", "policy.pdf"]
    assert archive.read("policy.pdf") == b"policy bytes"
    assert staff_client.get(f"{BASE}/produced/set?ids=not-an-id").status_code == 422
    assert (
        staff_client.get(
            f"{BASE}/produced/set?ids=00000000-0000-0000-0000-000000000000"
        ).status_code
        == 404
    )


def test_a_file_marked_as_a_record_before_any_review_is_still_listed(
    staff_client, actor, with_items
):
    source = make_source(actor)
    from tests.p8b_helpers import add_file

    record = add_file(actor, source, "old.pdf", role="produced_record")
    attach(actor, with_items, source, event_type="production")
    listing = staff_client.get(f"{BASE}/produced").json()
    assert [row["attachment"]["id"] for row in listing["items"]] == [str(record.pk)]
    assert listing["items"][0]["item_number"] is None


def test_links_over_http(staff_client, with_items, reserved):
    made = post_json(
        staff_client,
        f"{BASE}/requests/{with_items.pk}/links",
        {"to_request_id": str(reserved.pk), "kind": "duplicate", "reason": "Filed twice."},
    )
    assert made.status_code == 201, made.content
    link = made.json()
    assert link["to_tracking_number"] == reserved.tracking_number
    assert link["from_tracking_number"] == with_items.tracking_number
    # Seen from both ends, and on the detail.
    assert [
        row["id"] for row in staff_client.get(f"{BASE}/requests/{reserved.pk}/links").json()
    ] == [link["id"]]
    assert [
        row["id"] for row in staff_client.get(f"{BASE}/requests/{with_items.pk}").json()["links"]
    ] == [link["id"]]
    dup = post_json(
        staff_client,
        f"{BASE}/requests/{with_items.pk}/links",
        {"to_request_id": str(reserved.pk), "kind": "duplicate", "reason": "again"},
    )
    assert dup.status_code == 422, dup.content
    voided = post_json(staff_client, f"{BASE}/links/{link['id']}/void", {"reason": "Not so."})
    assert voided.status_code == 200 and voided.json()["voided_at"] is not None
    assert staff_client.get(f"{BASE}/requests/{reserved.pk}/links").json() == []
    assert (
        post_json(staff_client, f"{BASE}/links/{link['id']}/void", {"reason": ""}).status_code
        == 422
    )
