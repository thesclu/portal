from uuid import UUID

from django.http import HttpRequest
from ninja import Query, Router

from portal.records.api.permissions import (
    assignee_filter,
    require_human_actor,
    require_named_request_scope,
    require_named_source_scope,
    require_task_scope,
    require_team,
    viewer,
)
from portal.records.api.schemas import (
    AssignIn,
    ReopenTaskIn,
    TaskHistoryOut,
    TaskIn,
    TaskNoteIn,
    TaskNoteOut,
    TaskOut,
    TaskPatchIn,
    VoidTaskNoteIn,
)
from portal.records.api.serializers import task_out
from portal.records.services import audit as audit_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import UNSET

router = Router(tags=["tasks"])


@router.get("/tasks", response=list[TaskOut])
def list_tasks(
    request: HttpRequest,
    open: bool = True,
    request_id: UUID | None = None,
    assignee: str | None = Query(None, description="`me`, or a person's id."),
    state: str | None = Query(None, description="open, completed or all. Wins over `open`."),
    category: str | None = Query(None, description="One of GET /definitions' task_categories."),
) -> list[TaskOut]:
    """`open` is the original switch (true: open tasks, false: all of them) and is kept so nothing
    that already calls it changes; `state` is the full set and wins when both are sent."""
    require_team(request)
    rows = tasks_service.list_tasks(
        assignee_id=assignee_filter(request, assignee),
        state=state or ("open" if open else "all"),
        request_id=request_id,
        category=category,
        viewer=viewer(request),
    )
    return [task_out(t) for t in rows]


@router.post("/tasks", response={201: TaskOut})
def create_task(request: HttpRequest, payload: TaskIn):
    """`request_id` naming a request that does not exist trips `tasks.request_id`'s FK and
    surfaces as django.db.IntegrityError, not a ServiceError -- caught by the API's global
    IntegrityError -> 422 handler (see records/api/__init__.py)."""
    require_named_request_scope(request, payload.request_id)
    require_named_source_scope(request, payload.source_id)
    actor = require_human_actor(request)
    task = tasks_service.create_task(
        actor,
        payload.request_id,
        payload.task_type,
        payload.description,
        due_at=payload.due_at,
        assigned_to_user_id=payload.assigned_to_user_id,
        source_id=payload.source_id,
    )
    return 201, task_out(task)


@router.patch("/tasks/{task_id}", response=TaskOut)
def update_task(request: HttpRequest, task_id: UUID, payload: TaskPatchIn) -> TaskOut:
    """Edit a task's due date or description. A field left out is left alone; `due_at: null`
    clears the date. Every change is an audit line carrying `note`."""
    require_task_scope(request, task_id)
    actor = require_human_actor(request)
    sent = payload.model_dump(exclude_unset=True)
    task = tasks_service.update_task(
        actor,
        task_id,
        payload.note,
        due_at=sent.get("due_at", UNSET),
        description=sent.get("description", UNSET),
    )
    return task_out(task)


@router.post("/tasks/{task_id}/complete", response=TaskOut)
def complete_task(request: HttpRequest, task_id: UUID) -> TaskOut:
    require_task_scope(request, task_id)
    actor = require_human_actor(request)
    task = tasks_service.complete_task(actor, task_id)
    return task_out(task)


@router.post("/tasks/{task_id}/assign", response=TaskOut)
def assign_task(request: HttpRequest, task_id: UUID, payload: AssignIn) -> TaskOut:
    """Hand a task to a person; `user_id: null` unassigns."""
    require_task_scope(request, task_id)
    actor = require_human_actor(request)
    return task_out(tasks_service.assign_task(actor, task_id, payload.user_id))


@router.post("/tasks/{task_id}/reopen", response=TaskOut)
def reopen_task(request: HttpRequest, task_id: UUID, payload: ReopenTaskIn) -> TaskOut:
    """Undo a completion. Who had completed it is kept in the audit log."""
    require_task_scope(request, task_id)
    actor = require_human_actor(request)
    return task_out(tasks_service.reopen_task(actor, task_id, payload.note))


@router.get("/tasks/{task_id}/history", response=list[TaskHistoryOut])
def task_history(request: HttpRequest, task_id: UUID) -> list[TaskHistoryOut]:
    """What changed on a task and who changed it (177, 178): its audit lines, oldest first. A
    task is seen by whoever sees its request, so its history is too."""
    require_task_scope(request, task_id)
    return [
        TaskHistoryOut(
            at=row.edited_at,
            actor_id=row.edited_by_id,
            actor_name=row.edited_by.name if row.edited_by_id else None,
            field=row.field_name.split(".", 1)[-1],
            old_value=row.old_value,
            new_value=row.new_value,
            note=row.note,
        )
        for row in audit_service.lines_for("tasks", task_id)
    ]


def _note_out(note) -> TaskNoteOut:
    return TaskNoteOut(
        id=note.pk,
        task_id=note.task_id,
        author_user_id=note.author_id,
        author_name=note.author.name,
        body=note.body,
        created_at=note.created_at,
        voided_at=note.voided_at,
        voided_by_name=note.voided_by.name if note.voided_by_id else None,
        void_reason=note.void_reason,
    )


@router.get("/tasks/{task_id}/notes", response=list[TaskNoteOut])
def list_task_notes(request: HttpRequest, task_id: UUID) -> list[TaskNoteOut]:
    """A task's notes, oldest first, voided ones included (P23b, 177). Seen by whoever sees the
    task's request."""
    require_task_scope(request, task_id)
    return [_note_out(n) for n in tasks_service.task_notes(task_id)]


@router.post("/tasks/{task_id}/notes", response={201: TaskNoteOut})
def add_task_note(request: HttpRequest, task_id: UUID, payload: TaskNoteIn):
    """Write a note on a task ("Write a note…", 177). Never edited afterwards; void it instead."""
    require_task_scope(request, task_id)
    actor = require_human_actor(request)
    return 201, _note_out(tasks_service.add_task_note(actor, task_id, payload.body))


@router.post("/tasks/{task_id}/notes/{note_id}/void", response=TaskNoteOut)
def void_task_note(
    request: HttpRequest, task_id: UUID, note_id: UUID, payload: VoidTaskNoteIn
) -> TaskNoteOut:
    """Strike a note through with a reason. The note is addressed under its task, so the task's
    request decides who may, and a note of another task is not found."""
    require_task_scope(request, task_id)
    actor = require_human_actor(request)
    return _note_out(tasks_service.void_task_note(actor, task_id, note_id, payload.reason))
