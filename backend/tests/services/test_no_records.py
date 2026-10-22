"""`no_records_claimed`: non-terminal, opens exactly one review task, never auto-closes."""

from portal.records.models import StatusDefinition
from portal.records.services import events as events_service
from portal.records.services import tasks as tasks_service

from .conftest import FILED_AT


def _claim(actor, request, body="We located no responsive records."):
    return events_service.record_event(
        actor,
        request.pk,
        event_type="determination",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT,
        display_body=body,
        status="no_records_claimed",
    )


def test_a_no_records_claim_opens_one_task_and_leaves_the_request_open(
    reserved, staff_actor
) -> None:
    _claim(staff_actor, reserved)
    reserved.refresh_from_db()
    assert reserved.current_status_id == "no_records_claimed"
    assert StatusDefinition.objects.get(pk="no_records_claimed").is_terminal is False
    opened = [t for t in tasks_service.open_tasks(reserved.pk)]
    assert [t.task_type for t in opened] == ["review_no_records_claim"]


def test_a_second_claim_does_not_open_a_second_task(reserved, staff_actor) -> None:
    _claim(staff_actor, reserved)
    _claim(staff_actor, reserved, "Repeating: no responsive records.")
    assert len(tasks_service.open_tasks(reserved.pk)) == 1


def test_completing_the_task_lets_a_later_claim_open_a_fresh_one(reserved, staff_actor) -> None:
    """Once a person has dealt with it, a NEW claim is new information and must be seen again."""
    _claim(staff_actor, reserved)
    task = tasks_service.open_tasks(reserved.pk)[0]
    tasks_service.complete_task(staff_actor, task.pk)
    assert tasks_service.open_tasks(reserved.pk) == []
    _claim(staff_actor, reserved, "Still nothing.")
    assert [t.task_type for t in tasks_service.open_tasks(reserved.pk)] == [
        "review_no_records_claim"
    ]
