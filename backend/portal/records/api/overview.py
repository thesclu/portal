from datetime import date
from typing import Annotated

from django.http import HttpRequest
from ninja import Query, Router

from portal.records.api.inbox import inbox_out
from portal.records.api.permissions import require_team, viewer
from portal.records.api.schemas import (
    CalendarHolidaysOut,
    CalendarOut,
    DeadlineRowOut,
    HolidayOut,
    HomeOut,
    HomeTaskOut,
    MissingRuleOut,
    RuleCalendarOut,
    RuleOut,
    RulesOut,
)
from portal.records.api.serializers import deadline_out, task_out
from portal.records.services import overview as overview_service
from portal.records.services import rules_view as rules_view_service

router = Router(tags=["overview"])


def _row_out(row: overview_service.DeadlineRow) -> DeadlineRowOut:
    request = row.request
    return DeadlineRowOut(
        deadline=deadline_out(row.deadline),
        status=row.deadline.status,
        request_id=request.pk,
        tracking_number=request.tracking_number,
        subject=request.subject,
        agency_id=request.agency_id,
        agency_name=request.agency.name,
        kind_label=row.kind_label,
        days_from_today=row.days_from_today,
    )


def _holidays(items) -> list[HolidayOut]:
    return [HolidayOut(date=h.date, name=h.name) for h in items]


@router.get("/home", response=HomeOut)
def get_home(request: HttpRequest) -> HomeOut:
    """Overdue, due soon, assigned to you, and the flagged mail, all computed on the spot."""
    require_team(request)
    home = overview_service.home(viewer(request))
    return HomeOut(
        overdue=[_row_out(r) for r in home.overdue],
        due_soon=[_row_out(r) for r in home.due_soon],
        due_soon_window_days=home.due_soon_window_days,
        assigned_tasks=[
            HomeTaskOut(
                task=task_out(t.task),
                tracking_number=t.request.tracking_number,
                agency_name=t.request.agency.name,
            )
            for t in home.assigned_tasks
        ],
        flagged=inbox_out(home.flagged, limit=len(home.flagged.rows), offset=0),
    )


@router.get("/calendar", response=CalendarOut)
def get_calendar(
    request: HttpRequest,
    start: Annotated[date, Query(description="First date shown.")],
    end: Annotated[date, Query(description="Last date shown, inclusive.")],
) -> CalendarOut:
    """Every deadline falling due from `start` to `end`: the active ones, and the satisfied,
    waived and missed ones with their `status` so a finished one can be struck. Each
    jurisdiction calendar's weekend and holidays in that range, with the names an administrator
    typed. There is no calendar table."""
    require_team(request)
    view = overview_service.calendar(viewer(request), start, end)
    return CalendarOut(
        start=view.start,
        end=view.end,
        deadlines=[_row_out(r) for r in view.deadlines],
        calendars=[
            CalendarHolidaysOut(
                key=c.key,
                timezone=c.timezone,
                weekend=c.weekend,
                holidays=_holidays(c.holidays),
            )
            for c in view.calendars
        ],
    )


@router.get("/rules", response=RulesOut)
def get_rules(request: HttpRequest) -> RulesOut:
    """The rules and calendars as they stand. Written through /core/rules (P18b), by an
    administrator, each rule under its citation."""
    require_team(request)
    view = rules_view_service.rules_view(viewer(request))
    return RulesOut(
        rules=[RuleOut(**vars(r)) for r in view.rules],
        calendars=[
            RuleCalendarOut(
                key=c.key,
                timezone=c.timezone,
                laws=c.laws,
                holiday_count=c.holiday_count,
                last_holiday=c.last_holiday,
                upcoming_holidays=_holidays(c.upcoming_holidays),
                weekend=c.weekend,
                holidays=_holidays(c.holidays),
            )
            for c in view.calendars
        ],
        missing=[
            MissingRuleOut(
                task=task_out(m.task),
                tracking_number=m.request.tracking_number,
                agency_name=m.request.agency.name,
            )
            for m in view.missing
        ],
    )
