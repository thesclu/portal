"""Requests in design v2: who is working one, the docket's new filters and its signed day count,
and an item's own provision reaching the clock whichever door files the request."""

import uuid
from datetime import UTC, date, datetime

import pytest
from portal.records.models import AuditLog, Deadline, Request
from portal.records.services import deadlines as deadlines_service
from portal.records.services import directory
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service
from portal.records.services.common import NotAllowed, NotFound, ServiceError

from tests.p20h_helpers import give

from .conftest import FILED_AT

pytestmark = pytest.mark.usefixtures("as_each_role")


class _FrozenClock:
    """Same device test_concurrency.py and test_chokepoint.py use: a wall clock stopped at one
    instant, so a signed day count is a fixed number rather than a function of the date today."""

    def __init__(self, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


# ---- assignment --------------------------------------------------------------------------------


def test_assigning_a_request_audits_it_and_moves_updated_at(
    reserved, staff_actor, colleague_user
) -> None:
    before = reserved.updated_at
    request = requests_service.assign_request(staff_actor, reserved.pk, colleague_user.pk)
    assert request.assigned_to_id == colleague_user.pk
    assert request.updated_at > before
    (line,) = AuditLog.objects.filter(
        entity_type="requests", field_name="requests.assigned_to_user_id"
    )
    assert (line.entity_id, line.old_value, line.new_value) == (
        reserved.pk,
        None,
        str(colleague_user.pk),
    )
    assert line.edited_by_id == staff_actor.user_id

    request = requests_service.assign_request(staff_actor, reserved.pk, None)
    assert request.assigned_to_id is None
    assert AuditLog.objects.filter(field_name="requests.assigned_to_user_id").count() == 2
    requests_service.assign_request(staff_actor, reserved.pk, None)  # no change, no line
    assert AuditLog.objects.filter(field_name="requests.assigned_to_user_id").count() == 2


def test_assigning_a_request_refuses_a_process_a_stranger_and_a_requester(
    reserved, staff_actor, system_actor, colleague_user
) -> None:
    from portal.core.services.identity import create_user

    requester = create_user(email="asker@example.org", name="An Asker", records_role="requester")
    with pytest.raises(NotAllowed):
        requests_service.assign_request(system_actor, reserved.pk, colleague_user.pk)
    with pytest.raises(ServiceError, match="requester"):
        requests_service.assign_request(staff_actor, reserved.pk, requester.pk)
    with pytest.raises(NotFound):
        requests_service.assign_request(staff_actor, reserved.pk, uuid.uuid4())
    with pytest.raises(NotFound):
        requests_service.assign_request(staff_actor, uuid.uuid4(), colleague_user.pk)
    assert Request.objects.get(pk=reserved.pk).assigned_to_id is None


# ---- the docket's new filters ------------------------------------------------------------------


def test_the_docket_filters_by_assignee_and_sensitivity(
    reserved, staff_actor, staff_user, colleague_user, agency
) -> None:
    privileged = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Sensitive",
        request_text="Sensitive body.",
        sensitivity="privileged",
    )
    # Since P9 a privileged request is assigned only to someone on its list: share, then assign.

    give(privileged.pk, colleague_user.pk)
    requests_service.assign_request(staff_actor, privileged.pk, colleague_user.pk)

    def ids(**kw):
        return {r.pk for r in requests_service.docket(**kw)}

    assert ids() == {reserved.pk, privileged.pk}
    assert ids(assignee_id=colleague_user.pk) == {privileged.pk}
    assert ids(assignee_id=staff_user.pk) == set()
    assert ids(sensitivity="privileged") == {privileged.pk}
    assert ids(sensitivity="internal") == {reserved.pk}
    assert ids(sensitivity="privileged", assignee_id=colleague_user.pk) == {privileged.pk}
    assert ids(sensitivity="public") == set()


# ---- the signed day count ----------------------------------------------------------------------


def _request_due(actor, agency, user, due: date, subject: str):
    request = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=user.pk,
        subject=subject,
        request_text="Body.",
    )
    deadlines_service.open_deadline(
        actor, request.pk, deadline_type="production", due_at=due, basis="statutory"
    )
    return request


