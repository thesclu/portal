"""Office closures (BUILD_PLAN P16, DECISIONS 21): the dates an agency's office is closed.

A closure is one more holiday on that agency's calendar when a clock is computed
(`common.calendar_with_closures`). It is never a rule: the reason and the citation are a person's
words, typed by an administrator, and the portal fills neither.

Adding a closure after deadlines exist does not recompute them. Every active deadline of that
agency's requests due on or after the closure's first day is flagged for a person, with one task
per request, because a clock that was still running through those days may land later now.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.records.actors import Actor
from portal.records.models import Agency, AgencyClosure, Deadline, writable
from portal.records.services import tasks as tasks_service
from portal.records.services.common import (
    InvalidTransition,
    NotFound,
    ServiceError,
    require_human,
)

REVIEW_TASK = "manual"


@dataclass(frozen=True)
class Added:
    closures: list[AgencyClosure]
    flagged_deadlines: int
    tasks_raised: int


def closure_dates(agency_id: UUID, *, since: date | None = None) -> frozenset[date]:
    """Every closed day of one agency, voided closures left out."""
    rows = AgencyClosure.objects.filter(agency_id=agency_id, voided_at__isnull=True)
    if since is not None:
        rows = rows.filter(ends_on__gte=since)
    out: set[date] = set()
    step = timedelta(hours=24)
    for starts_on, ends_on in rows.values_list("starts_on", "ends_on"):
        current = starts_on
        while current <= ends_on:
            out.add(current)
            current += step
    return frozenset(out)


def closures_of(
    agency_id: UUID | None = None, *, include_past: bool = False, include_voided: bool = False
) -> list[AgencyClosure]:
    rows = AgencyClosure.objects.select_related("agency").order_by("starts_on", "agency__name")
    if agency_id is not None:
        rows = rows.filter(agency_id=agency_id)
    if not include_voided:
        rows = rows.filter(voided_at__isnull=True)
    if not include_past:
        rows = rows.filter(ends_on__gte=timezone.now().date())
    return list(rows)


def _text(value: str | None, what: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ServiceError(f"{what} is required.")
    return text


@transaction.atomic
def add_closures(
    actor: Actor,
    *,
    agency_ids: Iterable[UUID],
    starts_on: date,
    ends_on: date,
    reason: str,
    citation: str,
) -> Added:
    """One closure row per agency named, the same days, reason and citation on each."""
    user_id = require_human(actor, "Adding a closure")
    ids = list(dict.fromkeys(agency_ids))
    if not ids:
        raise ServiceError("Name at least one agency.")
    if ends_on < starts_on:
        raise ServiceError("The closure cannot end before it starts.")
    if (ends_on - starts_on) > timedelta(hours=24 * 366):
        raise ServiceError("A closure is at most a year long; add another for the rest.")
    reason_text = _text(reason, "A reason")
    citation_text = _text(citation, "The citation")
    agencies = {a.pk: a for a in Agency.objects.filter(pk__in=ids)}
    missing = [str(i) for i in ids if i not in agencies]
    if missing:
        raise NotFound(f"No agency {', '.join(missing)}.")
    merged = [a.name for a in agencies.values() if a.merged_into_id is not None]
    if merged:
        raise InvalidTransition(
            f"{', '.join(merged)}: merged into another agency; add the closure to that one."
        )
    made: list[AgencyClosure] = []
    with writable():
        for agency_id in ids:
            made.append(
                AgencyClosure.objects.create(
                    agency_id=agency_id,
                    starts_on=starts_on,
                    ends_on=ends_on,
                    reason=reason_text,
                    citation=citation_text,
                    created_by_id=user_id,
                )
            )
    flagged, raised = _flag_affected(actor, agencies.values(), starts_on, reason_text)
    return Added(closures=made, flagged_deadlines=flagged, tasks_raised=raised)


def _flag_affected(
    actor: Actor, agencies: Iterable[Agency], starts_on: date, reason: str
) -> tuple[int, int]:
    """Active deadlines due on or after the closure's first day may land later now. Flag each
    for a person, and raise one task per request so it shows up where work is."""
    names = {a.pk: a.name for a in agencies}
    rows = Deadline.objects.filter(
        request__agency_id__in=list(names), status="active", due_at__gte=starts_on
    ).select_related("request")
    affected = list(rows)
    if not affected:
        return 0, 0
    note = f"Closure days were added ({reason}); check whether this date moves."
    with writable():
        Deadline.objects.filter(pk__in=[d.pk for d in affected]).update(
            flagged_for_review=True, flag_note=note
        )
    raised = 0
    for request_id in dict.fromkeys(d.request_id for d in affected):
        agency_name = names[
            next(d for d in affected if d.request_id == request_id).request.agency_id
        ]
        open_now = tasks_service.open_tasks(request_id)
        if any(t.task_type == REVIEW_TASK and "Closure days" in t.description for t in open_now):
            continue
        tasks_service.create_task(
            actor,
            request_id,
            REVIEW_TASK,
            f"Closure days were added for {agency_name}: check the deadlines that may move.",
        )
        raised += 1
    return len(affected), raised


@transaction.atomic
def void_closure(actor: Actor, closure_id: UUID, note: str) -> AgencyClosure:
    user_id = require_human(actor, "Voiding a closure")
    text = _text(note, "A note")
    row = AgencyClosure.objects.select_for_update().filter(pk=closure_id).first()
    if row is None:
        raise NotFound(f"No closure {closure_id}.")
    if row.voided_at is not None:
        raise InvalidTransition("That closure is already voided.")
    with writable():
        AgencyClosure.objects.filter(pk=closure_id).update(
            voided_at=timezone.now(), voided_by_id=user_id, void_note=text
        )
    return AgencyClosure.objects.get(pk=closure_id)


def agencies_closed_on(day: date, agency_ids: Sequence[UUID]) -> set[UUID]:
    """Which of these agencies are closed on `day` (the Calendar screen's question)."""
    return set(
        AgencyClosure.objects.filter(
            agency_id__in=list(agency_ids),
            voided_at__isnull=True,
            starts_on__lte=day,
            ends_on__gte=day,
        ).values_list("agency_id", flat=True)
    )
