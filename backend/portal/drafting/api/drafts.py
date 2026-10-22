"""Drafts, their items and citations, their recipients, the preview, and filing."""

from uuid import UUID

from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from portal.drafting.api.library import citation_out, item_out
from portal.drafting.api.permissions import (
    require_draft_citation_scope,
    require_draft_item_scope,
    require_draft_scope,
    require_may_seal,
    require_recipient_scope,
    require_source_request_scope,
    scope_visible_drafts,
)
from portal.drafting.api.schemas import (
    CitationIn,
    CitationOut,
    DraftDetailOut,
    DraftIn,
    DraftOut,
    DraftPatchIn,
    FileDraftIn,
    FileDraftOut,
    FileRecipientIn,
    ItemIn,
    ItemOut,
    ItemPatchIn,
    LetterBlockOut,
    LetterCitationOut,
    LetterOut,
    LetterSaveIn,
    LetterShellOut,
    OutcomeOut,
    PieceOut,
    PreviewOut,
    RecipientIn,
    RecipientOut,
    RecipientPatchIn,
    RequestDraftOut,
    ReserveIn,
    SkipIn,
)
from portal.drafting.models import (
    LetterDraft,
    LetterDraftCitation,
    LetterDraftItem,
    LetterRecipient,
)
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import filing as filing_service
from portal.drafting.services import fork as fork_service
from portal.drafting.services import letter as letter_service
from portal.drafting.services.common import get_or_404
from portal.mail import pdf
from portal.records.api.permissions import (
    require_human_actor,
    require_request_scope,
    require_team,
)
from portal.records.models import Agency, Request
from portal.records.services import events as events_service
from portal.records.storage import safe_filename

router = Router(tags=["drafting"])


def _draft_out(row: LetterDraft, states: dict | None = None) -> DraftOut:
    master = row.fork_master_document_id
    if master is not None and states is None:
        states = fork_service.document_states([master])
    state = (states or {}).get(master) if master is not None else None
    return DraftOut(
        id=row.pk,
        subject=row.subject,
        shell_id=row.shell_id,
        source_template_id=row.source_template_id,
        source_request_id=row.source_request_id,
        source_revision_id=row.source_revision_id,
        sensitivity=row.sensitivity,
        created_at=row.created_at,
        fork_wave_id=row.fork_wave_id,
        fork_master_document_id=master,
        fork_master_version_number=row.fork_master_version_number,
        fork_suggestions=state.open_suggestions if state else 0,
    )


def _recipient_out(row: LetterRecipient, states: dict | None = None) -> RecipientOut:
    # Only ever called for a draft the caller may see, which means every request the draft names
    # is one they may see (drafting/api/permissions.py), this one included.
    reserved = Request.objects.filter(pk=row.request_id).first() if row.request_id else None
    if row.fork_document_id is not None and states is None:
        states = fork_service.document_states([row.fork_document_id])
    state = (states or {}).get(row.fork_document_id) if row.fork_document_id else None
    return RecipientOut(
        fork_document_id=row.fork_document_id,
        fork_version_number=row.fork_version_number,
        fork_suggestions=state.open_suggestions if state else 0,
        tracking_number=reserved.tracking_number if reserved else None,
        request_status=reserved.current_status_id if reserved else None,
        request_filed_at=reserved.filed_at if reserved else None,
        id=row.pk,
        draft_id=row.draft_id,
        agency_id=row.agency_id,
        agency_contact_id=row.agency_contact_id,
        channel=row.channel,
        status=row.status,
        hold_reason=row.hold_reason,
        placeholder_overrides=row.placeholder_overrides,
        request_id=row.request_id,
        template_revision_id=row.template_revision_id,
        filed_at=row.filed_at,
        letter_filename=letter_service.letter_filename(row),
    )


def _draft_detail(draft: LetterDraft) -> DraftDetailOut:
    recipients = list(
        LetterRecipient.objects.filter(draft_id=draft.pk).order_by("created_at", "id")
    )
    states = (
        fork_service.document_states(
            [draft.fork_master_document_id, *(r.fork_document_id for r in recipients)]
        )
        if draft.fork_wave_id is not None
        else {}
    )
    return DraftDetailOut(
        **_draft_out(draft, states).model_dump(),
        items=[
            item_out(r)
            for r in LetterDraftItem.objects.filter(draft_id=draft.pk).order_by(
                "sort_order", "item_number"
            )
        ],
        citations=[
            citation_out(r)
            for r in LetterDraftCitation.objects.filter(draft_id=draft.pk).order_by(
                "sort_order", "marker"
            )
        ],
        recipients=[_recipient_out(r, states) for r in recipients],
    )


