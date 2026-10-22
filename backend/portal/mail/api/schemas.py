from datetime import datetime
from uuid import UUID

from ninja import Schema


class AddressIn(Schema):
    address: str
    name: str | None = None


class MailRecipientOut(Schema):
    id: UUID
    kind: str
    address: str
    name: str | None
    delivery_state: str
    delivered_at: datetime | None
    bounced_at: datetime | None
    bounce_description: str | None


class MailMessageOut(Schema):
    id: UUID
    request_id: UUID
    tracking_number: str
    letter_recipient_id: UUID | None
    in_reply_to_source_id: UUID | None
    kind: str
    status: str
    from_address: str
    subject: str
    body_text: str
    attach_letter: bool
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    queued_at: datetime | None
    send_after: datetime | None
    sent_at: datetime | None
    test_send: bool
    provider_message_id: str | None
    source_id: UUID | None
    error: str | None
    recipients: list[MailRecipientOut]
    attachment_ids: list[UUID]


class MailMessageIn(Schema):
    request_id: UUID
    kind: str = "new"
    subject: str = ""
    body_text: str = ""
    to: list[AddressIn] = []
    cc: list[AddressIn] = []
    bcc: list[AddressIn] = []
    attachment_ids: list[UUID] = []
    in_reply_to_source_id: UUID | None = None
    attach_letter: bool = False


class MailMessagePatchIn(Schema):
    subject: str | None = None
    body_text: str | None = None
    to: list[AddressIn] | None = None
    cc: list[AddressIn] | None = None
    bcc: list[AddressIn] | None = None
    attachment_ids: list[UUID] | None = None
    attach_letter: bool | None = None


class PrefillIn(Schema):
    forward: bool = False


class MailMessagesOut(Schema):
    items: list[MailMessageOut]
    total: int


class MailCountsOut(Schema):
    drafts: int
    queued: int
    sent: int
    failed: int


class SendAndFileIn(Schema):
    """Stage 4 of New request, for an email agency: the cover message and who gets it. The
    letter is attached as a PDF; the request is filed when the provider accepts the message."""

    subject: str
    body_text: str
    to: list[AddressIn]
    cc: list[AddressIn] = []
    attach_letter: bool = True


class ApprovalRef(Schema):
    """What submitting a letter for approval made: the review, and the request it waits on."""

    id: UUID
    request_id: UUID | None
    subject_type: str


class SavedReplyOut(Schema):
    id: UUID
    name: str
    body: str
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class SavedReplyIn(Schema):
    name: str
    body: str


class SavedReplyPatchIn(Schema):
    name: str | None = None
    body: str | None = None
    archived: bool | None = None


class BadAddressIn(Schema):
    note: str


class SendingOut(Schema):
    """Whether messages really go out (`MAIL_SEND_ENABLED`); off means the provider's test
    token, which accepts and discards."""

    enabled: bool
    undo_send_seconds: int


class FollowupDraftOut(Schema):
    """A follow-up filled from the organisation's templates, for a person to change and send.
    Nothing is saved by asking for it."""

    to: list[str]
    cc: list[str]
    subject: str
    body: str
