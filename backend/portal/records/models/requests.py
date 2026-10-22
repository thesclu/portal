import uuid

from django.db import models
from django.utils import timezone

from portal.records.models._base import LedgerModel
from portal.records.models.choices import Origin, Sensitivity
from portal.records.models.directory import Agency, RecordsUser, StatusDefinition


class Request(LedgerModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tracking_number = models.TextField(unique=True)
    agency_reference_number = models.TextField(null=True, blank=True)
    origin = models.TextField(choices=Origin.choices)
    requester = models.ForeignKey(
        RecordsUser,
        on_delete=models.DO_NOTHING,
        db_column="requester_user_id",
        related_name="requests",
    )
    agency = models.ForeignKey(
        Agency, on_delete=models.DO_NOTHING, db_column="agency_id", related_name="requests"
    )
    inbound_alias_email = models.TextField(null=True, blank=True, unique=True)
    subject = models.TextField()
    request_text = models.TextField()
    auto_followups_enabled = models.BooleanField(default=True)
    # Cache columns. Only schema.sql's triggers move these; the app role cannot UPDATE them.
    current_status = models.ForeignKey(
        StatusDefinition,
        on_delete=models.DO_NOTHING,
        db_column="current_status",
        to_field="code",
        related_name="+",
    )
    current_status_changed_at = models.DateTimeField(null=True, blank=True)
    next_deadline_at = models.DateField(null=True, blank=True)
    sensitivity = models.TextField(choices=Sensitivity.choices, default=Sensitivity.INTERNAL)
    # records_db.0006. Who is working it; `requester` is who it is for.
    assigned_to = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="assigned_to_user_id",
        related_name="assigned_requests",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    filed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    # Not a column. `services.requests.docket` sets it on each row it returns: next_deadline_at
    # minus today on the agency's calendar, signed. A plain class attribute is ignored by Django's
    # field machinery, so the drift test still sees exactly the table's columns.
    days_to_next_deadline: int | None = None

    class Meta(LedgerModel.Meta):
        db_table = 'records"."requests'

    def __str__(self) -> str:
        return self.tracking_number


class RequestItem(LedgerModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="items"
    )
    item_number = models.TextField()
    parent_item = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="parent_item_id",
        related_name="children",
    )
    description = models.TextField()
    # records_db.0005. item_number is free text ('3', '4a'), so ordering by it is a string sort
    # that puts '10' ahead of '2'; this is the order the items are meant to be read in.
    sort_order = models.IntegerField(default=0)
    # records_db.0006. The rules-package provision this item has its own clock under, so
    # file_request computes the same deadlines whichever door files the request.
    provision = models.TextField(null=True, blank=True)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."request_items'

    def __str__(self) -> str:
        return f"{self.item_number}: {self.description[:60]}"


class RequestAccess(LedgerModel):
    """One person's access to one privileged request (records_db.0010).

    A row with `revoked_at` null is an active grant. Access ends by being revoked, never by the
    row being deleted; the app role may update only the two revoke columns. Read by
    `services/access.py` and, through it, by `api/permissions.py`, where the rule lives.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="access"
    )
    user = models.ForeignKey(
        RecordsUser, on_delete=models.DO_NOTHING, db_column="user_id", related_name="+"
    )
    granted_by = models.ForeignKey(
        RecordsUser,
        on_delete=models.DO_NOTHING,
        db_column="granted_by_user_id",
        related_name="+",
        null=True,
        blank=True,
    )
    note = models.TextField(null=True, blank=True)
    granted_at = models.DateTimeField(default=timezone.now, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        RecordsUser,
        on_delete=models.DO_NOTHING,
        db_column="revoked_by_user_id",
        related_name="+",
        null=True,
        blank=True,
    )

    class Meta(LedgerModel.Meta):
        db_table = 'records"."request_access'
        ordering = ["granted_at"]


class AccessRequest(LedgerModel):
    """One person asking for a privileged request (records_db.0014). Open while `decided_at` is
    null; allowing it writes the grant. Never deleted."""

    ALLOWED, DECLINED = "allowed", "declined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="access_asks"
    )
    user = models.ForeignKey(
        RecordsUser, on_delete=models.DO_NOTHING, db_column="user_id", related_name="+"
    )
    reason = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        RecordsUser,
        on_delete=models.DO_NOTHING,
        db_column="decided_by_user_id",
        related_name="+",
        null=True,
        blank=True,
    )
    decision = models.TextField(null=True, blank=True)
    decision_note = models.TextField(null=True, blank=True)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."access_requests'
        ordering = ["created_at"]