@router.get("/drafts", response=list[DraftOut])
def list_drafts(request: HttpRequest) -> list[DraftOut]:
    rows = scope_visible_drafts(request, LetterDraft.objects.order_by("-created_at"))
    return [_draft_out(d) for d in rows]


@router.post("/drafts", response={201: DraftDetailOut})
def create_draft(request: HttpRequest, payload: DraftIn):
    """P23b: `refile_reason` (097) is written as a note on the request the draft is started from,
    in the same transaction, so "why it was filed again" stays on the first request's record."""
    require_team(request)
    require_source_request_scope(request, payload.from_request_id)
    actor = require_human_actor(request)
    reason = (payload.refile_reason or "").strip()
    if reason and payload.from_request_id is None:
        raise HttpError(422, "A reason for filing again needs the request it is filed again from.")
    with transaction.atomic():
        draft = drafts_service.create_draft(
            **payload.model_dump(exclude={"refile_reason"}), created_by_user_id=actor.user_id
        )
        # P21c: while the drafting tool is in use, the letter is written there; its wave is made
        # now, as this person, or nothing is kept (a refusal rolls the draft back with it).
        draft = fork_service.start_wave(draft, request.user)
        if reason:
            assert payload.from_request_id is not None
            events_service.record_event(
                actor,
                payload.from_request_id,
                event_type="note",
                channel="system",
                direction="internal",
                occurred_at=timezone.now(),
                display_body=f"Filing again: {reason}",
                actor_user_id=actor.user_id,
            )
    return 201, _draft_detail(draft)


@router.get("/drafts/{draft_id}", response=DraftDetailOut)
def get_draft(request: HttpRequest, draft_id: UUID) -> DraftDetailOut:
    return _draft_detail(require_draft_scope(request, draft_id))


@router.patch("/drafts/{draft_id}", response=DraftDetailOut)
def update_draft(request: HttpRequest, draft_id: UUID, payload: DraftPatchIn) -> DraftDetailOut:
    draft = require_draft_scope(request, draft_id)
    changes = payload.model_dump(exclude_unset=True)
    require_may_seal(request, draft, changes.get("sensitivity"))
    updated = drafts_service.update_draft(draft_id, **changes)
    if "sensitivity" in changes and changes["sensitivity"] != draft.sensitivity:
        # P21c: who may open its wave in the drafting tool moves with it.
        fork_service.on_draft_changed(draft_id, request.user.id)
    return _draft_detail(updated)


@router.delete("/drafts/{draft_id}", response={204: None})
def delete_draft(request: HttpRequest, draft_id: UUID):
    """Refused with a 409 once anything has been filed from it -- the schema's trigger, translated
    into a sentence by the service layer rather than echoed as a constraint name."""
    require_draft_scope(request, draft_id)
    drafts_service.delete_draft(draft_id)
    return 204, None


@router.post("/drafts/{draft_id}/items", response={201: ItemOut})
def add_draft_item(request: HttpRequest, draft_id: UUID, payload: ItemIn):
    require_draft_scope(request, draft_id)
    return 201, item_out(drafts_service.add_draft_item(draft_id, **payload.model_dump()))


@router.patch("/draft-items/{item_id}", response=ItemOut)
def update_draft_item(request: HttpRequest, item_id: UUID, payload: ItemPatchIn) -> ItemOut:
    require_draft_item_scope(request, item_id)
    return item_out(
        drafts_service.update_draft_item(item_id, **payload.model_dump(exclude_unset=True))
    )


@router.delete("/draft-items/{item_id}", response={204: None})
def delete_draft_item(request: HttpRequest, item_id: UUID):
    require_draft_item_scope(request, item_id)
    drafts_service.delete_draft_item(item_id)
    return 204, None


@router.post("/drafts/{draft_id}/citations", response={201: CitationOut})
def add_draft_citation(request: HttpRequest, draft_id: UUID, payload: CitationIn):
    require_draft_scope(request, draft_id)
    return 201, citation_out(drafts_service.add_draft_citation(draft_id, **payload.model_dump()))


@router.delete("/draft-citations/{citation_id}", response={204: None})
def delete_draft_citation(request: HttpRequest, citation_id: UUID):
    require_draft_citation_scope(request, citation_id)
    drafts_service.delete_draft_citation(citation_id)
    return 204, None


