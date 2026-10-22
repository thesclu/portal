"""The intern approval workflow around `core.reviews` (P20h; DECISIONS 42, 43).

`core/services/review.py` keeps the rows and decides who may decide them; this module does what a
submission or a decision means for the request and its mail, in one transaction, under the locks
the ledger uses everywhere (the request row first, then the message).

  submit_letter   Stage 4, an intern, one agency (232). The letter is reserved if it is not yet,
                  and frozen again from its draft (`drafting.filing.refresh_reserved`), so what an
                  approver reads is what the draft says now. An email agency's cover message is
                  written as the filing draft and that MESSAGE is submitted; a web-form or postal
                  agency's letter is submitted as the RECIPIENT. The request moves to
                  `waiting_for_approval` (233).
  submit_message  An intern's reply, follow-up or forward (the Inbox, the request page): the
                  message is submitted. The request is already filed; no status moves.
  withdraw        The intern takes it back (233). A filing returns to `pending_filing`.
  decide          An approver (234-237). APPROVE AND SEND: a message is queued by `send.queue`,
                  the very call Send and stage 4's send-and-file make, so the worker sends it and,
                  for a filing, files the request exactly as the intern's stage 4 would have. It
                  happens once: the review row is locked and must be undecided, and the message
                  must still be the draft that was submitted, unchanged since. A filing first
                  returns to `pending_filing` with the approver's name on the status row (the
                  approver is recorded on the request there and in the activity line). APPROVE, for
                  a letter filed by hand, releases it: `pending_filing`, and the intern's own
                  filing is now cleared. SEND BACK needs a note (237); a filing returns to
                  `pending_filing`, the note on its status row and on the review (238).
"""

from dataclasses import dataclass, field
from uuid import UUID

from django.db import transaction

from portal.core.models import Review, User
from portal.core.services import review as review_service
from portal.drafting.models import LetterRecipient
from portal.drafting.services import filing as drafting_filing
from portal.mail.models import Message, MessageKind, MessageStatus
from portal.mail.services import compose, send
from portal.mail.services.common import get_message, lock_message
from portal.records import roles
from portal.records.actors import Actor
from portal.records.models import Request
from portal.records.services import status as status_service
from portal.records.services.common import (
    InvalidTransition,
    NotAllowed,
    NotFound,
    ServiceError,
    lock_request,
)
from portal.records.services.status import PRE_FILING, WAITING_FOR_APPROVAL

PENDING_FILING = "pending_filing"


@dataclass
class Cover:
    """The cover message an email agency's letter goes with, as stage 4 writes it (050/232)."""

    subject: str
    body_text: str
    to: list[compose.Address] = field(default_factory=list)
    cc: list[compose.Address] = field(default_factory=list)
    attach_letter: bool = True


def _require_intern(user: User) -> Actor:
    if not roles.needs_approval(user.pk):
        raise NotAllowed("Your role files and sends without approval; there is nothing to submit.")
    return Actor.human(user)


def _move(request: Request, status: str, actor: Actor, note: str) -> None:
    status_service._append_status_change(
        request, status, reason="manual_override", actor=actor, note=note
    )


def is_filing(review: Review, message: Message | None = None) -> bool:
    """Whether this approval is a letter's filing (it moves the request's status)."""
    if review.subject_type == Review.RECIPIENT:
        return True
    if review.subject_type != Review.MESSAGE:
        return False
    found = message or Message.objects.filter(pk=review.subject_id).first()
    return found is not None and found.kind == MessageKind.FILING


def filing_message_of(recipient_id: UUID) -> Message | None:
    """The cover message an email letter goes with, while it is still a draft."""
    return (
        Message.objects.filter(
            letter_recipient_id=recipient_id, kind=MessageKind.FILING, status=MessageStatus.DRAFT
        )
        .order_by("-created_at")
        .first()
    )


