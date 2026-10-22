"""Counting primitives: business-day arithmetic, roll-forward, and the calendar's local today.

Counting convention (pinned by tests in test_calendar.py and test_compute.py):
    due = clock_start + N (calendar unit), or the N-th business day strictly after clock_start
    (business unit); then, only if the rule says `roll_forward_on_non_business_day`, roll the
    result forward to the next business day if it is not already one.

`d` itself is never counted -- counting always starts from the first day strictly after `d`. A
calendar-unit count is plain date arithmetic and does not consult the calendar at all; only the
business-unit count and the roll-forward step look at weekends and holidays.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .errors import RulesDataError
from .model import Calendar

# A generous bound on how far add_business_days/roll_forward will step looking for a business
# day, so a calendar with effectively no reachable business day fails as a package error instead
# of spinning toward date's year-10000 ceiling (a bare ValueError from the stdlib, escaping this
# package's error hierarchy). Comfortably above any legitimate holiday span a human would ever
# enter by hand; loader.py separately rejects the load-time cause (a weekend covering all seven
# weekdays) before this bound is ever needed for that case.
_MAX_SEARCH_STEPS = 20_000


def _next(d: date) -> date:
    """The date immediately following `d`, by ordinal rather than `timedelta`.

    `add_business_days`/`roll_forward` step forward one at a time regardless of what `n` a rule
    carries -- that single fixed step is universal date arithmetic, never a statutory count, so
    it is written without a `timedelta(days=<literal>)` call.
    """
    return date.fromordinal(d.toordinal() + 1)


def is_business_day(d: date, cal: Calendar) -> bool:
    """A business day is neither a weekend day nor a listed holiday for `cal`."""
    return d.weekday() not in cal.weekend and d not in cal.holidays


def add_calendar_days(d: date, n: int) -> date:
    """Plain calendar-day arithmetic: `d` plus `n` days. No calendar involved."""
    return d + timedelta(days=n)


def add_business_days(d: date, n: int, cal: Calendar) -> date:
    """The n-th business day strictly after `d`. `d` itself counts as zero, not one."""
    current = d
    counted = 0
    steps = 0
    while counted < n:
        current = _next(current)
        steps += 1
        if steps > _MAX_SEARCH_STEPS:
            raise RulesDataError(
                f"calendar {cal.key!r}: found no business day within {_MAX_SEARCH_STEPS} steps "
                f"of {d.isoformat()} -- check 'weekend' and 'holidays'"
            )
        if is_business_day(current, cal):
            counted += 1
    return current


def roll_forward(d: date, cal: Calendar) -> date:
    """`d` if it is already a business day, else the next business day after it."""
    current = d
    steps = 0
    while not is_business_day(current, cal):
        current = _next(current)
        steps += 1
        if steps > _MAX_SEARCH_STEPS:
            raise RulesDataError(
                f"calendar {cal.key!r}: found no business day within {_MAX_SEARCH_STEPS} steps "
                f"of {d.isoformat()} -- check 'weekend' and 'holidays'"
            )
    return current


def today_in(cal: Calendar) -> date:
    """The current civil date in `cal`'s timezone."""
    return datetime.now(ZoneInfo(cal.timezone)).date()
