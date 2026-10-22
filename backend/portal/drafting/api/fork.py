"""The drafting tool's routes, under /api/drafting (P21c; DECISIONS 49).

Every one but the webhook takes a signed-in team member, and every one that names a draft or a
request passes through `drafting/api/permissions.py` or `records/api/permissions.py` before
anything else is looked at (the census holds them to it). While the drafting tool is not in use
the routes that would call it answer 409 and nothing else changes; the webhook is the fork's own
call, authenticated by its signature alone.
"""

from typing import Literal
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from ninja import Router
from ninja.errors import HttpError

from portal.drafting.api.library import _template_out
from portal.drafting.api.permissions import require_draft_scope
from portal.drafting.api.schemas import (
    ForkLetterOut,
    ForkLettersOut,
    ForkMergeFailureOut,
    ForkMergeOut,
    ForkStatusOut,
    ForkTemplateIn,
    ForkValuesOut,
    TemplateOut,
    WebhookAckOut,
)
from portal.drafting.models import LetterRecipient
from portal.drafting.services import fork as fork_service
from portal.drafting.services import letter as letter_service
from portal.drafting.services.common import get_or_404
from portal.records.api.permissions import (
    require_human_actor,
    require_request_scope,
    require_staff,
    require_team,
)
from portal.records.models import Agency, Request
from portal.records.services.common import InvalidTransition
from portal.records.storage import safe_filename

router = Router(tags=["drafting"])

_OFF = "The drafting tool is switched off in Settings → System."


@router.get("/fork", response=ForkStatusOut)
def fork_status(request: HttpRequest) -> ForkStatusOut:
    """Whether letters are written in the drafting tool now, and where its pages are."""
    require_team(request)
    cfg = fork_service.config()
    if not cfg.in_use:
        return ForkStatusOut(in_use=False)
    return ForkStatusOut(in_use=True, web_url=cfg.web_url, web_origin=cfg.web_origin)


@router.post("/fork/webhook", auth=None, response={200: WebhookAckOut})
def fork_webhook(request: HttpRequest):
    """The drafting tool's webhooks (SCLU.md 9.3): verified over the exact bytes received (HMAC
    v1, constant time, five minutes either way), recorded once by event id, then applied. A
    delivery seen before is acknowledged and changes nothing (the fork retries with the same id).
    """
    try:
        result = fork_service.receive_webhook(
            request.body,
            event_header=request.headers.get("X-SCLU-Event"),
            delivery_header=request.headers.get("X-SCLU-Delivery"),
            timestamp=request.headers.get("X-SCLU-Timestamp"),
            signature=request.headers.get("X-SCLU-Signature"),
        )
    except fork_service.WebhookRefused as refused:
        raise HttpError(refused.status, refused.detail) from refused
    return 200, WebhookAckOut(status=result["status"])


@router.post("/fork/templates", response=TemplateOut)
def mirror_template(request: HttpRequest, payload: ForkTemplateIn) -> TemplateOut:
    """What the Templates frame said about a template it created or changed (`sclu-templates/1`
    "template"): the portal's row for it, so stage 1 offers it. Staff, as the library is."""
    require_staff(request)
    row = fork_service.mirror_template(
        require_human_actor(request),
        template_id=payload.template_id,
        name=payload.name,
        archived=payload.archived,
    )
    return _template_out(row)


@router.post("/drafts/{draft_id}/fork/merge", response=ForkMergeOut)
def make_letters(request: HttpRequest, draft_id: UUID) -> ForkMergeOut:
    """Make the agency letters the drafting tool has not made yet (a reserve whose merge failed):
    every reserved recipient of this draft still to go out and without one."""
    draft = require_draft_scope(request, draft_id)
    actor = require_human_actor(request)
    if not fork_service.is_fork_draft(draft):
        raise InvalidTransition("This letter is written in the portal, not the drafting tool.")
    fork_service.require_in_use_for(draft)
    outcome = fork_service.make_letters(actor, draft, fork_service.letters_to_make(draft))
    return ForkMergeOut(
        made=outcome.made,
        failed=[ForkMergeFailureOut(recipient_id=k, reason=v) for k, v in outcome.failed.items()],
    )


