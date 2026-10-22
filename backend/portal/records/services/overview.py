"""What Home and the Calendar read. Read only; everything here is a query over rows that exist.

There is no calendar table and no "overdue" or "due this week" column (BUILD_PLAN rule 3): the
answers are computed from `deadlines` each time, so they cannot go stale and no trigger has to keep
them true.

**Overdue means the earliest active deadline of a kind.** A request can carry two active dates for
one thing (the statute says one, the agency's letter another, and nobody has resolved it). Only the
earlier one is "the deadline" for Home, the rule the overdue sweep already follows, so the later
date is not listed as a second, softer clock. The Calendar is different: it shows every active
deadline on the date it falls due.

**"Today" is where the agency is.** A request's "today" is the civil date it is NOW on the agency's
own calendar (`agency_calendar`), the reading `file_request` uses to start a clock and `docket`
uses for its day count. An agency whose law has no calendar mapped falls back to the UTC date, so
one data-entry gap cannot blank the page. Requests whose status is terminal are left out of both:
a closed request has stopped racing anything.

How far ahead "due soon" looks is a settings row (`records.due_soon_window_days`), never a literal
here. `viewer` is who is asking (a `Viewer`, or a person's id): Home's "assigned to you" is theirs,
and a privileged request they are not on the list of contributes no deadline, no task and no
message to anything here (`access.visible_q`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from portal.core.services.rules import holiday_names
from portal.core.services.settings import get_setting
from portal.records.models import Deadline, DeadlineDefinition, Request, Task
from portal.records.services import access
from portal.records.services import inbox as inbox_service
from portal.records.services import tasks as tasks_service
from portal.records.services.access import Viewer
from portal.records.services.common import (
    NoCalendarMapped,
    RuleData,
    ServiceError,
    agency_calendar,
    local_date,
    rule_data,
)


@dataclass(frozen=True)
class DeadlineRow:
    """An active deadline with the request it belongs to (agency loaded) and how far off it is."""

    deadline: Deadline
    request: Request
    kind_label: str
    # due_at minus today on the agency's calendar: positive is time left, negative is time overdue.
    days_from_today: int


@dataclass(frozen=True)
class HomeTask:
    task: Task
    request: Request


@dataclass(frozen=True)
class Home:
    overdue: list[DeadlineRow]
    due_soon: list[DeadlineRow]
    # How far ahead `due_soon` looked, from the settings row, so the screen can say so.
    due_soon_window_days: int
    # The viewer's open tasks, soonest due first.
    assigned_tasks: list[HomeTask]
    # Flagged mail, from the Inbox's own query, with the bucket counts for the navigation.
    flagged: inbox_service.InboxPage


@dataclass(frozen=True)
class Holiday:
    date: date
    # The name an administrator typed in Settings -> Rules & calendars (P20b2), or None. The rule
    # files hold dates only, and the portal never invents a name.
    name: str | None


def holidays_of(key: str, dates, names: dict[tuple[str, date], str]) -> list[Holiday]:
    """`dates` of the calendar `key` as holidays, sorted, each with its typed name if any."""
    return [Holiday(date=d, name=names.get((key, d)) or None) for d in sorted(dates)]


@dataclass(frozen=True)
class CalendarHolidays:
    """One jurisdiction calendar's weekend and its holidays inside the range asked for."""

    key: str
    timezone: str
    # `date.weekday()` numbers (Monday is zero), straight from the calendar.
    weekend: list[int]
    holidays: list[Holiday]


@dataclass(frozen=True)
class CalendarView:
    start: date
    end: date
    deadlines: list[DeadlineRow]
    calendars: list[CalendarHolidays]


def _today_by_law(now: datetime, data: RuleData) -> Callable[[Request], date]:
    """A function from an agency to its civil date now, computed once per law."""
    cache: dict[str, date] = {}

    def today_for(request: Request) -> date:
        agency = request.agency
        law = agency.records_law or ""
        if law not in cache:
            try:
                cache[law] = local_date(now, agency_calendar(agency, data))
            except NoCalendarMapped:
                cache[law] = now.astimezone(UTC).date()
        return cache[law]

    return today_for


