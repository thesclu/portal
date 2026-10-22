"""P11: messages the portal sends. Who may see a message is who may see its request; the
decision is `records/api/permissions.py`'s, asked here for every route."""

from uuid import UUID

from django.db.models.functions import Coalesce
from django.http import HttpRequest
from ninja import Query, Router
from ninja.errors import HttpError

from portal.core.services.settings import get_setting
from portal.drafting.api.permissions import require_recipient_scope
from portal.mail.api.schemas import (
    AddressIn,
    ApprovalRef,
    BadAddressIn,
    FollowupDraftOut,
    MailCountsOut,
    MailMessageIn,
    MailMessageOut,
    MailMessagePatchIn,
    MailMessagesOut,
    MailRecipientOut,
    PrefillIn,
    SavedReplyIn,
    SavedReplyOut,
    SavedReplyPatchIn,
    SendAndFileIn,
    SendingOut,
)
from portal.mail.models import Message, MessageKind, MessageStatus, SavedReply
from portal.mail.services import approval, compose, delivery, followups, replies, send, transport
from portal.mail.services.common import get_message
from portal.records.api.inbox import Since, Until, viewer_span
from portal.records.api.permissions import (
    require_human_actor,
    require_may_release,
    require_named_request_scope,
    require_request_scope,
    require_source_scope,
    require_staff,
    require_team,
    require_viewable_request,
    viewer,
)
from portal.records.models import AgencyContact
from portal.records.services import access
from portal.records.services.common import NotFound

router = Router(tags=["mail"])
BUCKETS = ("drafts", "queued", "sent", "failed", "all")
_BUCKET_STATUS = {
    "drafts": [MessageStatus.DRAFT],
    "queued": [MessageStatus.QUEUED, MessageStatus.SENDING],
    "sent": [MessageStatus.SENT],
    "failed": [MessageStatus.FAILED],
}


def message_out(message: Message) -> MailMessageOut:
    return MailMessageOut(
        id=message.pk,
        request_id=message.request_id,
        tracking_number=message.request.tracking_number,
        letter_recipient_id=message.letter_recipient_id,
        in_reply_to_source_id=message.in_reply_to_source_id,
        kind=message.kind,
        status=message.status,
        from_address=message.from_address,
        subject=message.subject,
        body_text=message.body_text,
        attach_letter=message.attach_letter,
        created_by_user_id=message.created_by_id,
        created_at=message.created_at,
        updated_at=message.updated_at,
        queued_at=message.queued_at,
        send_after=message.send_after,
        sent_at=message.sent_at,
        test_send=message.test_send,
        provider_message_id=message.provider_message_id,
        source_id=message.source_id,
        error=message.error,
        recipients=[
            MailRecipientOut(
                id=r.pk,
                kind=r.kind,
                address=r.address,
                name=r.name,
                delivery_state=r.delivery_state,
                delivered_at=r.delivered_at,
                bounced_at=r.bounced_at,
                bounce_description=r.bounce_description,
            )
            for r in message.recipients.order_by("kind", "address")
        ],
        attachment_ids=[f.attachment_id for f in message.files.all()],
    )


def _full(message_id: UUID) -> MailMessageOut:
    return message_out(
        Message.objects.select_related("request")
        .prefetch_related("recipients", "files")
        .get(pk=message_id)
    )


def require_message_scope(request: HttpRequest, message_id: UUID) -> Message:
    require_team(request)
    message = get_message(message_id)
    require_viewable_request(request, message.request_id)
    return message


def _addresses(items) -> list[compose.Address]:
    return [compose.Address(a.address, a.name) for a in items]


# ---- messages ---------------------------------------------------------------------------------


@router.get("/sending", response=SendingOut)
def sending(request: HttpRequest) -> SendingOut:
    require_team(request)
    return SendingOut(
        enabled=transport.enabled(), undo_send_seconds=int(get_setting("mail.undo_send_seconds"))
    )


@router.get("/counts", response=MailCountsOut)
def counts(request: HttpRequest) -> MailCountsOut:
    require_team(request)
    rows = Message.objects.filter(access.visible_q(viewer(request), "request__"))
    return MailCountsOut(
        **{
            name: rows.filter(status__in=statuses).count()
            for name, statuses in _BUCKET_STATUS.items()
        }
    )


