"""The Docket's stat strip, its two sparklines and the assign dialog's counts (BUILD_PLAN P20b2).

Read only, and every figure is a query over rows that exist, made under the same visibility rule
as the docket list (`access.visible_q`): a privileged request the viewer is not on the list of
moves no number they are shown. There is no summary table and no cache (BUILD_PLAN rule 3): the
answers are different tomorrow and nothing has to keep them true.

The definitions are the ones the screen computed from the list route before this existed
(`frontend/src/screens/docket/stats.ts`), so swapping one for the other moves no number:

- **overdue**: the request's next active deadline is before the viewer's today (the list's
  `due_before=today`).
- **due_by_date**: for each date Monday to Friday of the viewer's current week, how many requests
  have their next active deadline on that date. A Sunday looks at the week that starts the next
  morning, as the screen did.
- **producing** / **producing_unreviewed** / **waiting_on_agency**: the statuses each box counts
  (below; DECISIONS 34c, Claude's reading of the labels, awaiting owner's status table).
- **assigned_to_me**: every request assigned to the viewer, whatever its status (the list's
  `assignee=me`).
- **all_open**: every request whose status is not terminal (`status_definitions.is_terminal`).

"Today" here is the viewer's civil date in their own time zone, as the screen reads it; a
deadline's lateness on the agency's calendar is the docket row's own figure and is not this.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone

from portal.core.services.settings import get_setting
from portal.records.models import DeadlineDefinition, Request
from portal.records.services import access
from portal.records.services.access import Viewer
from portal.records.services.common import ServiceError

# The stat boxes that count statuses (025). Which statuses each box counts is not written in the
# design; these are Claude's reading of the labels (DECISIONS 34c), awaiting owner's approval.
# The summary returns them, so the screen filters the list by exactly what it counted.
PRODUCING_STATUSES = (
    "new_production",
    "production_unreviewed",
    "partial_production",
    "production_overdue",
)
UNREVIEWED_STATUSES = ("new_production", "production_unreviewed")
WAITING_ON_AGENCY_STATUSES = ("filed", "acknowledged", "in_progress")

# The most dates the history route reconstructs in one call. A bound on the work one read may
# ask for, not a legal period; the default span is a settings row.
MAX_HISTORY_SPAN = 366
HISTORY_SPAN_SETTING = "records.docket_history_span"


@dataclass(frozen=True)
class DueOnDate:
    date: date
    count: int


@dataclass(frozen=True)
class Summary:
    today: date
    overdue: int
    due_by_date: list[DueOnDate]
    producing: int
    producing_unreviewed: int
    waiting_on_agency: int
    assigned_to_me: int
    all_open: int


@dataclass(frozen=True)
class HistoryPoint:
    date: date
    overdue: int
    open: int


def _shift(when: date, by: int) -> date:
    """`when` moved by `by` calendar dates, without a literal span anywhere."""
    return date.fromordinal(when.toordinal() + by)


def viewer_zone(name: str | None) -> ZoneInfo:
    """The viewer's own zone; the organisation's default when theirs is not a zone name; UTC
    when neither is."""
    for candidate in (name, get_setting("org.default_display_timezone"), "UTC"):
        try:
            return ZoneInfo(str(candidate or ""))
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def viewer_today(zone: ZoneInfo, now: datetime | None = None) -> date:
    return (now or timezone.now()).astimezone(zone).date()


def work_week(today: date) -> list[date]:
    """Monday to Friday of the week `today` falls in; a Sunday looks at the coming week."""
    weekday = today.weekday()  # Monday is zero
    monday = _shift(today, 1) if weekday == 6 else _shift(today, -weekday)
    return [_shift(monday, offset) for offset in range(5)]


def _visible(viewer: Viewer):
    return Request.objects.filter(access.visible_q(viewer))


def summary(viewer: Viewer | UUID, today: date) -> Summary:
    """Every figure of the stat strip in two queries: one for the counts, one for the week."""
    viewer = access.as_viewer(viewer)
    rows = _visible(viewer)
    figures = rows.aggregate(
        overdue=Count("pk", filter=Q(next_deadline_at__lt=today)),
        producing=Count("pk", filter=Q(current_status__in=PRODUCING_STATUSES)),
        producing_unreviewed=Count("pk", filter=Q(current_status__in=UNREVIEWED_STATUSES)),
        waiting_on_agency=Count("pk", filter=Q(current_status__in=WAITING_ON_AGENCY_STATUSES)),
        assigned_to_me=Count("pk", filter=Q(assigned_to_id=viewer.user_id)),
        all_open=Count("pk", filter=Q(current_status__is_terminal=False)),
    )
    week = work_week(today)
    on_date = dict(
        rows.filter(next_deadline_at__in=week)
        .values("next_deadline_at")
        .annotate(n=Count("pk"))
        .values_list("next_deadline_at", "n")
    )
    return Summary(
        today=today,
        due_by_date=[DueOnDate(date=d, count=on_date.get(d, 0)) for d in week],
        **figures,
    )


def default_history_span() -> int:
    return int(get_setting(HISTORY_SPAN_SETTING))


def clamp_span(span: int | None) -> int:
    """The span asked for, or the settings default, kept between one date and the maximum."""
    wanted = default_history_span() if span is None else span
    return max(1, min(int(wanted), MAX_HISTORY_SPAN))


def history(viewer: Viewer | UUID, today: date, span: int, zone: ZoneInfo) -> list[HistoryPoint]:
    """For each of the `span` calendar dates ending `today`, how many visible requests were open
    and how many overdue at the end of that date, reconstructed from the append-only status
    ledger. One query.

    A request counts on a date when it existed by the end of it and its status then (the latest
    non-voided `status_changes` row effective before the next midnight in the viewer's zone) is
    not terminal; it is overdue when that status is one a deadline kind names as its overdue
    status (`deadline_definitions.overdue_status`). A voided row never counts, whenever it was
    voided: the ledger as it stands is the history.
    """
    if span < 1:
        raise ServiceError("The history covers at least one date.")
    viewer = access.as_viewer(viewer)
    dates = [_shift(today, -back) for back in range(span - 1, -1, -1)]
    # The instant each date ends where the viewer is: the next date's midnight, in UTC.
    ends = [datetime.combine(_shift(d, 1), time.min, tzinfo=zone) for d in dates]
    overdue_statuses = sorted(
        set(
            DeadlineDefinition.objects.exclude(overdue_status__isnull=True).values_list(
                "overdue_status_id", flat=True
            )
        )
    )
    visible_sql, visible_params = _visible(viewer).values("pk").query.sql_with_params()
    sql = f"""
        WITH span AS (
            SELECT d.on_date, d.until
            FROM unnest(%s::date[], %s::timestamptz[]) AS d(on_date, until)
        )
        SELECT span.on_date,
               count(r.id) FILTER (WHERE NOT sd.is_terminal) AS open,
               count(r.id) FILTER (WHERE latest.status = ANY(%s::text[])) AS overdue
        FROM span
        LEFT JOIN records.requests r
               ON r.created_at < span.until AND r.id IN ({visible_sql})
        LEFT JOIN LATERAL (
            SELECT sc.status
            FROM records.status_changes sc
            WHERE sc.request_id = r.id
              AND sc.voided_at IS NULL
              AND sc.effective_at < span.until
            ORDER BY sc.effective_at DESC
            LIMIT 1
        ) latest ON true
        LEFT JOIN records.status_definitions sd ON sd.code = latest.status
        GROUP BY span.on_date
        ORDER BY span.on_date
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [dates, ends, overdue_statuses, *visible_params])
        found = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    return [
        HistoryPoint(date=d, open=found.get(d, (0, 0))[0], overdue=found.get(d, (0, 0))[1])
        for d in dates
    ]


def assignee_counts(viewer: Viewer | UUID) -> dict[UUID, int]:
    """Each person's open requests (status not terminal), as the viewer may see them: the assign
    dialog's "4 open". People with none are absent."""
    viewer = access.as_viewer(viewer)
    rows: Sequence[tuple[UUID, int]] = (
        _visible(viewer)
        .filter(current_status__is_terminal=False, assigned_to_id__isnull=False)
        .values("assigned_to_id")
        .annotate(n=Count("pk"))
        .values_list("assigned_to_id", "n")
    )
    return {user_id: n for user_id, n in rows}