def test_the_docket_carries_the_signed_distance_to_the_next_deadline(
    staff_actor, staff_user, agency, monkeypatch
) -> None:
    # 20:00 on the 9th in Pacific time is already 03:00 on the 10th in UTC. The agency's calendar
    # is Pacific, so "today" for it is the 9th, and a deadline on the 10th is one day away, not
    # none: a count taken from the server's UTC date would be off by exactly that one.
    monkeypatch.setattr(
        requests_service, "timezone", _FrozenClock(datetime(2026, 3, 10, 3, 0, tzinfo=UTC))
    )
    ahead = _request_due(staff_actor, agency, staff_user, date(2026, 3, 13), "ahead")
    tomorrow = _request_due(staff_actor, agency, staff_user, date(2026, 3, 10), "tomorrow")
    behind = _request_due(staff_actor, agency, staff_user, date(2026, 3, 6), "behind")
    undated = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="undated",
        request_text="Body.",
    )

    rows = {r.pk: r.days_to_next_deadline for r in requests_service.docket()}

    assert rows[ahead.pk] == 4
    assert rows[tomorrow.pk] == 1
    assert rows[behind.pk] == -3
    assert rows[undated.pk] is None


def test_an_agency_whose_law_has_no_calendar_gets_none_not_an_error(
    staff_actor, staff_user
) -> None:
    unmapped = directory.create_agency(
        staff_actor, name="No Calendar Agency", jurisdiction="Testland", records_law=None
    )
    request = _request_due(staff_actor, unmapped, staff_user, date(2026, 3, 13), "unmapped")
    rows = {r.pk: r.days_to_next_deadline for r in requests_service.docket()}
    assert rows[request.pk] is None


# ---- an item's own provision -------------------------------------------------------------------


@pytest.fixture
def widgets_request(staff_actor, staff_user, agency):
    return requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Widgets",
        request_text="Widget records.",
        items=[
            requests_service.NewItem("1", "Widget inspection reports"),
            requests_service.NewItem("2", "Widget camera footage", provision="widgets"),
            requests_service.NewItem("3", "Blank provision", provision="   "),
        ],
    )


def test_a_new_item_stores_its_provision_and_a_blank_one_is_none(widgets_request) -> None:
    by_number = {i.item_number: i for i in widgets_request.items.all()}
    assert by_number["1"].provision is None
    assert by_number["2"].provision == "widgets"
    assert by_number["3"].provision is None


def _clocks(result):
    return {(d.deadline_type, d.request_item_id): d.due_at for d in result.deadlines}


def test_filing_with_no_item_provisions_argument_uses_the_items_own_provision(
    widgets_request, staff_actor, rules
) -> None:
    footage = widgets_request.items.get(item_number="2")
    result = filing_service.file_request(
        staff_actor, widgets_request.pk, filed_at=FILED_AT, channel="email"
    )
    clocks = _clocks(result)
    # The same date the walkthrough gets by passing the mapping in by hand.
    assert clocks[("production", footage.pk)] == date(2026, 2, 26)
    assert ("production", None) in clocks and ("statutory_determination", None) in clocks
    # The TESTLAW carve-out has a production rule and no determination rule: unresolved, as a task.
    assert [t.task_type for t in result.tasks] == ["enter_deadline_rules"]
    assert str(footage.pk) in result.tasks[0].description


def test_both_doors_open_the_same_deadlines(
    staff_actor, staff_user, agency, widgets_request, rules
) -> None:
    by_column = filing_service.file_request(
        staff_actor, widgets_request.pk, filed_at=FILED_AT, channel="email"
    )
    twin = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Widgets, by hand",
        request_text="Widget records.",
        items=[
            requests_service.NewItem("1", "Widget inspection reports"),
            requests_service.NewItem("2", "Widget camera footage"),
            requests_service.NewItem("3", "Blank provision"),
        ],
    )
    twin_footage = twin.items.get(item_number="2")
    by_mapping = filing_service.file_request(
        staff_actor,
        twin.pk,
        filed_at=FILED_AT,
        channel="email",
        item_provisions={twin_footage.pk: ("widgets",)},
    )

    def shape(result, footage_pk):
        return sorted(
            (d.deadline_type, "item" if d.request_item_id == footage_pk else "request", d.due_at)
            for d in result.deadlines
        )

    footage = widgets_request.items.get(item_number="2")
    assert shape(by_column, footage.pk) == shape(by_mapping, twin_footage.pk)


def test_an_item_provisions_mapping_that_is_passed_wins_over_the_columns(
    widgets_request, staff_actor, rules
) -> None:
    result = filing_service.file_request(
        staff_actor, widgets_request.pk, filed_at=FILED_AT, channel="email", item_provisions={}
    )
    assert all(d.request_item_id is None for d in result.deadlines)
    assert result.tasks == []
    assert Deadline.objects.filter(request_id=widgets_request.pk).count() == 2
