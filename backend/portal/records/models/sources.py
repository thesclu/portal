import uuid

from django.db import models
from django.utils import timezone

from portal.records.models._base import LedgerModel
from portal.records.models.choices import AttachmentRole, SourceType
from portal.records.models.directory import RecordsUser


class Source(LedgerModel):
    """The untouched original. A source belongs to no request; events do."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.TextField(choices=SourceType.choices)
    external_ref = models.TextField(null=True, blank=True)
    message_id = models.TextField(null=True, blank=True)
    raw_metadata = models.JSONField(null=True, blank=True)
    raw_storage_url = models.TextField()
    flagged_for_review = models.BooleanField(default=False)
    flag_note = models.TextField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="reviewed_by_user_id",
        related_name="+",
    )
    # records_db.0006. The Inbox: why a message was flagged, who has it, and how it was dealt with
    # when it was not attached to a request. Vocabularies are checked in services/sources.py.
    flag_reason = models.TextField(null=True, blank=True)
    # records_db.0011: when the inbound job finished with this message. Null for anything that
    # is not mail, and for mail the job has not reached yet.
    ingested_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="assigned_to_user_id",
        related_name="assigned_sources",
    )
    resolution = models.TextField(null=True, blank=True)
    resolution_note = models.TextField(null=True, blank=True)
    captured_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."sources'


class Attachment(LedgerModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        Source, on_delete=models.DO_NOTHING, db_column="source_id", related_name="attachments"
    )
    filename = models.TextField()
    mime_type = models.TextField(null=True, blank=True)
    size_bytes = models.IntegerField(null=True, blank=True)
    storage_url = models.TextField()
    role = models.TextField(choices=AttachmentRole.choices, default=AttachmentRole.ATTACHMENT)
    checksum_sha256 = models.TextField(null=True, blank=True)
    # records_db.0006. "34 pp". Set at ingest; null when the file's pages cannot be counted.
    page_count = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."attachments'


class SourceNote(LedgerModel):
    """A note on a message, written before (or whether or not) it belongs to any request.

    records_db.0009. Why a table and not a `note` event: `events.request_id` is NOT NULL, and a
    message needs notes while it is still unattached. Voided, never deleted; the text is not
    editable (the app role may update only `voided_at` and `void_reason`).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        Source, on_delete=models.DO_NOTHING, db_column="source_id", related_name="notes"
    )
    author = models.ForeignKey(
        RecordsUser, on_delete=models.DO_NOTHING, db_column="author_user_id", related_name="+"
    )
    body = models.TextField()
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."source_notes'
        ordering = ["created_at"]
