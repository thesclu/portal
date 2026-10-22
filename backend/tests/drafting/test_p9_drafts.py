"""A privileged draft is its creator's alone, and what it files stays theirs (BUILD_PLAN P9).

The census (tests/api/test_p9_census.py) holds every drafting route to the rule; this file pins
what the rule is at its edges.
"""

import pytest
from django.test import Client
from portal.drafting.models import LetterDraft, LetterRecipient
from portal.drafting.services import drafts as drafts_service
from portal.records.models import Request
from portal.records.services import access as access_service
from portal.records.services import requests as requests_service

from tests.drafting.test_api import DRAFTING_PREFIX, patch_json, post_json


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def creator(staff_user) -> Client:
    return _client(staff_user)


@pytest.fixture
def colleague(colleague_user) -> Client:
    return _client(colleague_user)


@pytest.fixture
def sealed_draft(db, template, staff_user):
    return drafts_service.create_draft(
        from_template_id=template.pk, sensitivity="privileged", created_by_user_id=staff_user.pk
    )


def test_a_privileged_draft_is_listed_and_opened_by_its_creator_only(
    sealed_draft, draft, creator, colleague, admin_user
) -> None:
    def listed(client):
        return {row["id"] for row in client.get(f"{DRAFTING_PREFIX}/drafts").json()}

    assert listed(creator) == {str(sealed_draft.pk), str(draft.pk)}
    assert listed(colleague) == {str(draft.pk)}
    refused = colleague.get(f"{DRAFTING_PREFIX}/drafts/{sealed_draft.pk}")
    assert refused.status_code == 403
    assert refused.json()["code"] == "privileged" and refused.json()["tracking_number"] is None
    assert creator.get(f"{DRAFTING_PREFIX}/drafts/{sealed_draft.pk}").status_code == 200
    # P20h: an administrator sees every privileged request, and so the letter of one (37a).
    admin = _client(admin_user)
    assert listed(admin) == {str(sealed_draft.pk), str(draft.pk)}
    assert admin.get(f"{DRAFTING_PREFIX}/drafts/{sealed_draft.pk}").status_code == 200


def test_only_its_creator_can_make_a_draft_privileged(draft, creator, colleague) -> None:
    refused = patch_json(
        colleague, f"{DRAFTING_PREFIX}/drafts/{draft.pk}", {"sensitivity": "privileged"}
    )
    assert refused.status_code == 422 and "started" in refused.json()["detail"]
    assert LetterDraft.objects.get(pk=draft.pk).sensitivity == "internal"
    # Any other edit by a colleague is as it was.
    assert (
        patch_json(colleague, f"{DRAFTING_PREFIX}/drafts/{draft.pk}", {"subject": "x"}).status_code
        == 200
    )
    sealed = patch_json(
        creator, f"{DRAFTING_PREFIX}/drafts/{draft.pk}", {"sensitivity": "privileged"}
    )
    assert sealed.status_code == 200
    assert colleague.get(f"{DRAFTING_PREFIX}/drafts/{draft.pk}").status_code == 403


def test_a_draft_cannot_be_started_from_a_request_the_caller_may_not_see(
    staff_actor, staff_user, agency, creator, colleague
) -> None:
    sealed = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="SEALED-SUBJECT",
        request_text="SEALED-TEXT",
        items=[requests_service.NewItem("1", "SEALED-ITEM")],
        sensitivity="privileged",
    )
    refused = post_json(colleague, f"{DRAFTING_PREFIX}/drafts", {"from_request_id": str(sealed.pk)})
    assert refused.status_code == 403 and b"SEALED" not in refused.content
    assert not LetterDraft.objects.exists()

    made = post_json(creator, f"{DRAFTING_PREFIX}/drafts", {"from_request_id": str(sealed.pk)})
    assert made.status_code == 201, made.content
    # It inherits the request's sensitivity (P3b, F2), so it is the creator's alone from the start.
    assert made.json()["sensitivity"] == "privileged"
    assert colleague.get(f"{DRAFTING_PREFIX}/drafts/{made.json()['id']}").status_code == 403


