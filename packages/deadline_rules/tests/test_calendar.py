"""Unit tests for the counting primitives in deadline_rules.calendar.

Every case here is hand-verifiable by inspection (small day spans, no fixture files) --
these establish ground truth for the primitives that test_compute.py's table-driven cases then
build on without re-deriving the arithmetic.
"""

from datetime import date, timedelta

import pytest
from deadline_rules.calendar import (
    add_business_days,
    add_calendar_days,
    is_business_day,
    roll_forward,
    today_in,
)
from deadline_rules.errors import RulesDataError
from deadline_rules.model import Calendar

HOLIDAY = date(2026, 1, 13)  # a Tuesday; fictional testland holiday, matches the fixtures
SECOND_HOLIDAY = date(2026, 11, 11)  # a Wednesday
TESTLAND = Calendar(
    key="testland",
    timezone="America/New_York",
    holidays=frozenset({HOLIDAY, SECOND_HOLIDAY}),
)


def test_is_business_day_true_for_an_ordinary_weekday() -> None:
    assert date(2026, 1, 6).strftime("%A") == "Tuesday"
    assert is_business_day(date(2026, 1, 6), TESTLAND) is True


def test_is_business_day_false_for_saturday_and_sunday() -> None:
    assert date(2026, 1, 3).strftime("%A") == "Saturday"
    assert date(2026, 1, 4).strftime("%A") == "Sunday"
    assert is_business_day(date(2026, 1, 3), TESTLAND) is False
    assert is_business_day(date(2026, 1, 4), TESTLAND) is False


def test_is_business_day_false_for_a_listed_holiday_even_on_a_weekday() -> None:
    assert HOLIDAY.strftime("%A") == "Tuesday"
    assert is_business_day(HOLIDAY, TESTLAND) is False


def test_is_business_day_true_the_day_after_a_holiday() -> None:
    assert is_business_day(HOLIDAY.replace(day=HOLIDAY.day + 1), TESTLAND) is True


def test_add_calendar_days_is_plain_date_arithmetic_ignoring_the_calendar() -> None:
    assert add_calendar_days(date(2026, 1, 1), 5) == date(2026, 1, 6)
    assert add_calendar_days(date(2026, 1, 1), 0) == date(2026, 1, 1)


def test_add_business_days_zero_returns_the_start_date_unchanged() -> None:
    """d itself counts as zero, not one -- the first counted day is strictly after d."""
    assert add_business_days(date(2026, 1, 6), 0, TESTLAND) == date(2026, 1, 6)


def test_add_business_days_skips_a_weekend() -> None:
    # Friday 2026-01-02 + 1 business day = Monday 2026-01-05 (skips Sat 01-03, Sun 01-04).
    assert date(2026, 1, 2).strftime("%A") == "Friday"
    assert add_business_days(date(2026, 1, 2), 1, TESTLAND) == date(2026, 1, 5)


def test_add_business_days_skips_a_holiday() -> None:
    # Monday 2026-01-12 + 1 business day would land on the 01-13 holiday; skip to 01-14.
    assert add_business_days(date(2026, 1, 12), 1, TESTLAND) == date(2026, 1, 14)


def test_add_business_days_counts_only_business_days_over_a_longer_span() -> None:
    # Monday 2026-01-05 + 12 business days lands on 2026-01-22, five calendar days later than
    # a naive +12 (2026-01-17): two weekends (01-10/11, 01-17/18 = 4 days) plus the 01-13
    # holiday (1 day) are skipped along the way.
    assert add_business_days(date(2026, 1, 5), 12, TESTLAND) == date(2026, 1, 22)


def test_roll_forward_returns_the_same_date_when_already_a_business_day() -> None:
    assert roll_forward(date(2026, 1, 6), TESTLAND) == date(2026, 1, 6)


def test_roll_forward_moves_a_saturday_to_the_following_monday() -> None:
    assert roll_forward(date(2026, 1, 3), TESTLAND) == date(2026, 1, 5)


def test_roll_forward_moves_a_holiday_to_the_next_business_day() -> None:
    assert roll_forward(HOLIDAY, TESTLAND) == date(2026, 1, 14)


# ---- G4: a calendar with no reachable business day fails as a package error, not a bare
# ValueError from walking `date` off its year-10000 end. loader.py separately rejects the
# load-time cause (weekend covering all seven weekdays); these construct the Calendar directly,
# bypassing the loader on purpose, to pin calendar.py's OWN bound as an independent line of
# defense (a Calendar can be built by hand, not only loaded from TOML).

ALL_WEEKEND = Calendar(
    key="all-blocked", timezone="UTC", holidays=frozenset(), weekend=frozenset(range(7))
)


def test_add_business_days_raises_rules_data_error_when_no_business_day_is_reachable() -> None:
    with pytest.raises(RulesDataError):
        add_business_days(date(2026, 1, 1), 5, ALL_WEEKEND)


def test_roll_forward_raises_rules_data_error_when_no_business_day_is_reachable() -> None:
    with pytest.raises(RulesDataError):
        roll_forward(date(2026, 1, 1), ALL_WEEKEND)


def test_roll_forward_still_resolves_correctly_across_a_long_finite_holiday_span() -> None:
    """Must-not-regress: the step bound above must not break legitimate data. A long but FINITE
    holiday span (5000 consecutive dates, ~13.7 years -- well under the step bound) still
    resolves to the first business day after it, not an error."""
    span_start = date(2026, 1, 1)
    long_holidays = frozenset(span_start + timedelta(days=i) for i in range(5000))
    cal = Calendar(
        key="longspan", timezone="UTC", holidays=long_holidays, weekend=frozenset({5, 6})
    )
    result = roll_forward(span_start, cal)
    assert result not in long_holidays
    assert is_business_day(result, cal) is True


def test_today_in_returns_a_date_in_the_calendars_timezone() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    before = datetime.now(ZoneInfo(TESTLAND.timezone)).date()
    result = today_in(TESTLAND)
    after = datetime.now(ZoneInfo(TESTLAND.timezone)).date()
    assert before <= result <= after


def test_today_in_is_exact_for_the_calendars_own_timezone_not_a_fixed_one() -> None:
    """Non-negotiable #7: `due_at` is counted on the jurisdiction's own local civil date, so
    `today_in` must be exact for whichever timezone a calendar carries -- not merely 'close
    enough' to whatever zone happens to be hardcoded. The test above compares against `now()` in
    the SAME zone it's testing, so a `today_in` hardcoded to UTC still passes it whenever UTC and
    that zone share a civil date -- true for ~20 of 24 hours a day for a UTC-4/-5 zone like
    America/New_York, so it would rarely catch a UTC-hardcoded implementation.

    These two zones are picked so their civil dates ALWAYS differ, at every possible instant
    (Pacific/Kiritimati is UTC+14, Etc/GMT+12 is UTC-12 -- a 26-hour gap, more than a full day, so
    the two local dates can never coincide): this assertion is never accidentally vacuous.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ahead = Calendar(key="ahead", timezone="Pacific/Kiritimati", holidays=frozenset())
    behind = Calendar(key="behind", timezone="Etc/GMT+12", holidays=frozenset())

    expected_ahead = datetime.now(ZoneInfo(ahead.timezone)).date()
    expected_behind = datetime.now(ZoneInfo(behind.timezone)).date()
    assert expected_ahead != expected_behind, "test zones must straddle the date line right now"

    assert today_in(ahead) == expected_ahead
    assert today_in(behind) == expected_behind
    assert today_in(ahead) != today_in(behind)
