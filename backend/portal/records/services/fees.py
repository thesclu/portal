"""What an agency has assessed, waived, or is owed.

Its own table specifically so it can grow its own status lifecycle without touching requests.
"""

from datetime import date, datetime
from uuid import UUID

from django.db import transaction

from portal.records.actors import Actor
from portal.records.models import Event, Fee, writable
from portal.records.services import edits as edits_service
from portal.records.services.common import (
    UNSET,
    NotFound,
    ServiceError,
    Unset,
    require_of_request,
)


def _get(fee_id: UUID) -> Fee:
    fee = Fee.objects.filter(pk=fee_id).first()
    if fee is None:
        raise NotFound(f"No fee {fee_id}.")
    return fee


@transaction.atomic
def record_fee(
    actor: Actor,
    request_id: UUID,
    *,
    status: str,
    amount_cents: int | None = None,
    source_event_id: UUID | None = None,
    waiver_reason: str | None = None,
    due_at: date | None = None,
    description: str | None = None,
    payment_method: str | None = None,
) -> Fee:
    require_of_request(Event, source_event_id, request_id, "event")
    with writable():
        return Fee.objects.create(
            request_id=request_id,
            status=status,
            amount_cents=amount_cents,
            source_event_id=source_event_id,
            waiver_reason=waiver_reason,
            due_at=due_at,
            description=description,
            payment_method=payment_method,
        )


@transaction.atomic
def update_fee(
    actor: Actor,
    fee_id: UUID,
    *,
    status: str,
    paid_at: datetime | None | Unset = UNSET,
    waiver_reason: str | None | Unset = UNSET,
    note: str,
    description: str | None | Unset = UNSET,
    payment_method: str | None | Unset = UNSET,
) -> Fee:
    """Money is disputable, so every field that moves gets its own audit line.

    `status` is always written. Every other field is PATCH-shaped: leaving it out (`UNSET`) leaves
    it alone, and only an explicit `None` clears it. `paid_at` and `waiver_reason` used to read a
    missing value as "set to None", so a method-only edit of a paid fee erased its paid date; the
    four are now one rule, the same one `PATCH /tasks/{id}` follows for `due_at`.
    """
    if not note or not note.strip():
        raise ServiceError("A fee change needs a note saying why.")
    fee = _get(fee_id)
    changes: dict[str, str | datetime | None] = {"status": status}
    for field, value in (
        ("paid_at", paid_at),
        ("waiver_reason", waiver_reason),
        ("description", description),
        ("payment_method", payment_method),
    ):
        if not isinstance(value, Unset):
            changes[field] = value
    changed = {field: value for field, value in changes.items() if getattr(fee, field) != value}
    if not changed:
        return fee
    Fee.objects.filter(pk=fee_id).update(**changed)
    for field, value in changed.items():
        edits_service.record_audit(
            actor,
            entity_type="fees",
            entity_id=fee_id,
            field=field,
            old_value=getattr(fee, field),
            new_value=value,
            note=note,
        )
    return _get(fee_id)
