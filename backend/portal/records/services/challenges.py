"""Appeals and search challenges (BUILD_PLAN P14).

A challenge is one of two things: an appeal of a withholding (scoped to a request item, or to
the production-review row that recorded the withholding), or a challenge to a "no records"
search. It carries a letter, which is a mail message written by the person (nothing here writes
a word that goes to an agency), and a date to follow up by, which becomes a deadline of kind
`challenge_response` with basis `internal`.

Three moments: `open_challenge` records the ask; the letter goes out (through mail, which calls
`on_message_sent`, or by some other channel, through `mark_sent`), which records the `appeal`
or `challenge` event, moves an appealed request to `appealed` by that event, and opens the
follow-up deadline; `record_outcome` says what the agency did. The outcome moves no status by
itself: what follows a reversal is a production, recorded as productions are.

`records` knows nothing of mail: `message_id` is a plain uuid, and mail reports a sent letter
back by calling into here.
"""

from datetime import date, datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.core.services import activity as core_activity
from portal.records.actors import Actor
from portal.records.models import (
    Challenge,
    ChallengeKind,
    ChallengeOutcome,
    DeadlineBasis,
    ProductionReviewItem,
    RequestItem,
    writable,
)
from portal.records.services import deadlines as deadlines_service
from portal.records.services import events as events_service
from portal.records.services import status as status_service
from portal.records.services.common import (
    InvalidTransition,
    NotFound,
    ServiceError,
    lock_request,
    require_human,
    require_of_request,
)

FOLLOWUP_KIND = "challenge_response"
EVENT_TYPE = {ChallengeKind.APPEAL: "appeal", ChallengeKind.SEARCH: "challenge"}
APPEALED = "appealed"


def _get(challenge_id: UUID) -> Challenge:
    row = Challenge.objects.filter(pk=challenge_id).first()
    if row is None:
        raise NotFound(f"No challenge {challenge_id}.")
    return row


@transaction.atomic
def open_challenge(
    actor: Actor,
    request_id: UUID,
    *,
    kind: str,
    basis: str,
    request_item_id: UUID | None = None,
    review_item_id: UUID | None = None,
    follow_up_by: date | None = None,
) -> Challenge:
    user_id = require_human(actor, "Opening a challenge")
    if kind not in ChallengeKind.values:
        raise ServiceError(
            f"Unknown challenge kind {kind!r}; expected one of {ChallengeKind.values}."
        )
    if not basis or not basis.strip():
        raise ServiceError("Say why: the basis is what the letter argues and what a reader sees.")
    lock_request(request_id)
    require_of_request(RequestItem, request_item_id, request_id, "item")
    if review_item_id is not None:
        row = (
            ProductionReviewItem.objects.select_related("review").filter(pk=review_item_id).first()
        )
        if row is None or row.review.request_id != request_id:
            raise ServiceError("That review row is not this request's.")
        if row.disposition not in ("withheld", "partly"):
            raise InvalidTransition("Only a withheld or partly produced item can be appealed.")
        request_item_id = request_item_id or row.request_item_id
    with writable():
        return Challenge.objects.create(
            request_id=request_id,
            kind=kind,
            request_item_id=request_item_id,
            review_item_id=review_item_id,
            basis=basis.strip(),
            follow_up_by=follow_up_by,
            created_by_id=user_id,
        )


@transaction.atomic
def attach_message(actor: Actor, challenge_id: UUID, message_id: UUID) -> Challenge:
    """The letter for this challenge is this mail message. Set once, before it is sent."""
    require_human(actor, "Attaching a letter")
    row = _get(challenge_id)
    lock_request(row.request_id)
    row = Challenge.objects.get(pk=challenge_id)
    if row.sent_at is not None:
        raise InvalidTransition("This challenge's letter has already gone out.")
    Challenge.objects.filter(pk=challenge_id).update(message_id=message_id)
    return Challenge.objects.get(pk=challenge_id)


