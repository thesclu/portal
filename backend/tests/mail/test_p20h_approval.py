"""P20h: an intern's letters and messages go out only through approval (DECISIONS 42, 43).

Every branch of the workflow (`mail/services/approval.py`) and every door an intern might use to
send or file by hand. The provider is the fake of this package's conftest, so a "send" is a call
recorded on it; the job runner is replaced, so a queued message is delivered by calling
`send.deliver` here, as the worker would.
"""

import json
from pathlib import Path

import pytest
from django.test import Client
from portal.core.models import Notification, Review
from portal.core.services import review as review_service
from portal.core.services.identity import create_user
from portal.drafting.models import LetterRecipient
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import filing as drafting_filing
from portal.mail.models import Message, MessageKind, MessageStatus
from portal.mail.services import approval, compose, send
from portal.mail.services.compose import Address
from portal.records.actors import Actor
from portal.records.models import AuditLog, Request, StatusChange
from portal.records.services import common
from portal.records.services import requests as requests_service
from portal.records.services import status as status_service
from portal.records.services.common import InvalidTransition, NotAllowed, ServiceError

from tests.drafting.conftest import sendable_agency
from tests.p20h_helpers import give

FIXTURES = Path(__file__).resolve().parents[1] / "services" / "fixtures"
COVER = {
    "subject": "Our request",
    "body_text": "Please find our request attached.",
    "to": [{"address": "records@agency.example.org"}],
}


@pytest.fixture(autouse=True)
def _fictional_rule_data_only():
    with common.using_rule_data(
        rules_path=FIXTURES / "rules_test.toml", calendars_path=FIXTURES / "calendars_test.toml"
    ) as data:
        yield data


def _person(email: str, role: str, **extra):
    return create_user(
        email=email,
        name=email.split("@")[0].title(),
        records_role=role,
        **extra,
    )


@pytest.fixture
def intern(db):
    return _person("intern@example.org", "intern")


@pytest.fixture
def attorney(db):
    return _person("attorney@example.org", "staff_attorney")


@pytest.fixture
def counsel(db):
    """A limited-scope attorney: given the intern's request in the tests that need them to."""
    return _person("counsel@example.org", "limited_attorney")


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _post(client, path, body=None):
    return client.post(path, data=json.dumps(body or {}), content_type="application/json")


def _letter(intern, actor, *, channel="email", name="Approval agency"):
    """An intern's draft to one agency, reserved (stage 3 reserves), not yet out."""
    agency = sendable_agency(actor, name)
    draft = drafts_service.create_draft(subject="An intern's letter", created_by_user_id=intern.pk)
    drafts_service.add_draft_item(draft.pk, item_number="1", body="The only ask")
    recipient = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel=channel)
    return draft, drafting_filing.reserve_recipient(Actor.human(intern), recipient.pk)


def _cover() -> approval.Cover:
    return approval.Cover(
        subject="Our request",
        body_text="Please find our request attached.",
        to=[Address("records@agency.example.org")],
    )


def _to_agency(provider) -> list[dict]:
    """What the fake provider was handed for the agency (the portal's own notification emails to
    people go through the same provider and are not what these tests count)."""
    return [p for _, p in provider.calls if "agency.example.org" in p.get("To", "")]


def _status(request_id) -> str:
    return Request.objects.get(pk=request_id).current_status_id


# ---- submitting and withdrawing ---------------------------------------------------------------


def test_submitting_a_letter_waits_for_approval_and_tells_whoever_may_approve(
    intern, attorney, counsel, admin_user, staff_user, actor
) -> None:
    _, recipient = _letter(intern, actor)
    give(recipient.request_id, counsel.pk)
    stranger = _person("stranger@example.org", "limited_attorney")

    review = approval.submit_letter(intern, recipient.pk, cover=_cover(), note="First go.")

    assert (review.subject_type, review.kind, review.decision) == ("message", "approval", None)
    assert _status(recipient.request_id) == "waiting_for_approval"
    message = Message.objects.get(pk=review.subject_id)
    assert (message.kind, message.status, message.letter_recipient_id) == (
        MessageKind.FILING,
        MessageStatus.DRAFT,
        recipient.pk,
    )
    told = set(
        Notification.objects.filter(subject_type="review", subject_id=review.pk).values_list(
            "user_id", flat=True
        )
    )
    # Every administrator, the staff attorney, the attorney given it; not staff, not an attorney
    # who was not given it, not the intern.
    assert {admin_user.pk, attorney.pk, counsel.pk} <= told
    assert not told & {staff_user.pk, stranger.pk, intern.pk}
    # Nothing went out.
    assert not Message.objects.exclude(status=MessageStatus.DRAFT).exists()


