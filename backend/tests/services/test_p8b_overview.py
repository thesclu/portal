"""What Home and the Calendar read, and what the Rules screen reads from the rules file.

Every number in a rule below is a fictional TESTLAW one (`fixtures/rules_test.toml`); nothing here
asserts what a real statute says. "Now" is passed in, never read from the clock, so the day a test
runs cannot change its answer.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from portal.core.models import Setting
from portal.records.models import Deadline, Request
from portal.records.services import deadlines as deadlines_service
from portal.records.services import directory
from portal.records.services import filing as filing_service
from portal.records.services import overview as overview_service
from portal.records.services import requests as requests_service
from portal.records.services import rules_view as rules_view_service
from portal.records.services import sources as sources_service
from portal.records.services import status as status_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import ServiceError

from tests.p8b_helpers import make_source

from .conftest import FILED_AT

pytestmark = pytest.mark.usefixtures("as_each_role")

# 10:00 on 2026-02-10 in testland. The determination clock runs out on the 17th, the production
# clock a good deal later; the tests read the dates back rather than trusting this arithmetic.
NOW = datetime(2026, 2, 20, 20, 0, tzinfo=UTC)  # noon on the 20th in testland


@pytest.fixture
def filed(reserved, staff_actor, rules):
    filing_service.file_request(staff_actor, reserved.pk, filed_at=FILED_AT, channel="email")
    return Request.objects.get(pk=reserved.pk)


def _due(request, kind):
    return Deadline.objects.get(request_id=request.pk, deadline_type=kind, status="active").due_at


def _rows(rows):
    return [(r.request.tracking_number, r.deadline.deadline_type) for r in rows]


# ---- Home --------------------------------------------------------------------------------------


def test_overdue_and_due_soon_are_read_from_the_agencys_own_date(filed, staff_user) -> None:
    determination = _due(filed, "statutory_determination")
    production = _due(filed, "production")
    home = overview_service.home(staff_user.pk, now=NOW)

    (overdue,) = home.overdue
    assert overdue.deadline.deadline_type == "statutory_determination"
    assert overdue.kind_label == "Determination"
    assert overdue.days_from_today == (determination - date(2026, 2, 20)).days
    assert overdue.days_from_today < 0
    assert overdue.request.agency.name == "Test Agency"
    # The production clock is further off than the window, so it is neither.
    assert (production - date(2026, 2, 20)).days > home.due_soon_window_days
    assert home.due_soon == []


def test_a_deadline_due_today_where_the_agency_is_is_not_overdue_even_if_it_is_tomorrow_in_utc(
    filed, staff_user
) -> None:
    determination = _due(filed, "statutory_determination")
    # Midnight UTC on the following day is still the evening before in testland.
    late = datetime.combine(determination + timedelta(days=1), time(3, 0), tzinfo=UTC)
    assert late.date() > determination
    home = overview_service.home(staff_user.pk, now=late)
    assert home.overdue == []
    (soon,) = [r for r in home.due_soon if r.deadline.deadline_type == "statutory_determination"]
    assert soon.days_from_today == 0


def test_the_due_soon_window_is_a_settings_row(filed, staff_user) -> None:
    production = _due(filed, "production")
    wide = (production - date(2026, 2, 20)).days
    Setting.objects.update_or_create(
        key="records.due_soon_window_days", defaults={"value": wide, "description": "test"}
    )
    home = overview_service.home(staff_user.pk, now=NOW)
    assert home.due_soon_window_days == wide
    assert _rows(home.due_soon) == [(filed.tracking_number, "production")]
    Setting.objects.filter(key="records.due_soon_window_days").update(value=wide - 1)
    assert overview_service.home(staff_user.pk, now=NOW).due_soon == []


def test_only_the_earliest_active_date_of_a_kind_counts_on_home(
    filed, staff_actor, staff_user
) -> None:
    """The statute says one date and the agency's letter another; the earlier is THE deadline."""
    determination_due = _due(filed, "statutory_determination")
    deadlines_service.open_deadline(
        staff_actor,
        filed.pk,
        deadline_type="statutory_determination",
        due_at=date(2026, 2, 25),
        basis="agency_commitment",
    )
    home = overview_service.home(staff_user.pk, now=NOW)
    assert _rows(home.overdue) == [(filed.tracking_number, "statutory_determination")]
    assert all(r.deadline.deadline_type != "statutory_determination" for r in home.due_soon)
    # The Calendar, by contrast, shows both dates.
    view = overview_service.calendar(staff_user.pk, date(2026, 2, 1), date(2026, 2, 28), now=NOW)
    assert sorted(r.deadline.due_at for r in view.deadlines) == [
        determination_due,
        date(2026, 2, 25),
    ]


