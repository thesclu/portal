"""An agency's response record: how it has actually answered, computed from the ledger.

Read only, and nothing here is stored. "Median days to determination", "determinations on time"
and "extensions taken" are queries over events and deadlines that already exist, so they cannot
drift from them and no trigger has to keep them true (BUILD_PLAN rule 3).

It takes the request ids to count over rather than reading every request the agency has. The caller
passes the ones the viewer is allowed to see, so a request hidden from someone cannot move a figure
shown to them.

Dates are civil dates where the agency is, from its calendar -- the same reading `file_request`
uses to start a clock. Without a mapped calendar the two date-based figures are None, never a
guess in some other zone; the count of extensions needs no date and is always given.
"""

import statistics
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from portal.records.models import Agency, Deadline, Event, Request
from portal.records.services.common import NoCalendarMapped, agency_calendar, local_date


@dataclass(frozen=True)
class ResponseRecord:
    """What the agency detail page shows under "Response record"."""

    # Calendar days from a request being filed to the first determination on it, median over the
    # requests that have one. None when there is no such request or no calendar.
    median_days_to_determination: float | None
    # Of the determination deadlines that were satisfied, how many were satisfied on or before
    # their due date, and how many there were. Missed-and-never-answered ones are not counted: this
    # is about the determinations the agency actually made.
    determinations_on_time: int | None
    determinations_total: int | None
    # Non-voided extension notices received.
    extensions_taken: int


def response_record(agency: Agency, request_ids: Collection[UUID]) -> ResponseRecord:
    ids = list(request_ids)
    extensions = Event.objects.filter(
        request_id__in=ids, event_type_id="extension_notice", voided_at__isnull=True
    ).count()
    try:
        calendar = agency_calendar(agency)
    except NoCalendarMapped:
        return ResponseRecord(None, None, None, extensions)

    filed = {
        pk: filed_at
        for pk, filed_at in Request.objects.filter(pk__in=ids, filed_at__isnull=False).values_list(
            "pk", "filed_at"
        )
        if filed_at is not None
    }
    first_determination: dict[UUID, datetime] = {}
    for request_id, occurred_at in (
        Event.objects.filter(
            request_id__in=ids, event_type_id="determination", voided_at__isnull=True
        )
        .order_by("occurred_at")
        .values_list("request_id", "occurred_at")
    ):
        first_determination.setdefault(request_id, occurred_at)
    spans = [
        (local_date(answered, calendar) - local_date(filed[request_id], calendar)).days
        for request_id, answered in first_determination.items()
        if request_id in filed
    ]
    median = float(statistics.median(spans)) if spans else None

    answered_deadlines = list(
        Deadline.objects.filter(
            request_id__in=ids,
            deadline_type="statutory_determination",
            status="satisfied",
            satisfied_by_event__isnull=False,
            satisfied_by_event__voided_at__isnull=True,
        ).select_related("satisfied_by_event")
    )
    on_time = [
        deadline
        for deadline in answered_deadlines
        if deadline.satisfied_by_event is not None
        and local_date(deadline.satisfied_by_event.occurred_at, calendar) <= deadline.due_at
    ]
    return ResponseRecord(median, len(on_time), len(answered_deadlines), extensions)