def test_filing_a_privileged_draft_puts_the_filer_on_each_request_it_produces(
    sealed_draft, agency, second_agency, staff_user, creator, colleague
) -> None:
    for target in (agency, second_agency):
        drafts_service.add_recipient(sealed_draft.pk, agency_id=target.pk, channel="email")
    filed = post_json(creator, f"{DRAFTING_PREFIX}/drafts/{sealed_draft.pk}/file", {})
    assert filed.status_code == 200, filed.content
    assert filed.json()["filed_count"] == 2, filed.json()

    produced = [
        Request.objects.get(pk=r.request_id)
        for r in LetterRecipient.objects.filter(draft_id=sealed_draft.pk)
    ]
    assert len(produced) == 2
    for request in produced:
        assert request.sensitivity == "privileged"
        assert [row.user_id for row in access_service.active_access(request.pk)] == [staff_user.pk]
        assert colleague.get(f"/api/records/requests/{request.pk}").status_code == 403
        assert creator.get(f"/api/records/requests/{request.pk}").status_code == 200
    assert colleague.get("/api/records/requests").json()["total"] == 0


# ---- found by the second-pass review: a draft is the text of the requests it names (F1) ---------


def _seal(client, request_id):
    return post_json(
        client,
        f"/api/records/requests/{request_id}/edits",
        {
            "entity_type": "requests",
            "entity_id": str(request_id),
            "field": "sensitivity",
            "new_value": "privileged",
            "note": "Counsel asked.",
        },
    )


def _reach(client, draft_id, recipient_id) -> dict[str, int]:
    """Every way to read a draft."""
    listed = {row["id"] for row in client.get(f"{DRAFTING_PREFIX}/drafts").json()}
    return {
        "listed": int(str(draft_id) in listed),
        "open": client.get(f"{DRAFTING_PREFIX}/drafts/{draft_id}").status_code,
        "preview": client.get(f"{DRAFTING_PREFIX}/recipients/{recipient_id}/preview").status_code,
        "file": post_json(client, f"{DRAFTING_PREFIX}/drafts/{draft_id}/file", {}).status_code,
    }


def test_sealing_a_request_seals_the_draft_it_was_filed_from(
    draft, agency, second_agency, creator, colleague, admin_user
) -> None:
    """The ordinary life of a request: drafted, filed, then made privileged. The draft holds its
    subject, its items and its text, and was being served to all staff by the drafting routes."""
    first = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")
    drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk, channel="email")
    assert (
        post_json(creator, f"{DRAFTING_PREFIX}/drafts/{draft.pk}/file", {}).json()["filed_count"]
        == 2
    )
    produced = LetterRecipient.objects.get(pk=first.pk).request_id
    open_to_all = {"listed": 1, "open": 200, "preview": 200, "file": 200}
    assert _reach(colleague, draft.pk, first.pk) == open_to_all

    # Sealing ONE of the two requests it was filed as is enough: the text is the same text.
    assert _seal(creator, produced).status_code == 200
    sealed_off = {"listed": 0, "open": 403, "preview": 403, "file": 403}
    assert _reach(colleague, draft.pk, first.pk) == sealed_off
    refused = colleague.get(f"{DRAFTING_PREFIX}/drafts/{draft.pk}")
    assert refused.json()["code"] == "privileged" and refused.json()["tracking_number"] is None
    assert str(produced) not in refused.content.decode()
    assert _reach(creator, draft.pk, first.pk) == open_to_all
    # P20h: an administrator sees the sealed request, so the draft it came from too.
    assert _reach(_client(admin_user), draft.pk, first.pk) == open_to_all
    # No edit either, by any of the draft's doors.
    assert (
        patch_json(colleague, f"{DRAFTING_PREFIX}/drafts/{draft.pk}", {"subject": "x"}).status_code
        == 403
    )
    assert colleague.delete(f"{DRAFTING_PREFIX}/recipients/{first.pk}").status_code == 403


def test_sealing_a_request_seals_a_draft_that_was_started_from_it(
    reserved_with_items, creator, colleague
) -> None:
    made = post_json(
        colleague, f"{DRAFTING_PREFIX}/drafts", {"from_request_id": str(reserved_with_items.pk)}
    )
    assert made.status_code == 201, made.content
    draft_id = made.json()["id"]
    assert colleague.get(f"{DRAFTING_PREFIX}/drafts/{draft_id}").status_code == 200

    assert _seal(creator, reserved_with_items.pk).status_code == 200
    # Its own creator included: they cannot see the request, and the draft is its text.
    assert colleague.get(f"{DRAFTING_PREFIX}/drafts/{draft_id}").status_code == 403
    assert draft_id not in {r["id"] for r in colleague.get(f"{DRAFTING_PREFIX}/drafts").json()}
    assert creator.get(f"{DRAFTING_PREFIX}/drafts/{draft_id}").status_code == 200