@router.get("/messages", response=MailMessagesOut)
def list_messages(
    request: HttpRequest,
    bucket: str = "sent",
    request_id: UUID | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    since: Since = None,
    until: Until = None,
) -> MailMessagesOut:
    """`since` and `until` (P23b, the Inbox's "Any time", 154) are the person's own dates, both
    inclusive: a message counts from when it was sent, a draft from when it last changed."""
    require_team(request)
    if bucket not in BUCKETS:
        raise HttpError(422, f"bucket must be one of {', '.join(BUCKETS)}.")
    rows = (
        Message.objects.filter(access.visible_q(viewer(request), "request__"))
        .select_related("request")
        .prefetch_related("recipients", "files")
    )
    if bucket != "all":
        rows = rows.filter(status__in=_BUCKET_STATUS[bucket])
    start, end = viewer_span(request, since, until)
    if start is not None or end is not None:
        rows = rows.annotate(_when=Coalesce("sent_at", "updated_at"))
        if start is not None:
            rows = rows.filter(_when__gte=start)
        if end is not None:
            rows = rows.filter(_when__lt=end)
    if request_id is not None:
        rows = rows.filter(request_id=request_id)
    if q:
        needle = q.strip()
        rows = rows.filter(subject__icontains=needle) | rows.filter(body_text__icontains=needle)
        rows = rows.distinct()
    rows = rows.order_by("-updated_at")
    total = rows.count()
    return MailMessagesOut(
        items=[message_out(m) for m in rows[offset : offset + limit]], total=total
    )


@router.post("/messages", response={201: MailMessageOut})
def create_message(request: HttpRequest, payload: MailMessageIn):
    require_request_scope(request, payload.request_id)
    if payload.in_reply_to_source_id is not None:
        require_source_scope(request, payload.in_reply_to_source_id)
    actor = require_human_actor(request)
    if payload.kind == MessageKind.FILING:
        # A follow-up a person writes (the Docket's 034) is a message like any other; sending it
        # records the follow-up on the request (`send.EVENT_TYPE`). A filing is not: stage 4.
        raise HttpError(422, "A filing is made from stage 4.")
    message = compose.create_draft(
        actor,
        payload.request_id,
        kind=payload.kind,
        subject=payload.subject,
        body_text=payload.body_text,
        to=_addresses(payload.to),
        cc=_addresses(payload.cc),
        bcc=_addresses(payload.bcc),
        attachment_ids=payload.attachment_ids,
        in_reply_to_source_id=payload.in_reply_to_source_id,
        attach_letter=payload.attach_letter,
    )
    return 201, _full(message.pk)


@router.post("/requests/{request_id}/followup-draft", response=FollowupDraftOut)
def followup_draft(request: HttpRequest, request_id: UUID) -> FollowupDraftOut:
    """The follow-up the Docket's dialog (034) opens with, rendered here from the settings
    templates the automatic follow-up uses. Read only: it creates nothing. Who may ask is who
    may read the request."""
    require_request_scope(request, request_id)
    draft = followups.draft_for(request_id)
    return FollowupDraftOut(to=draft.to, cc=draft.cc, subject=draft.subject, body=draft.body)


@router.post("/sources/{source_id}/reply-draft", response={201: MailMessageOut})
def reply_draft(request: HttpRequest, source_id: UUID, payload: PrefillIn):
    """A draft reply (or forward) to a message, prefilled from it, on its request."""
    require_source_scope(request, source_id)
    filled = compose.prefill(source_id, forward=payload.forward)
    require_viewable_request(request, filled.request_id)
    actor = require_human_actor(request)
    message = compose.create_draft(
        actor,
        filled.request_id,
        kind=MessageKind.FORWARD if payload.forward else MessageKind.REPLY,
        subject=filled.subject,
        body_text=filled.body_text,
        to=filled.to,
        cc=filled.cc,
        attachment_ids=filled.attachment_ids,
        in_reply_to_source_id=source_id,
    )
    return 201, _full(message.pk)


@router.get("/messages/{message_id}", response=MailMessageOut)
def get_one(request: HttpRequest, message_id: UUID) -> MailMessageOut:
    require_message_scope(request, message_id)
    return _full(message_id)


@router.patch("/messages/{message_id}", response=MailMessageOut)
def update_message(
    request: HttpRequest, message_id: UUID, payload: MailMessagePatchIn
) -> MailMessageOut:
    require_message_scope(request, message_id)
    actor = require_human_actor(request)
    sent = payload.model_dump(exclude_unset=True)
    compose.update_draft(
        actor,
        message_id,
        subject=sent.get("subject"),
        body_text=sent.get("body_text"),
        to=_addresses(payload.to) if payload.to is not None else None,
        cc=_addresses(payload.cc) if payload.cc is not None else None,
        bcc=_addresses(payload.bcc) if payload.bcc is not None else None,
        attachment_ids=payload.attachment_ids,
        attach_letter=payload.attach_letter,
    )
    return _full(message_id)


@router.delete("/messages/{message_id}", response={204: None})
def discard_message(request: HttpRequest, message_id: UUID):
    require_message_scope(request, message_id)
    actor = require_human_actor(request)
    compose.discard_draft(actor, message_id)
    return 204, None


@router.post("/messages/{message_id}/send", response=MailMessageOut)
def send_message(request: HttpRequest, message_id: UUID) -> MailMessageOut:
    """Send is pressed: the message waits its undo window, then the worker sends it. An intern's
    message is refused (403) unless it was approved and has not changed since: theirs go out
    through approval (P20h; `send.queue` checks)."""
    require_message_scope(request, message_id)
    actor = require_human_actor(request)
    send.queue(actor, message_id)
    return _full(message_id)


