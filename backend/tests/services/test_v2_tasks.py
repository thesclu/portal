"""Tasks in design v2: who made one, who has it, editing it, reopening it, and the Tasks filters."""

from datetime import UTC, datetime

import pytest
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.models import AuditLog, Task
from portal.records.services import tasks as tasks_service
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError

pytestmark = pytest.mark.usefixtures("as_each_role")

DUE = datetime(2026, 3, 1, 17, 0, tzinfo=UTC)
LATER = datetime(2026, 4, 1, 17, 0, tzinfo=UTC)


def _task(actor, request, task_type="manual", description="Call the records officer", **kw):
    return tasks_service.create_task(actor, request.pk, task_type, description, **kw)


def _audit(task, field):
    return list(
        AuditLog.objects.filter(entity_type="tasks", entity_id=task.pk, field_name=f"tasks.{field}")
    )


# ---- the vocabulary ----------------------------------------------------------------------------


def test_every_task_type_has_exactly_one_category_and_no_synonyms_were_added() -> None:
    assert set(tasks_service.TASK_CATEGORY) == set(tasks_service.TASK_TYPES)
    assert set(tasks_service.TASK_CATEGORY.values()) <= set(tasks_service.TASK_CATEGORIES)
    assert set(tasks_service.TASK_CATEGORIES) == {
        "delivery_and_filing",
        "deadlines",
        "follow_ups",
        "person",
    }
    for added in (
        "manual",
        "reply_to_clarification",
        "decide_fee",
        "review_production",
        "confirm_receipt",
        # P11: a message the portal sent bounced. Its own work, not a synonym of anything: the
        # BUILD_PLAN outline names it, and before P11 nothing could bounce.
        "retry_delivery",
    ):
        assert added in tasks_service.TASK_TYPES
    # The schema brief's names for work that already has a type here. A synonym would split one
    # kind of work across two names.
    for synonym in ("print_and_mail", "send_follow_up"):
        assert synonym not in tasks_service.TASK_TYPES


# ---- creator and assignee ----------------------------------------------------------------------


def test_a_task_made_by_a_person_records_the_person(reserved, staff_actor, staff_user) -> None:
    task = _task(staff_actor, reserved)
    assert task.created_by_id == staff_user.pk
    assert task.created_by_system is None


def test_a_task_made_by_a_process_records_its_name(reserved, system_actor) -> None:
    task = _task(system_actor, reserved, "check_portal")
    assert task.created_by_id is None
    assert task.created_by_system == "test_sweep"


def test_a_task_can_be_assigned_at_creation(reserved, staff_actor, colleague_user) -> None:
    task = _task(staff_actor, reserved, assigned_to_user_id=colleague_user.pk)
    assert task.assigned_to_id == colleague_user.pk


def test_work_cannot_be_handed_to_a_requester_a_deactivated_person_or_nobody_real(
    reserved, staff_actor
) -> None:
    requester = create_user(email="req@example.org", name="A Requester", records_role="requester")
    gone = create_user(email="gone@example.org", name="Gone Person")
    gone.is_active = False
    gone.save()
    import uuid

    with pytest.raises(ServiceError, match="requester"):
        _task(staff_actor, reserved, assigned_to_user_id=requester.pk)
    with pytest.raises(ServiceError, match="deactivated"):
        _task(staff_actor, reserved, assigned_to_user_id=gone.pk)
    with pytest.raises(NotFound):
        _task(staff_actor, reserved, assigned_to_user_id=uuid.uuid4())


def test_assigning_audits_who_it_went_to_and_none_unassigns(
    reserved, staff_actor, colleague_user
) -> None:
    task = _task(staff_actor, reserved)
    task = tasks_service.assign_task(staff_actor, task.pk, colleague_user.pk)
    assert task.assigned_to_id == colleague_user.pk
    (line,) = _audit(task, "assigned_to_user_id")
    assert (line.old_value, line.new_value) == (None, str(colleague_user.pk))
    assert line.edited_by_id == staff_actor.user_id
    assert "Colleague Person" in line.note

    task = tasks_service.assign_task(staff_actor, task.pk, None)
    assert task.assigned_to_id is None
    assert len(_audit(task, "assigned_to_user_id")) == 2

    # Assigning to who already has it is not a change and leaves no line.
    tasks_service.assign_task(staff_actor, task.pk, None)
    assert len(_audit(task, "assigned_to_user_id")) == 2


def test_assigning_is_human_only(reserved, staff_actor, system_actor, colleague_user) -> None:
    task = _task(staff_actor, reserved)
    with pytest.raises(NotAllowed):
        tasks_service.assign_task(system_actor, task.pk, colleague_user.pk)


# ---- editing -----------------------------------------------------------------------------------


def test_an_edit_audits_each_field_that_changes_and_only_those(reserved, staff_actor) -> None:
    task = _task(staff_actor, reserved, due_at=DUE)
    task = tasks_service.update_task(
        staff_actor, task.pk, "agency asked for a later call", due_at=LATER, description="Call at 3"
    )
    assert (task.due_at, task.description) == (LATER, "Call at 3")
    (due_line,) = _audit(task, "due_at")
    assert due_line.old_value == str(DUE) and due_line.new_value == str(LATER)
    assert due_line.note == "agency asked for a later call"
    assert len(_audit(task, "description")) == 1

    # Naming a field with the value it already has changes nothing and writes nothing.
    tasks_service.update_task(staff_actor, task.pk, "same again", description="Call at 3")
    assert len(_audit(task, "description")) == 1


