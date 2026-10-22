"""Corrections, and the audit row that makes them defensible.

The raw source is never editable -- it is the evidence. What is editable is the cleaned display
copy and a handful of fields that are learned rather than observed (the agency's own reference
number, a deadline's date). Every one of them writes an audit_log row saying who, what and why;
"why" is the whole point of that table.
"""

from typing import Any
from uuid import UUID

from django.db import transaction

from portal.records.actors import Actor
from portal.records.models import (
    AuditLog,
    Deadline,
    Event,
    Request,
    RequestItem,
)
from portal.records.services import access as access_service
from portal.records.services import status as status_service

# Re-exported: `record_audit` began in this module and fees.py and directory.py still reach it as
# `edits_service.record_audit`. It moved to audit.py to break the edits -> status -> tasks loop.
from portal.records.services.audit import record_audit
from portal.records.services.common import (
    NotFound,
    ServiceError,
    lock_request,
    require_human,
)

EDITABLE = {
    "requests": {
        "agency_reference_number",
        "subject",
        "request_text",
        "auto_followups_enabled",
        "sensitivity",
    },
    "events": {"display_body", "event_type", "occurred_at"},
    "deadlines": {"due_at", "legal_basis", "provision"},
    # sort_order: records_db.0005 grants UPDATE (sort_order) on records.request_items for the
    # drafting fan-out (filing.py copies a draft's own order into it), but granted nothing about
    # who else may change it after the fact. Listing it here is what makes that grant reachable
    # through the one audited edit door instead of being a standing, unaudited write nobody can
    # reach -- an unreachable grant is a standing invitation, not a safety margin.
    #
    # provision: records_db.0006 grants UPDATE (provision) so an item typed with the wrong
    # carve-out on a request that is not yet filed can be corrected, and file_request now defaults
    # an item's clock from it. Same reasoning as sort_order. An edit made after filing changes the
    # label only: deadlines already opened are not recomputed, and the audit row is the trail.
    "request_items": {"item_number", "description", "sort_order", "provision"},
}

_MODELS: dict[str, Any] = {
    "requests": Request,
    "events": Event,
    "deadlines": Deadline,
    "request_items": RequestItem,
}


def request_id_of(entity_type: str, entity_id: UUID) -> UUID | None:
    """The request an editable row belongs to. None for a type this door does not edit, which
    `edit_with_audit` then refuses; `NotFound` for a row that does not exist. Reads the same table
    of models the edit itself uses, so the two cannot disagree about what is editable."""
    model = _MODELS.get(entity_type)
    if model is None:
        return None
    row = model.objects.filter(pk=entity_id).first()
    if row is None:
        raise NotFound(f"No {entity_type} row {entity_id}.")
    return row.pk if entity_type == "requests" else row.request_id


@transaction.atomic
def edit_with_audit(
    actor: Actor,
    entity_type: str,
    entity_id: UUID,
    field: str,
    new_value: Any,
    note: str,
    *,
    status: str | None = None,
) -> AuditLog:
    require_human(actor, "Editing a record")
    if not note or not note.strip():
        raise ServiceError("An edit needs a note saying why.")
    if field not in EDITABLE.get(entity_type, set()):
        raise ServiceError(f"{entity_type}.{field} is not editable.")

    model = _MODELS[entity_type]
    column = "event_type_id" if (entity_type, field) == ("events", "event_type") else field
    row = model.objects.filter(pk=entity_id).first()
    if row is None:
        raise NotFound(f"No {entity_type} row {entity_id}.")
    if entity_type == "deadlines" and field == "due_at":
        # due_at is in the schema trigger's `update of status, due_at` list -- an edit here
        # recomputes requests.next_deadline_at exactly like satisfy_deadline/mark_deadline_missed,
        # and races the same way without the lock.
        lock_request(row.request_id)
    changes_visibility = (entity_type, field) == ("requests", "sensitivity")
    if changes_visibility:
        # Who may see the request is about to change. The lock serialises this with grants and
        # revokes, and the row is re-read under it so `old_value` is what is really being replaced.
        row = lock_request(entity_id)
        access_service.require_may_change_sensitivity(actor, row)
    old_value = getattr(row, column)
    model.objects.filter(pk=entity_id).update(**{column: new_value})
    if changes_visibility:
        # Same transaction: making a request privileged puts the editor on its list, so it is
        # never privileged with nobody on it (BUILD_PLAN P9, rule 4).
        access_service.on_sensitivity_changed(actor, entity_id, old_value, new_value)

    audit = record_audit(
        actor,
        entity_type=entity_type,
        entity_id=entity_id,
        field=field,
        old_value=old_value,
        new_value=new_value,
        note=note,
    )
    if entity_type == "events" and status is not None:
        # How a person classifies an auto-matched inbound message: the event already exists, and
        # saying what it MEANS is a status row triggered by it.
        event = Event.objects.get(pk=entity_id)
        status_service._append_status_change(
            lock_request(event.request_id),
            status,
            reason="event",
            actor=actor,
            triggering_event=event,
            note=note,
        )
    return audit
