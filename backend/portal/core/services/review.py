"""Intern approval (P20h; DECISIONS 42, 43), and "Ask a teammate" (P13), in one table.

One table, `core.reviews`, append-only. The newest row for a subject is what stands.

AN APPROVAL is how an intern's letter or message leaves the portal: nothing an intern writes goes
out without an approver's yes (42b). Only an intern submits one; nobody else needs to. Its
subject is one of:
  `message`    a `mail.Message` the intern wrote: the cover message of a stage-4 email filing
               (kind `filing`), a reply, a follow-up, a forward, a challenge's letter. "Approve and
               send" queues it, through `mail.send.queue`, the call Send makes.
  `recipient`  a letter to one agency that is filed by hand (web form or post). Approving
               releases it: the intern then files it exactly as stage 4 does.
  `draft`      P13's director review of a letter; kept so its rows still read. Nothing submits
               one now and no door waits on one.
The work around a decision -- the request's status, the send -- is `mail/services/approval.py`'s;
this module keeps the rows, who may decide them, and clearance.

WHO APPROVES (42a; `may_approve`): an administrator; a staff attorney who may see the request; a
limited-scope attorney who holds a grant on it. Not staff. Never the submitter.

CLEARANCE is checked at the doors that file or send (`drafting.filing.file_recipient`,
`mail.send.queue`): for an intern, the subject's newest approval must be `approved` and newer than
the subject's last change; for every other role it is not required (the role decides filing:
`drafting.review_required` and the "files without review" mark are retired, 42d).

A QUESTION ("Ask a teammate" on a message in the Inbox, or on a request from its page, 069) is
answered by the person it was put to, who must be able to open what it is about.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.core.models import Review, User
from portal.core.services import activity, notifications
from portal.records import roles
from portal.records.actors import Actor
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError

SUBJECTS = (Review.DRAFT, Review.MESSAGE, Review.SOURCE, Review.RECIPIENT, Review.REQUEST)
# What an intern submits for approval now. `draft` stays readable in SUBJECTS for P13's rows.
APPROVAL_SUBJECTS = (Review.MESSAGE, Review.RECIPIENT)
KINDS = (Review.APPROVAL, Review.QUESTION)
_DECISIONS = {
    Review.APPROVAL: (Review.APPROVED, Review.SENT_BACK),
    Review.QUESTION: (Review.ANSWERED,),
}


class NeedsReview(NotAllowed):
    """Filing or sending refused: this is an intern's, and an approver has not approved it. A 403:
    the person may not do this themselves, whatever they send (P20h)."""


@dataclass(frozen=True)
class Clearance:
    required: bool
    cleared: bool
    why: str
    review: Review | None = None


def latest(subject_type: str, subject_id: UUID, *, kind: str = Review.APPROVAL) -> Review | None:
    return (
        Review.objects.filter(subject_type=subject_type, subject_id=subject_id, kind=kind)
        .order_by("-submitted_at")
        .first()
    )


def open_approval(subject_type: str, subject_id: UUID) -> Review | None:
    """The approval waiting on this subject, if one is."""
    newest = latest(subject_type, subject_id)
    return newest if newest is not None and newest.decision is None else None


def clearance(
    user: User | None, subject_type: str, subject_id: UUID, changed_at: datetime | None
) -> Clearance:
    """Whether this person may file or send this subject as it stands now."""
    if user is None or not roles.needs_approval(user.pk):
        return Clearance(False, True, "Your role files and sends without approval.")
    newest = latest(subject_type, subject_id)
    if newest is None:
        return Clearance(True, False, "An attorney has to approve this before it goes out.")
    if newest.decision is None:
        return Clearance(True, False, "Waiting for approval.", newest)
    if newest.decision == Review.SENT_BACK:
        return Clearance(True, False, "It was sent back; edit it and submit it again.", newest)
    if newest.decision == Review.WITHDRAWN:
        return Clearance(True, False, "You withdrew it; submit it again when it is ready.", newest)
    if newest.decided_at is not None and changed_at is not None and newest.decided_at < changed_at:
        return Clearance(True, False, "It changed after it was approved; submit it again.", newest)
    return Clearance(True, True, "Approved.", newest)


def require_cleared(
    user_id: UUID | None, subject_type: str, subject_id: UUID, changed_at: datetime | None
) -> None:
    user = User.objects.filter(pk=user_id).first() if user_id else None
    found = clearance(user, subject_type, subject_id, changed_at)
    if not found.cleared:
        raise NeedsReview(found.why)


# ---- who may approve ----------------------------------------------------------------------------


def may_approve(user: User, review: Review) -> bool:
    """42a: an administrator; a staff attorney who may see the request; a limited-scope attorney
    holding a grant on it. Never the person who submitted it, and only an approval."""
    from portal.records.models import Request
    from portal.records.services import access

    if review.kind != Review.APPROVAL or review.submitted_by_id == user.pk or not user.is_active:
        return False
    role = roles.role_of(user.pk)
    if role == roles.ADMIN:
        return True
    if review.request_id is None:
        return False
    if role == roles.LIMITED_ATTORNEY:
        return access.holds_grant(user.pk, review.request_id)
    if role in roles.APPROVERS:
        request = Request.objects.filter(pk=review.request_id).first()
        return request is not None and access.can_view(
            access.Viewer(user_id=user.pk, role=role), request
        )
    return False


def approvers_for(request_id: UUID | None) -> list[UUID]:
    """Everyone who may approve a submission on this request: whom to tell. Read with the system's
    eyes (the submitter cannot see who else may see the request), decided by `may_approve`'s
    rule. A request nobody may approve still has every administrator."""
    from portal.records.models import RecordsUser, Request, RequestAccess
    from portal.records.services import access

    people = User.objects.filter(is_active=True)
    out = set(people.filter(is_staff=True).values_list("pk", flat=True))
    if request_id is None:
        return sorted(out)
    request = Request.objects.filter(pk=request_id).first()
    attorneys = RecordsUser.objects.filter(role=roles.STAFF_ATTORNEY)
    for person in attorneys:
        if request is not None and access.can_view(
            access.Viewer(user_id=person.pk, role=person.role), request
        ):
            out.add(person.pk)
    granted = RequestAccess.objects.filter(
        request_id=request_id, revoked_at__isnull=True, user__role=roles.LIMITED_ATTORNEY
    ).values_list("user_id", flat=True)
    out |= set(granted)
    active = set(people.filter(pk__in=out).values_list("pk", flat=True))
    return sorted(active)


# ---- submitting, withdrawing, deciding ----------------------------------------------------------


@transaction.atomic
def submit(
    user: User,
    subject_type: str,
    subject_id: UUID,
    *,
    request_id: UUID | None = None,
    note: str = "",
    kind: str = Review.APPROVAL,
    assigned_to: User | None = None,
    title: str = "",
) -> Review:
    """Write the row and tell whoever decides it. An approval's request status, when it is a
    filing, is `mail/services/approval.py`'s to move, in the same transaction."""
    if subject_type not in SUBJECTS:
        raise ServiceError(f"Unknown review subject {subject_type!r}; expected one of {SUBJECTS}.")
    if kind not in KINDS:
        raise ServiceError(f"Unknown review kind {kind!r}; expected one of {KINDS}.")
    if kind == Review.QUESTION:
        if assigned_to is None:
            raise ServiceError("A question needs the teammate it is put to.")
        if not note.strip():
            raise ServiceError("Say what you are asking.")
        if assigned_to.pk == user.pk:
            raise ServiceError("Ask someone else.")
    else:
        if subject_type not in APPROVAL_SUBJECTS:
            raise ServiceError(
                f"A {subject_type} is not submitted for approval; a message or a letter is."
            )
        if not roles.needs_approval(user.pk):
            raise NotAllowed(
                "Your role sends and files without approval; there is nothing to submit."
            )
        if request_id is None:
            raise ServiceError("An approval is about a request; this one names none.")
        if open_approval(subject_type, subject_id) is not None:
            raise InvalidTransition("This is already waiting for approval.")
    row = Review.objects.create(
        subject_type=subject_type,
        subject_id=subject_id,
        request_id=request_id,
        kind=kind,
        submitted_by=user,
        note=note.strip(),
        assigned_to=assigned_to,
    )
    if kind == Review.QUESTION and assigned_to is not None:
        notifications.notify(
            assigned_to.pk,
            "review",
            f"{user.name} asked you a question",
            link=f"/approvals/{row.pk}",
            subject_type="review",
            subject_id=row.pk,
            request_id=request_id,
            actor_user_id=user.pk,
        )
    else:
        notifications.notify_many(
            approvers_for(request_id),
            "review",
            f"{user.name} submitted {title or _subject_label(subject_type)} for approval",
            link=f"/approvals/{row.pk}",
            subject_type="review",
            subject_id=row.pk,
            request_id=request_id,
            actor_user_id=user.pk,
        )
    return row


