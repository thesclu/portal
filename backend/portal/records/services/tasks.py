"""Things a human still has to go do, because some channels cannot be automated.

A task has a type, an optional assignee ("Mine" and "Everyone" on the Tasks screen) and a creator:
a person, or a named process ("Created by the system"). The task row itself carries no history, so
every change to one that matters is an audit_log line (assign, edit, reopen), and reopening writes
who had completed it before it clears them.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from portal.core.services import notifications as core_notifications
from portal.records.actors import Actor
from portal.records.models import Event, Request, Task, TaskNote, writable
from portal.records.services import access
from portal.records.services import audit as audit_service
from portal.records.services.access import Viewer
from portal.records.services.common import (
    UNSET,
    InvalidTransition,
    NotFound,
    ServiceError,
    Unset,
    require_assignable,
    require_human,
)

TASK_TYPES = (
    "mail_letter",
    "check_portal",
    "confirm_filed",
    "manual_followup",
    "review_no_records_claim",
    "enter_deadline_rules",
    "review_source",
    # Added for design v2. The schema brief's `print_and_mail`, "confirm filed" and `send_follow_up`
    # are NOT here: they are mail_letter, confirm_filed and manual_followup above, and a synonym
    # would split one kind of work across two names.
    "manual",
    "reply_to_clarification",
    "decide_fee",
    "review_production",
    "confirm_receipt",
    # P11: a message the portal sent bounced; a person chooses an address and resends.
    "retry_delivery",
)

# The Tasks screen's type filter. A task type belongs to exactly one category. `person` is the
# design's "Created by a person": the one type a person makes by hand with no system task behind it.
TASK_CATEGORIES = ("delivery_and_filing", "deadlines", "follow_ups", "person")

TASK_CATEGORY: dict[str, str] = {
    "mail_letter": "delivery_and_filing",
    "confirm_filed": "delivery_and_filing",
    "confirm_receipt": "delivery_and_filing",
    "enter_deadline_rules": "deadlines",
    "check_portal": "follow_ups",
    "manual_followup": "follow_ups",
    "reply_to_clarification": "follow_ups",
    "decide_fee": "follow_ups",
    "review_production": "follow_ups",
    "review_no_records_claim": "follow_ups",
    "review_source": "follow_ups",
    "retry_delivery": "delivery_and_filing",
    "manual": "person",
}

TASK_STATES = ("open", "completed", "all")


def _locked(task_id: UUID) -> Task:
    """The task row under `FOR UPDATE`, so assign / edit / reopen / complete cannot interleave and
    leave an audit line describing a state that never existed."""
    task = Task.objects.select_for_update().filter(pk=task_id).first()
    if task is None:
        raise NotFound(f"No task {task_id}.")
    return task


def _need_note(note: str, what: str) -> None:
    if not note or not note.strip():
        raise ServiceError(f"{what} needs a note saying why.")


@transaction.atomic
def create_task(
    actor: Actor,
    request_id: UUID,
    task_type: str,
    description: str,
    *,
    due_at: datetime | None = None,
    assigned_to_user_id: UUID | None = None,
    source_id: UUID | None = None,
) -> Task:
    """The creator is stamped from the actor: a person's uuid, or the system process's name.

    `source_id` (P23b) is the message the task is about, written once: it must be a message on
    this request (an event of the request names it, voided or not), so a task can never point at
    mail from somewhere else."""
    if task_type not in TASK_TYPES:
        raise ServiceError(f"Unknown task type {task_type!r}; expected one of {TASK_TYPES}.")
    if (
        source_id is not None
        and not Event.objects.filter(source_id=source_id, request_id=request_id).exists()
    ):
        raise ServiceError("That message is not on this task's request.")
    if assigned_to_user_id is not None:
        require_assignable(assigned_to_user_id)
        target = Request.objects.filter(pk=request_id).first()
        if target is not None:  # a request that does not exist is the foreign key's to refuse
            access.require_assignee_can_view(target, assigned_to_user_id)
    with writable():
        task = Task.objects.create(
            request_id=request_id,
            task_type=task_type,
            description=description,
            due_at=due_at,
            assigned_to_id=assigned_to_user_id,
            created_by_id=actor.user_id,
            created_by_system=actor.system,
            source_id=source_id,
        )
    if assigned_to_user_id is not None:
        _tell_assignee(actor, task, assigned_to_user_id)
    return task


def _tell_assignee(actor: Actor, task: Task, user_id: UUID) -> None:
    core_notifications.notify(
        user_id,
        "assigned",
        f"Task for you: {task.description.splitlines()[0][:80]}",
        link=f"/requests/{task.request_id}",
        subject_type="task",
        subject_id=task.pk,
        request_id=task.request_id,
        actor_user_id=actor.user_id,
    )


@transaction.atomic
def complete_task(actor: Actor, task_id: UUID) -> Task:
    user_id = require_human(actor, "Completing a task")
    task = _locked(task_id)
    if task.completed_at is not None:
        return task
    completed_at = timezone.now()
    Task.objects.filter(pk=task_id).update(completed_at=completed_at, completed_by_id=user_id)
    return Task.objects.get(pk=task_id)


@transaction.atomic
def assign_task(actor: Actor, task_id: UUID, user_id: UUID | None) -> Task:
    """Hand a task to a person, or to nobody (`None`: it shows under "Everyone" only).

    Human only, because the audit line is "who reassigned it" and a process has no name the line
    can carry.
    """
    require_human(actor, "Assigning a task")
    task = _locked(task_id)
    person = require_assignable(user_id) if user_id is not None else None
    if task.assigned_to_id == user_id:
        return task
    access.require_assignee_can_view(task.request, user_id)
    Task.objects.filter(pk=task_id).update(assigned_to_id=user_id)
    if user_id is not None:
        _tell_assignee(actor, task, user_id)
    audit_service.record_audit(
        actor,
        entity_type="tasks",
        entity_id=task_id,
        field="assigned_to_user_id",
        old_value=task.assigned_to_id,
        new_value=user_id,
        note=f"Assigned to {person.name}." if person is not None else "Unassigned.",
    )
    return Task.objects.get(pk=task_id)


@transaction.atomic
def update_task(
    actor: Actor,
    task_id: UUID,
    note: str,
    *,
    due_at: datetime | None | Unset = UNSET,
    description: str | Unset = UNSET,
) -> Task:
    """Edit a task's due date or description, one audit line per field that actually changes.

    `due_at=None` clears the date; leaving it out leaves it alone (hence `UNSET`, not `None`).
    """
    require_human(actor, "Editing a task")
    _need_note(note, "A task edit")
    if isinstance(due_at, Unset) and isinstance(description, Unset):
        raise ServiceError("Nothing to change: pass a due date, a description, or both.")
    task = _locked(task_id)
    wanted: dict[str, object] = {}
    if not isinstance(description, Unset):
        if not description or not description.strip():
            raise ServiceError("A task needs a description.")
        wanted["description"] = description
    if not isinstance(due_at, Unset):
        wanted["due_at"] = due_at
    changed = {f: v for f, v in wanted.items() if getattr(task, f) != v}
    if not changed:
        return task
    Task.objects.filter(pk=task_id).update(**changed)
    for field, value in changed.items():
        audit_service.record_audit(
            actor,
            entity_type="tasks",
            entity_id=task_id,
            field=field,
            old_value=getattr(task, field),
            new_value=value,
            note=note,
        )
    return Task.objects.get(pk=task_id)


@transaction.atomic
def reopen_task(actor: Actor, task_id: UUID, note: str) -> Task:
    """Undo a completion. Human only, and it leaves who finished it on the record.

    `completed_at` and `completed_by_user_id` are about to be cleared, and nothing else holds
    them, so each gets an audit line carrying its old value first. Without those lines a reopened
    task would look as if nobody had ever completed it.
    """
    require_human(actor, "Reopening a task")
    _need_note(note, "Reopening a task")
    task = _locked(task_id)
    if task.completed_at is None:
        raise InvalidTransition(f"Task {task_id} is not completed, so there is nothing to reopen.")
    for field, old in (
        ("completed_at", task.completed_at),
        ("completed_by_user_id", task.completed_by_id),
    ):
        if old is not None:
            audit_service.record_audit(
                actor,
                entity_type="tasks",
                entity_id=task_id,
                field=field,
                old_value=old,
                new_value=None,
                note=note,
            )
    Task.objects.filter(pk=task_id).update(completed_at=None, completed_by_id=None)
    return Task.objects.get(pk=task_id)


def open_tasks(request_id: UUID | None = None) -> Sequence[Task]:
    query = Task.objects.filter(completed_at__isnull=True)
    if request_id is not None:
        query = query.filter(request_id=request_id)
    return list(query.order_by("created_at"))


def list_tasks(
    *,
    assignee_id: UUID | None = None,
    state: str = "open",
    request_id: UUID | None = None,
    category: str | None = None,
    viewer: Viewer | UUID | None = None,
) -> Sequence[Task]:
    """The Tasks screen's query. Open tasks come soonest-due first, undated ones last.

    `viewer` is who the list is for: tasks on a request they may not see are left out, whoever
    they are assigned to. None lists every task, which is what a job with no viewer wants."""
    if state not in TASK_STATES:
        raise ServiceError(f"Unknown task state {state!r}; expected one of {TASK_STATES}.")
    if category is not None and category not in TASK_CATEGORIES:
        raise ServiceError(
            f"Unknown task category {category!r}; expected one of {TASK_CATEGORIES}."
        )
    rows = Task.objects.select_related("request__agency")
    if viewer is not None:
        rows = rows.filter(access.visible_q(access.as_viewer(viewer), "request__"))
    if state == "open":
        rows = rows.filter(completed_at__isnull=True)
    elif state == "completed":
        rows = rows.filter(completed_at__isnull=False)
    if assignee_id is not None:
        rows = rows.filter(assigned_to_id=assignee_id)
    if request_id is not None:
        rows = rows.filter(request_id=request_id)
    if category is not None:
        rows = rows.filter(task_type__in=[t for t, c in TASK_CATEGORY.items() if c == category])
    if state == "open":
        return list(rows.order_by(F("due_at").asc(nulls_last=True), "created_at"))
    if state == "completed":
        return list(rows.order_by("-completed_at"))
    return list(rows.order_by("created_at"))


# ---- notes on a task (P23b, 177 "Write a note…") ------------------------------------------------


@transaction.atomic
def add_task_note(actor: Actor, task_id: UUID, body: str) -> TaskNote:
    """A note on a task, by the person writing it. A completed task still takes notes: what
    happened after is part of its story. Human only; the text is never edited afterwards."""
    user_id = require_human(actor, "Writing a note on a task")
    text = (body or "").strip()
    if not text:
        raise ServiceError("A note needs some text.")
    _locked(task_id)
    with writable():
        return TaskNote.objects.create(task_id=task_id, author_id=user_id, body=text)


@transaction.atomic
def void_task_note(actor: Actor, task_id: UUID, note_id: UUID, reason: str) -> TaskNote:
    """Strike a mistaken note through. It stays, with who voided it, when and why; voiding twice
    is refused rather than overwriting the first reason."""
    user_id = require_human(actor, "Voiding a note on a task")
    _need_note(reason, "Voiding a note")
    why = reason.strip()
    _locked(task_id)
    note = TaskNote.objects.select_for_update().filter(pk=note_id, task_id=task_id).first()
    if note is None:
        raise NotFound(f"No note {note_id} on task {task_id}.")
    if note.voided_at is not None:
        raise InvalidTransition("That note is already voided.")
    TaskNote.objects.filter(pk=note_id).update(
        voided_at=timezone.now(), voided_by_id=user_id, void_reason=why
    )
    return TaskNote.objects.select_related("author", "voided_by").get(pk=note_id)


def task_notes(task_id: UUID) -> list[TaskNote]:
    """One task's notes, oldest first, voided ones included (struck through on the screen)."""
    return list(
        TaskNote.objects.filter(task_id=task_id)
        .select_related("author", "voided_by")
        .order_by("created_at", "id")
    )
