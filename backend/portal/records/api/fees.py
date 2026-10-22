from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from portal.records.api.permissions import (
    require_fee_scope,
    require_human_actor,
    require_request_scope,
)
from portal.records.api.schemas import FeeIn, FeeOut, FeePatchIn
from portal.records.api.serializers import fee_out
from portal.records.services import fees as fees_service
from portal.records.services.common import UNSET

router = Router(tags=["fees"])


@router.post("/requests/{request_id}/fees", response={201: FeeOut})
def record_fee(request: HttpRequest, request_id: UUID, payload: FeeIn):
    """An invalid `status` trips fees.status's CHECK constraint and surfaces as
    django.db.IntegrityError -- caught by the API's global handler (see records/api/__init__.py)."""
    require_request_scope(request, request_id)
    actor = require_human_actor(request)
    fee = fees_service.record_fee(
        actor,
        request_id,
        status=payload.status,
        amount_cents=payload.amount_cents,
        source_event_id=payload.source_event_id,
        waiver_reason=payload.waiver_reason,
        due_at=payload.due_at,
        description=payload.description,
        payment_method=payload.payment_method,
    )
    return 201, fee_out(fee)


@router.patch("/fees/{fee_id}", response=FeeOut)
def update_fee(request: HttpRequest, fee_id: UUID, payload: FeePatchIn) -> FeeOut:
    require_fee_scope(request, fee_id)
    actor = require_human_actor(request)
    sent = payload.model_dump(exclude_unset=True)
    fee = fees_service.update_fee(
        actor,
        fee_id,
        status=payload.status,
        paid_at=sent.get("paid_at", UNSET),
        waiver_reason=sent.get("waiver_reason", UNSET),
        note=payload.note,
        description=sent.get("description", UNSET),
        payment_method=sent.get("payment_method", UNSET),
    )
    return fee_out(fee)