def test_a_request_with_a_terminal_status_is_left_out_of_both(
    filed, staff_actor, staff_user
) -> None:
    status_service.override_status(staff_actor, filed.pk, "closed", "withdrawn by the requester")
    home = overview_service.home(staff_user.pk, now=NOW)
    assert home.overdue == [] and home.due_soon == []
    view = overview_service.calendar(staff_user.pk, date(2026, 1, 1), date(2026, 12, 31), now=NOW)
    assert view.deadlines == []


def test_a_deadline_that_is_no_longer_active_is_not_overdue(filed, staff_actor, staff_user) -> None:
    deadline = Deadline.objects.get(request_id=filed.pk, deadline_type="statutory_determination")
    deadlines_service.waive_deadline(staff_actor, deadline.pk, "agency answered by phone")
    assert overview_service.home(staff_user.pk, now=NOW).overdue == []


def test_an_agency_whose_law_has_no_calendar_falls_back_to_the_utc_date(
    staff_actor, staff_user, agency
) -> None:
    directory.update_agency(staff_actor, agency.pk, "no calendar yet", records_law="TESTLAW_NOCAL")
    request = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Anything",
        request_text="Anything at all.",
    )
    deadlines_service.open_deadline(
        staff_actor,
        request.pk,
        deadline_type="other",
        due_at=date(2026, 2, 19),
        basis="agency_commitment",
    )
    home = overview_service.home(staff_user.pk, now=datetime(2026, 2, 20, 1, 0, tzinfo=UTC))
    (row,) = home.overdue
    assert row.days_from_today == -1 and row.kind_label == "Other"


def test_home_lists_the_viewers_open_tasks_soonest_due_first(
    filed, staff_actor, staff_user, colleague_user
) -> None:
    later = tasks_service.create_task(
        staff_actor,
        filed.pk,
        "manual",
        "Later",
        due_at=datetime(2026, 3, 9, tzinfo=UTC),
        assigned_to_user_id=staff_user.pk,
    )
    soon = tasks_service.create_task(
        staff_actor,
        filed.pk,
        "manual",
        "Soon",
        due_at=datetime(2026, 3, 2, tzinfo=UTC),
        assigned_to_user_id=staff_user.pk,
    )
    undated = tasks_service.create_task(
        staff_actor, filed.pk, "manual", "Undated", assigned_to_user_id=staff_user.pk
    )
    done = tasks_service.create_task(
        staff_actor, filed.pk, "manual", "Done", assigned_to_user_id=staff_user.pk
    )
    tasks_service.complete_task(staff_actor, done.pk)
    tasks_service.create_task(
        staff_actor, filed.pk, "manual", "Theirs", assigned_to_user_id=colleague_user.pk
    )
    tasks_service.create_task(staff_actor, filed.pk, "manual", "Nobody's")

    home = overview_service.home(staff_user.pk, now=NOW)
    assert [t.task.pk for t in home.assigned_tasks] == [soon.pk, later.pk, undated.pk]
    assert home.assigned_tasks[0].request.agency.name == "Test Agency"
    theirs = overview_service.home(colleague_user.pk, now=NOW).assigned_tasks
    assert [t.task.description for t in theirs] == ["Theirs"]


def test_home_carries_the_flagged_mail_and_the_bucket_counts_from_the_inboxs_own_query(
    staff_actor, staff_user
) -> None:
    flagged = make_source(staff_actor, flag=("delivery_failed", "bounced"))
    make_source(staff_actor)
    home = overview_service.home(staff_user.pk, now=NOW)
    assert [r.source.pk for r in home.flagged.rows] == [flagged.pk]
    assert home.flagged.counts == {"flagged": 1, "new": 0, "all": 2}
    sources_service.mark_source_reviewed(staff_actor, flagged.pk)
    assert overview_service.home(staff_user.pk, now=NOW).flagged.rows == []


# ---- the Calendar ------------------------------------------------------------------------------


def test_the_calendar_is_every_active_deadline_in_the_range_by_date(filed, staff_user) -> None:
    determination = _due(filed, "statutory_determination")
    production = _due(filed, "production")
    only_first = overview_service.calendar(staff_user.pk, determination, determination, now=NOW)
    assert [r.deadline.deadline_type for r in only_first.deadlines] == ["statutory_determination"]

    both = overview_service.calendar(staff_user.pk, determination, production, now=NOW)
    assert [r.deadline.due_at for r in both.deadlines] == [determination, production]
    assert both.deadlines[1].days_from_today == (production - date(2026, 2, 20)).days

    before = overview_service.calendar(staff_user.pk, date(2025, 1, 1), date(2025, 12, 31), now=NOW)
    assert before.deadlines == []


