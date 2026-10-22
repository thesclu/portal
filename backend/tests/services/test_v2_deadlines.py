"""Deadline kinds as rows, a lapse that moves no status, and resolving two competing dates."""

import uuid
from datetime import UTC, date, datetime

import pytest
from django.db import connection
from portal.records.models import AuditLog, Deadline, DeadlineDefinition, Request, StatusChange
from portal.records.services import deadlines as deadlines_service
from portal.records.services import events as events_service
from portal.records.services import requests as requests_service
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError

pytestmark = pytest.mark.usefixtures("as_each_role")

EARLY = date(2026, 3, 1)
LATE = date(2026, 4, 1)


def _open(actor, request, kind, due, **kw):
    return deadlines_service.open_deadline(
        actor, request.pk, deadline_type=kind, due_at=due, basis="agency_commitment", **kw
    )


# ---- the kinds are rows ------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["fee_response", "other"])
def test_a_deadline_of_each_new_kind_opens_and_feeds_next_deadline_at(
    reserved, staff_actor, kind
) -> None:
    deadline = _open(staff_actor, reserved, kind, EARLY)
    assert deadline.deadline_type == kind
    assert Request.objects.get(pk=reserved.pk).next_deadline_at == EARLY


@pytest.mark.parametrize("kind", ["fee_response", "other"])
def test_a_missed_deadline_of_a_kind_with_no_overdue_status_leaves_the_status_alone(
    reserved, staff_actor, system_actor, kind
) -> None:
    deadline = _open(staff_actor, reserved, kind, EARLY)
    status_before = Request.objects.get(pk=reserved.pk).current_status_id
    rows_before = StatusChange.objects.filter(request_id=reserved.pk).count()

    result = deadlines_service.mark_deadline_missed(system_actor, deadline.pk)

    assert result is None
    assert Deadline.objects.get(pk=deadline.pk).status == "missed"
    assert Request.objects.get(pk=reserved.pk).current_status_id == status_before
    assert StatusChange.objects.filter(request_id=reserved.pk).count() == rows_before
    assert Request.objects.get(pk=reserved.pk).next_deadline_at is None


@pytest.mark.parametrize(
    ("kind", "overdue"),
    [("statutory_determination", "determination_overdue"), ("production", "production_overdue")],
)
def test_the_two_statutory_kinds_still_move_the_status_they_always_did(
    reserved, staff_actor, system_actor, kind, overdue
) -> None:
    deadline = _open(staff_actor, reserved, kind, EARLY)
    change = deadlines_service.mark_deadline_missed(system_actor, deadline.pk)
    assert change is not None and change.status_id == overdue
    assert Request.objects.get(pk=reserved.pk).current_status_id == overdue


def test_what_a_lapse_does_is_read_from_the_table_not_from_code(
    reserved, staff_actor, system_actor
) -> None:
    """The overdue status used to live in a dict in services/deadlines.py. Change the row and the
    behaviour changes with it, which a dict could not do."""
    with connection.cursor() as cur:
        cur.execute("select current_user")
        if cur.fetchone()[0] == "portal_app":
            pytest.skip("alters a lookup row, which only the owner may do")
    DeadlineDefinition.objects.filter(pk="other").update(overdue_status="production_overdue")
    deadline = _open(staff_actor, reserved, "other", EARLY)
    change = deadlines_service.mark_deadline_missed(system_actor, deadline.pk)
    assert change is not None and change.status_id == "production_overdue"


def test_the_overdue_dict_is_gone() -> None:
    assert not hasattr(deadlines_service, "OVERDUE_STATUS")


# ---- the sweep: only an active deadline can lapse ----------------------------------------------


@pytest.mark.parametrize("how", ["waived", "satisfied", "superseded", "missed"])
def test_the_sweep_leaves_a_deadline_that_is_no_longer_active_alone(
    reserved, staff_actor, system_actor, how
) -> None:
    """F2: `mark_deadline_missed` wrote `missed` with no status check, so a deadline a person had
    waived or satisfied was overwritten and an overdue status was appended after the answer came."""
    old = _open(staff_actor, reserved, "statutory_determination", EARLY)
    if how == "waived":
        deadlines_service.waive_deadline(staff_actor, old.pk, "agency granted an extension")
    elif how == "satisfied":
        answer = _answer(staff_actor, reserved, "determination")
        deadlines_service.satisfy_deadline(staff_actor, old.pk, answer.pk)
    elif how == "superseded":
        _open(staff_actor, reserved, "statutory_determination", LATE, supersedes=old.pk)
    else:
        assert deadlines_service.mark_deadline_missed(system_actor, old.pk) is not None
    rows_before = StatusChange.objects.filter(request_id=reserved.pk).count()
    status_before = Request.objects.get(pk=reserved.pk).current_status_id

    assert deadlines_service.mark_deadline_missed(system_actor, old.pk) is None

    assert Deadline.objects.get(pk=old.pk).status == how
    assert StatusChange.objects.filter(request_id=reserved.pk).count() == rows_before
    assert Request.objects.get(pk=reserved.pk).current_status_id == status_before
    if how != "missed":
        assert status_before != "determination_overdue"


