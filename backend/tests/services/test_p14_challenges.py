"""P14: an appeal of a withholding or a challenge to the search, its letter, its follow-up date,
and its outcome. The letter is the person's; nothing here writes one."""

from datetime import date, timedelta

import pytest
from django.utils import timezone
from portal.records.actors import Actor
from portal.records.models import Challenge, Deadline, Event
from portal.records.services import challenges, review
from portal.records.services import requests as requests_service
from portal.records.services.common import InvalidTransition, NotAllowed, ServiceError
from portal.records.services.review import ItemReview

from tests.p8b_helpers import attach, make_source


@pytest.fixture
def actor(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def with_items(db, actor, staff_user, agency):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Two items",
        request_text="Body.",
        items=[
            requests_service.NewItem("1", "Contracts", sort_order=1),
            requests_service.NewItem("2", "Policies", sort_order=2),
        ],
    )


def _withheld_row(actor, with_items):
    event = attach(actor, with_items, make_source(actor), event_type="production")
    items = {i.item_number: i for i in with_items.items.all()}
    done = review.review_production(
        actor,
        with_items.pk,
        event_id=event.pk,
        rows=[
            ItemReview(items["1"].pk, "produced"),
            ItemReview(items["2"].pk, "withheld", exemption_cited="cited"),
        ],
        files=[],
    )
    return {r.request_item_id: r for r in done.rows.all()}, items


def test_an_appeal_is_scoped_sent_deadlined_and_resolved(actor, staff_user, with_items) -> None:
    rows, items = _withheld_row(actor, with_items)
    with pytest.raises(InvalidTransition, match="withheld or partly"):
        challenges.open_challenge(
            actor, with_items.pk, kind="appeal", basis="x", review_item_id=rows[items["1"].pk].pk
        )
    with pytest.raises(ServiceError, match="Say why"):
        challenges.open_challenge(actor, with_items.pk, kind="appeal", basis=" ")
    appeal = challenges.open_challenge(
        actor,
        with_items.pk,
        kind="appeal",
        basis="The exemption does not cover these.",
        review_item_id=rows[items["2"].pk].pk,
        follow_up_by=date.today() + timedelta(days=30),
    )
    assert appeal.request_item_id == items["2"].pk and appeal.sent_at is None
    with pytest.raises(InvalidTransition, match="not gone out"):
        challenges.record_outcome(actor, appeal.pk, "upheld")
    # Sent some other way: the event, the status, the follow-up deadline.
    sent = challenges.mark_sent(actor, appeal.pk, channel="mail", note="Posted the appeal.")
    assert sent.sent_at is not None and sent.event_id is not None
    event = Event.objects.get(pk=sent.event_id)
    assert (event.event_type_id, event.direction, event.channel) == ("appeal", "outbound", "mail")
    with_items.refresh_from_db()
    assert with_items.current_status_id == "appealed"
    deadline = Deadline.objects.get(pk=sent.followup_deadline_id)
    assert (deadline.deadline_type, deadline.basis, deadline.request_item_id) == (
        "challenge_response",
        "internal",
        items["2"].pk,
    )
    with pytest.raises(InvalidTransition, match="already gone"):
        challenges.mark_sent(actor, appeal.pk)
    with pytest.raises(InvalidTransition, match="has gone"):
        challenges.set_follow_up(actor, appeal.pk, date.today())
    resolved = challenges.record_outcome(actor, appeal.pk, "partly_reversed", "They released two.")
    assert resolved.outcome == "partly_reversed" and resolved.resolved_by_id == staff_user.pk
    with pytest.raises(InvalidTransition):
        challenges.record_outcome(actor, appeal.pk, "upheld")
    with_items.refresh_from_db()
    assert with_items.current_status_id == "appealed"  # an outcome moves no status by itself
    assert [c.pk for c in challenges.challenges_of(with_items.pk)] == [appeal.pk]


def test_a_search_challenge_sent_through_mail_is_told_back(
    actor, with_items, agency, media, monkeypatch
) -> None:
    from portal.mail.services import compose, send, transport

    monkeypatch.setattr(send, "_defer", lambda message_id, send_after: None)
    monkeypatch.setattr(
        transport, "_post", lambda token, payload: (200, {"ErrorCode": 0, "MessageID": "pm-c1"})
    )
    challenge = challenges.open_challenge(
        actor,
        with_items.pk,
        kind="search_challenge",
        basis="The search skipped the unit that holds these.",
        follow_up_by=date.today() + timedelta(days=14),
    )
    message = compose.create_draft(
        actor,
        with_items.pk,
        subject="Challenge",
        body_text="Please search again.",
        to=["records@agency.example.org"],
    )
    challenges.attach_message(actor, challenge.pk, message.pk)
    send.queue(actor, message.pk, delay_seconds=0)
    assert send.deliver(message.pk).outcome == "sent"
    row = Challenge.objects.get(pk=challenge.pk)
    assert row.sent_at is not None
    event = Event.objects.get(pk=row.event_id)
    assert (event.event_type_id, event.channel) == ("challenge", "email")
    assert event.source_id is not None and event.display_body == "Please search again."
    with_items.refresh_from_db()
    assert with_items.current_status_id != "appealed"  # a search challenge is not an appeal
    assert Deadline.objects.get(pk=row.followup_deadline_id).basis == "internal"
    with pytest.raises(InvalidTransition):
        challenges.attach_message(actor, challenge.pk, message.pk)
    with pytest.raises(NotAllowed):
        challenges.record_outcome(Actor.for_system("job"), challenge.pk, "upheld")
    assert timezone.now() >= row.sent_at