def lock(review_id: UUID) -> Review:
    review = Review.objects.select_for_update().filter(pk=review_id).first()
    if review is None:
        raise NotFound(f"No review {review_id}.")
    return review


@transaction.atomic
def withdraw(user: User, review_id: UUID) -> Review:
    """The intern takes back what they submitted, while it waits (233). Only the submitter; only
    an approval nobody has decided."""
    review = lock(review_id)
    if review.kind != Review.APPROVAL:
        raise InvalidTransition("Only something waiting for approval can be withdrawn.")
    if review.submitted_by_id != user.pk:
        raise NotAllowed("Only the person who submitted it can withdraw it.")
    if review.decision is not None:
        raise InvalidTransition("This has already been decided.")
    Review.objects.filter(pk=review_id).update(
        decision=Review.WITHDRAWN, decided_by=user, decided_at=timezone.now()
    )
    activity.record(
        Actor.human(user),
        "review_withdrawn",
        f"Withdrew {_subject_label(review.subject_type)} from approval",
        subject_type="review",
        subject_id=review_id,
        request_id=review.request_id,
    )
    return Review.objects.get(pk=review_id)


@transaction.atomic
def decide(user: User, review_id: UUID, decision: str, comment: str = "") -> Review:
    """Write a decision. For an approval this is only the row: what approving releases and what
    sending back moves is `mail/services/approval.py`'s, which calls this inside its own
    transaction after taking the request's lock."""
    review = lock(review_id)
    if review.decision is not None:
        raise InvalidTransition("This has already been decided.")
    if decision not in _DECISIONS[review.kind]:
        raise ServiceError(
            f"A {review.kind} is decided as one of {_DECISIONS[review.kind]}, not {decision!r}."
        )
    if review.submitted_by_id == user.pk:
        raise NotAllowed("You cannot decide your own submission.")
    if review.kind == Review.APPROVAL and not may_approve(user, review):
        raise NotAllowed(
            "Only an administrator, a staff attorney, or an attorney given this request approves "
            "or sends back."
        )
    if review.kind == Review.QUESTION and review.assigned_to_id != user.pk:
        raise NotAllowed("Only the person asked can answer.")
    if decision == Review.SENT_BACK and not comment.strip():
        raise ServiceError("Write a note saying what should change; the intern will read it.")
    if decision == Review.ANSWERED and not comment.strip():
        raise ServiceError("Write the answer.")
    Review.objects.filter(pk=review_id).update(
        decision=decision, decided_by=user, decided_at=timezone.now(), comment=comment.strip()
    )
    activity.record(
        Actor.human(user),
        "review_decided",
        f"{decision.replace('_', ' ').capitalize()}: {_subject_label(review.subject_type)}",
        subject_type="review",
        subject_id=review_id,
        request_id=review.request_id,
        data={"decision": decision, "submitted_by": str(review.submitted_by_id)},
    )
    notifications.notify(
        review.submitted_by_id,
        "review",
        f"{user.name} {_decided_words(decision)} what you submitted",
        link=f"/approvals/{review_id}"
        if review.request_id is None
        else f"/requests/{review.request_id}",
        subject_type="review_decision",
        subject_id=review_id,
        request_id=review.request_id,
        actor_user_id=user.pk,
    )
    return Review.objects.get(pk=review_id)