def test_only_an_intern_submits_and_only_once(intern, staff_user, actor) -> None:
    _, recipient = _letter(intern, actor)
    with pytest.raises(NotAllowed):
        approval.submit_letter(staff_user, recipient.pk, cover=_cover())
    approval.submit_letter(intern, recipient.pk, cover=_cover())
    with pytest.raises(InvalidTransition):
        approval.submit_letter(intern, recipient.pk, cover=_cover())


def test_an_email_letter_needs_its_cover_message(intern, actor) -> None:
    _, recipient = _letter(intern, actor)
    with pytest.raises(ServiceError, match="cover message"):
        approval.submit_letter(intern, recipient.pk, cover=None)
    assert _status(recipient.request_id) == "pending_filing"


def test_withdrawing_gives_it_back_and_it_can_be_submitted_again(intern, actor) -> None:
    _, recipient = _letter(intern, actor)
    review = approval.submit_letter(intern, recipient.pk, cover=_cover())
    message_id = review.subject_id
    # While it waits, the message is not edited under the approver.
    with pytest.raises(InvalidTransition, match="Withdraw it first"):
        compose.update_draft(Actor.human(intern), message_id, subject="Changed")

    back = approval.withdraw(intern, review.pk)

    assert back.decision == Review.WITHDRAWN and back.decided_by_id == intern.pk
    assert _status(recipient.request_id) == "pending_filing"
    compose.update_draft(Actor.human(intern), message_id, subject="Changed")
    again = approval.submit_letter(intern, recipient.pk, cover=_cover())
    assert again.subject_id == message_id  # the same cover message, resubmitted
    assert _status(recipient.request_id) == "waiting_for_approval"


def test_only_the_intern_who_submitted_it_withdraws_it(intern, admin_user, actor) -> None:
    _, recipient = _letter(intern, actor)
    review = approval.submit_letter(intern, recipient.pk, cover=_cover())
    with pytest.raises(NotAllowed):
        approval.withdraw(admin_user, review.pk)
    assert _status(recipient.request_id) == "waiting_for_approval"


# ---- approve and send ---------------------------------------------------------------------------


def test_approve_and_send_sends_exactly_once_and_files_as_stage_four_would(
    intern, attorney, actor, provider, media, _no_worker
) -> None:
    _, recipient = _letter(intern, actor)
    review = approval.submit_letter(intern, recipient.pk, cover=_cover())

    decided = approval.decide(attorney, review.pk, Review.APPROVED)

    assert decided.decision == Review.APPROVED and decided.decided_by_id == attorney.pk
    message = Message.objects.get(pk=review.subject_id)
    assert message.status == MessageStatus.QUEUED
    assert _no_worker == [str(message.pk)]  # queued once
    # The approver is on the request: on its status row and in the activity line.
    notes = list(
        StatusChange.objects.filter(request_id=recipient.request_id)
        .order_by("effective_at")
        .values_list("status", "note")
    )
    assert notes[-2:] == [
        ("waiting_for_approval", "Submitted for approval"),
        ("pending_filing", f"Approved by {attorney.name}"),
    ]
    # A second approval of the same submission is refused, and nothing is queued again.
    with pytest.raises(InvalidTransition):
        approval.decide(attorney, review.pk, Review.APPROVED)
    assert _no_worker == [str(message.pk)]

    Message.objects.filter(pk=message.pk).update(send_after=None)
    done = send.deliver(message.pk)
    assert done.outcome == "sent", done.message.error
    assert len(_to_agency(provider)) == 1
    assert _status(recipient.request_id) == "filed"
    assert LetterRecipient.objects.get(pk=recipient.pk).status == LetterRecipient.FILED
    # The worker running again sends nothing more.
    assert send.deliver(message.pk).outcome == "skipped"
    assert len(_to_agency(provider)) == 1


def test_approving_a_message_that_changed_after_it_was_submitted_is_refused(
    intern, attorney, actor, _no_worker
) -> None:
    _, recipient = _letter(intern, actor)
    review = approval.submit_letter(intern, recipient.pk, cover=_cover())
    later = review.submitted_at.replace(year=review.submitted_at.year + 1)
    Message.objects.filter(pk=review.subject_id).update(updated_at=later)
    with pytest.raises(InvalidTransition, match="changed after it was submitted"):
        approval.decide(attorney, review.pk, Review.APPROVED)
    assert Review.objects.get(pk=review.pk).decision is None and _no_worker == []