def test_leaving_a_field_out_leaves_it_alone_and_none_clears_a_due_date(
    reserved, staff_actor
) -> None:
    task = _task(staff_actor, reserved, due_at=DUE)
    task = tasks_service.update_task(staff_actor, task.pk, "reworded", description="New words")
    assert task.due_at == DUE
    task = tasks_service.update_task(staff_actor, task.pk, "no longer time-bound", due_at=None)
    assert task.due_at is None and task.description == "New words"


def test_an_edit_needs_a_human_a_note_something_to_change_and_a_real_description(
    reserved, staff_actor, system_actor
) -> None:
    task = _task(staff_actor, reserved)
    with pytest.raises(NotAllowed):
        tasks_service.update_task(system_actor, task.pk, "why", description="x")
    with pytest.raises(ServiceError, match="note"):
        tasks_service.update_task(staff_actor, task.pk, "  ", description="x")
    with pytest.raises(ServiceError, match="Nothing to change"):
        tasks_service.update_task(staff_actor, task.pk, "why")
    with pytest.raises(ServiceError, match="description"):
        tasks_service.update_task(staff_actor, task.pk, "why", description="  ")
    with pytest.raises(NotFound):
        import uuid

        tasks_service.update_task(staff_actor, uuid.uuid4(), "why", description="x")


# ---- reopening ---------------------------------------------------------------------------------


def test_reopening_leaves_audit_rows_naming_who_had_completed_it(
    reserved, staff_actor, staff_user, colleague_user
) -> None:
    task = _task(staff_actor, reserved)
    completed = tasks_service.complete_task(staff_actor, task.pk)
    assert completed.completed_by_id == staff_user.pk

    # A different person reopens it: the lines must carry who finished it, not who reopened it.
    reopener = Actor.human(colleague_user)
    reopened = tasks_service.reopen_task(reopener, task.pk, "the agency never actually called back")

    assert reopened.completed_at is None and reopened.completed_by_id is None
    (by,) = _audit(task, "completed_by_user_id")
    assert by.old_value == str(staff_user.pk) and by.new_value is None
    assert by.edited_by_id == colleague_user.pk
    assert by.note == "the agency never actually called back"
    (at,) = _audit(task, "completed_at")
    assert at.old_value == str(completed.completed_at) and at.new_value is None


def test_reopening_is_human_only_needs_a_note_and_refuses_an_open_task(
    reserved, staff_actor, system_actor
) -> None:
    task = _task(staff_actor, reserved)
    with pytest.raises(InvalidTransition, match="not completed"):
        tasks_service.reopen_task(staff_actor, task.pk, "why")
    tasks_service.complete_task(staff_actor, task.pk)
    with pytest.raises(NotAllowed):
        tasks_service.reopen_task(system_actor, task.pk, "why")
    with pytest.raises(ServiceError, match="note"):
        tasks_service.reopen_task(staff_actor, task.pk, "")
    assert Task.objects.get(pk=task.pk).completed_at is not None


# ---- the Tasks screen's query ------------------------------------------------------------------


def test_list_tasks_filters_by_assignee_state_request_and_category(
    reserved, staff_actor, colleague_user, agency
) -> None:
    from portal.records.services import requests as requests_service

    other = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_actor.user_id,
        subject="Other",
        request_text="Other body.",
    )
    mine = _task(
        staff_actor, reserved, "mail_letter", "Mail it", assigned_to_user_id=staff_actor.user_id
    )
    theirs = _task(
        staff_actor, reserved, "decide_fee", "Pay or not", assigned_to_user_id=colleague_user.pk
    )
    nobody = _task(staff_actor, other, "manual", "Anyone's")
    done = _task(staff_actor, reserved, "confirm_filed", "Confirm")
    tasks_service.complete_task(staff_actor, done.pk)

    def ids(**kw):
        return {t.pk for t in tasks_service.list_tasks(**kw)}

    assert ids() == {mine.pk, theirs.pk, nobody.pk}
    assert ids(state="completed") == {done.pk}
    assert ids(state="all") == {mine.pk, theirs.pk, nobody.pk, done.pk}
    assert ids(assignee_id=staff_actor.user_id) == {mine.pk}
    assert ids(assignee_id=colleague_user.pk) == {theirs.pk}
    assert ids(request_id=other.pk) == {nobody.pk}
    assert ids(category="delivery_and_filing") == {mine.pk}
    assert ids(category="follow_ups") == {theirs.pk}
    assert ids(category="person") == {nobody.pk}
    assert ids(category="delivery_and_filing", state="all") == {mine.pk, done.pk}
    with pytest.raises(ServiceError, match="state"):
        tasks_service.list_tasks(state="finished")
    with pytest.raises(ServiceError, match="category"):
        tasks_service.list_tasks(category="everything")


def test_open_tasks_come_soonest_due_first_and_undated_ones_last(reserved, staff_actor) -> None:
    undated = _task(staff_actor, reserved, description="undated")
    later = _task(staff_actor, reserved, description="later", due_at=LATER)
    sooner = _task(staff_actor, reserved, description="sooner", due_at=DUE)
    assert [t.pk for t in tasks_service.list_tasks()] == [sooner.pk, later.pk, undated.pk]
