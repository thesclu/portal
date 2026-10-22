"""The Records screen (BUILD_PLAN P12): every produced file across the requests a person may see.

A row is an attachment whose role is `produced_record`. Which request it answers comes from its
`attachment_items` row when a review tied it to one (and, through that, which item); otherwise
from the live event that carries its message, which is how files marked as records before P12
still appear. Searching inside files is not here: it needs extracted text.
"""

from dataclasses import dataclass
from uuid import UUID

from portal.records.models import (
    Attachment,
    AttachmentItem,
    AttachmentRole,
    Event,
    RequestItem,
)
from portal.records.services import access
from portal.records.services.access import Viewer


@dataclass(frozen=True)
class ProducedRow:
    attachment: Attachment
    request_id: UUID
    tracking_number: str
    subject: str
    agency_id: UUID
    agency_name: str
    item: RequestItem | None


def produced_records(
    viewer: Viewer,
    *,
    agency_id: UUID | None = None,
    request_id: UUID | None = None,
    q: str | None = None,
) -> list[ProducedRow]:
    visible = access.visible_q(viewer, "request__")
    rows: dict[tuple[UUID, UUID, UUID | None], ProducedRow] = {}

    links = (
        AttachmentItem.objects.filter(voided_at__isnull=True)
        .filter(visible)
        .filter(attachment__role=AttachmentRole.PRODUCED_RECORD)
        .select_related("attachment", "request__agency", "request_item")
    )
    for link in links:
        key = (link.attachment_id, link.request_id, link.request_item_id)
        rows[key] = ProducedRow(
            link.attachment,
            link.request_id,
            link.request.tracking_number,
            link.request.subject,
            link.request.agency_id,
            link.request.agency.name,
            link.request_item,
        )

    # Files marked as records without a review: on every live event carrying their message.
    linked_attachments = {key[0] for key in rows}
    events = (
        Event.objects.filter(voided_at__isnull=True, source_id__isnull=False)
        .filter(visible)
        .filter(source__attachments__role=AttachmentRole.PRODUCED_RECORD)
        .select_related("request__agency")
        .distinct()
    )
    for event in events:
        assert event.source_id is not None
        for attachment in Attachment.objects.filter(
            source_id=event.source_id, role=AttachmentRole.PRODUCED_RECORD
        ):
            if attachment.pk in linked_attachments:
                continue
            key = (attachment.pk, event.request_id, None)
            rows.setdefault(
                key,
                ProducedRow(
                    attachment,
                    event.request_id,
                    event.request.tracking_number,
                    event.request.subject,
                    event.request.agency_id,
                    event.request.agency.name,
                    None,
                ),
            )

    out = list(rows.values())
    if agency_id is not None:
        out = [r for r in out if r.agency_id == agency_id]
    if request_id is not None:
        out = [r for r in out if r.request_id == request_id]
    if q:
        needle = q.strip().lower()
        out = [
            r
            for r in out
            if needle in r.attachment.filename.lower()
            or needle in r.subject.lower()
            or needle in r.tracking_number.lower()
            or (r.item is not None and needle in r.item.description.lower())
        ]
    out.sort(key=lambda r: (r.attachment.created_at, r.attachment.filename), reverse=True)
    return out
