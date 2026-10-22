"""Intern approval (P20h) and asking a teammate (P13). Who may see a subject is decided where that
subject lives (records, drafting, mail permissions); this router asks them, and the workflow is
`mail/services/approval.py`'s."""

from datetime import datetime
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from portal.core.models import Review, User
from portal.core.services import review as review_service
from portal.core.services.identity import records_role_of
from portal.drafting.api.permissions import (
    draft_visible_to,
    require_draft_scope,
    require_recipient_scope,
)
from portal.drafting.models import LetterDraft, LetterRecipient
from portal.mail.api.messages import require_message_scope
from portal.mail.models import Message
from portal.mail.services import approval as approval_service
from portal.records import roles
from portal.records.api.permissions import (
    require_request_scope,
    require_source_scope,
    require_team,
    visible_request_ids,
)
from portal.records.models import Event, Request
from portal.records.services import access
from portal.records.services.access import Viewer
from portal.records.services.common import NotFound

router = Router(tags=["reviews"])


class ApprovalOut(Schema):
    id: UUID
    subject_type: str
    subject_id: UUID
    request_id: UUID | None
    kind: str
    submitted_by_user_id: UUID
    submitted_by_name: str
    # P20h: the submitter's role (234 "Maya Torres · Intern").
    submitted_by_role: str | None = None
    submitted_at: datetime
    note: str
    assigned_to_user_id: UUID | None
    decision: str | None
    decided_by_user_id: UUID | None
    decided_by_name: str | None
    decided_at: datetime | None
    comment: str
    # A line naming the subject, for a list: the letter's subject for a filing, the message's
    # subject otherwise, or the inbound message's text.
    title: str
    # P20h, for the queue and the review page (234, 235): the request as the reader may see it
    # (null when they may not), what kind of message it is, and the letter it belongs to.
    tracking_number: str | None = None
    agency_name: str | None = None
    agency_short_name: str | None = None
    message_kind: str | None = None
    recipient_id: UUID | None = None
    draft_id: UUID | None = None
    channel: str | None = None
    # Whether the reader may approve or send this back (`review.may_approve`).
    can_decide: bool = False


class ApprovalIn(Schema):
    subject_type: str
    subject_id: UUID
    kind: str = "approval"
    note: str = ""
    assigned_to_user_id: UUID | None = None


class ApprovalDecisionIn(Schema):
    decision: str
    comment: str = ""


class ClearanceOut(Schema):
    required: bool
    cleared: bool
    why: str
    review: ApprovalOut | None = None


def _letter_parts(review: Review) -> tuple[Message | None, LetterRecipient | None]:
    message = None
    recipient = None
    if review.subject_type == Review.MESSAGE:
        message = Message.objects.filter(pk=review.subject_id).first()
        if message is not None and message.letter_recipient_id:
            recipient = LetterRecipient.objects.filter(pk=message.letter_recipient_id).first()
    elif review.subject_type == Review.RECIPIENT:
        recipient = LetterRecipient.objects.filter(pk=review.subject_id).first()
    return message, recipient


def _title(review: Review, message: Message | None, recipient: LetterRecipient | None) -> str:
    if recipient is not None:
        draft = LetterDraft.objects.filter(pk=recipient.draft_id).first()
        return (draft.subject if draft else "") or "A letter"
    if review.subject_type == Review.DRAFT:
        row = LetterDraft.objects.filter(pk=review.subject_id).first()
        return row.subject if row else "A letter"
    if review.subject_type == Review.MESSAGE:
        return (message.subject if message else "") or "A message"
    if review.subject_type == Review.REQUEST:
        # Read through the policies, like the request in `review_out`.
        about = Request.objects.filter(pk=review.subject_id).first()
        return (about.subject if about else "") or "A request"
    event = Event.objects.filter(source_id=review.subject_id, voided_at__isnull=True).first()
    return (event.display_body[:80] if event else "A message in the Inbox") or "A message"


