import uuid

from django.db import models
from django.utils import timezone

from portal.records.models._base import LedgerModel
from portal.records.models.choices import (
    Channel,
    DeadlineBasis,
    DeadlineStatus,
    Direction,
    StatusReason,
)
from portal.records.models.directory import (
    AgencyContact,
    EventDefinition,
    RecordsUser,
    StatusDefinition,
)
from portal.records.models.requests import Request, RequestItem
from portal.records.models.sources import Source


class Event(LedgerModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="events"
    )
    occurred_at = models.DateTimeField()
    logged_at = models.DateTimeField(default=timezone.now)
    event_type = models.ForeignKey(
        EventDefinition,
        on_delete=models.DO_NOTHING,
        db_column="event_type",
        to_field="code",
        related_name="+",
    )
    channel = models.TextField(choices=Channel.choices)
    direction = models.TextField(choices=Direction.choices)
    actor_user = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="actor_user_id",
        related_name="+",
    )
    actor_agency_contact = models.ForeignKey(
        AgencyContact,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="actor_agency_contact_id",
        related_name="+",
    )
    recorded_by_user = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="recorded_by_user_id",
        related_name="+",
    )
    recorded_by_system = models.TextField(null=True, blank=True)
    display_body = models.TextField()
    source = models.ForeignKey(
        Source,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="source_id",
        related_name="events",
    )
    related_deadline = models.ForeignKey(
        "records.Deadline",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="related_deadline_id",
        related_name="nudges",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."events'
        ordering = ["occurred_at"]


class Deadline(LedgerModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="deadlines"
    )
    request_item = models.ForeignKey(
        RequestItem,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="request_item_id",
        related_name="deadlines",
    )
    # A foreign key to deadline_definitions (records_db.0007), kept as a plain text column on
    # purpose: a ForeignKey field would rename every `.deadline_type` read to `.deadline_type_id`
    # for no behavioural gain, and the database already refuses an unknown kind.
    deadline_type = models.TextField()
    legal_basis = models.TextField(null=True, blank=True)
    provision = models.TextField(null=True, blank=True)
    basis = models.TextField(choices=DeadlineBasis.choices)
    due_at = models.DateField()
    status = models.TextField(choices=DeadlineStatus.choices, default=DeadlineStatus.ACTIVE)
    source_event = models.ForeignKey(
        Event,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="source_event_id",
        related_name="deadlines_created",
    )
    satisfied_by_event = models.ForeignKey(
        Event,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="satisfied_by_event_id",
        related_name="deadlines_satisfied",
    )
    superseded_by_deadline = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="superseded_by_deadline_id",
        related_name="supersedes",
    )
    flagged_for_review = models.BooleanField(default=False)
    flag_note = models.TextField(null=True, blank=True)
    rule_ref = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."deadlines'
        ordering = ["due_at"]


class DeadlineFollowup(LedgerModel):
    """One nudge cadence per (request, deadline type), never per deadline row."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="followups"
    )
    deadline_type = models.TextField()  # FK to deadline_definitions (records_db.0007)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    followup_count = models.IntegerField(default=0)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."deadline_followups'


class StatusChange(LedgerModel):
    """The ledger. Rows are only ever inserted, or voided alongside a voided event."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="status_changes"
    )
    status = models.ForeignKey(
        StatusDefinition,
        on_delete=models.DO_NOTHING,
        db_column="status",
        to_field="code",
        related_name="+",
    )
    effective_at = models.DateTimeField(default=timezone.now)
    triggering_event = models.ForeignKey(
        Event,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="triggering_event_id",
        related_name="status_changes",
    )
    triggering_deadline = models.ForeignKey(
        Deadline,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="triggering_deadline_id",
        related_name="status_changes",
    )
    reason = models.TextField(choices=StatusReason.choices)
    note = models.TextField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."status_changes'
        ordering = ["effective_at"]