@transaction.atomic
def submit_letter(user: User, recipient_id: UUID, *, cover: Cover | None, note: str = "") -> Review:
    actor = _require_intern(user)
    recipient = LetterRecipient.objects.filter(pk=recipient_id).first()
    if recipient is None:
        raise NotFound(f"No recipient {recipient_id}.")
    if recipient.request_id is None:
        recipient = drafting_filing.reserve_recipient(actor, recipient_id)
    assert recipient.request_id is not None
    request = lock_request(recipient.request_id)
    recipient = drafting_filing.reconcile_recipient(recipient_id)
    if recipient.status != LetterRecipient.PENDING:
        raise InvalidTransition(f"This letter is {recipient.status}; only a pending one is sent.")
    if request.current_status_id != PENDING_FILING:
        raise InvalidTransition(
            f"{request.tracking_number} is {request.current_status_id.replace('_', ' ')}; only a "
            "letter that is pending filing is submitted for approval."
        )
    on_its_way = Message.objects.filter(
        letter_recipient_id=recipient_id,
        kind=MessageKind.FILING,
        status__in=(MessageStatus.QUEUED, MessageStatus.SENDING, MessageStatus.SENT),
    )
    if on_its_way.exists():
        raise InvalidTransition("This letter is already on its way to the agency.")
    drafting_filing.refresh_reserved(actor, recipient_id)
    if recipient.channel == "email":
        if cover is None:
            raise ServiceError("A letter that goes by email is submitted with its cover message.")
        message = filing_message_of(recipient_id)
        if message is None:
            message = compose.create_draft(
                actor,
                request.pk,
                kind=MessageKind.FILING,
                subject=cover.subject,
                body_text=cover.body_text,
                to=cover.to,
                cc=cover.cc,
                letter_recipient_id=recipient_id,
                attach_letter=cover.attach_letter,
            )
        else:
            compose.update_draft(
                actor,
                message.pk,
                subject=cover.subject,
                body_text=cover.body_text,
                to=cover.to,
                cc=cover.cc,
                attach_letter=cover.attach_letter,
            )
        message = get_message(message.pk)
        send._check_sendable(message)
        review = review_service.submit(
            user,
            Review.MESSAGE,
            message.pk,
            request_id=request.pk,
            note=note,
            title=f"the letter to {recipient.agency.name}",
        )
    else:
        review = review_service.submit(
            user,
            Review.RECIPIENT,
            recipient.pk,
            request_id=request.pk,
            note=note,
            title=f"the letter to {recipient.agency.name}",
        )
    _move(Request.objects.get(pk=request.pk), WAITING_FOR_APPROVAL, actor, "Submitted for approval")
    return review


@transaction.atomic
def submit_message(user: User, message_id: UUID, *, note: str = "") -> Review:
    _require_intern(user)
    head = get_message(message_id)
    lock_request(head.request_id)
    message = lock_message(message_id)
    if message.kind == MessageKind.FILING:
        raise ServiceError("A letter's cover message is submitted from stage 4, with the letter.")
    if message.status not in (MessageStatus.DRAFT, MessageStatus.FAILED):
        raise InvalidTransition(f"This message is {message.status}; it cannot be submitted.")
    send._check_sendable(message)
    titles = {
        MessageKind.REPLY: "a reply",
        MessageKind.FOLLOWUP: "a follow-up",
        MessageKind.FORWARD: "a forward",
    }
    return review_service.submit(
        user,
        Review.MESSAGE,
        message.pk,
        request_id=message.request_id,
        note=note,
        title=titles.get(message.kind, "a message"),
    )


@transaction.atomic
def withdraw(user: User, review_id: UUID) -> Review:
    head = Review.objects.filter(pk=review_id).first()
    if head is None:
        raise NotFound(f"No review {review_id}.")
    request = lock_request(head.request_id) if head.request_id else None
    review = review_service.withdraw(user, review_id)
    waiting = request is not None and request.current_status_id == WAITING_FOR_APPROVAL
    if waiting and is_filing(review):
        assert request is not None
        _move(request, PENDING_FILING, Actor.human(user), "Withdrawn from approval")
    return review


@transaction.atomic
def decide(user: User, review_id: UUID, decision: str, comment: str = "") -> Review:
    head = Review.objects.filter(pk=review_id).first()
    if head is None:
        raise NotFound(f"No review {review_id}.")
    if head.kind != Review.APPROVAL or head.request_id is None:
        # A question, or one of P13's draft reviews: the row is all there is.
        return review_service.decide(user, review_id, decision, comment)
    request = lock_request(head.request_id)
    message: Message | None = None
    if head.subject_type == Review.MESSAGE:
        message = lock_message(head.subject_id)
    filing = is_filing(head, message)
    if decision == Review.APPROVED:
        if filing and request.current_status_id not in PRE_FILING:
            raise InvalidTransition(
                f"{request.tracking_number} is {request.current_status_id.replace('_', ' ')}; "
                "this letter can no longer go out from here."
            )
        if message is not None:
            if message.status not in (MessageStatus.DRAFT, MessageStatus.FAILED):
                raise InvalidTransition(
                    f"This message is {message.status}; it is not the draft that was submitted."
                )
            if message.updated_at > head.submitted_at:
                raise InvalidTransition(
                    "It changed after it was submitted. Ask for it to be submitted again."
                )
    review = review_service.decide(user, review_id, decision, comment)
    actor = Actor.human(user)
    if filing and request.current_status_id == WAITING_FOR_APPROVAL:
        if decision == Review.APPROVED:
            note = f"Approved by {user.name}"
        else:
            note = f"Sent back by {user.name}: {comment.strip()}"
        _move(request, PENDING_FILING, actor, note)
    if decision == Review.APPROVED and message is not None:
        # The one send. `queue` refuses anything but a draft (or a failed send), so a second
        # approval of the same message could not send it twice even if it got this far; it does
        # not, because the review row is locked and decided above.
        send.queue(actor, message.pk)
    return review