def review_out(review: Review, reader: User | None = None) -> ApprovalOut:
    message, recipient = _letter_parts(review)
    # Read through the policies: a request the reader may not see comes back as nothing.
    request = (
        Request.objects.select_related("agency").filter(pk=review.request_id).first()
        if review.request_id
        else None
    )
    return ApprovalOut(
        id=review.pk,
        subject_type=review.subject_type,
        subject_id=review.subject_id,
        request_id=review.request_id,
        kind=review.kind,
        submitted_by_user_id=review.submitted_by_id,
        submitted_by_name=review.submitted_by.name,
        submitted_by_role=records_role_of(review.submitted_by),
        submitted_at=review.submitted_at,
        note=review.note,
        assigned_to_user_id=review.assigned_to_id,
        decision=review.decision,
        decided_by_user_id=review.decided_by_id,
        decided_by_name=review.decided_by.name if review.decided_by_id else None,
        decided_at=review.decided_at,
        comment=review.comment,
        title=_title(review, message, recipient),
        tracking_number=request.tracking_number if request else None,
        agency_name=request.agency.name if request else None,
        agency_short_name=(request.agency.short_name or None) if request else None,
        message_kind=message.kind if message else None,
        recipient_id=recipient.pk if recipient else None,
        draft_id=recipient.draft_id if recipient else None,
        channel=recipient.channel if recipient else ("email" if message else None),
        can_decide=bool(
            reader is not None
            and review.decision is None
            and (
                review_service.may_approve(reader, review)
                if review.kind == Review.APPROVAL
                else review.assigned_to_id == reader.pk
            )
        ),
    )


def _subject_changed_at(subject_type: str, subject_id: UUID) -> datetime | None:
    from portal.drafting.services.filing import letter_changed_at

    if subject_type == Review.DRAFT:
        row = LetterDraft.objects.filter(pk=subject_id).first()
        return row.updated_at if row else None
    if subject_type == Review.MESSAGE:
        message = Message.objects.filter(pk=subject_id).first()
        return message.updated_at if message else None
    if subject_type == Review.RECIPIENT:
        recipient = LetterRecipient.objects.filter(pk=subject_id).first()
        return letter_changed_at(recipient) if recipient else None
    return None


def require_subject_scope(request: HttpRequest, subject_type: str, subject_id: UUID) -> UUID | None:
    """The subject's own permission check; returns the request it belongs to, if any."""
    require_team(request)
    if subject_type == Review.DRAFT:
        require_draft_scope(request, subject_id)
        return None
    if subject_type == Review.MESSAGE:
        return require_message_scope(request, subject_id).request_id
    if subject_type == Review.RECIPIENT:
        return require_recipient_scope(request, subject_id).request_id
    if subject_type == Review.SOURCE:
        require_source_scope(request, subject_id)
        event = Event.objects.filter(source_id=subject_id, voided_at__isnull=True).first()
        return event.request_id if event else None
    if subject_type == Review.REQUEST:
        return require_request_scope(request, subject_id).pk
    raise HttpError(422, f"Unknown review subject {subject_type!r}.")


def _visible(request: HttpRequest, rows: list[Review]) -> list[Review]:
    """A review is seen by whoever sees its subject. Drafts are checked one by one (their rule
    is their own); the rest by request."""
    wanted = {r.request_id for r in rows if r.request_id}
    ok = visible_request_ids(request, wanted) if wanted else set()
    out = []
    for row in rows:
        if row.subject_type == Review.DRAFT:
            try:
                require_draft_scope(request, row.subject_id)
            except Exception:  # noqa: BLE001 -- refused or gone: not shown
                continue
            out.append(row)
        elif row.request_id is None or row.request_id in ok:
            out.append(row)
    return out


@router.get("/pending", response=list[ApprovalOut])
def pending(request: HttpRequest) -> list[ApprovalOut]:
    """What the caller is asked to decide (Tasks › Approvals, 234): the interns' letters and
    messages they may approve, and the questions put to them. Only subjects they may see."""
    require_team(request)
    rows = _visible(request, review_service.pending_for(request.user))
    return [review_out(r, request.user) for r in rows]


@router.get("/mine", response=list[ApprovalOut])
def mine(request: HttpRequest) -> list[ApprovalOut]:
    """What the caller submitted, newest first, with its decision."""
    require_team(request)
    rows = list(Review.objects.filter(submitted_by=request.user).order_by("-submitted_at")[:100])
    return [review_out(r, request.user) for r in rows]


@router.get("/clearance", response=ClearanceOut)
def clearance(request: HttpRequest, subject_type: str, subject_id: UUID) -> ClearanceOut:
    """Whether the caller may file or send this subject now, and if not, why."""
    require_subject_scope(request, subject_type, subject_id)
    found = review_service.clearance(
        request.user, subject_type, subject_id, _subject_changed_at(subject_type, subject_id)
    )
    return ClearanceOut(
        required=found.required,
        cleared=found.cleared,
        why=found.why,
        review=review_out(found.review, request.user) if found.review else None,
    )


