"""Unmanaged models over the `mail` schema (db/mail/mail-schema.sql)."""

import uuid

from django.db import models
from django.utils import timezone

from portal.drafting.models import LetterRecipient
from portal.records.models import Attachment, RecordsUser, Request, Source


class MailModel(models.Model):
    class Meta:
        abstract = True
        managed = False


class MessageKind(models.TextChoices):
    NEW = "new", "New message"
    REPLY = "reply", "Reply"
    FORWARD = "forward", "Forward"
    FILING = "filing", "Filing"
    FOLLOWUP = "followup", "Follow-up"


class MessageStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class RecipientKind(models.TextChoices):
    TO = "to", "To"
    CC = "cc", "Cc"
    BCC = "bcc", "Bcc"


class DeliveryState(models.TextChoices):
    PENDING = "pending", "Pending"
    DELIVERED = "delivered", "Delivered"
    BOUNCED = "bounced", "Bounced"
    COMPLAINED = "complained", "Marked as spam"


class Message(MailModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        Request, on_delete=models.DO_NOTHING, db_column="request_id", related_name="+"
    )
    letter_recipient = models.ForeignKey(
        LetterRecipient,
        on_delete=models.DO_NOTHING,
        db_column="letter_recipient_id",
        related_name="+",
        null=True,
        blank=True,
    )
    in_reply_to_source = models.ForeignKey(
        Source,
        on_delete=models.DO_NOTHING,
        db_column="in_reply_to_source_id",
        related_name="+",
        null=True,
        blank=True,
    )
    kind = models.TextField(choices=MessageKind.choices)
    status = models.TextField(choices=MessageStatus.choices, default=MessageStatus.DRAFT)
    from_address = models.TextField()
    subject = models.TextField(default="")
    body_text = models.TextField(default="")
    attach_letter = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        RecordsUser,
        on_delete=models.DO_NOTHING,
        db_column="created_by_user_id",
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)
    queued_at = models.DateTimeField(null=True, blank=True)
    send_after = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    test_send = models.BooleanField(default=False)
    provider_message_id = models.TextField(null=True, blank=True)
    source = models.ForeignKey(
        Source,
        on_delete=models.DO_NOTHING,
        db_column="source_id",
        related_name="+",
        null=True,
        blank=True,
    )
    error = models.TextField(null=True, blank=True)

    class Meta(MailModel.Meta):
        db_table = 'mail"."messages'
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.kind} {self.subject!r} ({self.status})"


class MessageRecipient(MailModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        Message, on_delete=models.DO_NOTHING, db_column="message_id", related_name="recipients"
    )
    kind = models.TextField(choices=RecipientKind.choices)
    address = models.TextField()
    name = models.TextField(null=True, blank=True)
    delivery_state = models.TextField(choices=DeliveryState.choices, default=DeliveryState.PENDING)
    delivered_at = models.DateTimeField(null=True, blank=True)
    bounced_at = models.DateTimeField(null=True, blank=True)
    bounce_description = models.TextField(null=True, blank=True)

    class Meta(MailModel.Meta):
        db_table = 'mail"."message_recipients'

    def __str__(self) -> str:
        return f"{self.kind}: {self.address} ({self.delivery_state})"


class MessageAttachment(MailModel):
    pk = models.CompositePrimaryKey("message", "attachment")
    message = models.ForeignKey(
        Message, on_delete=models.DO_NOTHING, db_column="message_id", related_name="files"
    )
    attachment = models.ForeignKey(
        Attachment, on_delete=models.DO_NOTHING, db_column="attachment_id", related_name="+"
    )

    class Meta(MailModel.Meta):
        db_table = 'mail"."message_attachments'

    def __str__(self) -> str:
        return f"{self.message_id} -> {self.attachment_id}"


class DeliveryEvent(MailModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        Message, on_delete=models.DO_NOTHING, db_column="message_id", related_name="deliveries"
    )
    recipient = models.ForeignKey(
        MessageRecipient,
        on_delete=models.DO_NOTHING,
        db_column="recipient_id",
        related_name="+",
        null=True,
        blank=True,
    )
    provider_event_id = models.TextField(unique=True)
    record_type = models.TextField()
    payload = models.JSONField()
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta(MailModel.Meta):
        db_table = 'mail"."delivery_events'

    def __str__(self) -> str:
        return f"{self.record_type} {self.provider_event_id}"


class SavedReply(MailModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    body = models.TextField()
    created_by = models.ForeignKey(
        RecordsUser,
        on_delete=models.DO_NOTHING,
        db_column="created_by_user_id",
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta(MailModel.Meta):
        db_table = 'mail"."saved_replies'
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


MAIL_MODELS = [Message, MessageRecipient, MessageAttachment, DeliveryEvent, SavedReply]