@router.get("/requests/{request_id}/draft", response=RequestDraftOut)
def draft_of_request(request: HttpRequest, request_id: UUID) -> RequestDraftOut:
    """The draft a reserved request was made from (the request page's "Review & file", 076): the
    newest letter that reserved it. 404 when the request was not made from a draft; the privileged
    refusal when the draft is one this person may not open (a privileged draft is its creator's)."""
    require_request_scope(request, request_id)
    recipient = (
        LetterRecipient.objects.filter(request_id=request_id).order_by("-created_at").first()
    )
    if recipient is None:
        raise HttpError(404, "This request was not made from a draft.")
    require_draft_scope(request, recipient.draft_id)
    return RequestDraftOut(draft_id=recipient.draft_id, recipient_id=recipient.pk)


@router.post("/drafts/{draft_id}/recipients", response={201: RecipientOut})
def add_recipient(request: HttpRequest, draft_id: UUID, payload: RecipientIn):
    require_draft_scope(request, draft_id)
    return 201, _recipient_out(drafts_service.add_recipient(draft_id, **payload.model_dump()))


@router.patch("/recipients/{recipient_id}", response=RecipientOut)
def update_recipient(
    request: HttpRequest, recipient_id: UUID, payload: RecipientPatchIn
) -> RecipientOut:
    require_recipient_scope(request, recipient_id)
    return _recipient_out(
        drafts_service.update_recipient(recipient_id, **payload.model_dump(exclude_unset=True))
    )


@router.delete("/recipients/{recipient_id}", response={204: None})
def delete_recipient(request: HttpRequest, recipient_id: UUID):
    require_recipient_scope(request, recipient_id)
    drafts_service.delete_recipient(recipient_id)
    return 204, None


@router.get("/recipients/{recipient_id}/preview", response=PreviewOut)
def preview_recipient(request: HttpRequest, recipient_id: UUID) -> PreviewOut:
    """Exactly what filing would send, rendered through the same code path."""
    require_recipient_scope(request, recipient_id)
    rendered = filing_service.preview(recipient_id)
    return PreviewOut(
        recipient_id=recipient_id,
        text=rendered.text,
        unresolved=list(rendered.unresolved),
    )


@router.post("/drafts/{draft_id}/file", response=FileDraftOut)
def file_draft(request: HttpRequest, draft_id: UUID, payload: FileDraftIn) -> FileDraftOut:
    """Fan the draft out. Each recipient files in its own transaction, so one malformed row does
    not roll back the others; a recipient that failed comes back in `outcomes` with its reason."""
    require_draft_scope(request, draft_id)
    actor = require_human_actor(request)
    result = filing_service.file_draft(actor, draft_id, **payload.model_dump())
    return FileDraftOut(
        draft_id=result.draft_id,
        revision_id=result.revision_id,
        revision=result.revision,
        filed_count=result.filed_count,
        outcomes=[OutcomeOut(**vars(o)) for o in result.outcomes],
    )


@router.get("/drafts/{draft_id}/preview", response=PreviewOut)
def preview_draft_as(
    request: HttpRequest, draft_id: UUID, agency_id: UUID, channel: str | None = None
) -> PreviewOut:
    """ "Preview as": the draft as it would read for an agency that is not a recipient yet, by the
    channel given or else the agency's own filing method."""
    require_draft_scope(request, draft_id)
    rendered = filing_service.preview_for_agency(draft_id, agency_id, channel=channel)
    return PreviewOut(text=rendered.text, unresolved=list(rendered.unresolved))


@router.post("/recipients/{recipient_id}/reserve", response=RecipientOut)
def reserve_recipient(request: HttpRequest, recipient_id: UUID, payload: ReserveIn) -> RecipientOut:
    """Reserve this recipient's tracking number and alias and freeze its letter, without sending
    anything. 422, naming them, while any blank in the letter is unresolved; 409 if it is already
    reserved or is not pending."""
    require_recipient_scope(request, recipient_id)
    actor = require_human_actor(request)
    return _recipient_out(
        filing_service.reserve_recipient(
            actor, recipient_id, requester_user_id=payload.requester_user_id
        )
    )


