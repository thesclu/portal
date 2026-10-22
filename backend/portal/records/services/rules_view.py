"""The Rules and calendars screen's read: what the rules file says, and what it is missing.

Read only, and nothing here is a rule. Every number, citation and date comes from `rule_data()`
(the shipped `rules.toml` and `calendars.toml`, or the fictional ones a test substitutes), passed
through untouched: `Rule.days` is handed on as `count` and this module writes no number of its own.
The rules come back in the order the file lists them, which is the order owner wrote them in.
Adding or changing a rule is an edit to `rules.toml` under citation, by owner, never an insert
(DECISIONS, "Design v2"), so there is deliberately nothing here that writes.

The rule files hold dates; a holiday's name is what an administrator typed into the managed
table (P20b2), or none. The upcoming holidays are all
of them from the calendar's own today on; "the next few" is the screen's to slice, which keeps the
count out of this module.

The provisions with no rule are read from the open `enter_deadline_rules` tasks: `file_request`
opens one whenever a request or one of its items names a clock no rule covers, and its description
says which. They are listed as the tasks they are.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from django.utils import timezone

from portal.core.services.rules import holiday_names
from portal.records.models import Request, Task
from portal.records.services import access
from portal.records.services.access import Viewer
from portal.records.services.common import local_date, rule_data
from portal.records.services.overview import Holiday, holidays_of


@dataclass(frozen=True)
class RuleRow:
    id: str
    law: str
    provision: str
    deadline_type: str
    count: int
    unit: str
    citation: str
    effective_from: date
    effective_to: date | None
    roll_forward_on_non_business_day: bool


@dataclass(frozen=True)
class CalendarSummary:
    key: str
    timezone: str
    # The laws (agencies' `records_law`) that count their clocks on this calendar.
    laws: list[str]
    holiday_count: int
    last_holiday: date | None
    upcoming_holidays: list[Holiday]
    weekend: list[int]
    holidays: list[Holiday]


@dataclass(frozen=True)
class MissingRule:
    task: Task
    request: Request


@dataclass(frozen=True)
class RulesView:
    rules: list[RuleRow]
    calendars: list[CalendarSummary]
    missing: list[MissingRule]


def rules_view(viewer: Viewer | UUID | None = None, *, now: datetime | None = None) -> RulesView:
    now = now or timezone.now()
    data = rule_data()
    rules = [
        RuleRow(
            id=rule.id,
            law=rule.law,
            provision=rule.provision,
            deadline_type=rule.deadline_type,
            count=rule.days,
            unit=rule.unit,
            roll_forward_on_non_business_day=rule.roll_forward_on_non_business_day,
            citation=rule.citation,
            effective_from=rule.effective_from,
            effective_to=rule.effective_to,
        )
        for rule in data.rules
    ]
    calendars = []
    names = holiday_names()
    for key, calendar in sorted(data.calendars.items()):
        today = local_date(now, calendar)
        calendars.append(
            CalendarSummary(
                key=key,
                timezone=calendar.timezone,
                laws=sorted(law for law, cal_key in data.law_calendar.items() if cal_key == key),
                holiday_count=len(calendar.holidays),
                last_holiday=max(calendar.holidays, default=None),
                upcoming_holidays=holidays_of(
                    key, (d for d in calendar.holidays if d >= today), names
                ),
                weekend=sorted(calendar.weekend),
                holidays=holidays_of(key, calendar.holidays, names),
            )
        )
    open_tasks = Task.objects.filter(task_type="enter_deadline_rules", completed_at__isnull=True)
    if viewer is not None:
        # "What is missing" names requests; one the viewer may not see is not named to them.
        open_tasks = open_tasks.filter(access.visible_q(access.as_viewer(viewer), "request__"))
    by_request = {
        r.pk: r
        for r in Request.objects.filter(
            pk__in=open_tasks.values_list("request_id", flat=True)
        ).select_related("agency")
    }
    missing = [
        MissingRule(task=t, request=by_request[t.request_id])
        for t in open_tasks.order_by("created_at")
    ]
    return RulesView(rules=rules, calendars=calendars, missing=missing)
