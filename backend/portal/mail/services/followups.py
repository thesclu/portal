"""Automatic follow-ups that send (P11, P18a). The records sweep opens the task and hands the
draft here; this sends it only when the setting `records.followups_send_enabled` is on
(Settings → Filing & deadlines), to the agency's filing address with its copy-on-every-request
contacts in Cc, from the request's alias, with no undo window (there is nobody at the keyboard to
undo it). Off by default: the text that goes out is the body template a person edited, and
switching this on is a decision about sending that text in the organisation's name."""

import logging
from dataclasses import dataclass
from uuid import UUID

from portal.core.services.settings import get_setting
from portal.mail.models import MessageKind
from portal.mail.services import compose, send
from portal.records.actors import Actor
from portal.records.models import Deadline, DeadlineDefinition, Request
from portal.records.services.sweeps import followup_placeholders, render_followup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FollowupDraft:
    to: list[str]
    cc: list[str]
    subject: str
    body: str


def filing_address(request: Request) -> str:
    """Where a follow-up goes: the agency's filing address, as the automatic one uses."""
    return (request.agency.default_request_email or "").strip()


def followup_subject(request: Request, kind: DeadlineDefinition | None) -> str:
    return (
        render_followup(
            str(get_setting("records.followup_subject") or ""),
            followup_placeholders(request, kind),
        )
        or f"Follow-up on our records request {request.tracking_number}"
    )


def followup_kind(request: Request) -> DeadlineDefinition | None:
    """What a follow-up written by a person is about: the kind of the request's most recently
    missed deadline, else of its next active one, else nothing in particular."""
    deadline = (
        Deadline.objects.filter(request_id=request.pk, status="missed")
        .order_by("-due_at", "-created_at")
        .first()
    ) or (
        Deadline.objects.filter(request_id=request.pk, status="active")
        .order_by("due_at", "created_at")
        .first()
    )
    if deadline is None:
        return None
    return DeadlineDefinition.objects.filter(code=deadline.deadline_type).first()


def draft_for(request_id: UUID) -> FollowupDraft:
    """The follow-up a person is about to send, filled the way the automatic one is: the subject
    and body templates from settings with the ledger's placeholders, To the agency's filing
    address, Cc its copy-on-every-request contacts. Read only: nothing is written. The signature
    is not in the body because `send` adds the organisation's signature to every message as it
    goes out (no person has a signature setting of their own)."""
    request = Request.objects.select_related("agency").get(pk=request_id)
    kind = followup_kind(request)
    fields = followup_placeholders(request, kind)
    address = filing_address(request)
    return FollowupDraft(
        to=[address] if address else [],
        cc=[a.address for a in compose.agency_copy_addresses(request)],
        subject=followup_subject(request, kind),
        body=render_followup(str(get_setting("records.followup_body") or ""), fields),
    )


def send_followup(actor: Actor, request: Request, kind: DeadlineDefinition, draft: str) -> None:
    if not bool(get_setting("records.followups_send_enabled")):
        return
    address = filing_address(request)
    if not address or not draft.strip():
        logger.info("follow-up for %s not sent: no agency address", request.tracking_number)
        return
    message = compose.create_draft(
        actor,
        request.pk,
        kind=MessageKind.FOLLOWUP,
        subject=followup_subject(request, kind),
        body_text=draft,
        to=[address],
        cc=[a.address for a in compose.agency_copy_addresses(request)],
    )
    send.queue(actor, message.pk, delay_seconds=0)
