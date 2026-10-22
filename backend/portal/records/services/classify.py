"""What a person says an inbound event MEANS: the "Record as..." and "Log a response" menus.

An inbound event starts life as plain `correspondence` (mail that landed on a request, or something
a person just logged). Saying "this is an acknowledgment" or "this is a fee estimate" changes the
ledger in up to four ways, and `KINDS` below is the one table that says which. Both
`preview_classification` (what the dialog shows before anything is written) and `classify` (what
happens when the person presses the button) are driven by it through `_plan`, so they cannot
disagree about what a row does.

    kind                 event type        status appended        task opened
    acknowledgment       acknowledgment    acknowledged           -
    determination        determination     determination_issued   -
    extension            extension_notice  -                      -
    fee_estimate         fee_notice        fee_estimate_received  decide_fee
    production_partial   production        partial_production     review_production
    production_complete  production        production_received    -  (through override_status)
    no_records           determination     no_records_claimed     the status chokepoint's own
    denial               determination     denied                 -
    clarification        clarification     clarification_requested  reply_to_clarification

Three rules carry the weight:

* **"Production -- complete" cannot ride the event.** The schema allows `production_received` only
  as a manual override with no triggering event, so that row goes through `override_status`: a
  person, a note that says why, `reason = manual_override`. `classify` is human only as a whole, and
  independently the status chokepoint (`status._append_status_change`) refuses a system actor for
  that status, so there are two locks on the one door.
* **An extension notice computes no date.** The person enters the agency's date through the "add a
  deadline" door. Nothing here opens, moves, supersedes or satisfies a deadline: `classify` leaves
  every deadline exactly as it found it, which is why the preview can show them "as they stand".
* **A task is opened with no due date unless the person gives one.** `task_due_at` is that.

Everything runs in one transaction under `lock_request`, composed from the services that already
own each write (`edits`, `status`, `fees`, `tasks`, `sources`), so a refusal at the last step leaves
the first untouched. Validation happens before the first write; the transaction is the backstop.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from django.db import transaction

from portal.records.actors import Actor
from portal.records.models import Attachment, Deadline, Event, Fee, Request, StatusChange, Task
from portal.records.services import deadlines as deadlines_service
from portal.records.services import edits as edits_service
from portal.records.services import events as events_service
from portal.records.services import fees as fees_service
from portal.records.services import sources as sources_service
from portal.records.services import status as status_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import (
    InvalidTransition,
    ServiceError,
    lock_request,
    require_human,
)


@dataclass(frozen=True)
class Kind:
    """One row of the classification table."""

    key: str
    label: str
    event_type: str
    # The status the request moves to. None: the row changes no status.
    status: str | None = None
    # True: `status` is written through `override_status` (human, note required), not as the
    # event's own trigger. Only `production_received` is.
    via_override: bool = False
    task_type: str | None = None
    task_description: str | None = None
    takes_fee: bool = False
    takes_records: bool = False
    note_required: bool = False


# The nine rows. Their keys are what `GET /events/{id}/classify?kind=` takes; their labels are the
# menu items. Adding a row here adds it to the preview, the write, GET /definitions and the tests.
KINDS: tuple[Kind, ...] = (
    Kind("acknowledgment", "Acknowledgment", "acknowledgment", "acknowledged"),
    Kind("determination", "Determination", "determination", "determination_issued"),
    Kind("extension", "Extension of time", "extension_notice"),
    Kind(
        "fee_estimate",
        "Fee estimate",
        "fee_notice",
        "fee_estimate_received",
        task_type="decide_fee",
        task_description="Decide whether to pay or dispute the fee estimate.",
        takes_fee=True,
    ),
    Kind(
        "production_partial",
        "Production — partial",
        "production",
        "partial_production",
        task_type="review_production",
        task_description="Review the records the agency produced.",
        takes_records=True,
    ),
    Kind(
        "production_complete",
        "Production — complete",
        "production",
        status_service.FULL_PRODUCTION,
        via_override=True,
        takes_records=True,
        note_required=True,
    ),
    Kind("no_records", "No records", "determination", status_service.NO_RECORDS_CLAIMED),
    Kind("denial", "Denial", "determination", "denied"),
    Kind(
        "clarification",
        "Asks to clarify or narrow",
        "clarification",
        "clarification_requested",
        task_type="reply_to_clarification",
        task_description="Reply to the agency's request to clarify or narrow the request.",
    ),
)
_BY_KEY = {kind.key: kind for kind in KINDS}

# The task the status chokepoint opens by itself for this status, when none is open already.
_CHOKEPOINT_TASK = {status_service.NO_RECORDS_CLAIMED: "review_no_records_claim"}


@dataclass(frozen=True)
class FeeInput:
    """What a fee estimate says, as far as the person has it. Every field may be missing."""

    amount_cents: int | None = None
    description: str | None = None
    due_at: date | None = None


@dataclass(frozen=True)
class PlannedTask:
    task_type: str
    description: str
    due_at: datetime | None
    # `classify` opens it, or the status chokepoint does when the status is appended.
    opened_by: Literal["classify", "status_chokepoint"]


@dataclass(frozen=True)
class Preview:
    """What recording this event as this kind will do, before it is done."""

    kind: Kind
    event_id: UUID
    request_id: UUID
    event_type_from: str
    event_type_to: str
    status_from: str
    # None: no status is appended (an extension notice). `status_reason` is how it is written.
    status_to: str | None
    status_reason: Literal["event", "manual_override"] | None
    # Every active deadline of the request, as it stands. Classifying changes none of them.
    deadlines: list[Deadline]
    # None: no task is opened (for no_records: because one is already open).
    task: PlannedTask | None


@dataclass(frozen=True)
class Classification:
    """What `classify` did."""

    event: Event
    request: Request
    status_change: StatusChange | None
    fee: Fee | None
    task: Task | None
    attachments: list[Attachment]


def get_kind(key: str) -> Kind:
    kind = _BY_KEY.get(key)
    if kind is None:
        raise ServiceError(f"Unknown classification {key!r}; expected one of {sorted(_BY_KEY)}.")
    return kind


def _plan(event_id: UUID, key: str, task_due_at: datetime | None = None) -> Preview:
    """Read the event, the request and the table row; decide what would happen. Writes nothing."""
    kind = get_kind(key)
    event = events_service.get_event(event_id)
    if event.voided_at is not None:
        raise InvalidTransition(f"Event {event_id} is voided and cannot be recorded as anything.")
    if event.direction != "inbound":
        raise ServiceError(
            f"Event {event_id} did not come in from the agency (direction {event.direction!r}); "
            "only an inbound event can be recorded as a response."
        )
    request = Request.objects.get(pk=event.request_id)
    status = kind.status
    task = None
    if kind.task_type is not None and kind.task_description is not None:
        task = PlannedTask(kind.task_type, kind.task_description, task_due_at, "classify")
    elif status in _CHOKEPOINT_TASK:
        wanted = _CHOKEPOINT_TASK[status]
        if not any(t.task_type == wanted for t in tasks_service.open_tasks(request.pk)):
            task = PlannedTask(
                wanted,
                "Agency claims no responsive records; verify the search and decide the follow-up.",
                None,
                "status_chokepoint",
            )
    return Preview(
        kind=kind,
        event_id=event.pk,
        request_id=request.pk,
        event_type_from=event.event_type_id,
        event_type_to=kind.event_type,
        status_from=request.current_status_id,
        status_to=status,
        status_reason=(
            None if status is None else "manual_override" if kind.via_override else "event"
        ),
        deadlines=list(deadlines_service.active_deadlines(request.pk)),
        task=task,
    )


def preview_classification(event_id: UUID, kind: str) -> Preview:
    """What the "Record as..." dialog shows: the status change, the deadlines as they stand, the
    task. The same `_plan` `classify` runs, so what it shows is what the write then does."""
    return _plan(event_id, kind)


def _check_inputs(
    plan: Preview,
    event: Event,
    *,
    note: str | None,
    fee: FeeInput | None,
    record_attachment_ids: set[UUID],
    task_due_at: datetime | None,
) -> None:
    kind = plan.kind
    if kind.note_required and not (note or "").strip():
        raise ServiceError(f"Recording an event as {kind.label!r} needs a note saying why.")
    if fee is not None and not kind.takes_fee:
        raise ServiceError(f"{kind.label!r} does not take a fee.")
    if fee is not None and fee.amount_cents is not None and fee.amount_cents < 0:
        raise ServiceError("A fee cannot be a negative amount.")
    if record_attachment_ids and not kind.takes_records:
        raise ServiceError(f"{kind.label!r} does not mark any files as records.")
    if record_attachment_ids:
        if event.source_id is None:
            raise ServiceError("This event has no message behind it, so it has no files.")
        known = set(
            Attachment.objects.filter(
                source_id=event.source_id, pk__in=record_attachment_ids
            ).values_list("pk", flat=True)
        )
        stranger = sorted(str(pk) for pk in record_attachment_ids - known)
        if stranger:
            raise ServiceError(f"Not files of this message: {', '.join(stranger)}.")
    if task_due_at is not None and plan.task is None:
        raise ServiceError(f"{kind.label!r} opens no task, so a task due date means nothing.")
    if task_due_at is not None and plan.task is not None and plan.task.opened_by != "classify":
        raise ServiceError(
            f"The task {kind.label!r} opens is opened by the status change and takes no due date."
        )


@transaction.atomic
def classify(
    actor: Actor,
    event_id: UUID,
    kind: str,
    *,
    note: str | None = None,
    fee: FeeInput | None = None,
    record_attachment_ids: Iterable[UUID] = (),
    agency_reference_number: str | None = None,
    task_due_at: datetime | None = None,
) -> Classification:
    """Record an inbound event as one of `KINDS`: its type, the status it implies, the fee, the
    task, the files that are records and the agency's own number, in one transaction.

    Human only, whatever the row. `note` is the person's reason (optional, except for "Production
    -- complete", which needs it because it is a manual override). `fee` is for a fee estimate
    only. `record_attachment_ids` is for the two production rows: those files of the message are
    marked as records IN ADDITION to any already marked, and none is ever unmarked, because one
    message can answer several requests (see `sources.mark_attachments_as_records`; only
    `sources.set_attachment_roles` replaces the set); leave it empty to touch no file.
    `agency_reference_number`, when given and different, is saved on the request through the
    audited edit. `task_due_at` dates the task this row opens; without it the task has no due
    date. Anything that does not apply to the row is refused, not ignored.
    """
    require_human(actor, "Recording an event as a response")
    request = lock_request(events_service.get_event(event_id).request_id)
    plan = _plan(event_id, kind, task_due_at)
    event = events_service.get_event(event_id)
    wanted_records = set(record_attachment_ids)
    _check_inputs(
        plan,
        event,
        note=note,
        fee=fee,
        record_attachment_ids=wanted_records,
        task_due_at=task_due_at,
    )
    why = (note or "").strip() or None
    audit_note = why or f"Recorded as {plan.kind.label}."

    if event.event_type_id != plan.event_type_to:
        edits_service.edit_with_audit(
            actor, "events", event.pk, "event_type", plan.event_type_to, audit_note
        )

    change = None
    if plan.status_to is not None and plan.status_reason == "event":
        change = status_service._append_status_change(
            request,
            plan.status_to,
            reason="event",
            actor=actor,
            triggering_event=events_service.get_event(event.pk),
            note=why,
        )
    elif plan.status_to is not None:
        # "Production -- complete": a manual override with no triggering event. The note is
        # required (checked above) and names the event, since the row itself cannot.
        change = status_service.override_status(
            actor, request.pk, plan.status_to, f"{why} (recorded from event {event.pk})"
        )

    recorded_fee = None
    if fee is not None:
        recorded_fee = fees_service.record_fee(
            actor,
            request.pk,
            status="assessed",
            amount_cents=fee.amount_cents,
            source_event_id=event.pk,
            due_at=fee.due_at,
            description=fee.description,
        )

    opened = None
    if plan.task is not None and plan.task.opened_by == "classify":
        opened = tasks_service.create_task(
            actor,
            request.pk,
            plan.task.task_type,
            plan.task.description,
            due_at=task_due_at,
            # P23b: the task is about the message it was recorded from, when there is one.
            source_id=event.source_id,
        )
    elif plan.task is not None:
        # The chokepoint opened it inside `_append_status_change`; say which one it was.
        opened = (
            Task.objects.filter(
                request_id=request.pk, task_type=plan.task.task_type, completed_at__isnull=True
            )
            .order_by("-created_at")
            .first()
        )

    attachments: list[Attachment] = []
    if wanted_records and event.source_id is not None:
        # Additive: the message may answer other requests, whose records are marked on the same
        # files. Only the standalone "Mark files as records" door states a whole set.
        attachments = sources_service.mark_attachments_as_records(
            actor, event.source_id, wanted_records, audit_note
        )

    if agency_reference_number is not None and agency_reference_number.strip():
        number = agency_reference_number.strip()
        if number != request.agency_reference_number:
            edits_service.edit_with_audit(
                actor, "requests", request.pk, "agency_reference_number", number, audit_note
            )

    return Classification(
        event=events_service.get_event(event.pk),
        request=Request.objects.get(pk=request.pk),
        status_change=change,
        fee=recorded_fee,
        task=opened,
        attachments=attachments,
    )
