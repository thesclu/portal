"""The chokepoint. Every status row the ledger will ever hold is inserted by one function here.

Non-negotiable #4 -- only a human may declare a request fully produced -- lives in
`_append_status_change` and nowhere else. The database is only a partial second line: its
`full_production_requires_human` CHECK reads `status <> 'production_received' or reason =
'manual_override'`, which a system actor satisfies simply by passing that reason, with
`changed_by_user_id` left null. What actually stops automation is the `actor.is_human` test below
plus the `writable()` ban that keeps every other code path out of this table.
"""

from collections.abc import Sequence
from uuid import UUID

from django.db import transaction

from portal.core.services import activity as core_activity
from portal.records import roles
from portal.records.actors import Actor
from portal.records.models import (
    Deadline,
    Event,
    Request,
    StatusChange,
    StatusDefinition,
    writable,
)
from portal.records.services import tasks
from portal.records.services.common import (
    InvalidTransition,
    NotAllowed,
    ServiceError,
    lock_request,
    next_effective_at,
    require_human,
)

FULL_PRODUCTION = "production_received"
NO_RECORDS_CLAIMED = "no_records_claimed"
# P20h (records_db.0022): an intern's letter waiting for an approver. With `pending_filing`, the
# statuses of a request that is reserved and not yet out the door.
WAITING_FOR_APPROVAL = "waiting_for_approval"
PRE_FILING = ("pending_filing", WAITING_FOR_APPROVAL)

_TRIGGER_FIELD = {"event": "triggering_event", "deadline_expired": "triggering_deadline"}


def _append_status_change(
    request: Request,
    status_code: str,
    *,
    reason: str,
    actor: Actor,
    triggering_event: Event | None = None,
    triggering_deadline: Deadline | None = None,
    note: str | None = None,
    allow_system_manual: bool = False,
) -> StatusChange:
    if not StatusDefinition.objects.filter(pk=status_code).exists():
        raise ServiceError(f"Unknown status {status_code!r}; it is not a status_definitions row.")

    if reason not in ("event", "deadline_expired", "manual_override"):
        raise ServiceError(f"Unknown status reason {reason!r}.")
    given = {"event": triggering_event, "deadline_expired": triggering_deadline}
    for key, trigger in given.items():
        expected = reason == key
        if expected and trigger is None:
            raise InvalidTransition(f"reason={reason!r} needs its {_TRIGGER_FIELD[key]}.")
        if not expected and trigger is not None:
            raise InvalidTransition(f"reason={reason!r} must not carry a {_TRIGGER_FIELD[key]}.")

    if status_code == FULL_PRODUCTION and not (actor.is_human and reason == "manual_override"):
        raise NotAllowed(
            f"{FULL_PRODUCTION!r} is written only by a person, only as a manual override; "
            f"actor={actor!r} reason={reason!r}."
        )
    if reason == "manual_override" and not actor.is_human and not allow_system_manual:
        raise NotAllowed(f"A manual override needs a human actor; got {actor.system!r}.")

    with writable():
        change = StatusChange.objects.create(
            request_id=request.pk,
            status_id=status_code,
            effective_at=next_effective_at(request.pk),
            triggering_event=triggering_event,
            triggering_deadline=triggering_deadline,
            reason=reason,
            note=note,
        )

    # The insert trigger recomputes requests.current_status by `effective_at desc`. If the cache
    # did not land on the row just written, next_effective_at failed to outrank an existing row
    # and this transaction is about to leave the ledger and its cache disagreeing.
    landed = Request.objects.filter(pk=request.pk).values_list("current_status", flat=True).first()
    if landed != status_code:
        raise ServiceError(
            f"current_status is {landed!r} after appending {status_code!r} to request "
            f"{request.tracking_number}; the effective_at tiebreak did not hold."
        )

    core_activity.record(
        actor,
        "status_changed",
        f"{request.tracking_number} became {status_code.replace('_', ' ')}",
        subject_type="request",
        subject_id=request.pk,
        request_id=request.pk,
        data={"status": status_code, "reason": reason},
    )
    if status_code == NO_RECORDS_CLAIMED and not any(
        task.task_type == "review_no_records_claim" for task in tasks.open_tasks(request.pk)
    ):
        tasks.create_task(
            actor,
            request.pk,
            "review_no_records_claim",
            "Agency claims no responsive records; verify the search and decide the follow-up.",
        )
    return change


@transaction.atomic
def override_status(actor: Actor, request_id: UUID, status_code: str, note: str) -> StatusChange:
    """A person correcting the ledger by hand. The only door to `production_received`.

    P20h: `waiting_for_approval` is never set by hand, by anyone: only submitting a letter for
    approval sets it (`mail/services/approval.py`), so the status always means a review is open.
    And an intern does not move a request that is not yet filed, nor call one filed: their letter
    leaves the portal only through approval, and a hand-set status would say otherwise."""
    user_id = require_human(actor, "Overriding a status")
    if not note or not note.strip():
        raise ServiceError("An override needs a note saying why.")
    if status_code == WAITING_FOR_APPROVAL:
        raise InvalidTransition("Waiting for approval is set by submitting a letter for approval.")
    request = lock_request(request_id)
    if roles.needs_approval(user_id) and (
        request.current_status_id in PRE_FILING or status_code == "filed"
    ):
        raise NotAllowed(
            "An intern's request is filed through approval; its status is not changed by hand."
        )
    return _append_status_change(
        request, status_code, reason="manual_override", actor=actor, note=note
    )


def status_history(request_id: UUID) -> Sequence[StatusChange]:
    """Every row, voided included -- this is the ledger, not the current state."""
    return list(StatusChange.objects.filter(request_id=request_id).order_by("effective_at"))