def _sent(
    actor: Actor,
    row: Challenge,
    *,
    sent_at: datetime,
    channel: str,
    source_id: UUID | None,
    display_body: str,
) -> Challenge:
    request = lock_request(row.request_id)
    row = Challenge.objects.get(pk=row.pk)
    if row.sent_at is not None:
        raise InvalidTransition("This challenge's letter has already gone out.")
    follow_up_by = row.follow_up_by
    event = events_service.record_event(
        actor,
        row.request_id,
        event_type=EVENT_TYPE[row.kind],
        channel=channel,
        direction="outbound",
        occurred_at=sent_at,
        display_body=display_body,
        source_id=source_id,
        actor_user_id=actor.user_id,
    )
    if row.kind == ChallengeKind.APPEAL and request.current_status_id != APPEALED:
        status_service._append_status_change(
            request, APPEALED, reason="event", actor=actor, triggering_event=event
        )
    deadline_id = None
    if follow_up_by is not None:
        deadline = deadlines_service.open_deadline(
            actor,
            row.request_id,
            deadline_type=FOLLOWUP_KIND,
            due_at=follow_up_by,
            basis=DeadlineBasis.INTERNAL,
            request_item_id=row.request_item_id,
            source_event_id=event.pk,
            note="Follow up on the challenge if there is no answer by then.",
        )
        deadline_id = deadline.pk
    Challenge.objects.filter(pk=row.pk).update(
        sent_at=sent_at, event_id=event.pk, followup_deadline_id=deadline_id
    )
    core_activity.record(
        actor,
        "challenge_sent",
        f"{ChallengeKind(row.kind).label} sent on {request.tracking_number}",
        subject_type="challenge",
        subject_id=row.pk,
        request_id=row.request_id,
    )
    return Challenge.objects.get(pk=row.pk)


@transaction.atomic
def mark_sent(
    actor: Actor,
    challenge_id: UUID,
    *,
    sent_at: datetime | None = None,
    channel: str = "mail",
    note: str = "",
) -> Challenge:
    """The letter went out some way other than through the portal's mail."""
    require_human(actor, "Marking a challenge sent")
    row = _get(challenge_id)
    return _sent(
        actor,
        row,
        sent_at=sent_at or timezone.now(),
        channel=channel,
        source_id=None,
        display_body=note.strip() or f"{ChallengeKind(row.kind).label}: {row.basis}",
    )


@transaction.atomic
def on_message_sent(
    actor: Actor,
    message_id: UUID,
    *,
    source_id: UUID,
    sent_at: datetime,
    body: str,
) -> Challenge | None:
    """Called by mail when a message it sent is a challenge's letter. None when it is not."""
    row = Challenge.objects.filter(message_id=message_id, sent_at__isnull=True).first()
    if row is None:
        return None
    return _sent(
        actor, row, sent_at=sent_at, channel="email", source_id=source_id, display_body=body
    )


@transaction.atomic
def set_follow_up(actor: Actor, challenge_id: UUID, follow_up_by: date | None) -> Challenge:
    """The date to follow up by, chosen before the letter goes; it becomes the deadline when the
    letter is sent. Afterwards the deadline itself is what to change."""
    require_human(actor, "Setting a follow-up date")
    row = _get(challenge_id)
    lock_request(row.request_id)
    row = Challenge.objects.get(pk=challenge_id)
    if row.sent_at is not None:
        raise InvalidTransition("The letter has gone; change its follow-up deadline instead.")
    Challenge.objects.filter(pk=challenge_id).update(follow_up_by=follow_up_by)
    return Challenge.objects.get(pk=challenge_id)


@transaction.atomic
def record_outcome(actor: Actor, challenge_id: UUID, outcome: str, note: str = "") -> Challenge:
    user_id = require_human(actor, "Recording a challenge's outcome")
    if outcome not in ChallengeOutcome.values:
        raise ServiceError(
            f"Unknown outcome {outcome!r}; expected one of {ChallengeOutcome.values}."
        )
    row = _get(challenge_id)
    lock_request(row.request_id)
    row = Challenge.objects.get(pk=challenge_id)
    if row.resolved_at is not None:
        raise InvalidTransition("This challenge already has an outcome.")
    if row.sent_at is None and outcome != ChallengeOutcome.WITHDRAWN:
        raise InvalidTransition(
            "The letter has not gone out; only 'withdrawn' fits before it does."
        )
    Challenge.objects.filter(pk=challenge_id).update(
        outcome=outcome,
        outcome_note=note.strip() or None,
        resolved_at=timezone.now(),
        resolved_by_id=user_id,
    )
    return Challenge.objects.get(pk=challenge_id)


def challenges_of(request_id: UUID) -> list[Challenge]:
    return list(Challenge.objects.filter(request_id=request_id).order_by("created_at"))
