from uuid import UUID

from django.http import HttpRequest
from ninja import Query, Router

from portal.records.api.permissions import (
    require_event_scope,
    require_human_actor,
    require_may_release,
    require_named_source_scope,
    require_request_scope,
    require_team,
)
from portal.records.api.schemas import (
    ClassificationKindOut,
    ClassifyIn,
    ClassifyOut,
    ClassifyPreviewOut,
    DeadlineKindOut,
    DefinitionsOut,
    EventDefOut,
    EventIn,
    EventOut,
    PlannedTaskOut,
    StatusDefOut,
    TaskTypeOut,
    VoidIn,
)
from portal.records.api.serializers import (
    attachment_out,
    deadline_out,
    event_out,
    fee_out,
    request_out,
    status_change_out,
    task_out,
)
from portal.records.models import DeadlineDefinition, EventDefinition, StatusDefinition
from portal.records.services import classify as classify_service
from portal.records.services import events as events_service
from portal.records.services import inbox as inbox_service
from portal.records.services.sources import FLAG_REASONS, RESOLUTIONS
from portal.records.services.tasks import TASK_CATEGORIES, TASK_CATEGORY, TASK_TYPES

router = Router(tags=["events"])


_WRITTEN_CHANNELS = ("email", "mail", "portal")


def _says_something_went_out(payload: EventIn) -> bool:
    return (
        payload.event_type == "submission"
        or payload.status == "filed"
        or (payload.direction == "outbound" and payload.channel in _WRITTEN_CHANNELS)
    )


@router.post("/requests/{request_id}/events", response={201: EventOut})
def record_event(request: HttpRequest, request_id: UUID, payload: EventIn):
    """`status=production_received` here is refused by the chokepoint -- reason is always
    'event', never 'manual_override', so `_append_status_change` raises NotAllowed (403).
    Non-negotiable #4's one legal door is POST /requests/{id}/status."""
    require_request_scope(request, request_id)
    if _says_something_went_out(payload):
        # Recording that a letter or message of ours went out, or that the request was filed, is
        # not an intern's to do by hand: theirs leave through approval (P20h). A call is not a
        # letter and stays theirs to log.
        require_may_release(request)
    require_named_source_scope(request, payload.source_id)
    actor = require_human_actor(request)
    event = events_service.record_event(
        actor,
        request_id,
        event_type=payload.event_type,
        channel=payload.channel,
        direction=payload.direction,
        occurred_at=payload.occurred_at,
        display_body=payload.display_body,
        source_id=payload.source_id,
        actor_user_id=payload.actor_user_id,
        actor_agency_contact_id=payload.actor_agency_contact_id,
        related_deadline_id=payload.related_deadline_id,
        status=payload.status,
        note=payload.note,
    )
    return 201, event_out(event)


@router.post("/events/{event_id}/void", response=EventOut)
def void_event(request: HttpRequest, event_id: UUID, payload: VoidIn) -> EventOut:
    require_event_scope(request, event_id)
    actor = require_human_actor(request)
    event = events_service.void_event(actor, event_id, payload.reason)
    return event_out(event)


@router.get("/definitions", response=DefinitionsOut)
def definitions(request: HttpRequest) -> DefinitionsOut:
    """Both tables' own `sort_order` column exists precisely for display order, and every sibling
    list endpoint in this API orders explicitly -- so this one does too, instead of relying on the
    models' `Meta.ordering` to keep doing it invisibly."""
    require_team(request)
    statuses = [
        StatusDefOut(code=s.code, label=s.label, sort_order=s.sort_order, is_terminal=s.is_terminal)
        for s in StatusDefinition.objects.order_by("sort_order")
    ]
    event_types = [
        EventDefOut(code=e.code, label=e.label, sort_order=e.sort_order)
        for e in EventDefinition.objects.order_by("sort_order")
    ]
    deadline_kinds = [
        DeadlineKindOut(
            code=d.code,
            label=d.label,
            sort_order=d.sort_order,
            overdue_status=d.overdue_status_id,
        )
        for d in DeadlineDefinition.objects.order_by("sort_order")
    ]
    return DefinitionsOut(
        statuses=statuses,
        event_types=event_types,
        deadline_kinds=deadline_kinds,
        # The three below are Python tuples checked in the services (BUILD_PLAN rule 5), not
        # tables, so a client learns them here instead of keeping its own copy.
        task_types=[TaskTypeOut(code=t, category=TASK_CATEGORY[t]) for t in TASK_TYPES],
        task_categories=list(TASK_CATEGORIES),
        flag_reasons=list(FLAG_REASONS),
        resolutions=list(RESOLUTIONS),
        inbox_buckets=list(inbox_service.BUCKETS),
        # The classification table itself, so the menu and the dialog are never a second copy.
        classifications=[
            ClassificationKindOut(
                kind=k.key,
                label=k.label,
                event_type=k.event_type,
                status=k.status,
                task_type=k.task_type,
                through_override=k.via_override,
                note_required=k.note_required,
                takes_fee=k.takes_fee,
                takes_records=k.takes_records,
            )
            for k in classify_service.KINDS
        ],
    )


@router.get("/events/{event_id}/classify", response=ClassifyPreviewOut)
def preview_classify(
    request: HttpRequest, event_id: UUID, kind: str = Query(..., description="A classification.")
) -> ClassifyPreviewOut:
    """What "Record as..." will do to this event's request, before anything is written. Driven by
    the same table as the write, so it shows what the write then does. Valid kinds are GET
    /definitions' `classifications`."""
    require_event_scope(request, event_id)
    plan = classify_service.preview_classification(event_id, kind)
    return ClassifyPreviewOut(
        kind=plan.kind.key,
        label=plan.kind.label,
        event_id=plan.event_id,
        request_id=plan.request_id,
        event_type_from=plan.event_type_from,
        event_type_to=plan.event_type_to,
        status_from=plan.status_from,
        status_to=plan.status_to,
        status_reason=plan.status_reason,
        deadlines=[deadline_out(d) for d in plan.deadlines],
        task=(
            PlannedTaskOut(
                task_type=plan.task.task_type,
                description=plan.task.description,
                due_at=plan.task.due_at,
                opened_by=plan.task.opened_by,
            )
            if plan.task is not None
            else None
        ),
        note_required=plan.kind.note_required,
        takes_fee=plan.kind.takes_fee,
        takes_records=plan.kind.takes_records,
    )


@router.post("/events/{event_id}/classify", response=ClassifyOut)
def classify(request: HttpRequest, event_id: UUID, payload: ClassifyIn) -> ClassifyOut:
    """Record an inbound event as one of the classifications, in one transaction.

    "Production - complete" is the one row that cannot ride the event: it is written as a manual
    override, which needs a note. The service is atomic, so a refusal at any step leaves the
    request exactly as it was."""
    require_event_scope(request, event_id)
    actor = require_human_actor(request)
    fee = payload.fee
    result = classify_service.classify(
        actor,
        event_id,
        payload.kind,
        note=payload.note,
        fee=(
            classify_service.FeeInput(fee.amount_cents, fee.description, fee.due_at)
            if fee is not None
            else None
        ),
        record_attachment_ids=payload.record_attachment_ids,
        agency_reference_number=payload.agency_reference_number,
        task_due_at=payload.task_due_at,
    )
    return ClassifyOut(
        event=event_out(result.event),
        request=request_out(result.request),
        status_change=(
            status_change_out(result.status_change) if result.status_change is not None else None
        ),
        fee=fee_out(result.fee) if result.fee is not None else None,
        task=task_out(result.task) if result.task is not None else None,
        attachments=[attachment_out(a) for a in result.attachments],
    )