@router.post("/messages/{message_id}/cancel", response=MailMessageOut)
def cancel_message(request: HttpRequest, message_id: UUID) -> MailMessageOut:
    require_message_scope(request, message_id)
    actor = require_human_actor(request)
    send.cancel(actor, message_id)
    return _full(message_id)


@router.post("/recipients/{recipient_id}/send-and-file", response={201: MailMessageOut})
def send_and_file(request: HttpRequest, recipient_id: UUID, payload: SendAndFileIn):
    """Stage 4, an email agency: the cover message is sent from the request's alias with the
    letter attached, and the recipient's request is filed when the provider accepts it. Not an
    intern's door (403, before anything is written): theirs is POST /mail/recipients/{id}/submit,
    and an approver's "Approve and send" queues it as this does."""
    recipient = require_recipient_scope(request, recipient_id)
    require_may_release(request)
    if recipient.request_id is None:
        raise HttpError(422, "Reserve this recipient first; the letter is sent as reserved.")
    require_named_request_scope(request, recipient.request_id)
    actor = require_human_actor(request)
    message = compose.create_draft(
        actor,
        recipient.request_id,
        kind=MessageKind.FILING,
        subject=payload.subject,
        body_text=payload.body_text,
        to=_addresses(payload.to),
        cc=_addresses(payload.cc),
        letter_recipient_id=recipient_id,
        attach_letter=payload.attach_letter,
    )
    send.queue(actor, message.pk)
    return 201, _full(message.pk)


class SubmitLetterIn(SendAndFileIn):
    """Stage 4 for an intern (232): the cover message as `SendAndFileIn` (an email agency's; left
    empty for a web form or post) and a note for the approver."""

    subject: str = ""
    body_text: str = ""
    to: list[AddressIn] = []
    note: str = ""


@router.post("/recipients/{recipient_id}/submit", response={201: ApprovalRef})
def submit_for_approval(request: HttpRequest, recipient_id: UUID, payload: SubmitLetterIn):
    """An intern's "Submit for approval" (232): the letter to this agency, with its cover message
    when it goes by email, waits for an approver; the request becomes waiting for approval (233).
    Anyone else gets 403: their stage 4 sends or files."""
    recipient = require_recipient_scope(request, recipient_id)
    if recipient.request_id is not None:
        require_named_request_scope(request, recipient.request_id)
    cover = None
    if recipient.channel == "email":
        cover = approval.Cover(
            subject=payload.subject,
            body_text=payload.body_text,
            to=_addresses(payload.to),
            cc=_addresses(payload.cc),
            attach_letter=payload.attach_letter,
        )
    review = approval.submit_letter(request.user, recipient_id, cover=cover, note=payload.note)
    return 201, ApprovalRef(
        id=review.pk, request_id=review.request_id, subject_type=review.subject_type
    )


# ---- saved replies ----------------------------------------------------------------------------


def _reply_out(row: SavedReply) -> SavedReplyOut:
    return SavedReplyOut(
        id=row.pk,
        name=row.name,
        body=row.body,
        created_by_user_id=row.created_by_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
    )


@router.get("/saved-replies", response=list[SavedReplyOut])
def list_saved_replies(request: HttpRequest, include_archived: bool = False):
    require_team(request)
    return [_reply_out(r) for r in replies.list_replies(include_archived=include_archived)]


@router.post("/saved-replies", response={201: SavedReplyOut})
def create_saved_reply(request: HttpRequest, payload: SavedReplyIn):
    require_staff(request)
    actor = require_human_actor(request)
    return 201, _reply_out(replies.create_reply(actor, name=payload.name, body=payload.body))


@router.patch("/saved-replies/{reply_id}", response=SavedReplyOut)
def update_saved_reply(request: HttpRequest, reply_id: UUID, payload: SavedReplyPatchIn):
    require_staff(request)
    actor = require_human_actor(request)
    return _reply_out(
        replies.update_reply(
            actor, reply_id, name=payload.name, body=payload.body, archived=payload.archived
        )
    )


@router.delete("/saved-replies/{reply_id}", response={204: None})
def delete_saved_reply(request: HttpRequest, reply_id: UUID):
    require_staff(request)
    actor = require_human_actor(request)
    replies.delete_reply(actor, reply_id)
    return 204, None


# ---- a bad address ----------------------------------------------------------------------------


@router.post("/contacts/{contact_id}/bad-address", response={200: None})
def bad_address(request: HttpRequest, contact_id: UUID, payload: BadAddressIn):
    """After a bounce, a person marks the agency contact's address as wrong."""
    require_staff(request)
    if not AgencyContact.objects.filter(pk=contact_id).exists():
        raise NotFound(f"No contact {contact_id}.")
    actor = require_human_actor(request)
    delivery.mark_bad_address(actor, contact_id, payload.note)
    return 200, None
