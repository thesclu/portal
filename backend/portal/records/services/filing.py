"""Filing: the moment a reserved request is actually out the door, and its clocks start.

Everything statutory happens here and only here, because the clock start is the agency's local
civil date of `filed_at` -- a filing made late on a Pacific evening is not the following
day's filing merely because UTC has already rolled over.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from deadline_rules import (
    DeadlineComputation,
    DeadlineSpec,
    NoRuleError,
    UnknownCalendarError,
    UnresolvedProvision,
    compute_deadlines,
)
from deadline_rules import RequestItem as RuleItem
from django.db import transaction
from django.utils import timezone

from portal.core.services import activity as core_activity
from portal.records import roles
from portal.records.actors import Actor
from portal.records.models import Deadline, Event, Request, RequestItem, Task
from portal.records.services import deadlines as deadlines_service
from portal.records.services import events as events_service
from portal.records.services import status as status_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import (
    ChangedMeanwhile,
    InvalidTransition,
    NoCalendarMapped,
    NotAllowed,
    agency_calendar,
    calendars_for,
    last_status_change,
    local_date,
    lock_request,
    rule_data,
)
from portal.records.services.status import WAITING_FOR_APPROVAL

# What else must happen, in the same transaction, the moment a request is filed.
#
# `file_request` is the one door a request is filed through, and it is called from more than one
# screen: the request's own page here, and the letter it came from in `drafting`. `records` may
# not know that `drafting` exists (nothing here imports it, and a structural test keeps it so), so
# an app that needs to react to a filing registers a callable instead, from its `apps.ready()`.
# This is the ONLY callback out of `records` (HANDOFF convention 13).
#
# A callback runs inside `file_request`'s transaction, under the request's lock, after the request
# is `filed`. So: it may lock only rows that come AFTER a request in the ledger's lock order; and
# if it raises, the filing rolls back with it.
AfterFiled = Callable[[Actor, Request, datetime], None]
_after_filed: list[AfterFiled] = []


def after_filed(callback: AfterFiled) -> AfterFiled:
    """Register `callback(actor, request, filed_at)`. Registering the same callable twice is a
    no-op, so an app whose `ready()` runs more than once does not stamp twice."""
    if callback not in _after_filed:
        _after_filed.append(callback)
    return callback


@dataclass
class FileResult:
    request: Request
    submission_event: Event
    deadlines: list[Deadline] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)


@transaction.atomic
def file_request(
    actor: Actor,
    request_id: UUID,
    *,
    filed_at: datetime,
    channel: str,
    source_id: UUID | None = None,
    request_provision: str | None = None,
    item_provisions: Mapping[UUID, Sequence[str]] | None = None,
    display_body: str = "Request submitted.",
    released: bool = False,
) -> FileResult:
    """File a reserved request. `released=True` says the caller has already established that an
    intern's letter carries its approver's yes (`drafting.filing.file_recipient` does, and the mail
    worker filing an approved message does); without it, an intern filing is refused here, so no
    door that forgets the approval can file one (P20h; 42b)."""
    request = lock_request(request_id)
    if not released and actor.is_human and roles.needs_approval(actor.user_id):
        raise NotAllowed(
            "An intern's letter is filed only after it is approved: submit it for approval."
        )
    if request.current_status_id == WAITING_FOR_APPROVAL:
        raise InvalidTransition(
            f"{request.tracking_number} is waiting for approval; it is filed when an approver "
            "approves it, or after the intern withdraws it."
        )
    if request.current_status_id != "pending_filing":
        message = (
            f"{request.tracking_number} is {request.current_status_id!r}, not 'pending_filing'; "
            "only a request that is pending filing can be filed."
        )
        # P23b (094): say who moved it on and when, when the ledger knows.
        last = last_status_change(request.pk)
        if last is not None:
            who, when, who_id = last
            raise ChangedMeanwhile(
                message,
                changed_by=who,
                changed_at=when,
                status=request.current_status_id,
                mine=actor.user_id is not None and who_id == actor.user_id,
            )
        raise InvalidTransition(message)

    event = events_service.record_event(
        actor,
        request_id,
        event_type="submission",
        channel=channel,
        direction="outbound",
        occurred_at=filed_at,
        display_body=display_body,
        source_id=source_id,
        actor_user_id=actor.user_id,
    )
    # updated_at means "when this row last changed" everywhere else -- the schema's own triggers
    # and reserve_request's `updated_at=now` both use the write instant, never a domain date. A
    # backdated filed_at (recording a request actually sent last week) must not move updated_at
    # into the past to match it.
    Request.objects.filter(pk=request_id).update(filed_at=filed_at, updated_at=timezone.now())
    status_service._append_status_change(
        request, "filed", reason="event", actor=actor, triggering_event=event
    )
    core_activity.record(
        actor,
        "filed",
        f"Filed {request.tracking_number} with {request.agency.name} by {channel}",
        subject_type="request",
        subject_id=request.pk,
        request_id=request.pk,
    )
    for callback in _after_filed:
        callback(actor, request, filed_at)

    opened: list[Deadline] = []
    raised: list[Task] = []
    try:
        computation = _compute(request, filed_at, request_provision, item_provisions)
    except (NoRuleError, UnknownCalendarError, NoCalendarMapped) as exc:
        # The request IS filed; what is missing is owner's rule entry, not anything about this
        # request. Say so as a task rather than rolling back a filing that really happened.
        raised.append(
            tasks_service.create_task(
                actor,
                request_id,
                "enter_deadline_rules",
                f"No deadline could be computed for {request.tracking_number}: {exc}",
            )
        )
        return FileResult(
            request=Request.objects.get(pk=request_id),
            submission_event=event,
            deadlines=opened,
            tasks=raised,
        )

    for spec in computation.specs:
        opened.append(
            deadlines_service.open_deadline(
                actor,
                request_id,
                deadline_type=spec.deadline_type,
                due_at=spec.due_at,
                basis="statutory",
                legal_basis=spec.legal_basis,
                provision=spec.provision,
                request_item_id=spec.request_item_id,
                source_event_id=event.pk,
                rule_ref=spec.rule_ref,
            )
        )
    for missing in computation.unresolved:
        raised.append(
            tasks_service.create_task(
                actor,
                request_id,
                "enter_deadline_rules",
                f"No {missing.deadline_type} rule in force for provision "
                f"{missing.provision!r} on request item {missing.request_item_id} "
                f"({request.tracking_number}); that carve-out has no deadline.",
            )
        )
    return FileResult(
        request=Request.objects.get(pk=request_id),
        submission_event=event,
        deadlines=opened,
        tasks=raised,
    )


def _compute(
    request: Request,
    filed_at: datetime,
    request_provision: str | None,
    item_provisions: Mapping[UUID, Sequence[str]] | None,
) -> DeadlineComputation:
    """The deadlines filing at `filed_at` opens, from the rules, written nowhere. The one
    computation behind `file_request` and `preview_filing`, so the preview cannot disagree with
    what filing then does. Raises the rules' own errors when no rule or calendar covers it."""
    request_items = list(
        RequestItem.objects.filter(request_id=request.pk).order_by("sort_order", "item_number")
    )
    if item_provisions is None:
        # Not passed: each item's own `provision` names the clock it runs under, so the request
        # gets the same deadlines whichever door files it. A mapping that IS passed (even an empty
        # one) is the caller's whole answer and is not merged with the columns.
        provisions: Mapping[UUID, Sequence[str]] = {
            item.pk: (item.provision,) for item in request_items if item.provision
        }
    else:
        provisions = item_provisions
    items = [
        RuleItem(id=item.pk, provisions=tuple(provisions.get(item.pk, ())))
        for item in request_items
    ]
    data = rule_data()
    # The clock starts on the agency's own civil date, not on the UTC one.
    clock_start = local_date(filed_at, agency_calendar(request.agency))
    return compute_deadlines(
        records_law=request.agency.records_law or "",
        provision=request_provision,
        clock_start=clock_start,
        items=items,
        rules=data.rules,
        calendars=calendars_for(request.agency, data),
        law_calendar=data.law_calendar,
        jurisdiction=None,
    )


