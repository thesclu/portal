"""Deadlines are commitments, not a date column. A request races several clocks at once.

More than one active row of the same type is normal, not an edge case: the statute says one
thing and the agency's letter promises another, or a carve-out (body-worn camera footage) has its
own production clock beside the general one. Nothing here reconciles them -- the conflict is
recorded with `flagged_for_review` so a person decides -- and "overdue" always means the EARLIEST
active due date of a type, never the most generous one.
"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from django.db import transaction

from portal.records.actors import Actor
from portal.records.models import (
    Deadline,
    DeadlineDefinition,
    Event,
    RequestItem,
    StatusChange,
    writable,
)
from portal.records.services import audit as audit_service
from portal.records.services import status as status_service
from portal.records.services.common import (
    InvalidTransition,
    NotFound,
    ServiceError,
    lock_request,
    require_human,
    require_of_request,
)


def _get(deadline_id: UUID) -> Deadline:
    deadline = Deadline.objects.filter(pk=deadline_id).first()
    if deadline is None:
        raise NotFound(f"No deadline {deadline_id}.")
    return deadline


def _overdue_status(deadline_type: str) -> str | None:
    """The status a lapsed deadline of this kind moves its request to, or None if it moves none.

    Read from `deadline_definitions.overdue_status` (records_db.0007), where it replaced a dict in
    this module. A kind that is not a row cannot reach here -- the foreign key refuses it at
    insert -- so a missing row is a corrupted database, not a caller error.
    """
    kind = DeadlineDefinition.objects.filter(pk=deadline_type).first()
    if kind is None:
        raise ServiceError(f"Deadline kind {deadline_type!r} is not a deadline_definitions row.")
    return kind.overdue_status_id


@transaction.atomic
def open_deadline(
    actor: Actor,
    request_id: UUID,
    *,
    deadline_type: str,
    due_at: date,
    basis: str,
    legal_basis: str | None = None,
    provision: str | None = None,
    request_item_id: UUID | None = None,
    source_event_id: UUID | None = None,
    rule_ref: str | None = None,
    supersedes: UUID | None = None,
    note: str | None = None,
) -> Deadline:
    """Open a deadline on this request; with `supersedes`, also retire an existing one.

    `supersedes` names a deadline THIS request already has and that is still active. It is checked
    under `lock_request(request_id)` before anything is written, because it is the one place this
    function changes a row other than the one it inserts: a deadline of another request would have
    its status moved (and, through `trg_sync_next_deadline`, that request's `next_deadline_at`
    recomputed) without that request's lock, and a satisfied, waived or missed row would lose what
    it is. The refusals are the kinds `resolve_conflict` gives for the same faults: a deadline of
    another request is a `ServiceError` (422), one that is no longer active an `InvalidTransition`
    (409).
    """
    lock_request(request_id)
    require_of_request(RequestItem, request_item_id, request_id, "item")
    require_of_request(Event, source_event_id, request_id, "event")
    if supersedes is not None:
        superseded = _get(supersedes)
        if superseded.request_id != request_id:
            raise ServiceError(
                "That deadline belongs to a different request; it cannot be superseded here."
            )
        if superseded.status != "active":
            raise InvalidTransition(
                f"Deadline {superseded.pk} is {superseded.status!r}, not active; only an active "
                "deadline can be superseded."
            )
    conflict = (
        Deadline.objects.filter(
            request_id=request_id,
            deadline_type=deadline_type,
            request_item_id=request_item_id,
            status="active",
        )
        .exclude(due_at=due_at)
        .order_by("due_at")
        .first()
    )
    flag_note = note
    if conflict is not None:
        flag_note = (
            f"due {due_at.isoformat()} conflicts with active deadline {conflict.pk} "
            f"due {conflict.due_at.isoformat()}"
        )
        if note:
            flag_note = f"{flag_note}; {note}"
    with writable():
        deadline = Deadline.objects.create(
            request_id=request_id,
            request_item_id=request_item_id,
            deadline_type=deadline_type,
            legal_basis=legal_basis,
            provision=provision,
            basis=basis,
            due_at=due_at,
            source_event_id=source_event_id,
            rule_ref=rule_ref,
            flagged_for_review=conflict is not None,
            flag_note=flag_note,
        )
    if supersedes is not None:
        Deadline.objects.filter(pk=supersedes).update(
            status="superseded", superseded_by_deadline_id=deadline.pk
        )
    return deadline


@transaction.atomic
def satisfy_deadline(actor: Actor, deadline_id: UUID, satisfied_by_event_id: UUID) -> Deadline:
    deadline = _get(deadline_id)
    lock_request(deadline.request_id)
    require_of_request(Event, satisfied_by_event_id, deadline.request_id, "event")
    Deadline.objects.filter(pk=deadline.pk).update(
        status="satisfied", satisfied_by_event_id=satisfied_by_event_id
    )
    return _get(deadline_id)


@transaction.atomic
def waive_deadline(actor: Actor, deadline_id: UUID, note: str) -> Deadline:
    require_human(actor, "Waiving a deadline")
    if not note or not note.strip():
        raise InvalidTransition("Waiving a deadline needs a note saying why.")
    deadline = _get(deadline_id)
    lock_request(deadline.request_id)
    Deadline.objects.filter(pk=deadline.pk).update(status="waived", flag_note=note)
    return _get(deadline_id)


@transaction.atomic
def mark_deadline_missed(actor: Actor, deadline_id: UUID) -> StatusChange | None:
    """The overdue sweep's write. Returns the status row it appended, or None if none was due.

    The deadline is read once to learn which request to lock and AGAIN under the lock, and nothing
    happens unless it is still `active` there. A sweep selects past-due rows and then calls this one
    row at a time, so by the time it holds the lock a person may have satisfied or waived the
    deadline; marking it `missed` anyway would overwrite what they did and append an overdue status
    after the answer arrived. Satisfied, waived, superseded and already-missed rows are left alone.
    """
    request = lock_request(_get(deadline_id).request_id)
    deadline = _get(deadline_id)
    if deadline.status != "active":
        return None
    Deadline.objects.filter(pk=deadline.pk).update(status="missed")
    overdue = _overdue_status(deadline.deadline_type)
    if overdue is None:
        # A kind whose lapse moves no status (a fee response, an "other"): it is missed, and that
        # is the whole of it. Nothing is appended and the request's status is left alone.
        return None
    # A closed, denied or withdrawn request has stopped racing anything; moving it back out of a
    # terminal status because an old clock lapsed would misrepresent it.
    if request.current_status.is_terminal or request.current_status_id == overdue:
        return None
    return status_service._append_status_change(
        request,
        overdue,
        reason="deadline_expired",
        actor=actor,
        triggering_deadline=deadline,
    )


@transaction.atomic
def resolve_conflict(
    actor: Actor, keep_deadline_id: UUID, drop_deadline_id: UUID, note: str
) -> Deadline:
    """The "Resolve due date" dialog: two active dates for one thing, a person says which stands.

    `open_deadline` records a disagreement (the statute says one date, the agency's letter
    another) as two active rows and a flag, and reconciles nothing. This is the reconciliation, and
    it is a person's act: the dropped row becomes `superseded` pointing at the kept one (never
    deleted -- the date that was once believed stays on the record), the kept row's flag clears,
    and both changes are audited.

    The two rows are read once to learn which request to lock and AGAIN under the lock, so a
    deadline satisfied or waived by another transaction in between is judged by what it now is.
    """
    require_human(actor, "Resolving a deadline conflict")
    if not note or not note.strip():
        raise ServiceError("Resolving a deadline conflict needs a note saying why.")
    if keep_deadline_id == drop_deadline_id:
        raise ServiceError("Keep and drop name the same deadline; a conflict is between two.")
    lock_request(_get(keep_deadline_id).request_id)
    keep = _get(keep_deadline_id)
    drop = _get(drop_deadline_id)
    if drop.request_id != keep.request_id:
        raise ServiceError("Those two deadlines belong to different requests.")
    if (keep.deadline_type, keep.request_item_id) != (drop.deadline_type, drop.request_item_id):
        raise ServiceError(
            "Those two deadlines are not about the same thing: a conflict is between two dates "
            "of the same kind for the same request item."
        )
    for row in (keep, drop):
        if row.status != "active":
            raise InvalidTransition(f"Deadline {row.pk} is {row.status!r}, not active.")

    Deadline.objects.filter(pk=drop.pk).update(
        status="superseded", superseded_by_deadline_id=keep.pk
    )
    Deadline.objects.filter(pk=keep.pk).update(flagged_for_review=False, flag_note=None)
    changes: list[tuple[Deadline, str, object, object]] = [
        (drop, "status", drop.status, "superseded"),
        (drop, "superseded_by_deadline_id", None, keep.pk),
    ]
    if keep.flagged_for_review:
        changes.append((keep, "flagged_for_review", True, False))
    if keep.flag_note is not None:
        changes.append((keep, "flag_note", keep.flag_note, None))
    for row, field, old, new in changes:
        audit_service.record_audit(
            actor,
            entity_type="deadlines",
            entity_id=row.pk,
            field=field,
            old_value=old,
            new_value=new,
            note=note,
        )
    return _get(keep_deadline_id)


def earliest_active(request_id: UUID, deadline_type: str) -> Deadline | None:
    return (
        Deadline.objects.filter(request_id=request_id, deadline_type=deadline_type, status="active")
        .order_by("due_at")
        .first()
    )


def active_deadlines(request_id: UUID) -> Sequence[Deadline]:
    return list(Deadline.objects.filter(request_id=request_id, status="active").order_by("due_at"))