@router.get("", response=list[ApprovalOut])
def history(
    request: HttpRequest,
    subject_type: str | None = None,
    subject_id: UUID | None = None,
    request_id: UUID | None = None,
) -> list[ApprovalOut]:
    """A subject's reviews, newest first; or (P20h) the approvals on one request, for its page's
    strip (233, 238): waiting since when, or sent back by whom with what note. Scoped by the
    subject, or by the request, as everywhere."""
    if subject_type is not None and subject_id is not None:
        require_subject_scope(request, subject_type, subject_id)
        rows = review_service.history(subject_type, subject_id)
    elif request_id is not None:
        require_request_scope(request, request_id)
        rows = list(
            Review.objects.filter(request_id=request_id, kind=Review.APPROVAL)
            .exclude(subject_type=Review.DRAFT)
            .order_by("-submitted_at")
        )
    else:
        raise HttpError(422, "Name a subject (subject_type and subject_id) or a request_id.")
    return [review_out(r, request.user) for r in rows]


@router.post("", response={201: ApprovalOut})
def submit(request: HttpRequest, payload: ApprovalIn):
    """An intern submits a message (a reply, a follow-up) or a letter filed by hand for approval;
    anyone asks a teammate a question. A letter that goes by email is submitted with its cover
    message from stage 4 (POST /mail/recipients/{id}/submit)."""
    request_id = require_subject_scope(request, payload.subject_type, payload.subject_id)
    if payload.kind == Review.APPROVAL:
        if payload.subject_type == Review.MESSAGE:
            review = approval_service.submit_message(
                request.user, payload.subject_id, note=payload.note
            )
        elif payload.subject_type == Review.RECIPIENT:
            review = approval_service.submit_letter(
                request.user, payload.subject_id, cover=None, note=payload.note
            )
        else:
            raise HttpError(422, "A message or a letter is submitted for approval.")
        return 201, review_out(review, request.user)
    assigned = None
    if payload.assigned_to_user_id is not None:
        assigned = User.objects.filter(pk=payload.assigned_to_user_id, is_active=True).first()
        if assigned is None:
            raise NotFound(f"No active person {payload.assigned_to_user_id}.")
        # P27d: asking is not sharing. The teammate must be on the team and already able to open
        # what the question is about (the rule assigning keeps), or they would be told of
        # something they cannot open.
        person = access.person(assigned.pk)
        if person is None or person.role not in roles.TEAM:
            raise HttpError(422, f"{assigned.name} is not on the team, so cannot be asked.")
        if payload.subject_type == Review.SOURCE:
            access.require_assignee_can_view_source(payload.subject_id, assigned.pk)
        elif payload.subject_type == Review.DRAFT:
            draft = LetterDraft.objects.filter(pk=payload.subject_id).first()
            if draft is not None and not draft_visible_to(
                Viewer(user_id=assigned.pk, role=person.role), draft
            ):
                raise HttpError(
                    422, f"{assigned.name} cannot open this letter, so cannot be asked."
                )
        elif request_id is not None:
            about = Request.objects.filter(pk=request_id).first()
            if about is not None:
                access.require_assignee_can_view(about, assigned.pk)
    review = review_service.submit(
        request.user,
        payload.subject_type,
        payload.subject_id,
        request_id=request_id,
        note=payload.note,
        kind=payload.kind,
        assigned_to=assigned,
    )
    return 201, review_out(review, request.user)


def _review_in_scope(request: HttpRequest, review_id: UUID) -> Review:
    review = Review.objects.filter(pk=review_id).first()
    if review is None:
        raise NotFound(f"No review {review_id}.")
    require_subject_scope(request, review.subject_type, review.subject_id)
    return review


@router.get("/{review_id}", response=ApprovalOut)
def get_one(request: HttpRequest, review_id: UUID) -> ApprovalOut:
    return review_out(_review_in_scope(request, review_id), request.user)


@router.post("/{review_id}/decide", response=ApprovalOut)
def decide(request: HttpRequest, review_id: UUID, payload: ApprovalDecisionIn) -> ApprovalOut:
    """Approve (and send: an intern's message is queued exactly as Send queues it), send back with
    a note, or answer a question. Who may decide is the service's (`review.may_approve`)."""
    _review_in_scope(request, review_id)
    decided = approval_service.decide(request.user, review_id, payload.decision, payload.comment)
    return review_out(decided, request.user)


@router.post("/{review_id}/withdraw", response=ApprovalOut)
def withdraw(request: HttpRequest, review_id: UUID) -> ApprovalOut:
    """The intern who submitted it takes it back while it waits (233)."""
    _review_in_scope(request, review_id)
    return review_out(approval_service.withdraw(request.user, review_id), request.user)
