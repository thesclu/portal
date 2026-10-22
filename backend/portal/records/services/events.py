"""The timeline: one row per thing that happened to one request, and the way to unsay one.

A mistaken row is voided, never deleted -- in a tool whose job is an accurate, disputable record,
keeping "we thought X" and marking it wrong beats erasing it.
"""

from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.records.actors import Actor
from portal.records.models import AuditLog, Deadline, Event, Source, StatusChange, writable
from portal.records.services import status as status_service
from portal.records.services.common import (
    InvalidTransition,
    NotFound,
    lock_request,
    require_human,
    require_of_request,
)


def get_event(event_id: UUID) -> Event:
    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        raise NotFound(f"No event {event_id}.")
    return event


@transaction.atomic
def record_event(
    actor: Actor,
    request_id: UUID,
    *,
    event_type: str,
    channel: str,
    direction: str,
    occurred_at: datetime,
    display_body: str,
    source_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    actor_agency_contact_id: UUID | None = None,
    related_deadline_id: UUID | None = None,
    status: str | None = None,
    note: str | None = None,
) -> Event:
    """`status`, when given, is what this event MEANS -- appended with the event as its trigger.

    An event that names a `source_id` takes that source's lock after the request's (the ledger's
    lock order) and is refused if the source was marked handled without attaching: such a message
    is attached to nothing by decision, and `sources.resolve_without_attaching` takes the same lock
    to check that no event exists, so the two cannot both succeed. Undo the resolution first.
    """
    request = lock_request(request_id)
    require_of_request(Deadline, related_deadline_id, request_id, "deadline")
    if source_id is not None:
        source = Source.objects.select_for_update().filter(pk=source_id).first()
        if source is not None and source.resolution is not None:
            raise InvalidTransition(
                f"Source {source_id} was marked handled without attaching "
                f"({source.resolution}); mark it unreviewed before attaching it to a request."
            )
    with writable():
        event = Event.objects.create(
            request_id=request_id,
            occurred_at=occurred_at,
            event_type_id=event_type,
            channel=channel,
            direction=direction,
            actor_user_id=actor_user_id,
            actor_agency_contact_id=actor_agency_contact_id,
            display_body=display_body,
            source_id=source_id,
            related_deadline_id=related_deadline_id,
            **actor.recorded_by(),
        )
    if status is not None:
        status_service._append_status_change(
            request, status, reason="event", actor=actor, triggering_event=event, note=note
        )
    return event


@transaction.atomic
def void_event(actor: Actor, event_id: UUID, reason: str) -> Event:
    user_id = require_human(actor, "Voiding an event")
    event = get_event(event_id)
    if event.voided_at is not None:
        raise InvalidTransition(f"Event {event_id} is already voided.")
    lock_request(event.request_id)

    triggered = StatusChange.objects.filter(triggering_event_id=event.pk, voided_at__isnull=True)
    unvoided = StatusChange.objects.filter(
        request_id=event.request_id, voided_at__isnull=True
    ).count()
    # recompute_request_current_status writes `latest.status` unconditionally, so with no unvoided
    # row left it writes NULL into a NOT NULL column and the user sees a raw database error. The
    # refusal below is what stands between a wrong void and that.
    if unvoided - triggered.count() < 1:
        raise InvalidTransition(
            f"Voiding event {event_id} would leave request {event.request_id} with no status "
            "history at all. Append the corrected status first, then void this event."
        )

    voided_at = timezone.now()
    Event.objects.filter(pk=event.pk).update(voided_at=voided_at, void_reason=reason)
    triggered.update(voided_at=voided_at)
    void_note = f"source event voided: {reason}"
    for deadline in Deadline.objects.filter(source_event_id=event.pk):
        # Append, don't overwrite: a conflict note from open_deadline (or an earlier void) must
        # survive so a person reviewing the flag sees the whole history, not just the latest cause.
        combined = f"{deadline.flag_note}; {void_note}" if deadline.flag_note else void_note
        Deadline.objects.filter(pk=deadline.pk).update(flagged_for_review=True, flag_note=combined)
    with writable():
        AuditLog.objects.create(
            entity_type="events",
            entity_id=event.pk,
            field_name="events.voided_at",
            old_value=None,
            new_value=voided_at.isoformat(),
            edited_by_id=user_id,
            note=reason,
        )
    return Event.objects.get(pk=event.pk)