@dataclass(frozen=True)
class FilingPreview:
    """What filing would open (083's "Determination due Sep 18 · …"), before anything is filed.
    `specs` are the deadlines; `unresolved` the item carve-outs with no rule in force (filing
    opens an "enter rules" task for each); `problem` says why nothing could be computed at all
    (no rule or calendar for the agency's law), in which case filing opens one task instead."""

    specs: tuple[DeadlineSpec, ...]
    unresolved: tuple[UnresolvedProvision, ...]
    problem: str | None = None


def preview_filing(
    request_id: UUID,
    *,
    filed_at: datetime,
    request_provision: str | None = None,
    item_provisions: Mapping[UUID, Sequence[str]] | None = None,
) -> FilingPreview:
    """A dry run of filing's clocks (P23b, 083): the same rules service on the same inputs, under
    no lock and in no transaction of its own, writing nothing. Any status: it answers "what would
    filing on this date open", which is the same question before and after."""
    request = Request.objects.select_related("agency").get(pk=request_id)
    try:
        computation = _compute(request, filed_at, request_provision, item_provisions)
    except (NoRuleError, UnknownCalendarError, NoCalendarMapped) as exc:
        return FilingPreview(specs=(), unresolved=(), problem=str(exc))
    return FilingPreview(specs=computation.specs, unresolved=computation.unresolved)