def test_a_satisfied_deadline_keeps_its_satisfier_when_the_sweep_runs_late(
    reserved, staff_actor, system_actor
) -> None:
    old = _open(staff_actor, reserved, "production", EARLY)
    answer = _answer(staff_actor, reserved)
    deadlines_service.satisfy_deadline(staff_actor, old.pk, answer.pk)

    assert deadlines_service.mark_deadline_missed(system_actor, old.pk) is None

    row = Deadline.objects.get(pk=old.pk)
    assert (row.status, row.satisfied_by_event_id) == ("satisfied", answer.pk)
    assert not StatusChange.objects.filter(
        request_id=reserved.pk, reason="deadline_expired"
    ).exists()


# ---- superseding: what `supersedes` may name ---------------------------------------------------


def _another_request(staff_actor, staff_user, agency):
    return requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Elsewhere",
        request_text="Body.",
    )


def _answer(actor, request, event_type="production"):
    """An inbound event of the kind that answers a clock: the records, or the determination."""
    return events_service.record_event(
        actor,
        request.pk,
        event_type=event_type,
        channel="email",
        direction="inbound",
        occurred_at=datetime(2026, 2, 20, 18, 0, tzinfo=UTC),
        display_body="The agency's answer.",
    )


def test_a_deadline_of_the_same_request_can_still_be_superseded(reserved, staff_actor) -> None:
    old = _open(staff_actor, reserved, "production", EARLY)
    new = _open(staff_actor, reserved, "production", LATE, supersedes=old.pk)
    retired = Deadline.objects.get(pk=old.pk)
    assert (retired.status, retired.superseded_by_deadline_id) == ("superseded", new.pk)
    assert Request.objects.get(pk=reserved.pk).next_deadline_at == LATE


def test_a_deadline_of_another_request_cannot_be_superseded(
    reserved, staff_actor, staff_user, agency
) -> None:
    """F1: `supersedes` used to be looked up by id alone, so a write on request A silently ended
    request B's clock, and moved B's `next_deadline_at`, without B's lock."""
    elsewhere = _another_request(staff_actor, staff_user, agency)
    theirs = _open(staff_actor, elsewhere, "production", EARLY)
    assert Request.objects.get(pk=elsewhere.pk).next_deadline_at == EARLY

    with pytest.raises(ServiceError, match="different request"):
        _open(staff_actor, reserved, "production", LATE, supersedes=theirs.pk)

    untouched = Deadline.objects.get(pk=theirs.pk)
    assert (untouched.status, untouched.superseded_by_deadline_id) == ("active", None)
    assert Request.objects.get(pk=elsewhere.pk).next_deadline_at == EARLY
    assert not Deadline.objects.filter(request_id=reserved.pk).exists()


@pytest.mark.parametrize("how", ["waived", "satisfied", "missed", "superseded"])
def test_a_deadline_that_is_not_active_cannot_be_superseded(
    reserved, staff_actor, system_actor, how
) -> None:
    """F1: the update had no status condition, so a satisfied, waived or missed row was flipped to
    `superseded` and lost what it was."""
    old = _open(staff_actor, reserved, "production", EARLY)
    if how == "waived":
        deadlines_service.waive_deadline(staff_actor, old.pk, "agency withdrew the promise")
    elif how == "satisfied":
        deadlines_service.satisfy_deadline(staff_actor, old.pk, _answer(staff_actor, reserved).pk)
    elif how == "missed":
        deadlines_service.mark_deadline_missed(system_actor, old.pk)
    else:
        _open(staff_actor, reserved, "production", LATE, supersedes=old.pk)
    rows_before = Deadline.objects.filter(request_id=reserved.pk).count()
    state_before = Deadline.objects.get(pk=old.pk)

    with pytest.raises(InvalidTransition, match=f"is '{how}', not active"):
        _open(staff_actor, reserved, "production", date(2026, 5, 1), supersedes=old.pk)

    after = Deadline.objects.get(pk=old.pk)
    assert (after.status, after.superseded_by_deadline_id, after.satisfied_by_event_id) == (
        state_before.status,
        state_before.superseded_by_deadline_id,
        state_before.satisfied_by_event_id,
    )
    assert Deadline.objects.filter(request_id=reserved.pk).count() == rows_before


def test_superseding_a_deadline_that_does_not_exist_is_not_found(reserved, staff_actor) -> None:
    with pytest.raises(NotFound):
        _open(staff_actor, reserved, "production", LATE, supersedes=uuid.uuid4())
    assert not Deadline.objects.filter(request_id=reserved.pk).exists()