def test_a_letter_filed_by_hand_is_released_and_the_intern_then_files_it(
    intern, admin_user, actor
) -> None:
    """A web form or the post: approving files nothing (42a, plain "Approve"); it releases the
    letter, and the intern's own filing, refused before, is cleared."""
    _, recipient = _letter(intern, actor, channel="mail", name="Postal agency")
    with pytest.raises(review_service.NeedsReview):
        drafting_filing.file_recipient(Actor.human(intern), recipient.pk)
    review = approval.submit_letter(intern, recipient.pk, cover=None)
    assert review.subject_type == "recipient"
    approval.decide(admin_user, review.pk, Review.APPROVED)
    assert _status(recipient.request_id) == "pending_filing"
    filed = drafting_filing.file_recipient(Actor.human(intern), recipient.pk)
    assert filed.status == LetterRecipient.FILED
    assert _status(recipient.request_id) == "filed"


# ---- send back ----------------------------------------------------------------------------------


def test_sending_back_needs_a_note_and_returns_it_to_the_intern(intern, attorney, actor) -> None:
    _, recipient = _letter(intern, actor)
    review = approval.submit_letter(intern, recipient.pk, cover=_cover())
    with pytest.raises(ServiceError, match="note"):
        approval.decide(attorney, review.pk, Review.SENT_BACK, "  ")
    assert _status(recipient.request_id) == "waiting_for_approval"

    back = approval.decide(attorney, review.pk, Review.SENT_BACK, "Add a second item.")

    assert back.comment == "Add a second item."
    assert _status(recipient.request_id) == "pending_filing"
    assert StatusChange.objects.filter(
        request_id=recipient.request_id, note=f"Sent back by {attorney.name}: Add a second item."
    ).exists()
    found = review_service.clearance(intern, "message", review.subject_id, None)
    assert not found.cleared and "sent back" in found.why
    assert Notification.objects.filter(user=intern, subject_id=review.pk).exists()


def test_resubmitting_after_editing_the_letter_freezes_the_new_letter(
    intern, attorney, actor
) -> None:
    draft, recipient = _letter(intern, actor)
    review = approval.submit_letter(intern, recipient.pk, cover=_cover())
    approval.decide(attorney, review.pk, Review.SENT_BACK, "Add a second item.")

    drafts_service.add_draft_item(draft.pk, item_number="2", body="A second ask")
    approval.submit_letter(intern, recipient.pk, cover=_cover())

    request = Request.objects.get(pk=recipient.request_id)
    assert "A second ask" in request.request_text
    assert sorted(request.items.values_list("item_number", flat=True)) == ["1", "2"]
    assert AuditLog.objects.filter(
        entity_type="requests", entity_id=request.pk, field_name="requests.request_text"
    ).exists()


# ---- who approves -------------------------------------------------------------------------------


def test_who_may_approve(intern, attorney, counsel, staff_user, admin_user, actor) -> None:
    _, recipient = _letter(intern, actor)
    review = approval.submit_letter(intern, recipient.pk, cover=_cover())
    review = Review.objects.get(pk=review.pk)
    assert review_service.may_approve(admin_user, review)
    assert review_service.may_approve(attorney, review)
    assert not review_service.may_approve(staff_user, review)  # staff do not approve (42b)
    assert not review_service.may_approve(intern, review)
    assert not review_service.may_approve(counsel, review)  # not given the request
    give(recipient.request_id, counsel.pk)
    assert review_service.may_approve(counsel, review)
    assert review_service.pending_for(counsel) == [review]
    with pytest.raises(NotAllowed):
        approval.decide(staff_user, review.pk, Review.APPROVED)


def test_a_limited_attorney_without_access_can_neither_see_nor_approve_it(
    intern, counsel, actor, _no_worker
) -> None:
    _, recipient = _letter(intern, actor)
    review = approval.submit_letter(intern, recipient.pk, cover=_cover())
    client = _client(counsel)
    assert client.get("/api/core/reviews/pending").json() == []
    assert client.get(f"/api/core/reviews/{review.pk}").status_code in (403, 404)
    refused = _post(client, f"/api/core/reviews/{review.pk}/decide", {"decision": "approved"})
    assert refused.status_code in (403, 404)
    assert Review.objects.get(pk=review.pk).decision is None and _no_worker == []

    give(recipient.request_id, counsel.pk)
    assert [row["id"] for row in client.get("/api/core/reviews/pending").json()] == [str(review.pk)]
    approved = _post(client, f"/api/core/reviews/{review.pk}/decide", {"decision": "approved"})
    assert approved.status_code == 200, approved.content
    assert len(_no_worker) == 1


# ---- an intern cannot file or send by any door ---------------------------------------------------