def test_the_calendar_carries_each_calendars_weekend_and_the_holidays_in_range(staff_user) -> None:
    view = overview_service.calendar(staff_user.pk, date(2026, 1, 1), date(2026, 6, 30), now=NOW)
    (testland,) = view.calendars
    assert testland.key == "testland" and testland.timezone == "America/Los_Angeles"
    assert testland.weekend == [5, 6]
    assert [(h.date, h.name) for h in testland.holidays] == [(date(2026, 1, 13), None)]
    later = overview_service.calendar(staff_user.pk, date(2026, 7, 1), date(2026, 12, 31), now=NOW)
    assert [h.date for h in later.calendars[0].holidays] == [date(2026, 11, 11)]


def test_the_calendar_refuses_a_range_that_ends_before_it_starts(staff_user) -> None:
    with pytest.raises(ServiceError, match="before its start"):
        overview_service.calendar(staff_user.pk, date(2026, 3, 2), date(2026, 3, 1))


# ---- the Rules screen --------------------------------------------------------------------------


def test_the_rules_are_the_files_rules_passed_through_and_no_others(rules) -> None:
    view = rules_view_service.rules_view(now=NOW)
    assert [(r.id, r.law, r.provision, r.deadline_type, r.count, r.unit) for r in view.rules] == [
        (
            "testlaw.determination.general.2020-01-01",
            "TESTLAW",
            "",
            "statutory_determination",
            7,
            "calendar_days",
        ),
        ("testlaw.production.general.2020-01-01", "TESTLAW", "", "production", 30, "calendar_days"),
        (
            "testlaw.production.widgets.2020-01-01",
            "TESTLAW",
            "widgets",
            "production",
            12,
            "business_days",
        ),
    ]
    first = view.rules[0]
    assert first.citation == "TESTLAW § 1 (fictional statute, for tests only)"
    assert (first.effective_from, first.effective_to) == (date(2020, 1, 1), None)
    assert {r.law for r in view.rules} == {"TESTLAW"}


def test_each_calendars_zone_holiday_count_last_holiday_and_upcoming_holidays(rules) -> None:
    view = rules_view_service.rules_view(now=NOW)
    (testland,) = view.calendars
    assert testland.key == "testland" and testland.timezone == "America/Los_Angeles"
    assert testland.laws == ["TESTLAW", "TESTLAW_NORULES"]
    assert testland.holiday_count == 2 and testland.last_holiday == date(2026, 11, 11)
    assert [h.date for h in testland.upcoming_holidays] == [date(2026, 11, 11)]
    assert all(h.name is None for h in testland.upcoming_holidays)

    early = rules_view_service.rules_view(now=datetime(2025, 12, 1, tzinfo=UTC))
    assert [h.date for h in early.calendars[0].upcoming_holidays] == [
        date(2026, 1, 13),
        date(2026, 11, 11),
    ]
    late = rules_view_service.rules_view(now=datetime(2026, 12, 1, tzinfo=UTC))
    assert late.calendars[0].upcoming_holidays == []
    assert late.calendars[0].last_holiday == date(2026, 11, 11)


def test_a_holiday_today_is_upcoming_until_it_is_over(rules) -> None:
    on_the_day = datetime(2026, 11, 11, 20, 0, tzinfo=UTC)
    assert [
        h.date for h in rules_view_service.rules_view(now=on_the_day).calendars[0].upcoming_holidays
    ] == [date(2026, 11, 11)]


def test_the_provisions_with_no_rule_are_the_open_enter_deadline_rules_tasks(
    staff_actor, staff_user, norules_agency, rules
) -> None:
    assert rules_view_service.rules_view(now=NOW).missing == []
    request = requests_service.reserve_request(
        staff_actor,
        agency_id=norules_agency.pk,
        requester_user_id=staff_user.pk,
        subject="Anything",
        request_text="Anything at all.",
    )
    result = filing_service.file_request(
        staff_actor, request.pk, filed_at=FILED_AT, channel="email"
    )
    (task,) = result.tasks
    (missing,) = rules_view_service.rules_view(now=NOW).missing
    assert missing.task.pk == task.pk and missing.task.task_type == "enter_deadline_rules"
    assert missing.request.tracking_number == request.tracking_number
    assert missing.request.agency.name == "Test Agency Without Rules"
    tasks_service.complete_task(staff_actor, task.pk)
    assert rules_view_service.rules_view(now=NOW).missing == []
