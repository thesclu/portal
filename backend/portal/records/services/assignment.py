"""The ⋯ menu's "assign to another request", both of its answers.

"Also applies here" is additive: one letter genuinely answered three requests, so it gets three
events and the original is untouched. "Actually belongs here instead" is a move: the wrong event
and everything it triggered are voided, and a correctly targeted one is created -- same source
throughout, because the evidence never changed, only our reading of who it was about.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from django.db import transaction

from portal.records.actors import Actor
from portal.records.models import Event, Request
from portal.records.services import access as access_service
from portal.records.services import events as events_service
from portal.records.services import sources as sources_service
from portal.records.services.common import NotFound, ServiceError


@transaction.atomic
def assign_source_to_request(
    actor: Actor,
    source_id: UUID,
    request_id: UUID,
    *,
    mode: Literal["also_applies", "belongs_here_instead"],
    from_event_id: UUID | None = None,
    event_type: str = "correspondence",
    channel: str = "email",
    direction: str = "inbound",
    occurred_at: datetime | None = None,
    display_body: str | None = None,
    reason: str | None = None,
) -> Event:
    """`reason` (P23b, 075) is the person's why for a move; it is written into the void reason
    of the event the message leaves, after where it went."""
    target = Request.objects.filter(pk=request_id).first()
    if target is None:
        raise NotFound(f"No request {request_id}.")
    from_event = events_service.get_event(from_event_id) if from_event_id is not None else None

    if mode == "belongs_here_instead":
        if from_event is None:
            raise ServiceError("'belongs_here_instead' needs the event it is moving away from.")
        # The reason is written onto the request the message is LEAVING, where everyone who sees
        # that request reads it. It must not name a request they may not see.
        moved_to = (
            "another request" if access_service.is_privileged(target) else target.tracking_number
        )
        why = (reason or "").strip()
        events_service.void_event(
            actor, from_event.pk, f"reassigned to {moved_to}" + (f": {why}" if why else "")
        )
    elif mode != "also_applies":
        raise ServiceError(f"Unknown assignment mode {mode!r}.")

    if occurred_at is None:
        occurred_at = from_event.occurred_at if from_event else None
    if occurred_at is None:
        raise ServiceError("An assigned event needs an occurred_at, or an event to copy it from.")
    if display_body is None:
        display_body = from_event.display_body if from_event else ""

    event = events_service.record_event(
        actor,
        request_id,
        event_type=event_type,
        channel=channel,
        direction=direction,
        occurred_at=occurred_at,
        display_body=display_body,
        source_id=source_id,
    )
    sources_service.mark_source_reviewed(actor, source_id)
    return event
