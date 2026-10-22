import uuid

from django.db import models
from django.utils import timezone

from portal.records.models._base import LedgerModel
from portal.records.models.choices import FeeStatus
from portal.records.models.directory import RecordsUser
from portal.records.models.requests import Request
from portal.records.models.timeline import Event


class Fee(LedgerModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="fees"
    )
    source_event = models.ForeignKey(
        Event,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="source_event_id",
        related_name="fees",
    )
    amount_cents = models.IntegerField(null=True, blank=True)
    status = models.TextField(choices=FeeStatus.choices)
    waiver_reason = models.TextField(null=True, blank=True)
    due_at = models.DateField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    # records_db.0006. "For: Duplication - 425 pages" and "Method: Check #1041"; both free text.
    description = models.TextField(null=True, blank=True)
    payment_method = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."fees'


class Task(LedgerModel):
    """Something a human still has to go do."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="tasks"
    )
    task_type = models.TextField()
    description = models.TextField()
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="completed_by_user_id",
        related_name="+",
    )
    # records_db.0006. `assigned_to` is who has it. The creator is a person or a named process,
    # never both (tasks_creator_not_both), and is written once at insert: portal_app has no UPDATE
    # grant on either column, the same footing as deadlines.rule_ref.
    assigned_to = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="assigned_to_user_id",
        related_name="assigned_tasks",
    )
    created_by = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="created_by_user_id",
        related_name="+",
    )
    created_by_system = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    # records_db.0023 (P23b). The message the task is about: a bounce's sent message ("Retry
    # send" opens it), the message a classification opened it from, or the one a person made it
    # from. Written once, at insert; portal_app has no UPDATE grant on it.
    source = models.ForeignKey(
        "Source",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="source_id",
        related_name="+",
    )

    class Meta(LedgerModel.Meta):
        db_table = 'records"."tasks'


class TaskNote(LedgerModel):
    """A note on a task (P23b, 177 "Write a note…"; records_db.0023), the message notes' pattern:
    voided with a reason and by whom, never deleted, and its text is not editable (the app role
    may update only the three void columns)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        Task, on_delete=models.DO_NOTHING, db_column="task_id", related_name="notes"
    )
    author = models.ForeignKey(
        RecordsUser, on_delete=models.DO_NOTHING, db_column="author_user_id", related_name="+"
    )
    body = models.TextField()
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="voided_by_user_id",
        related_name="+",
    )
    void_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."task_notes'
        ordering = ["created_at"]


class AuditLog(LedgerModel):
    """Every edit to something that is supposed to be reliable. A readable diff, spanning tables."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity_type = models.TextField()
    entity_id = models.UUIDField()
    field_name = models.TextField()
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)
    edited_by = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="edited_by_user_id",
        related_name="+",
    )
    note = models.TextField()
    edited_at = models.DateTimeField(default=timezone.now)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."audit_log'
        ordering = ["-edited_at"]
