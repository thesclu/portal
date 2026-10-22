"""Production review, produced files tied to items, and request links (records_db.0012)."""

import uuid

from django.db import models
from django.utils import timezone

from portal.records.models._base import LedgerModel
from portal.records.models.directory import RecordsUser
from portal.records.models.requests import Request, RequestItem
from portal.records.models.sources import Attachment
from portal.records.models.timeline import Deadline, Event, StatusChange


class Disposition(models.TextChoices):
    PRODUCED = "produced", "Produced"
    PARTLY = "partly", "Partly"
    WITHHELD = "withheld", "Withheld"
    NOT_YET = "not_yet", "Not yet"


class LinkKind(models.TextChoices):
    DUPLICATE = "duplicate", "Duplicate of"
    FOLLOW_ON = "follow_on", "Follow-on to"


class ProductionReview(LedgerModel):
    """One look at what an agency produced, item by item. Append-only: a later review supersedes
    this one (`supersedes`), and the dispositions that stand are the newest review's."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="reviews"
    )
    event = models.ForeignKey(
        Event, on_delete=models.DO_NOTHING, db_column="event_id", related_name="reviews"
    )
    reviewed_by = models.ForeignKey(
        RecordsUser, on_delete=models.DO_NOTHING, db_column="reviewed_by_user_id", related_name="+"
    )
    reviewed_at = models.DateTimeField(default=timezone.now, editable=False)
    note = models.TextField(null=True, blank=True)
    outcome = models.TextField()
    status_change = models.ForeignKey(
        StatusChange,
        on_delete=models.DO_NOTHING,
        db_column="status_change_id",
        related_name="+",
        null=True,
        blank=True,
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        db_column="supersedes_review_id",
        related_name="+",
        null=True,
        blank=True,
    )

    class Meta(LedgerModel.Meta):
        db_table = 'records"."production_reviews'
        ordering = ["reviewed_at"]


class ProductionReviewItem(LedgerModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review = models.ForeignKey(
        ProductionReview, on_delete=models.DO_NOTHING, db_column="review_id", related_name="rows"
    )
    request_item = models.ForeignKey(
        RequestItem, on_delete=models.DO_NOTHING, db_column="request_item_id", related_name="+"
    )
    disposition = models.TextField(choices=Disposition.choices)
    exemption_cited = models.TextField(null=True, blank=True)
    cited_where = models.TextField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."production_review_items'


class AttachmentItem(LedgerModel):
    """A produced file, tied to a request and optionally to one of its items. A null item means
    the whole request. Made by a review, or by hand; voided, never deleted."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attachment = models.ForeignKey(
        Attachment, on_delete=models.DO_NOTHING, db_column="attachment_id", related_name="items"
    )
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="+"
    )
    request_item = models.ForeignKey(
        RequestItem,
        on_delete=models.DO_NOTHING,
        db_column="request_item_id",
        related_name="+",
        null=True,
        blank=True,
    )
    review = models.ForeignKey(
        ProductionReview,
        on_delete=models.DO_NOTHING,
        db_column="review_id",
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(null=True, blank=True)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."attachment_items'


class RequestLink(LedgerModel):
    """`from` is a duplicate of, or a follow-on to, `to`. Directional, with a reason."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="from_request_id", related_name="+"
    )
    to_request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="to_request_id", related_name="+"
    )
    kind = models.TextField(choices=LinkKind.choices)
    reason = models.TextField()
    created_by = models.ForeignKey(
        RecordsUser, on_delete=models.DO_NOTHING, db_column="created_by_user_id", related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(null=True, blank=True)

    class Meta(LedgerModel.Meta):
        db_table = 'records"."request_links'
        ordering = ["created_at"]


class ChallengeKind(models.TextChoices):
    APPEAL = "appeal", "Appeal of a withholding"
    SEARCH = "search_challenge", "Challenge to the search"


class ChallengeOutcome(models.TextChoices):
    REVERSED = "reversed", "Reversed"
    PARTLY_REVERSED = "partly_reversed", "Partly reversed"
    UPHELD = "upheld", "Upheld"
    WITHDRAWN = "withdrawn", "Withdrawn"


class Challenge(LedgerModel):
    """An appeal of a withholding, or a challenge to a "no records" search (records_db.0015).
    `message_id` names the letter in the mail schema without a foreign key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="challenges"
    )
    kind = models.TextField(choices=ChallengeKind.choices)
    request_item = models.ForeignKey(
        RequestItem,
        on_delete=models.DO_NOTHING,
        db_column="request_item_id",
        related_name="+",
        null=True,
        blank=True,
    )
    review_item = models.ForeignKey(
        ProductionReviewItem,
        on_delete=models.DO_NOTHING,
        db_column="review_item_id",
        related_name="+",
        null=True,
        blank=True,
    )
    basis = models.TextField()
    follow_up_by = models.DateField(null=True, blank=True)
    message_id = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(
        RecordsUser, on_delete=models.DO_NOTHING, db_column="created_by_user_id", related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    event = models.ForeignKey(
        Event,
        on_delete=models.DO_NOTHING,
        db_column="event_id",
        related_name="+",
        null=True,
        blank=True,
    )
    followup_deadline = models.ForeignKey(
        Deadline,
        on_delete=models.DO_NOTHING,
        db_column="followup_deadline_id",
        related_name="+",
        null=True,
        blank=True,
    )
    outcome = models.TextField(null=True, blank=True)
    outcome_note = models.TextField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        RecordsUser,
        on_delete=models.DO_NOTHING,
        db_column="resolved_by_user_id",
        related_name="+",
        null=True,
        blank=True,
    )

    class Meta(LedgerModel.Meta):
        db_table = 'records"."challenges'
        ordering = ["created_at"]