@router.post("/recipients/{recipient_id}/file", response=RecipientOut)
def file_recipient(
    request: HttpRequest, recipient_id: UUID, payload: FileRecipientIn
) -> RecipientOut:
    """Mark this one letter as sent, reserving it first if it has not been. The same filing as
    POST /api/records/requests/{id}/file on its request; 409 if the letter is already out."""
    require_recipient_scope(request, recipient_id)
    actor = require_human_actor(request)
    return _recipient_out(
        filing_service.file_recipient(actor, recipient_id, **payload.model_dump())
    )


@router.post("/recipients/{recipient_id}/skip", response=RecipientOut)
def skip_recipient(request: HttpRequest, recipient_id: UUID, payload: SkipIn) -> RecipientOut:
    """Decide not to send this letter. If it was already reserved this withdraws its request,
    which needs `note`."""
    require_recipient_scope(request, recipient_id)
    actor = require_human_actor(request)
    return _recipient_out(filing_service.skip_recipient(actor, recipient_id, payload.note))


# ---- P19b: the letter as the editor sees it, and the page -------------------------------------


@router.get("/drafts/{draft_id}/letter", response=LetterOut)
def get_letter(request: HttpRequest, draft_id: UUID) -> LetterOut:
    draft = require_draft_scope(request, draft_id)
    return _letter_out(letter_service.draft_view(draft))


@router.put("/drafts/{draft_id}/letter", response=LetterOut)
def put_letter(request: HttpRequest, draft_id: UUID, payload: LetterSaveIn) -> LetterOut:
    """The editor's save: the pieces and citations it holds replace the draft's; the plain text
    of every piece is derived from its document here."""
    require_draft_scope(request, draft_id)
    data = payload.model_dump()
    view = letter_service.save_draft(
        draft_id, subject=data["subject"], pieces=data["pieces"], citations=data["citations"]
    )
    return _letter_out(view)


def _placeholders_for_draft(
    draft: LetterDraft, agency_id: UUID | None, recipient_id: UUID | None, channel: str | None
) -> dict[str, str]:
    if recipient_id is not None:
        recipient = get_or_404(LetterRecipient, recipient_id, "recipient")
        if recipient.draft_id != draft.pk:
            raise HttpError(422, "That recipient is on another draft.")
        return filing_service.placeholders_for(recipient, recipient.agency)
    if agency_id is not None:
        agency = get_or_404(Agency, agency_id, "agency")
        return filing_service.agency_values(agency, channel=channel or agency.filing_method or "")
    return {}


@router.get("/drafts/{draft_id}/html")
def get_letter_html(
    request: HttpRequest,
    draft_id: UUID,
    agency_id: UUID | None = None,
    recipient_id: UUID | None = None,
    channel: str | None = None,
):
    """The page as the screen shows it, for one agency's values or with the placeholders
    standing."""
    draft = require_draft_scope(request, draft_id)
    html = letter_service.draft_html(
        draft, _placeholders_for_draft(draft, agency_id, recipient_id, channel), for_pdf=False
    )
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    # The page and the PDF are shown inside the editor, in a frame of our own origin.
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Cache-Control"] = "private, no-store"
    return response


@router.get("/drafts/{draft_id}/pdf")
def get_letter_pdf(
    request: HttpRequest,
    draft_id: UUID,
    agency_id: UUID | None = None,
    recipient_id: UUID | None = None,
    channel: str | None = None,
):
    draft = require_draft_scope(request, draft_id)
    html = letter_service.draft_html(
        draft, _placeholders_for_draft(draft, agency_id, recipient_id, channel), for_pdf=True
    )
    data = pdf.pdf_from_html(html)
    response = HttpResponse(data, content_type="application/pdf")
    name = f"{safe_filename(draft.subject)}.pdf"
    if recipient_id is not None:
        recipient = LetterRecipient.objects.filter(pk=recipient_id, draft_id=draft.pk).first()
        if recipient is not None:
            name = letter_service.letter_filename(recipient)
    response["Content-Disposition"] = f'inline; filename="{name}"'
    response["X-Page-Count"] = str(pdf.page_count(data))
    # The page and the PDF are shown inside the editor, in a frame of our own origin.
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Cache-Control"] = "private, no-store"
    return response


def _letter_out(view: letter_service.LetterView) -> LetterOut:
    return LetterOut(
        subject=view.subject,
        shell=LetterShellOut(**vars(view.shell)) if view.shell else None,
        pieces=[PieceOut(**vars(p)) for p in view.pieces],
        citations=[LetterCitationOut(**vars(c)) for c in view.citations],
        blocks=[LetterBlockOut(**b) for b in view.blocks],
    )
