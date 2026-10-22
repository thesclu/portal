"""Production review (BUILD_PLAN P12): what the agency produced, item by item.

A review is one person's reading of one production event: for every item of the request, a
disposition (produced, partly, withheld, not yet), the exemption the agency cited and where, and a
note; plus which of the event's files are the records and which item each answers. It is
append-only: a later review supersedes the earlier one, nothing is edited, and the dispositions
that stand are always the newest review's.

Two write paths behind one call (the plan's rule). A review in which every item is `produced` is
the person declaring the request fully produced, so it goes through `status.override_status`
(reason `manual_override`, a note, a human): the only door to `production_received`, as
non-negotiable 4 and the CHECK require. Any other review appends `partial_production` by
`reason='event'`, the event being the production reviewed. Nothing here can write
`production_received` any other way.

Files. The ones named become produced records on their source (`mark_attachments_as_records`,
which never unmarks, so a message answering two requests is safe), and each gets an
`attachment_items` row tying it to this request and, if named, one item: that row is what the
Records screen reads and what makes "whole request" expressible.

Lock order: the request row (taken first, by `lock_request`), then the source (inside
`mark_attachments_as_records`). The same order `classify` and `add_document` keep.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.core.services import activity as core_activity
from portal.records.actors import Actor
from portal.records.models import (
    Attachment,
    AttachmentItem,
    Disposition,
    Event,
    ProductionReview,
    ProductionReviewItem,
    RequestItem,
    writable,
)
from portal.records.services import sources as sources_service
from portal.records.services import status as status_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import (
    InvalidTransition,
    NotFound,
    ServiceError,
    lock_request,
    require_human,
)

REVIEWABLE_EVENT_TYPES = ("production",)
_REVIEW_TASK = "review_production"


@dataclass(frozen=True)
class ItemReview:
    request_item_id: UUID
    disposition: str
    exemption_cited: str | None = None
    cited_where: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class FileLink:
    attachment_id: UUID
    request_item_id: UUID | None = None


def _event_of(request_id: UUID, event_id: UUID) -> Event:
    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        raise NotFound(f"No event {event_id}.")
    if event.request_id != request_id:
        raise ServiceError("That event is on a different request.")
    if event.voided_at is not None:
        raise InvalidTransition("A voided event cannot be reviewed.")
    if event.event_type_id not in REVIEWABLE_EVENT_TYPES:
        raise InvalidTransition(
            f"Only a production can be reviewed; this event is {event.event_type_id!r}. Record "
            "it as a production first."
        )
    return event


def _check_rows(request_id: UUID, rows: Sequence[ItemReview]) -> list[RequestItem]:
    items = list(RequestItem.objects.filter(request_id=request_id))
    wanted = {row.request_item_id for row in rows}
    if len(wanted) != len(rows):
        raise ServiceError("An item is reviewed twice.")
    known = {item.pk for item in items}
    if wanted - known:
        raise ServiceError("A reviewed item does not belong to this request.")
    if known - wanted:
        missing = sorted(i.item_number for i in items if i.pk not in wanted)
        raise ServiceError(
            f"Every item needs a disposition; missing: {', '.join(missing)}. "
            "Use 'not yet' for an item the agency has not answered."
        )
    for row in rows:
        if row.disposition not in Disposition.values:
            raise ServiceError(
                f"Unknown disposition {row.disposition!r}; expected one of {Disposition.values}."
            )
    return items


def _check_files(event: Event, links: Sequence[FileLink], items: Sequence[RequestItem]) -> None:
    if not links:
        return
    if event.source_id is None:
        raise ServiceError("This event has no message, so it has no files to mark.")
    own = set(Attachment.objects.filter(source_id=event.source_id).values_list("pk", flat=True))
    stranger = {link.attachment_id for link in links} - own
    if stranger:
        raise ServiceError("A file named is not one of this production's files.")
    item_ids = {item.pk for item in items}
    for link in links:
        if link.request_item_id is not None and link.request_item_id not in item_ids:
            raise ServiceError("A file is tied to an item that is not this request's.")


@transaction.atomic
def review_production(
    actor: Actor,
    request_id: UUID,
    *,
    event_id: UUID,
    rows: Sequence[ItemReview],
    files: Sequence[FileLink] = (),
    note: str | None = None,
) -> ProductionReview:
    """Record a review. Whether the request becomes fully produced is read off the rows: every
    item `produced` (and at least one item) is the person saying so, and goes through the
    override door with their note; anything else is a partial production by this event."""
    user_id = require_human(actor, "Reviewing a production")
    request = lock_request(request_id)
    event = _event_of(request_id, event_id)
    items = _check_rows(request_id, rows)
    _check_files(event, files, items)
    why = (note or "").strip() or None

    complete = bool(rows) and all(row.disposition == Disposition.PRODUCED for row in rows)
    previous = (
        ProductionReview.objects.filter(request_id=request_id).order_by("-reviewed_at").first()
    )
    if complete:
        change = status_service.override_status(
            actor,
            request_id,
            status_service.FULL_PRODUCTION,
            f"Production review: every item produced.{' ' + why if why else ''}",
        )
        outcome = status_service.FULL_PRODUCTION
    else:
        change = status_service._append_status_change(
            request,
            "partial_production",
            reason="event",
            actor=actor,
            triggering_event=event,
            note=why,
        )
        outcome = "partial_production"

    with writable():
        review = ProductionReview.objects.create(
            request_id=request_id,
            event_id=event.pk,
            reviewed_by_id=user_id,
            reviewed_at=timezone.now(),
            note=why,
            outcome=outcome,
            status_change_id=change.pk,
            supersedes_id=previous.pk if previous else None,
        )
        for row in rows:
            ProductionReviewItem.objects.create(
                review_id=review.pk,
                request_item_id=row.request_item_id,
                disposition=row.disposition,
                exemption_cited=(row.exemption_cited or "").strip() or None,
                cited_where=(row.cited_where or "").strip() or None,
                note=(row.note or "").strip() or None,
            )
        for link in files:
            AttachmentItem.objects.create(
                attachment_id=link.attachment_id,
                request_id=request_id,
                request_item_id=link.request_item_id,
                review_id=review.pk,
            )
    if files:
        assert event.source_id is not None
        sources_service.mark_attachments_as_records(
            actor,
            event.source_id,
            [link.attachment_id for link in files],
            note="Marked as records in a production review.",
        )
    for task in tasks_service.open_tasks(request_id):
        if task.task_type == _REVIEW_TASK:
            tasks_service.complete_task(actor, task.pk)
    core_activity.record(
        actor,
        "reviewed_production",
        f"Reviewed a production on {request.tracking_number} ({outcome.replace('_', ' ')})",
        subject_type="production_review",
        subject_id=review.pk,
        request_id=request_id,
    )
    return review


def reviews_of(request_id: UUID) -> list[ProductionReview]:
    """Newest first. The first one's rows are the dispositions that stand."""
    return list(
        ProductionReview.objects.filter(request_id=request_id)
        .prefetch_related("rows")
        .order_by("-reviewed_at")
    )


def current_dispositions(request_id: UUID) -> dict[UUID, ProductionReviewItem]:
    """Item id -> the newest review's row for it, or empty when nothing has been reviewed."""
    newest = reviews_of(request_id)
    if not newest:
        return {}
    return {row.request_item_id: row for row in newest[0].rows.all()}


def file_links_of(request_id: UUID) -> list[AttachmentItem]:
    return list(
        AttachmentItem.objects.filter(request_id=request_id, voided_at__isnull=True)
        .select_related("attachment", "request_item")
        .order_by("created_at")
    )
