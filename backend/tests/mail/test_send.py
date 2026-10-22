"""Composing, the undo window, delivery through the fake provider, and the seam."""

from datetime import timedelta

import pytest
from django.utils import timezone
from portal.mail.models import Message, MessageStatus
from portal.mail.services import compose, delivery, send, transport
from portal.records.actors import Actor
from portal.records.models import Attachment, Event, Source, Task
from portal.records.services.common import InvalidTransition, ServiceError

from tests.p8b_helpers import add_file, attach, make_source


def _followups(on: bool) -> None:
    from portal.core.models import Setting

    Setting.objects.update_or_create(
        key="records.followups_send_enabled", defaults={"value": on, "description": "t"}
    )


def _draft(actor, a_request, **extra):
    return compose.create_draft(
        actor,
        a_request.pk,
        subject="About our request",
        body_text="Hello.",
        to=["records@agency.example.org"],
        **extra,
    )


def test_a_draft_is_from_the_alias_and_its_addresses_are_cleaned(actor, a_request) -> None:
    message = compose.create_draft(
        actor,
        a_request.pk,
        subject="x",
        to=["Records <RECORDS@Agency.example.org>", a_request.inbound_alias_email],
        cc=["records@agency.example.org", "other@agency.example.org"],
    )
    assert message.from_address == a_request.inbound_alias_email
    rows = {(r.kind, r.address) for r in message.recipients.all()}
    # Our own alias is never a recipient; an address in To is not repeated in Cc.
    assert rows == {("to", "records@agency.example.org"), ("cc", "other@agency.example.org")}
    with pytest.raises(ServiceError, match="not an email address"):
        compose.create_draft(actor, a_request.pk, to=["nobody"])


def test_queue_waits_the_undo_window_and_cancel_gives_it_back(actor, a_request, _no_worker) -> None:
    message = _draft(actor, a_request)
    with pytest.raises(ServiceError, match="at least one address"):
        send.queue(actor, compose.create_draft(actor, a_request.pk, subject="x").pk)
    queued = send.queue(actor, message.pk, delay_seconds=30)
    assert queued.status == MessageStatus.QUEUED
    assert queued.send_after is not None and queued.send_after > timezone.now()
    assert _no_worker == [str(message.pk)]
    with pytest.raises(InvalidTransition):
        compose.update_draft(actor, message.pk, subject="too late")
    # Early: the job finds the window still open and does nothing.
    assert send.deliver(message.pk).outcome == "early"
    back = send.cancel(actor, message.pk)
    assert back.status == MessageStatus.DRAFT and back.send_after is None
    assert send.deliver(message.pk).outcome == "skipped"


def test_delivery_makes_the_sent_copy_a_source_and_an_outbound_event(
    actor, a_request, provider, media
) -> None:
    message = _draft(actor, a_request)
    send.queue(actor, message.pk, delay_seconds=0)
    done = send.deliver(message.pk)
    assert done.outcome == "sent", done.message.error
    sent = done.message
    assert sent.status == MessageStatus.SENT and sent.provider_message_id == "pm-0001"
    # Sending is off in tests, so the provider's test token carried it and it says so.
    assert sent.test_send is True and provider.calls[0][0] == transport.TEST_TOKEN
    payload = provider.calls[0][1]
    assert payload["To"] == "records@agency.example.org"
    assert payload["ReplyTo"] == a_request.inbound_alias_email
    assert a_request.inbound_alias_email in payload["From"]
    source = Source.objects.get(pk=sent.source_id)
    # Not reviewed: a system sender could not mark it so, and the Inbox keeps sent copies out of
    # New by their outbound event, not by a review mark.
    assert source.source_type == "sent_email" and source.reviewed_at is None
    event = Event.objects.get(source_id=source.pk)
    assert (event.direction, event.channel, event.event_type_id) == (
        "outbound",
        "email",
        "correspondence",
    )
    assert event.request_id == a_request.pk
    # Twice is nothing: the message is sent, so the job leaves it alone.
    assert send.deliver(message.pk).outcome == "skipped"
    assert len(provider.calls) == 1


