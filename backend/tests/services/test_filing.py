"""Filing: it happens once, its clocks start on the agency's own date, and a missing rule is
reported rather than swallowed."""

from datetime import UTC, date, datetime, timedelta

import pytest
from django.utils import timezone
from portal.records.models import Deadline, Task
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service
from portal.records.services.common import InvalidTransition, NotAllowed

from .conftest import FILED_AT


def test_a_request_can_only_be_filed_once(reserved, staff_actor, rules) -> None:
    filing_service.file_request(staff_actor, reserved.pk, filed_at=FILED_AT, channel="email")
    with pytest.raises(InvalidTransition, match="only a request that is pending filing"):
        filing_service.file_request(staff_actor, reserved.pk, filed_at=FILED_AT, channel="email")


def test_no_rule_for_the_law_still_files_and_raises_a_task(
    db, staff_actor, staff_user, norules_agency, rules
) -> None:
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
    assert result.request.current_status_id == "filed"
    assert result.request.filed_at == FILED_AT
    assert Deadline.objects.filter(request_id=request.pk).count() == 0
    assert [t.task_type for t in result.tasks] == ["enter_deadline_rules"]
    assert "TESTLAW_NORULES" in Task.objects.get(request_id=request.pk).description


def test_an_unmapped_law_files_and_raises_a_task_too(
    db, staff_actor, staff_user, agency, rules
) -> None:
    """A law with no calendar at all -- the agency was entered before anyone mapped its law."""
    from portal.records.services import directory

    directory.update_agency(staff_actor, agency.pk, "no calendar yet", records_law="TESTLAW_NOCAL")
    request = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Anything",
        request_text="Anything at all.",
    )
    result = filing_service.file_request(
        staff_actor, request.pk, filed_at=FILED_AT, channel="email"
    )
    assert result.request.current_status_id == "filed"
    assert [t.task_type for t in result.tasks] == ["enter_deadline_rules"]
    assert "TESTLAW_NOCAL" in result.tasks[0].description


def test_clock_start_is_the_agency_local_date_not_the_utc_one(reserved, staff_actor, rules) -> None:
    """06:30 UTC on the 10th is 22:30 on the 9th in America/Los_Angeles, and the clock starts on
    the day the agency was actually handed the request."""
    filed_at = datetime(2026, 2, 10, 6, 30, tzinfo=UTC)
    result = filing_service.file_request(
        staff_actor, reserved.pk, filed_at=filed_at, channel="email"
    )
    determination = next(
        d for d in result.deadlines if d.deadline_type == "statutory_determination"
    )
    assert determination.due_at == date(2026, 2, 16)
    # The same instant read as a UTC date would have started the clock a day later.
    assert filed_at.date() == date(2026, 2, 10)
    assert determination.due_at != date(2026, 2, 10) + timedelta(days=7)


def test_a_backdated_filing_leaves_updated_at_near_now(reserved, staff_actor, rules) -> None:
    """D3: `filed_at` can be backdated -- recording a request that was actually sent last week --
    but `updated_at` means "when this row last changed" everywhere else in the system (the
    schema's own recompute triggers and `reserve_request`'s `updated_at=now` both use the write
    instant, never a domain date), so a backdated filing must not drag `updated_at` into the past
    to match it."""
    backdated = FILED_AT - timedelta(days=7)
    result = filing_service.file_request(
        staff_actor, reserved.pk, filed_at=backdated, channel="email"
    )
    after = timezone.now()
    assert result.request.filed_at == backdated
    assert result.request.updated_at != backdated
    # updated_at must track the real write instant, not the backdated filed_at -- these are
    # months apart under this suite's fixed FILED_AT constant (2026-02) vs the real wall clock
    # (2026-09), so a generous tolerance still cleanly tells "near now" apart from "pinned to
    # backdated". (Not a tight before/after bracket: later triggers in file_request re-stamp
    # updated_at from SQL's own now(), which is fixed for the whole test transaction under
    # pytest-django's default db fixture, not per-statement -- see the thoughts file.)
    assert abs((after - result.request.updated_at).total_seconds()) < 30


def test_an_unexpected_service_error_propagates_instead_of_becoming_a_missing_rule_task(
    reserved, staff_actor, rules, monkeypatch
) -> None:
    """R5: file_request's except clause is for the specific "no rule/calendar entered yet"
    errors, not every ServiceError. Something genuinely wrong inside the same try block (here,
    agency_calendar unexpectedly refusing) must propagate, not be swallowed into a task that
    misrepresents what actually happened."""

    def _boom(agency):
        raise NotAllowed("simulated unrelated failure")

    monkeypatch.setattr(filing_service, "agency_calendar", _boom)
    with pytest.raises(NotAllowed, match="simulated unrelated failure"):
        filing_service.file_request(staff_actor, reserved.pk, filed_at=FILED_AT, channel="email")
    assert Task.objects.filter(request_id=reserved.pk).count() == 0


def test_no_rule_tests_are_not_vacuous(
    db, staff_actor, staff_user, norules_agency, rules, monkeypatch
) -> None:
    """R6 sanity: prove the 'no rule' assertions above actually exercise the missing-rule path,
    by forcing the opposite (a rule always resolves) and confirming the assertions THEN fail --
    a same-shaped probe run manually below, not left as a permanent test (it would just duplicate
    the two tests above); see the R6 section of the worker thoughts file for the transcript."""
    request = requests_service.reserve_request(
        staff_actor,
        agency_id=norules_agency.pk,
        requester_user_id=staff_user.pk,
        subject="Anything",
        request_text="Anything at all.",
    )
    from deadline_rules import DeadlineComputation

    monkeypatch.setattr(
        filing_service,
        "compute_deadlines",
        lambda **kwargs: DeadlineComputation(specs=(), unresolved=()),
    )
    result = filing_service.file_request(
        staff_actor, request.pk, filed_at=FILED_AT, channel="email"
    )
    assert result.tasks == []
