import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from portal.records.models._base import LedgerModel
from portal.records.models.choices import PortalAutomation, RecordsRole


class Agency(LedgerModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    jurisdiction = models.TextField()
    records_law = models.TextField(null=True, blank=True)
    portal_automation = models.TextField(null=True, blank=True, choices=PortalAutomation.choices)
    default_request_email = models.TextField(null=True, blank=True)
    portal_url = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    # records_db.0006. `short_name` feeds {{AGENCY_ABBR}}; the three mailing fields are the postal
    # address the drafting P3b gate left open; `filing_method` (email | portal | mail) and
    # `portal_kind` are free text checked in services/directory.py, not CHECKs (BUILD_PLAN rule 5).
    short_name = models.TextField(null=True, blank=True)
    recipient_office = models.TextField(null=True, blank=True)
    mailing_street = models.TextField(null=True, blank=True)
    mailing_city_state_zip = models.TextField(null=True, blank=True)
    filing_method = models.TextField(null=True, blank=True)
    portal_kind = models.TextField(null=True, blank=True)
    request_email_verified_at = models.DateTimeField(null=True, blank=True)
    # records_db.0016 (P16): the duplicate is marked as the same as the one kept; nothing moves.
    merged_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="merged_into_agency_id",
        related_name="merged_from",
    )
    merged_at = models.DateTimeField(null=True, blank=True)
    merged_by = models.ForeignKey(
        "RecordsUser",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="merged_by_user_id",
        related_name="+",
    )
    merge_note = models.TextField(null=True, blank=True)
    # records_db.0023 (P23b): the file the importer created this agency from (172 "added by
    # import"); None for an agency added by hand. Written once, at insert.
    imported_from = models.TextField(null=True, blank=True)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."agencies'

    def __str__(self) -> str:
        return self.name


class AgencyContact(LedgerModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agency = models.ForeignKey(
        Agency, on_delete=models.DO_NOTHING, db_column="agency_id", related_name="contacts"
    )
    name = models.TextField()
    email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    title = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    # records_db.0006. A unique partial index allows one primary contact per agency.
    is_primary = models.BooleanField(default=False)
    # records_db.0013 (P11): in Cc on every message to the agency; and the bad-address mark a
    # person sets after mail to this contact bounces.
    copy_on_every_request = models.BooleanField(default=False)
    bad_address_at = models.DateTimeField(null=True, blank=True)
    bad_address_note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."agency_contacts'

    def __str__(self) -> str:
        return self.name


class RecordsUser(LedgerModel):
    """The records app's view of a person. Shares its uuid with core.User (records_db.0003)."""

    account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        primary_key=True,
        on_delete=models.DO_NOTHING,
        db_column="id",
        related_name="records_profile",
    )
    role = models.TextField(choices=RecordsRole.choices)
    name = models.TextField()
    email = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."users'

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"


class StatusDefinition(LedgerModel):
    code = models.TextField(primary_key=True)
    label = models.TextField()
    sort_order = models.IntegerField()
    is_terminal = models.BooleanField(default=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."status_definitions'
        ordering = ["sort_order"]

    def __str__(self) -> str:
        return self.label


class EventDefinition(LedgerModel):
    code = models.TextField(primary_key=True)
    label = models.TextField()
    sort_order = models.IntegerField()

    class Meta(LedgerModel.Meta):
        db_table = 'records"."event_definitions'
        ordering = ["sort_order"]

    def __str__(self) -> str:
        return self.label


class DeadlineDefinition(LedgerModel):
    """A kind of deadline, as a row (records_db.0007). Adding one is an insert.

    `overdue_status` is the status a lapsed deadline of this kind moves its request to; null means
    lapsing moves no status. It replaces the dict services/deadlines.py used to hold.
    """

    code = models.TextField(primary_key=True)
    label = models.TextField()
    sort_order = models.IntegerField()
    overdue_status = models.ForeignKey(
        StatusDefinition,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="overdue_status",
        to_field="code",
        related_name="+",
    )

    class Meta(LedgerModel.Meta):
        db_table = 'records"."deadline_definitions'
        ordering = ["sort_order"]

    def __str__(self) -> str:
        return self.label


class AgencyClosure(LedgerModel):
    """records_db.0016 (P16): a dated range on which an agency's office is closed. One more
    holiday on that agency's calendar when a clock is computed. The citation is a person's; never
    filled by the portal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agency = models.ForeignKey(
        Agency, on_delete=models.DO_NOTHING, db_column="agency_id", related_name="closures"
    )
    starts_on = models.DateField()
    ends_on = models.DateField()
    reason = models.TextField()
    citation = models.TextField()
    created_by = models.ForeignKey(
        RecordsUser,
        on_delete=models.DO_NOTHING,
        db_column="created_by_user_id",
        related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="voided_by_user_id",
        related_name="+",
    )
    void_note = models.TextField(null=True, blank=True)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."agency_closures'


class AgencyLogin(LedgerModel):
    """records_db.0018 (P19g): an agency portal's login, the password encrypted at rest. The
    value is never in the audit log or the activity log; who changed it and when is."""

    agency = models.OneToOneField(
        Agency,
        primary_key=True,
        on_delete=models.DO_NOTHING,
        db_column="agency_id",
        related_name="login",
    )
    login = models.TextField(default="")
    password_ciphertext = models.TextField(default="")
    notes = models.TextField(null=True, blank=True)
    changed_at = models.DateTimeField(default=timezone.now)
    changed_by = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="changed_by_user_id",
        related_name="+",
    )

    class Meta(LedgerModel.Meta):
        db_table = 'records"."agency_logins'

    def __str__(self) -> str:
        return f"login for {self.agency_id}"
