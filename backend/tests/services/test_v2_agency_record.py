"""An agency's response record: computed from events and deadlines, stored nowhere."""

from datetime import timedelta

import pytest
from portal.records.models import Deadline
from portal.records.services import agency_record, directory
from portal.records.services import events as events_service
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service

from .conftest import FILED_AT

pytestmark = pytest.mark.usefixtures("as_each_role")


def _filed(actor, user, agency, subject):
    request = requests_service.reserve_request(
        actor, agency_id=agency.pk, requester_user_id=user.pk, subject=subject, request_text="Body."
    )
    filing_service.file_request(actor, request.pk, filed_at=FILED_AT, channel="email")
    return request


def _determination(actor, request, after: timedelta):
    """The agency answers `after` the filing; the determination deadline is satisfied by it."""
    from portal.records.services import deadlines as deadlines_service

    event = events_service.record_event(
        actor,
        request.pk,
        event_type="determination",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + after,
        display_body="Determination.",
        status="determination_issued",
    )
    deadline = Deadline.objects.get(request_id=request.pk, deadline_type="statutory_determination")
    deadlines_service.satisfy_deadline(actor, deadline.pk, event.pk)
    return event


def _extension(actor, request):
    return events_service.record_event(
        actor,
        request.pk,
        event_type="extension_notice",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + timedelta(days=2),
        display_body="We need more time.",
    )


@pytest.fixture
def three(staff_actor, staff_user, agency, rules):
    quick = _filed(staff_actor, staff_user, agency, "quick")
    slow = _filed(staff_actor, staff_user, agency, "slow")
    silent = _filed(staff_actor, staff_user, agency, "silent")
    return quick, slow, silent


def test_the_record_is_computed_from_what_the_agency_actually_did(
    three, staff_actor, agency
) -> None:
    quick, slow, silent = three
    # Filed 10:00 on 2026-02-10 Pacific. One answer three calendar days later, one ten days later,
    # and the determination deadline is the TESTLAW fixture's, due on the 17th.
    _determination(staff_actor, quick, timedelta(days=3))
    _determination(staff_actor, slow, timedelta(days=10))
    _extension(staff_actor, quick)

    record = agency_record.response_record(agency, [quick.pk, slow.pk, silent.pk])

    assert record.median_days_to_determination == 6.5
    assert (record.determinations_on_time, record.determinations_total) == (1, 2)
    assert record.extensions_taken == 1


def test_a_voided_event_counts_for_nothing(three, staff_actor, agency) -> None:
    quick, slow, silent = three
    _determination(staff_actor, quick, timedelta(days=3))
    late = _determination(staff_actor, slow, timedelta(days=10))
    voided_extension = _extension(staff_actor, slow)
    events_service.void_event(staff_actor, late.pk, "belongs to another request")
    events_service.void_event(staff_actor, voided_extension.pk, "mis-logged")

    record = agency_record.response_record(agency, [quick.pk, slow.pk, silent.pk])

    assert record.median_days_to_determination == 3.0
    assert (record.determinations_on_time, record.determinations_total) == (1, 1)
    assert record.extensions_taken == 0


def test_only_the_requests_it_is_given_are_counted(three, staff_actor, agency) -> None:
    quick, slow, silent = three
    _determination(staff_actor, quick, timedelta(days=3))
    _determination(staff_actor, slow, timedelta(days=10))
    _extension(staff_actor, slow)

    # A viewer who may see only `quick` must not have `slow` move any figure they are shown.
    record = agency_record.response_record(agency, [quick.pk])

    assert record.median_days_to_determination == 3.0
    assert (record.determinations_on_time, record.determinations_total) == (1, 1)
    assert record.extensions_taken == 0


def test_an_agency_with_no_history_has_nothing_to_report(agency) -> None:
    record = agency_record.response_record(agency, [])
    assert record.median_days_to_determination is None
    assert (record.determinations_on_time, record.determinations_total) == (0, 0)
    assert record.extensions_taken == 0


def test_without_a_calendar_the_date_figures_are_none_and_the_count_still_works(
    staff_actor, staff_user
) -> None:
    unmapped = directory.create_agency(
        staff_actor, name="No Calendar Agency", jurisdiction="Testland", records_law=None
    )
    request = requests_service.reserve_request(
        staff_actor,
        agency_id=unmapped.pk,
        requester_user_id=staff_user.pk,
        subject="Unmapped",
        request_text="Body.",
    )
    _extension(staff_actor, request)
    record = agency_record.response_record(unmapped, [request.pk])
    assert record.median_days_to_determination is None
    assert record.determinations_on_time is None and record.determinations_total is None
    assert record.extensions_taken == 1
