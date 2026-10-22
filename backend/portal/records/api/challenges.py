"""P14: appeals and search challenges. The letter is a mail message the person writes; this
router starts it (the mail compose service is the records API's to call; the records services
are not) and records what became of the challenge."""

from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from portal.records.api.permissions import (
    require_human_actor,
    require_may_release,
    require_request_scope,
    require_team,
    require_viewable_request,
)
from portal.records.api.schemas import (
    ChallengeFollowUpIn,
    ChallengeIn,
    ChallengeOut,
    ChallengeOutcomeIn,
    ChallengeSentIn,
)
from portal.records.models import Challenge, ChallengeKind, Request
from portal.records.services import challenges as challenges_service
from portal.records.services.common import NotFound

router = Router(tags=["challenges"])


def challenge_out(row: Challenge) -> ChallengeOut:
    return ChallengeOut(
        id=row.pk,
        request_id=row.request_id,
        kind=row.kind,
        request_item_id=row.request_item_id,
        review_item_id=row.review_item_id,
        basis=row.basis,
        follow_up_by=row.follow_up_by,
        message_id=row.message_id,
        created_by_user_id=row.created_by_id,
        created_at=row.created_at,
        sent_at=row.sent_at,
        event_id=row.event_id,
        followup_deadline_id=row.followup_deadline_id,
        outcome=row.outcome,
        outcome_note=row.outcome_note,
        resolved_at=row.resolved_at,
        resolved_by_user_id=row.resolved_by_id,
    )


def _require_challenge_scope(request: HttpRequest, challenge_id: UUID) -> Challenge:
    require_team(request)
    row = Challenge.objects.filter(pk=challenge_id).first()
    if row is None:
        raise NotFound(f"No challenge {challenge_id}.")
    require_viewable_request(request, row.request_id)
    return row


@router.get("/requests/{request_id}/challenges", response=list[ChallengeOut])
def list_challenges(request: HttpRequest, request_id: UUID) -> list[ChallengeOut]:
    require_team(request)
    require_viewable_request(request, request_id)
    return [challenge_out(r) for r in challenges_service.challenges_of(request_id)]


@router.post("/requests/{request_id}/challenges", response={201: ChallengeOut})
def open_challenge(request: HttpRequest, request_id: UUID, payload: ChallengeIn):
    req = require_request_scope(request, request_id)
    actor = require_human_actor(request)
    row = challenges_service.open_challenge(
        actor,
        request_id,
        kind=payload.kind,
        basis=payload.basis,
        request_item_id=payload.request_item_id,
        review_item_id=payload.review_item_id,
        follow_up_by=payload.follow_up_by,
    )
    if payload.with_letter:
        from portal.mail.services import compose

        label = "Appeal" if row.kind == ChallengeKind.APPEAL else "Challenge to the search"
        message = compose.create_draft(
            actor,
            request_id,
            subject=payload.subject or f"{label}: {req.subject} ({req.tracking_number})",
            to=[req.agency.default_request_email] if req.agency.default_request_email else [],
            cc=[a.address for a in compose.agency_copy_addresses(req)],
        )
        row = challenges_service.attach_message(actor, row.pk, message.pk)
    return 201, challenge_out(row)


@router.post("/challenges/{challenge_id}/sent", response=ChallengeOut)
def mark_sent(request: HttpRequest, challenge_id: UUID, payload: ChallengeSentIn) -> ChallengeOut:
    """The letter went out some way other than the portal's mail. Not an intern's to say: their
    letters leave only through approval (P20h)."""
    _require_challenge_scope(request, challenge_id)
    require_may_release(request)
    actor = require_human_actor(request)
    return challenge_out(
        challenges_service.mark_sent(
            actor, challenge_id, sent_at=payload.sent_at, channel=payload.channel, note=payload.note
        )
    )


@router.post("/challenges/{challenge_id}/follow-up", response=ChallengeOut)
def set_follow_up(
    request: HttpRequest, challenge_id: UUID, payload: ChallengeFollowUpIn
) -> ChallengeOut:
    _require_challenge_scope(request, challenge_id)
    actor = require_human_actor(request)
    return challenge_out(
        challenges_service.set_follow_up(actor, challenge_id, payload.follow_up_by)
    )


@router.post("/challenges/{challenge_id}/outcome", response=ChallengeOut)
def record_outcome(
    request: HttpRequest, challenge_id: UUID, payload: ChallengeOutcomeIn
) -> ChallengeOut:
    _require_challenge_scope(request, challenge_id)
    actor = require_human_actor(request)
    return challenge_out(
        challenges_service.record_outcome(actor, challenge_id, payload.outcome, payload.note)
    )


__all__ = ["router", "challenge_out", "Request"]
