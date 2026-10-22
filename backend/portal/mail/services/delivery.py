"""What the provider tells us afterwards: delivered, bounced, marked as spam.

Each webhook call is one row in `delivery_events`, keyed on the provider's own id, so a call
delivered twice is a no-op. A bounce moves the recipient to `bounced`, flags the sent source
`delivery_failed` with the provider's sentence, and opens a `retry_delivery` task on the request
if none is open. It moves no status: a bounced filing is still filed until a person says
otherwise, which is their call, not the mail provider's.
"""

import logging
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from portal.core.services import notifications as core_notifications
from portal.mail.models import DeliveryEvent, DeliveryState, Message, MessageRecipient
from portal.mail.services.common import clean_address
from portal.records.actors import Actor
from portal.records.models import Request
from portal.records.services import sources as sources_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import ServiceError

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "postmark_delivery"
RETRY_TASK = "retry_delivery"
_STATE = {
    "Delivery": DeliveryState.DELIVERED,
    "Bounce": DeliveryState.BOUNCED,
    "SpamComplaint": DeliveryState.COMPLAINED,
}


def _when(payload: dict[str, Any]):
    for key in ("DeliveredAt", "BouncedAt", "ReceivedAt"):
        raw = payload.get(key)
        if raw:
            parsed = parse_datetime(str(raw))
            if parsed is not None:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return timezone.now()


@transaction.atomic
def record(payload: dict[str, Any]) -> str:
    """Returns `recorded`, `duplicate`, or `unknown` (no message of ours carries that id)."""
    record_type = str(payload.get("RecordType") or "")
    state = _STATE.get(record_type)
    provider_message_id = str(payload.get("MessageID") or "")
    if state is None or not provider_message_id:
        return "unknown"
    message = (
        Message.objects.select_for_update().filter(provider_message_id=provider_message_id).first()
    )
    if message is None:
        return "unknown"
    try:
        address = clean_address(str(payload.get("Recipient") or payload.get("Email") or ""))
    except ServiceError:
        address = ""
    recipient = (
        MessageRecipient.objects.filter(message_id=message.pk, address=address).first()
        if address
        else None
    )
    event_id = f"{record_type}:{payload.get('ID') or provider_message_id}:{address}"
    try:
        with transaction.atomic():
            DeliveryEvent.objects.create(
                message_id=message.pk,
                recipient_id=recipient.pk if recipient else None,
                provider_event_id=event_id[:500],
                record_type=record_type,
                payload=payload,
                occurred_at=_when(payload),
            )
    except IntegrityError:
        return "duplicate"
    when = _when(payload)
    if recipient is not None:
        changes: dict[str, Any] = {"delivery_state": state}
        if state == DeliveryState.DELIVERED:
            changes["delivered_at"] = when
        else:
            changes["bounced_at"] = when
            changes["bounce_description"] = str(
                payload.get("Description") or payload.get("Details") or record_type
            )[:1000]
        MessageRecipient.objects.filter(pk=recipient.pk).update(**changes)
    if state in (DeliveryState.BOUNCED, DeliveryState.COMPLAINED):
        _on_bounce(message, address, payload)
    return "recorded"


def _on_bounce(message: Message, address: str, payload: dict[str, Any]) -> None:
    actor = Actor.for_system(SYSTEM_ACTOR)
    what = str(payload.get("Description") or payload.get("Type") or "bounced")
    note = f"Delivery to {address or 'a recipient'} failed: {what}"
    if message.source_id:
        try:
            sources_service.flag_source(actor, message.source_id, note, reason="delivery_failed")
        except ServiceError:
            logger.exception("could not flag source %s after a bounce", message.source_id)
    assignee = (
        Request.objects.filter(pk=message.request_id)
        .values_list("assigned_to_id", flat=True)
        .first()
    )
    if assignee:
        core_notifications.notify(
            assignee,
            "bounce",
            f"{note} ({message.request.tracking_number})",
            link=f"/requests/{message.request_id}",
            subject_type="message",
            subject_id=message.pk,
            request_id=message.request_id,
        )
    if not any(t.task_type == RETRY_TASK for t in tasks_service.open_tasks(message.request_id)):
        tasks_service.create_task(
            actor,
            message.request_id,
            RETRY_TASK,
            f"{note}. Choose an address and send it again.",
            # P23b: the task names the bounced message, so "Retry send" opens it.
            source_id=message.source_id,
        )


def mark_bad_address(actor: Actor, contact_id: UUID, note: str) -> None:
    """A person confirms an agency contact's address is wrong. Audited through directory."""
    from portal.records.services import directory

    directory.update_contact(
        actor,
        contact_id,
        note,
        bad_address_at=timezone.now(),
        bad_address_note=note,
    )