def _rows(deadlines: QuerySet[Deadline], now: datetime) -> list[DeadlineRow]:
    labels = dict(DeadlineDefinition.objects.values_list("code", "label"))
    today_for = _today_by_law(now, rule_data())
    rows = []
    for deadline in deadlines.select_related("request", "request__agency"):
        rows.append(
            DeadlineRow(
                deadline=deadline,
                request=deadline.request,
                kind_label=labels.get(deadline.deadline_type, deadline.deadline_type),
                days_from_today=(deadline.due_at - today_for(deadline.request)).days,
            )
        )
    return rows


def _active(viewer: Viewer) -> QuerySet[Deadline]:
    """Active deadlines on requests that are still running and that this viewer may see."""
    return Deadline.objects.filter(
        status="active", request__current_status__is_terminal=False
    ).filter(access.visible_q(viewer, "request__"))


# What the Calendar shows besides active deadlines: the ones that are done, so they can be struck.
# A superseded date was replaced by another and is not shown; the replacement is.
CALENDAR_STATUSES = ("active", "satisfied", "waived", "missed")


def _in_range(viewer: Viewer) -> QuerySet[Deadline]:
    """The Calendar's deadlines: active ones on requests still running (as before), and every
    satisfied, waived or missed one, on requests this viewer may see."""
    return (
        Deadline.objects.filter(status__in=CALENDAR_STATUSES)
        .filter(
            Q(status="active", request__current_status__is_terminal=False) | ~Q(status="active")
        )
        .filter(access.visible_q(viewer, "request__"))
    )


def home(viewer: Viewer | UUID, *, now: datetime | None = None) -> Home:
    """Overdue, due soon, assigned to you, flagged. `now` is for tests that freeze the clock."""
    viewer = access.as_viewer(viewer)
    now = now or timezone.now()
    window = int(get_setting("records.due_soon_window_days"))

    earliest: dict[tuple[UUID, str], DeadlineRow] = {}
    for row in _rows(_active(viewer), now):
        key = (row.request.pk, row.deadline.deadline_type)
        held = earliest.get(key)
        if held is None or row.deadline.due_at < held.deadline.due_at:
            earliest[key] = row
    ordered = sorted(
        earliest.values(), key=lambda r: (r.days_from_today, r.request.tracking_number)
    )
    overdue = [r for r in ordered if r.days_from_today < 0]
    due_soon = [r for r in ordered if 0 <= r.days_from_today <= window]

    mine = list(tasks_service.list_tasks(assignee_id=viewer.user_id, state="open", viewer=viewer))
    requests = Request.objects.filter(pk__in={t.request_id for t in mine}).select_related("agency")
    by_id = {r.pk: r for r in requests}
    return Home(
        overdue=overdue,
        due_soon=due_soon,
        due_soon_window_days=window,
        assigned_tasks=[HomeTask(task=t, request=by_id[t.request_id]) for t in mine],
        flagged=inbox_service.inbox(viewer, "flagged"),
    )


def calendar(
    viewer: Viewer | UUID, start: date, end: date, *, now: datetime | None = None
) -> CalendarView:
    """Every deadline falling due from `start` to `end` inclusive, by date: the active ones, and
    the satisfied, waived and missed ones (each row carries its status, so a finished one can be
    struck), with each jurisdiction calendar's weekend and holidays in that range, named where
    an administrator named them. Soonest first."""
    if end < start:
        raise ServiceError("The calendar's end is before its start.")
    viewer = access.as_viewer(viewer)
    now = now or timezone.now()
    rows = _rows(_in_range(viewer).filter(due_at__gte=start, due_at__lte=end), now)
    rows.sort(key=lambda r: (r.deadline.due_at, r.request.tracking_number))
    names = holiday_names()
    calendars = [
        CalendarHolidays(
            key=key,
            timezone=cal.timezone,
            weekend=sorted(cal.weekend),
            holidays=holidays_of(key, (d for d in cal.holidays if start <= d <= end), names),
        )
        for key, cal in sorted(rule_data().calendars.items())
    ]
    return CalendarView(start=start, end=end, deadlines=rows, calendars=calendars)
