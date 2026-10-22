from datetime import date, datetime, time
from typing import Annotated
from uuid import UUID

from django.http import HttpRequest
from ninja import Query, Router
from ninja.errors import HttpError

from portal.records.api.permissions import (
    assignee_filter,
    require_source_scope,
    require_team,
    viewer,
)
from portal.records.api.schemas import (
    InboxCountsOut,
    InboxEventOut,
    InboxItemOut,
    InboxOut,
    InboxRowOut,
)
from portal.records.api.serializers import attachment_out, source_out
from portal.records.services import docket_summary as summary_service
from portal.records.services import inbox as inbox_service
from portal.records.services.common import date_after

router = Router(tags=["inbox"])

MAX_LIMIT = 200
DEFAULT_LIMIT = 50

# P23b: "Any time" (154). The person's own calendar dates, both inclusive.
Since = Annotated[date | None, Query(description="Arrived on or after this date (yours).")]
Until = Annotated[date | None, Query(description="Arrived on or before this date (yours).")]


def _row_fields(row: inbox_service.InboxRow) -> dict:
    return {
        "source": source_out(row.source),
        "attachments": [attachment_out(a) for a in row.attachments],
        "events": [
            InboxEventOut(
                id=e.pk,
                request_id=e.request_id,
                tracking_number=e.request.tracking_number,
                agency_id=e.request.agency_id,
                agency_name=e.request.agency.name,
                event_type=e.event_type_id,
                channel=e.channel,
                direction=e.direction,
                occurred_at=e.occurred_at,
                display_body=e.display_body,
            )
            for e in row.events
        ],
        "note_count": row.note_count,
        "assignee_name": row.assignee_name,
        "flagged_by": row.flagged_by,
        "flagged_at": row.flagged_at,
        "recorded_by": row.recorded_by,
        "recorded_at": row.recorded_at,
    }


def inbox_out(page: inbox_service.InboxPage, *, limit: int, offset: int) -> InboxOut:
    """One builder for the Inbox page and for the flagged panel on Home, which is the same query."""
    return InboxOut(
        bucket=page.bucket,
        counts=InboxCountsOut(**page.counts),
        total=page.total,
        limit=limit,
        offset=offset,
        items=[InboxRowOut(**_row_fields(row)) for row in page.rows],
    )


def viewer_span(
    request: HttpRequest, since: date | None, until: date | None
) -> tuple[datetime | None, datetime | None]:
    """`since` and `until` are the person's own calendar dates, both inclusive (P23b, "Any time",
    154), read in their own time zone, as everything else on their screen is. Returned as the
    instants a query compares: the start of `since`, and the start of the date after `until`."""
    if since is not None and until is not None and until < since:
        raise HttpError(422, "The end of the range is before its start.")
    zone = summary_service.viewer_zone(getattr(request.user, "timezone", None))
    start = datetime.combine(since, time.min, tzinfo=zone) if since is not None else None
    end = datetime.combine(date_after(until), time.min, tzinfo=zone) if until is not None else None
    return start, end


@router.get("/inbox", response=InboxOut)
def get_inbox(
    request: HttpRequest,
    bucket: str = Query(..., description="One of GET /definitions' inbox_buckets."),
    agency_id: UUID | None = None,
    request_id: UUID | None = None,
    assignee: str | None = Query(None, description="`me`, or a person's id."),
    q: str | None = Query(
        None, description="Searches a message's text, notes, file names and where it landed."
    ),
    sort: str = Query(
        "newest", description=f"One of {', '.join(inbox_service.SORTS)}; newest first by default."
    ),
    limit: int = Query(DEFAULT_LIMIT, ge=0, description=f"Page size, at most {MAX_LIMIT}."),
    offset: int = Query(0, ge=0),
    since: Since = None,
    until: Until = None,
) -> InboxOut:
    """One bucket of the Inbox, newest first unless `sort` says otherwise, with the count of
    every bucket for the navigation. `since` and `until` narrow it to when it arrived, in the
    caller's own time zone."""
    require_team(request)
    limit = min(limit, MAX_LIMIT)
    start, end = viewer_span(request, since, until)
    page = inbox_service.inbox(
        viewer(request),
        bucket,
        agency_id=agency_id,
        request_id=request_id,
        assignee_id=assignee_filter(request, assignee),
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
        since=start,
        until=end,
    )
    return inbox_out(page, limit=limit, offset=offset)


@router.get("/inbox/{source_id}", response=InboxItemOut)
def get_inbox_item(request: HttpRequest, source_id: UUID) -> InboxItemOut:
    """One message as an Inbox row, with the buckets it is in now (P23b). Seen by whoever may
    see the message; where it landed on a request the caller may not see is left off the row,
    exactly as on the list."""
    require_source_scope(request, source_id)
    row, buckets = inbox_service.bucket_of(viewer(request), source_id)
    return InboxItemOut(**_row_fields(row), buckets=buckets)