def test_an_intern_cannot_file_or_send_directly_by_any_route(
    intern, actor, provider, media, _no_worker
) -> None:
    draft, recipient = _letter(intern, actor)
    client = _client(intern)
    before = Message.objects.count()

    # Stage 4, an email agency: send-and-file.
    resp = _post(client, f"/api/mail/recipients/{recipient.pk}/send-and-file", COVER)
    assert resp.status_code == 403
    assert Message.objects.count() == before  # refused before anything was written
    # Stage 4, by hand: mark this letter filed, and the whole draft's wave.
    assert _post(client, f"/api/drafting/recipients/{recipient.pk}/file", {}).status_code == 403
    wave = _post(client, f"/api/drafting/drafts/{draft.pk}/file", {}).json()
    assert wave["filed_count"] == 0 and "approve" in wave["outcomes"][0]["reason"]
    # The request page: mark filed, set the status to filed, record a letter sent by post.
    request_id = recipient.request_id
    page = f"/api/records/requests/{request_id}"
    filed = _post(client, f"{page}/file", {"filed_at": "2026-03-02T17:30:00Z", "channel": "mail"})
    assert filed.status_code == 403
    status = _post(client, f"{page}/status", {"status": "filed", "note": "I posted it."})
    assert status.status_code == 403
    sent_event = _post(
        client,
        f"{page}/events",
        {
            "event_type": "correspondence",
            "channel": "mail",
            "direction": "outbound",
            "occurred_at": "2026-03-02T17:30:00Z",
            "display_body": "Posted the letter.",
        },
    )
    assert sent_event.status_code == 403
    assert _status(request_id) == "pending_filing"
    assert _to_agency(provider) == [] and _no_worker == []


def test_an_intern_cannot_send_a_reply_or_a_follow_up_but_may_submit_it(
    intern, attorney, actor, agency, provider, media, _no_worker
) -> None:
    request = requests_service.reserve_request(
        Actor.human(intern),
        agency_id=agency.pk,
        requester_user_id=intern.pk,
        subject="An intern's request",
        request_text="Body.",
    )
    status_service.override_status(actor, request.pk, "filed", "Filed on paper by staff.")
    client = _client(intern)
    for kind in ("reply", "followup"):
        made = _post(
            client,
            "/api/mail/messages",
            {
                "request_id": str(request.pk),
                "kind": kind,
                "subject": f"A {kind}",
                "body_text": "Words.",
                "to": [{"address": "records@agency.example.org"}],
            },
        )
        assert made.status_code == 201, made.content
        message_id = made.json()["id"]
        assert _post(client, f"/api/mail/messages/{message_id}/send").status_code == 403
        assert Message.objects.get(pk=message_id).status == MessageStatus.DRAFT
        submitted = _post(
            client,
            "/api/core/reviews",
            {"subject_type": "message", "subject_id": message_id, "kind": "approval"},
        )
        assert submitted.status_code == 201, submitted.content
        assert _status(request.pk) == "filed"  # a reply moves no status
        decided = _post(
            _client(attorney),
            f"/api/core/reviews/{submitted.json()['id']}/decide",
            {"decision": "approved"},
        )
        assert decided.status_code == 200, decided.content
        assert Message.objects.get(pk=message_id).status == MessageStatus.QUEUED
    assert len(_no_worker) == 2 and _to_agency(provider) == []


def test_staff_send_and_file_without_approval(staff_user, actor, provider, media) -> None:
    agency = sendable_agency(actor, "Staff agency")
    draft = drafts_service.create_draft(subject="Staff letter", created_by_user_id=staff_user.pk)
    recipient = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")
    drafting_filing.reserve_recipient(actor, recipient.pk)
    client = _client(staff_user)
    made = _post(client, f"/api/mail/recipients/{recipient.pk}/send-and-file", COVER)
    assert made.status_code == 201, made.content
    refused = _post(client, f"/api/mail/recipients/{recipient.pk}/submit", COVER)
    assert refused.status_code == 403  # nothing to submit: staff send


def test_an_intern_submits_from_stage_four_through_the_api(intern, actor) -> None:
    _, recipient = _letter(intern, actor)
    client = _client(intern)
    made = _post(client, f"/api/mail/recipients/{recipient.pk}/submit", {**COVER, "note": "Hi"})
    assert made.status_code == 201, made.content
    assert made.json()["request_id"] == str(recipient.request_id)
    assert _status(recipient.request_id) == "waiting_for_approval"
    review_id = made.json()["id"]
    withdrawn = _post(client, f"/api/core/reviews/{review_id}/withdraw")
    assert withdrawn.status_code == 200 and withdrawn.json()["decision"] == "withdrawn"
    assert _status(recipient.request_id) == "pending_filing"


def test_nobody_sets_waiting_for_approval_by_hand(actor, a_request) -> None:
    with pytest.raises(InvalidTransition):
        status_service.override_status(actor, a_request.pk, "waiting_for_approval", "x")
