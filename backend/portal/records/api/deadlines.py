from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from portal.records.api.permissions import (
    require_deadline_scope,
    require_human_actor,
    require_records_admin,
    require_request_scope,
)
from portal.records.api.schemas import (
    DeadlineOut,
    OpenDeadlineIn,
    ResolveDeadlineIn,
    SatisfyDeadlineIn,
    WaiveDeadlineIn,
)
from portal.records.api.serializers import deadline_out
from portal.records.services import deadlines as deadlines_service

router = Router(tags=["deadlines"])


@router.post("/requests/{request_id}/deadlines", response={201: DeadlineOut})
def open_deadline(request: HttpRequest, request_id: UUID, payload: OpenDeadlineIn):
    """An unknown `deadline_type` trips the foreign key to deadline_definitions and surfaces as
    IntegrityError, mapped to 422 by the API's global handler (HANDOFF convention 9).

    `supersedes` replaces an EXISTING deadline's date with this new one, which is exactly what
    POST /deadlines/{id}/supersede does, so it needs the same administrator. Guarding that door
    and leaving this one open would make the rule a formality."""
    require_request_scope(request, request_id)
    if payload.supersedes is not None:
        require_records_admin(request)
    actor = require_human_actor(request)
    deadline = deadlines_service.open_deadline(
        actor,
        request_id,
        deadline_type=payload.deadline_type,
        due_at=payload.due_at,
        basis=payload.basis,
        legal_basis=payload.legal_basis,
        provision=payload.provision,
        request_item_id=payload.request_item_id,
        source_event_id=payload.source_event_id,
        rule_ref=payload.rule_ref,
        supersedes=payload.supersedes,
        note=payload.note,
    )
    return 201, deadline_out(deadline)


@router.post("/deadlines/{deadline_id}/satisfy", response=DeadlineOut)
def satisfy(request: HttpRequest, deadline_id: UUID, payload: SatisfyDeadlineIn) -> DeadlineOut:
    require_deadline_scope(request, deadline_id)
    actor = require_human_actor(request)
    deadline = deadlines_service.satisfy_deadline(actor, deadline_id, payload.satisfied_by_event_id)
    return deadline_out(deadline)


@router.post("/deadlines/{deadline_id}/waive", response=DeadlineOut)
def waive(request: HttpRequest, deadline_id: UUID, payload: WaiveDeadlineIn) -> DeadlineOut:
    require_deadline_scope(request, deadline_id)
    actor = require_human_actor(request)
    deadline = deadlines_service.waive_deadline(actor, deadline_id, payload.note)
    return deadline_out(deadline)


@router.post("/deadlines/{deadline_id}/supersede", response={201: DeadlineOut})
def supersede(request: HttpRequest, deadline_id: UUID, payload: OpenDeadlineIn):
    """`payload.supersedes`, if the client sent one, is ignored -- the path id is what this new
    deadline supersedes. There is no service door that marks an EXISTING deadline superseded by
    another existing one; opening a new deadline with `supersedes=<old id>` is the only path.

    One of the two doors that move an existing deadline's date, so an administrator's."""
    old = require_deadline_scope(request, deadline_id)
    require_records_admin(request)
    actor = require_human_actor(request)
    deadline = deadlines_service.open_deadline(
        actor,
        old.request_id,
        deadline_type=payload.deadline_type,
        due_at=payload.due_at,
        basis=payload.basis,
        legal_basis=payload.legal_basis,
        provision=payload.provision,
        request_item_id=payload.request_item_id,
        source_event_id=payload.source_event_id,
        rule_ref=payload.rule_ref,
        supersedes=deadline_id,
        note=payload.note,
    )
    return 201, deadline_out(deadline)


@router.post("/deadlines/{deadline_id}/resolve", response=DeadlineOut)
def resolve(request: HttpRequest, deadline_id: UUID, payload: ResolveDeadlineIn) -> DeadlineOut:
    """The "Resolve due date" dialog. The deadline in the path is the date that STANDS; the body
    names the competing one, which becomes superseded by it. Returns the deadline that stands."""
    require_deadline_scope(request, deadline_id)
    actor = require_human_actor(request)
    kept = deadlines_service.resolve_conflict(
        actor, deadline_id, payload.drop_deadline_id, payload.note
    )
    return deadline_out(kept)
