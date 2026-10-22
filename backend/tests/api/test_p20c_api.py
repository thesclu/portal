"""P20c: what the request page reads that it did not before.

An item's hover (063, 064) says when the production it was read from arrived, the exemption the
agency cited and the reviewer's note; the "Turn off automatic follow-ups" item (088) needs the
request's own switch, which the audited edit door already changes.
"""

from datetime import datetime, timedelta

from tests.api.conftest import post_json
from tests.api.test_p12_api import _ids, actor, production, with_items  # noqa: F401

BASE = "/api/records"


def test_an_item_carries_what_its_review_said(staff_client, with_items, production):  # noqa: F811
    items = _ids(with_items)
    before = staff_client.get(f"{BASE}/requests/{with_items.pk}").json()
    assert [
        (i["disposition"], i["disposition_at"], i["exemption_cited"], i["disposition_note"])
        for i in before["items"]
    ] == [(None, None, None, None), (None, None, None, None)]

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
                    "note": "Still with counsel.",
                },
            ],
            "files": [],
        },
    )
    assert made.status_code == 201, made.content

    detail = staff_client.get(f"{BASE}/requests/{with_items.pk}").json()
    first, second = detail["items"]
    arrived = production.event.occurred_at
    assert first["disposition"] == "produced"
    # The API writes instants to the millisecond.
    read = datetime.fromisoformat(first["disposition_at"].replace("Z", "+00:00"))
    assert abs(read - arrived) < timedelta(milliseconds=1)
    assert (first["exemption_cited"], first["disposition_note"]) == (None, None)
    assert second["disposition"] == "withheld"
    assert (second["exemption_cited"], second["disposition_note"]) == (
        "cited",
        "Still with counsel.",
    )


def test_the_request_says_whether_its_follow_ups_run(staff_client, with_items):  # noqa: F811
    path = f"{BASE}/requests/{with_items.pk}"
    assert staff_client.get(path).json()["auto_followups_enabled"] is True
    edited = post_json(
        staff_client,
        f"{path}/edits",
        {
            "entity_type": "requests",
            "entity_id": str(with_items.pk),
            "field": "auto_followups_enabled",
            "new_value": False,
            "note": "The agency asked us to wait.",
        },
    )
    assert edited.status_code == 200, edited.content
    assert staff_client.get(path).json()["auto_followups_enabled"] is False
    listed = staff_client.get(f"{BASE}/requests").json()["items"]
    assert all("auto_followups_enabled" not in row for row in listed)


def test_the_draft_a_reserved_request_was_made_from(staff_client, staff_user, agency, with_items):  # noqa: F811
    """076 "Review & file": the newest letter that reserved the request, and its draft; 404 for a
    request that was not made from a draft."""
    from portal.drafting.services import drafts as drafts_service

    missing = staff_client.get(f"/api/drafting/requests/{with_items.pk}/draft")
    assert missing.status_code == 404, missing.content

    draft = drafts_service.create_draft(subject="Letter", created_by_user_id=staff_user.pk)
    recipient = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")
    # What reserving does to the letter, without a whole letter to reserve.
    recipient.request_id = with_items.pk
    recipient.save(update_fields=["request"])

    found = staff_client.get(f"/api/drafting/requests/{with_items.pk}/draft")
    assert found.status_code == 200, found.content
    assert found.json() == {"draft_id": str(draft.pk), "recipient_id": str(recipient.pk)}