# ---- resolving two competing dates -------------------------------------------------------------


@pytest.fixture
def conflict(reserved, staff_actor):
    """Two active production deadlines for one request; the second is flagged against the first."""
    first = _open(staff_actor, reserved, "production", EARLY)
    second = _open(staff_actor, reserved, "production", LATE, note="agency promised a later date")
    assert second.flagged_for_review and not first.flagged_for_review
    return first, second


def _lines(deadline, field):
    return list(AuditLog.objects.filter(entity_id=deadline.pk, field_name=f"deadlines.{field}"))


def test_resolving_keeps_one_supersedes_the_other_and_audits_both_changes(
    reserved, staff_actor, conflict
) -> None:
    earlier, later = conflict
    assert Request.objects.get(pk=reserved.pk).next_deadline_at == EARLY

    kept = deadlines_service.resolve_conflict(
        staff_actor, later.pk, earlier.pk, "the agency's letter is the operative date"
    )

    assert kept.pk == later.pk and kept.status == "active"
    assert kept.flagged_for_review is False and kept.flag_note is None
    dropped = Deadline.objects.get(pk=earlier.pk)
    assert dropped.status == "superseded" and dropped.superseded_by_deadline_id == later.pk
    # The row that was once believed is still there, and the cache followed the one that stands.
    assert Request.objects.get(pk=reserved.pk).next_deadline_at == LATE

    note = "the agency's letter is the operative date"
    (status_line,) = _lines(earlier, "status")
    assert (status_line.old_value, status_line.new_value) == ("active", "superseded")
    (pointer,) = _lines(earlier, "superseded_by_deadline_id")
    assert (pointer.old_value, pointer.new_value) == (None, str(later.pk))
    (flag,) = _lines(later, "flagged_for_review")
    assert (flag.old_value, flag.new_value) == ("True", "False")
    (flag_note,) = _lines(later, "flag_note")
    assert "conflicts with active deadline" in flag_note.old_value and flag_note.new_value is None
    assert {line.note for line in (status_line, pointer, flag, flag_note)} == {note}
    assert {line.edited_by_id for line in (status_line, pointer, flag, flag_note)} == {
        staff_actor.user_id
    }


def test_resolving_either_way_round_is_allowed(reserved, staff_actor, conflict) -> None:
    earlier, later = conflict
    deadlines_service.resolve_conflict(staff_actor, earlier.pk, later.pk, "the statute governs")
    assert Deadline.objects.get(pk=later.pk).status == "superseded"
    assert Deadline.objects.get(pk=earlier.pk).status == "active"
    assert Request.objects.get(pk=reserved.pk).next_deadline_at == EARLY


def test_resolving_is_refused_for_a_process_a_blank_note_and_a_pair_that_is_no_conflict(
    reserved, staff_actor, system_actor, conflict, agency, staff_user
) -> None:
    earlier, later = conflict
    with pytest.raises(NotAllowed):
        deadlines_service.resolve_conflict(system_actor, later.pk, earlier.pk, "why")
    with pytest.raises(ServiceError, match="note"):
        deadlines_service.resolve_conflict(staff_actor, later.pk, earlier.pk, "  ")
    with pytest.raises(ServiceError, match="same deadline"):
        deadlines_service.resolve_conflict(staff_actor, later.pk, later.pk, "why")

    other_kind = _open(staff_actor, reserved, "fee_response", EARLY)
    with pytest.raises(ServiceError, match="not about the same thing"):
        deadlines_service.resolve_conflict(staff_actor, earlier.pk, other_kind.pk, "why")
    item = reserved.items.first()
    other_item = _open(staff_actor, reserved, "production", EARLY, request_item_id=item.pk)
    with pytest.raises(ServiceError, match="not about the same thing"):
        deadlines_service.resolve_conflict(staff_actor, earlier.pk, other_item.pk, "why")

    elsewhere = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Elsewhere",
        request_text="Body.",
    )
    foreign = _open(staff_actor, elsewhere, "production", LATE)
    with pytest.raises(ServiceError, match="different requests"):
        deadlines_service.resolve_conflict(staff_actor, earlier.pk, foreign.pk, "why")

    import uuid

    with pytest.raises(NotFound):
        deadlines_service.resolve_conflict(staff_actor, earlier.pk, uuid.uuid4(), "why")
    # Nothing above changed anything.
    assert Deadline.objects.get(pk=earlier.pk).status == "active"
    assert Deadline.objects.get(pk=later.pk).status == "active"


def test_resolving_refuses_a_deadline_that_is_no_longer_active(
    reserved, staff_actor, conflict
) -> None:
    earlier, later = conflict
    deadlines_service.waive_deadline(staff_actor, later.pk, "agency withdrew the promise")
    with pytest.raises(InvalidTransition, match="not active"):
        deadlines_service.resolve_conflict(staff_actor, earlier.pk, later.pk, "why")