@router.get("/drafts/{draft_id}/fork/values", response=ForkValuesOut)
def merge_values(request: HttpRequest, draft_id: UUID, agency_id: UUID) -> ForkValuesOut:
    """One agency's merge values, for stage 2's "Preview as" before its letter exists: its
    recipient's when it is one (with its contact and overrides), else the agency's own."""
    draft = require_draft_scope(request, draft_id)
    if not fork_service.config().in_use:
        raise InvalidTransition(_OFF)
    agency = get_or_404(Agency, agency_id, "agency")
    recipient = LetterRecipient.objects.filter(draft_id=draft.pk, agency_id=agency_id).first()
    if recipient is None:
        recipient = LetterRecipient(
            draft_id=draft.pk, agency=agency, channel=agency.filing_method or ""
        )
    request_row = (
        Request.objects.filter(pk=recipient.request_id).first() if recipient.request_id else None
    )
    return ForkValuesOut(
        agency_id=agency.pk,
        label=agency.name,
        values=fork_service.merge_values(recipient, request_row),
    )


@router.get("/drafts/{draft_id}/fork/file")
def letter_file(
    request: HttpRequest,
    draft_id: UUID,
    format: Literal["pdf", "docx"] = "pdf",
    recipient_id: UUID | None = None,
):
    """The letter from the drafting tool as the person asking: an agency's (`recipient_id`), else
    the master. The same bytes for a version every time (SCLU.md 9.2)."""
    draft = require_draft_scope(request, draft_id)
    if not fork_service.is_fork_draft(draft):
        raise InvalidTransition("This letter is written in the portal, not the drafting tool.")
    document_id = draft.fork_master_document_id
    name = safe_filename(draft.subject or "letter")
    if recipient_id is not None:
        recipient = LetterRecipient.objects.filter(pk=recipient_id, draft_id=draft.pk).first()
        if recipient is None:
            raise HttpError(404, "That recipient is not on this draft.")
        if recipient.fork_document_id is None:
            raise InvalidTransition("The drafting tool has not made this agency's letter yet.")
        document_id = recipient.fork_document_id
        name = letter_service.letter_filename(recipient).removesuffix(".pdf")
    assert document_id is not None
    answer = fork_service.fetch_file(request.user, document_id, format)
    kind = (
        "application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    response = HttpResponse(answer.body, content_type=kind)
    response["Content-Disposition"] = f'inline; filename="{name}.{format}"'
    response["Cache-Control"] = "private, no-store"
    # Shown in the portal's own frames (stage 4's letter), like the portal's PDF.
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


@router.get("/requests/{request_id}/fork/letter", response=ForkLettersOut)
def request_letters(request: HttpRequest, request_id: UUID) -> ForkLettersOut:
    """The request's letter in the drafting tool, for its Files tab: which version, when it last
    changed, its open review comments and its latest citation check, from the webhooks received.
    Empty for a request not written there. The team's (a requester cannot use the drafting
    tool, QUESTIONS 33)."""
    require_team(request)
    require_request_scope(request, request_id)
    rows = list(
        LetterRecipient.objects.filter(
            request_id=request_id, fork_document_id__isnull=False
        ).order_by("-created_at")
    )
    states = fork_service.document_states(r.fork_document_id for r in rows)
    letters = []
    for row in rows:
        assert row.fork_document_id is not None
        state = states.get(row.fork_document_id, fork_service.DocumentState())
        letters.append(
            ForkLetterOut(
                recipient_id=row.pk,
                draft_id=row.draft_id,
                document_id=row.fork_document_id,
                version_number=row.fork_version_number,
                filename=letter_service.letter_filename(row),
                revised_at=state.revised_at,
                open_suggestions=state.open_suggestions,
                verification=state.verification,
                filed=row.status == LetterRecipient.FILED,
            )
        )
    return ForkLettersOut(in_use=fork_service.config().in_use, letters=letters)