def pending_for(user: User) -> list[Review]:
    """What this person is asked to decide: the open approvals they may approve (an intern's
    message or letter), and the open questions put to them. Visibility of the subjects is the
    API's to filter as well."""
    rows = Review.objects.filter(decision__isnull=True)
    approvals = rows.filter(kind=Review.APPROVAL, subject_type__in=APPROVAL_SUBJECTS).exclude(
        submitted_by=user
    )
    mine = [r for r in approvals.order_by("submitted_at") if may_approve(user, r)]
    asked = list(rows.filter(kind=Review.QUESTION, assigned_to=user).order_by("submitted_at"))
    return sorted(mine + asked, key=lambda r: r.submitted_at)


def history(subject_type: str, subject_id: UUID) -> list[Review]:
    return list(
        Review.objects.filter(subject_type=subject_type, subject_id=subject_id).order_by(
            "-submitted_at"
        )
    )


def _decided_words(decision: str) -> str:
    return {
        Review.APPROVED: "approved",
        Review.SENT_BACK: "sent back",
        Review.ANSWERED: "answered",
    }.get(decision, decision.replace("_", " "))


def _subject_label(subject_type: str) -> str:
    return {
        Review.DRAFT: "a letter",
        Review.RECIPIENT: "a letter",
        Review.MESSAGE: "a message",
        Review.SOURCE: "a message",
        Review.REQUEST: "a request",
    }.get(subject_type, "something")
