"""Model -> response shapes that more than one router returns.

A task and a fee are returned by their own router AND inside GET /requests/{id}; the docket row by
GET /requests AND by GET /agencies/{id}; an event, a deadline and a status change by their own
routers, by the request page and by the classify and document doors. When each router built its
own copy, a new field had to be added in every copy and forgetting one silently returned null from
the other door. One builder per shape, here.
"""

from portal.records.api.schemas import (
    AttachmentOut,
    DeadlineOut,
    DocketRowOut,
    EventOut,
    FeeOut,
    RequestOut,
    SourceNoteOut,
    SourceOut,
    StatusChangeOut,
    TaskOut,
)
from portal.records.models import (
    Attachment,
    Deadline,
    Event,
    Fee,
    Request,
    Source,
    SourceNote,
    StatusChange,
    Task,
)
from portal.records.services.tasks import TASK_CATEGORY


def task_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.pk,
        request_id=task.request_id,
        task_type=task.task_type,
        description=task.description,
        due_at=task.due_at,
        completed_at=task.completed_at,
        completed_by_id=task.completed_by_id,
        assigned_to_user_id=task.assigned_to_id,
        created_by_user_id=task.created_by_id,
        created_by_system=task.created_by_system,
        category=TASK_CATEGORY.get(task.task_type),
        created_at=task.created_at,
        tracking_number=task.request.tracking_number,
        agency_name=task.request.agency.name,
        request_subject=task.request.subject,
        source_id=task.source_id,
    )


def fee_out(fee: Fee) -> FeeOut:
    return FeeOut(
        id=fee.pk,
        request_id=fee.request_id,
        source_event_id=fee.source_event_id,
        amount_cents=fee.amount_cents,
        status=fee.status,
        waiver_reason=fee.waiver_reason,
        due_at=fee.due_at,
        paid_at=fee.paid_at,
        description=fee.description,
        payment_method=fee.payment_method,
        created_at=fee.created_at,
    )


def docket_row_out(r: Request) -> DocketRowOut:
    """`r` must come from `requests_service.docket`, which sets `days_to_next_deadline` and
    selects the agency; a bare `Request` would report no day count."""
    return DocketRowOut(
        id=r.pk,
        tracking_number=r.tracking_number,
        inbound_alias_email=r.inbound_alias_email,
        subject=r.subject,
        agency_name=r.agency.name,
        current_status=r.current_status_id,
        current_status_changed_at=r.current_status_changed_at,
        next_deadline_at=r.next_deadline_at,
        days_to_next_deadline=r.days_to_next_deadline,
        filed_at=r.filed_at,
        sensitivity=r.sensitivity,
        assigned_to_user_id=r.assigned_to_id,
        item_count=getattr(r, "item_count", 0) or 0,
    )


def event_out(event: Event) -> EventOut:
    return EventOut(
        id=event.pk,
        request_id=event.request_id,
        occurred_at=event.occurred_at,
        event_type=event.event_type_id,
        channel=event.channel,
        direction=event.direction,
        actor_user_id=event.actor_user_id,
        actor_agency_contact_id=event.actor_agency_contact_id,
        display_body=event.display_body,
        source_id=event.source_id,
        related_deadline_id=event.related_deadline_id,
        voided_at=event.voided_at,
        void_reason=event.void_reason,
    )


def deadline_out(deadline: Deadline) -> DeadlineOut:
    return DeadlineOut(
        id=deadline.pk,
        request_id=deadline.request_id,
        request_item_id=deadline.request_item_id,
        deadline_type=deadline.deadline_type,
        legal_basis=deadline.legal_basis,
        provision=deadline.provision,
        basis=deadline.basis,
        due_at=deadline.due_at,
        status=deadline.status,
        source_event_id=deadline.source_event_id,
        satisfied_by_event_id=deadline.satisfied_by_event_id,
        superseded_by_deadline_id=deadline.superseded_by_deadline_id,
        flagged_for_review=deadline.flagged_for_review,
        flag_note=deadline.flag_note,
        rule_ref=deadline.rule_ref,
    )


def status_change_out(change: StatusChange) -> StatusChangeOut:
    return StatusChangeOut(
        id=change.pk,
        request_id=change.request_id,
        status=change.status_id,
        effective_at=change.effective_at,
        triggering_event_id=change.triggering_event_id,
        triggering_deadline_id=change.triggering_deadline_id,
        reason=change.reason,
        note=change.note,
        voided_at=change.voided_at,
    )


def request_out(req: Request) -> RequestOut:
    return RequestOut(
        id=req.pk,
        tracking_number=req.tracking_number,
        agency_id=req.agency_id,
        requester_user_id=req.requester_id,
        inbound_alias_email=req.inbound_alias_email,
        subject=req.subject,
        request_text=req.request_text,
        current_status=req.current_status_id,
        current_status_changed_at=req.current_status_changed_at,
        next_deadline_at=req.next_deadline_at,
        sensitivity=req.sensitivity,
        assigned_to_user_id=req.assigned_to_id,
        filed_at=req.filed_at,
        created_at=req.created_at,
        updated_at=req.updated_at,
        agency_reference_number=req.agency_reference_number,
        auto_followups_enabled=req.auto_followups_enabled,
    )


def source_out(source: Source) -> SourceOut:
    return SourceOut(
        id=source.pk,
        source_type=source.source_type,
        external_ref=source.external_ref,
        message_id=source.message_id,
        raw_metadata=source.raw_metadata,
        raw_storage_url=source.raw_storage_url,
        flagged_for_review=source.flagged_for_review,
        flag_note=source.flag_note,
        flag_reason=source.flag_reason,
        assigned_to_user_id=source.assigned_to_id,
        resolution=source.resolution,
        resolution_note=source.resolution_note,
        reviewed_at=source.reviewed_at,
        reviewed_by_id=source.reviewed_by_id,
        captured_at=source.captured_at,
    )


def attachment_out(attachment: Attachment) -> AttachmentOut:
    """`storage_url` is deliberately not here: it is where the bytes are kept, which is the
    server's business. A client reaches the file through GET /attachments/{id}/download."""
    return AttachmentOut(
        id=attachment.pk,
        source_id=attachment.source_id,
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        role=attachment.role,
        page_count=attachment.page_count,
        checksum_sha256=attachment.checksum_sha256,
        created_at=attachment.created_at,
    )


def note_out(note: SourceNote) -> SourceNoteOut:
    return SourceNoteOut(
        id=note.pk,
        source_id=note.source_id,
        author_user_id=note.author_id,
        body=note.body,
        voided_at=note.voided_at,
        void_reason=note.void_reason,
        created_at=note.created_at,
    )