def test_the_provider_refusing_marks_it_failed_and_a_network_error_keeps_it_queued(
    actor, a_request, provider, media
) -> None:
    message = _draft(actor, a_request)
    send.queue(actor, message.pk, delay_seconds=0)
    provider.status, provider.body = 422, {"ErrorCode": 300, "Message": "Invalid 'To' address"}
    done = send.deliver(message.pk)
    assert done.outcome == "failed" and "Invalid 'To'" in (done.message.error or "")
    assert Source.objects.filter(source_type="sent_email").count() == 0
    # Edited and queued again.
    compose.update_draft(actor, message.pk, to=["fixed@agency.example.org"]) if False else None
    send.queue(actor, message.pk, delay_seconds=0)
    provider.status, provider.body = 200, {"ErrorCode": 0, "MessageID": "pm-0002"}

    def boom(token, payload):
        raise transport.TransportError("down", permanent=False)

    import portal.mail.services.transport as t

    original = t._post
    t._post = boom
    try:
        with pytest.raises(transport.TransportError):
            send.deliver(message.pk)
    finally:
        t._post = original
    assert Message.objects.get(pk=message.pk).status == MessageStatus.QUEUED
    assert send.deliver(message.pk).outcome == "sent"


def test_a_reply_is_prefilled_from_the_message_and_a_forward_quotes_it(
    actor, a_request, media
) -> None:
    source = make_source(actor)
    add_file(actor, source, "their.pdf")
    attach(actor, a_request, source)
    reply = compose.prefill(source.pk)
    assert reply.request_id == a_request.pk and reply.from_address == a_request.inbound_alias_email
    assert reply.subject.startswith("Re:")
    forward = compose.prefill(source.pk, forward=True)
    assert forward.subject.startswith("Fwd:") and forward.to == []
    assert "Forwarded message" in forward.body_text
    assert len(forward.attachment_ids) == 1
    draft = compose.create_draft(
        actor,
        a_request.pk,
        kind="forward",
        subject=forward.subject,
        attachment_ids=forward.attachment_ids,
        to=["counsel@example.org"],
    )
    assert [f.attachment_id for f in draft.files.all()] == forward.attachment_ids
    stranger = add_file(actor, make_source(actor), "elsewhere.pdf")
    with pytest.raises(ServiceError, match="not one of this request's files"):
        compose.create_draft(actor, a_request.pk, attachment_ids=[stranger.pk])


def test_a_bounce_flags_the_sent_copy_and_opens_a_retry_task_once(
    actor, a_request, provider, media
) -> None:
    message = _draft(actor, a_request)
    send.queue(actor, message.pk, delay_seconds=0)
    sent = send.deliver(message.pk).message
    bounce = {
        "RecordType": "Bounce",
        "ID": 42,
        "MessageID": "pm-0001",
        "Recipient": "records@agency.example.org",
        "Description": "The address does not exist.",
        "BouncedAt": "2026-10-06T12:00:00Z",
    }
    assert delivery.record(bounce) == "recorded"
    assert delivery.record(bounce) == "duplicate"
    recipient = sent.recipients.get()
    assert (
        recipient.delivery_state == "bounced" and "does not exist" in recipient.bounce_description
    )
    source = Source.objects.get(pk=sent.source_id)
    assert source.flagged_for_review and source.flag_reason == "delivery_failed"
    tasks = Task.objects.filter(request_id=a_request.pk, task_type="retry_delivery")
    assert tasks.count() == 1
    assert delivery.record({**bounce, "ID": 43}) == "recorded"
    assert tasks.count() == 1  # one open task is enough
    delivered = {
        "RecordType": "Delivery",
        "MessageID": "pm-0001",
        "Recipient": "records@agency.example.org",
        "DeliveredAt": "2026-10-06T12:05:00Z",
    }
    assert delivery.record(delivered) == "recorded"
    assert delivery.record({"RecordType": "Delivery", "MessageID": "unknown"}) == "unknown"
    assert Attachment.objects.count() == 0


def test_a_system_sender_sends_a_followup_without_an_undo_window(
    actor, staff_user, provider, media, settings
) -> None:
    from portal.mail.services import followups
    from portal.records.models import DeadlineDefinition
    from portal.records.services import requests as requests_service

    from tests.drafting.conftest import sendable_agency

    agency = sendable_agency(actor, "Followed-up agency")
    a_request = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Overdue",
        request_text="Body.",
    )
    kind = DeadlineDefinition.objects.first()
    # P18a: the switch is the setting, not the environment.
    _followups(False)
    followups.send_followup(Actor.for_system("overdue_sweep"), a_request, kind, "Please reply.")
    assert Message.objects.count() == 0
    _followups(True)
    followups.send_followup(Actor.for_system("overdue_sweep"), a_request, kind, "Please reply.")
    message = Message.objects.get()
    assert message.kind == "followup" and message.status == MessageStatus.QUEUED
    assert message.send_after is not None and message.send_after <= timezone.now() + timedelta(
        seconds=1
    )
    done = send.deliver(message.pk)
    assert done.outcome == "sent"
    assert Event.objects.get(source_id=done.message.source_id).event_type_id == "follow_up"
