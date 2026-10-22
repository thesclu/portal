"""Shells, shared blocks and templates, under /api/drafting.

Reading is for the whole team (P20h: an intern or a limited-scope attorney starts a letter from a
template like anyone); changing a letterhead, a block or a template is for admins, staff and staff
attorneys only (`require_staff`): the library is the organisation's, not one request's. Nothing
here is a requester's.
"""

import mimetypes
from uuid import UUID

from django.http import FileResponse, HttpRequest, HttpResponse
from ninja import File, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile

from portal.drafting import render
from portal.drafting.api.schemas import (
    BlockCitationIn,
    BlockCitationOut,
    BlockIn,
    BlockOut,
    BlockPatchIn,
    CitationIn,
    CitationOut,
    ItemIn,
    ItemOut,
    ItemPatchIn,
    LetterBlockOut,
    LetterCitationOut,
    LetterOut,
    LetterSaveIn,
    LetterShellOut,
    PieceOut,
    RevisionOut,
    SaveTemplateIn,
    ShellIn,
    ShellOut,
    ShellPatchIn,
    TemplateBlocksIn,
    TemplateDetailOut,
    TemplateIn,
    TemplateOut,
    TemplatePatchIn,
)
from portal.drafting.models import (
    LetterShell,
    LetterShellBlock,
    LetterTemplate,
    LetterTemplateBlock,
    LetterTemplateCitation,
    LetterTemplateItem,
    LetterTemplateRevision,
    SharedBlock,
)
from portal.drafting.services import letter as letter_service
from portal.drafting.services import library as library_service
from portal.drafting.services.common import get_or_404, letter_of_template
from portal.mail import pdf
from portal.records import storage
from portal.records.api.permissions import require_human_actor, require_staff, require_team
from portal.records.storage import safe_filename

router = Router(tags=["drafting"])


def _shell_out(row: LetterShell) -> ShellOut:
    return ShellOut(
        id=row.pk,
        name=row.name,
        letterhead=row.letterhead,
        opening_body=row.opening_body,
        closing_body=row.closing_body,
        is_default=row.is_default,
        archived_at=row.archived_at,
        signer_name=row.signer_name,
        signer_title=row.signer_title,
        font_size_pt=row.font_size_pt,
        margin_mm=row.margin_mm,
        opening_rich=row.opening_rich,
        closing_rich=row.closing_rich,
        font=row.font,
        margin_inches=float(row.margin_inches) if row.margin_inches is not None else None,
        seal_position=row.seal_position,
        has_seal=bool(row.seal_file),
        block_ids=library_service.shell_block_ids(row.pk),
    )


def _block_out(row: SharedBlock) -> BlockOut:
    return BlockOut(
        id=row.pk,
        name=row.name,
        body=row.body,
        body_rich=row.body_rich,
        archived_at=row.archived_at,
        updated_at=row.updated_at,
        used_in=LetterTemplateBlock.objects.filter(shared_block_id=row.pk).count(),
        used_in_letterheads=LetterShellBlock.objects.filter(shared_block_id=row.pk).count(),
        citations=[
            BlockCitationOut(
                id=c.pk, marker=c.marker, citation_text=c.citation_text, sort_order=c.sort_order
            )
            for c in library_service.block_citations(row.pk)
        ],
    )


def item_out(row) -> ItemOut:
    """Shared with drafts.py: a template item and a draft item have the same printed shape."""
    return ItemOut(
        id=row.pk,
        item_number=row.item_number,
        body=row.body,
        parent_item_id=row.parent_item_id,
        sort_order=row.sort_order,
        provision=row.provision,
        kind=getattr(row, "kind", "item") or "item",
        body_rich=row.body_rich,
    )


def citation_out(row) -> CitationOut:
    return CitationOut(
        id=row.pk,
        marker=row.marker,
        citation_text=row.citation_text,
        item_id=row.item_id,
        sort_order=row.sort_order,
    )


def _template_out(row) -> TemplateOut:
    return TemplateOut(
        id=row.pk,
        name=row.name,
        jurisdiction=row.jurisdiction,
        legal_basis=row.legal_basis,
        shell_id=row.shell_id,
        notes=row.notes,
        archived_at=row.archived_at,
        created_at=row.created_at,
        filed_count=sum(library_service.filing_counts(row.pk).values()),
        item_count=LetterTemplateItem.objects.filter(template_id=row.pk, kind="item").count(),
        last_used_at=library_service.last_used_at(row.pk),
        latest_revision=library_service.latest_revision_number(row.pk),
        fork_template_id=row.fork_template_id,
    )


# ---- shells ----------------------------------------------------------------


@router.get("/shells", response=list[ShellOut])
def list_shells(request: HttpRequest) -> list[ShellOut]:
    require_team(request)
    return [_shell_out(r) for r in LetterShell.objects.order_by("name")]


@router.post("/shells", response={201: ShellOut})
def create_shell(request: HttpRequest, payload: ShellIn):
    require_staff(request)
    fields = payload.model_dump()
    block_ids = fields.pop("block_ids", [])
    row = library_service.create_shell(**fields)
    if block_ids:
        library_service.set_shell_blocks(row.pk, block_ids)
    return 201, _shell_out(row)


@router.patch("/shells/{shell_id}", response=ShellOut)
def update_shell(request: HttpRequest, shell_id: UUID, payload: ShellPatchIn) -> ShellOut:
    require_staff(request)
    fields = payload.model_dump(exclude_unset=True)
    block_ids = fields.pop("block_ids", None)
    row = library_service.update_shell(shell_id, **fields) if fields else None
    if block_ids is not None:
        library_service.set_shell_blocks(shell_id, block_ids)
    return _shell_out(row or get_or_404(LetterShell, shell_id, "shell"))


@router.delete("/shells/{shell_id}", response={204: None})
def delete_shell(request: HttpRequest, shell_id: UUID):
    require_staff(request)
    library_service.delete_shell(shell_id)
    return 204, None


# ---- shared blocks ---------------------------------------------------------


@router.get("/blocks", response=list[BlockOut])
def list_blocks(request: HttpRequest) -> list[BlockOut]:
    require_team(request)
    return [_block_out(r) for r in SharedBlock.objects.order_by("name")]


@router.post("/blocks", response={201: BlockOut})
def create_block(request: HttpRequest, payload: BlockIn):
    require_staff(request)
    return 201, _block_out(library_service.create_block(**payload.model_dump()))


@router.patch("/blocks/{block_id}", response=BlockOut)
def update_block(request: HttpRequest, block_id: UUID, payload: BlockPatchIn) -> BlockOut:
    require_staff(request)
    return _block_out(
        library_service.update_block(block_id, **payload.model_dump(exclude_unset=True))
    )


@router.delete("/blocks/{block_id}", response={204: None})
def delete_block(request: HttpRequest, block_id: UUID):
    require_staff(request)
    library_service.delete_block(block_id)
    return 204, None


@router.post("/blocks/{block_id}/citations", response={201: BlockOut})
def add_block_citation(request: HttpRequest, block_id: UUID, payload: BlockCitationIn):
    """P19h: a footnote of the block's own."""
    require_staff(request)
    library_service.add_block_citation(block_id, **payload.model_dump())
    return 201, _block_out(get_or_404(SharedBlock, block_id, "shared block"))


@router.delete("/block-citations/{citation_id}", response={204: None})
def delete_block_citation(request: HttpRequest, citation_id: UUID):
    require_staff(request)
    library_service.delete_block_citation(citation_id)
    return 204, None


# ---- templates -------------------------------------------------------------


@router.get("/templates", response=list[TemplateOut])
def list_templates(request: HttpRequest, include_archived: bool = False) -> list[TemplateOut]:
    require_team(request)
    return [
        _template_out(t) for t in library_service.list_templates(include_archived=include_archived)
    ]


@router.post("/templates", response={201: TemplateOut})
def create_template(request: HttpRequest, payload: TemplateIn):
    require_staff(request)
    actor = require_human_actor(request)
    row = library_service.create_template(**payload.model_dump(), created_by_user_id=actor.user_id)
    return 201, _template_out(row)


def _template_detail(template) -> TemplateDetailOut:
    newest = LetterTemplateRevision.objects.filter(template_id=template.pk).first()
    links = list(LetterTemplateBlock.objects.filter(template_id=template.pk).order_by("sort_order"))
    return TemplateDetailOut(
        **_template_out(template).model_dump(),
        items=[
            item_out(r)
            for r in LetterTemplateItem.objects.filter(template_id=template.pk).order_by(
                "sort_order", "item_number"
            )
        ],
        citations=[
            citation_out(r)
            for r in LetterTemplateCitation.objects.filter(template_id=template.pk).order_by(
                "sort_order", "marker"
            )
        ],
        block_ids=[link.shared_block_id for link in links],
        block_sort_orders=[link.sort_order for link in links],
        saved=newest is not None
        and newest.content == render.snapshot(letter_of_template(template)),
    )


@router.get("/templates/{template_id}", response=TemplateDetailOut)
def get_template(request: HttpRequest, template_id: UUID) -> TemplateDetailOut:
    require_team(request)
    return _template_detail(get_or_404(LetterTemplate, template_id, "template"))


@router.patch("/templates/{template_id}", response=TemplateDetailOut)
def update_template(
    request: HttpRequest, template_id: UUID, payload: TemplatePatchIn
) -> TemplateDetailOut:
    require_staff(request)
    row = library_service.update_template(template_id, **payload.model_dump(exclude_unset=True))
    return _template_detail(row)


@router.post("/templates/{template_id}/archive", response=TemplateDetailOut)
def archive_template(
    request: HttpRequest, template_id: UUID, archived: bool = True
) -> TemplateDetailOut:
    """Retirement rather than deletion, and the only option once anything has been filed from it."""
    require_staff(request)
    return _template_detail(library_service.archive_template(template_id, archived=archived))


@router.delete("/templates/{template_id}", response={204: None})
def delete_template(request: HttpRequest, template_id: UUID):
    require_staff(request)
    library_service.delete_template(template_id)
    return 204, None


@router.get("/templates/{template_id}/revisions", response=list[RevisionOut])
def list_revisions(request: HttpRequest, template_id: UUID) -> list[RevisionOut]:
    require_team(request)
    counts = library_service.filing_counts(template_id)
    return [
        _revision_out(r, counts.get(r.pk, 0)) for r in library_service.revisions_of(template_id)
    ]


def _revision_out(r: LetterTemplateRevision, filed_count: int) -> RevisionOut:
    return RevisionOut(
        id=r.pk,
        template_id=r.template_id,
        revision=r.revision,
        content=r.content,
        created_at=r.created_at,
        created_by_user_id=r.created_by_user_id,
        created_by_name=r.created_by_user.name if r.created_by_user_id else None,
        summary=r.summary,
        filed_count=filed_count,
    )


@router.post("/templates/{template_id}/save", response=RevisionOut)
def save_template(request: HttpRequest, template_id: UUID, payload: SaveTemplateIn) -> RevisionOut:
    """The editor's Save: record the template as it stands, with who saved it and what changed.
    Editing writes no history; this does. Saving a template that has not changed since its newest
    revision returns that revision and writes nothing."""
    require_staff(request)
    revision = library_service.save_template(
        require_human_actor(request), template_id, payload.summary
    )
    return _revision_out(revision, library_service.filing_counts(template_id).get(revision.pk, 0))


@router.post("/templates/{template_id}/items", response={201: ItemOut})
def add_template_item(request: HttpRequest, template_id: UUID, payload: ItemIn):
    require_staff(request)
    return 201, item_out(library_service.add_template_item(template_id, **payload.model_dump()))


@router.patch("/template-items/{item_id}", response=ItemOut)
def update_template_item(request: HttpRequest, item_id: UUID, payload: ItemPatchIn) -> ItemOut:
    require_staff(request)
    return item_out(
        library_service.update_template_item(item_id, **payload.model_dump(exclude_unset=True))
    )


@router.delete("/template-items/{item_id}", response={204: None})
def delete_template_item(request: HttpRequest, item_id: UUID):
    require_staff(request)
    library_service.delete_template_item(item_id)
    return 204, None


@router.post("/templates/{template_id}/citations", response={201: CitationOut})
def add_template_citation(request: HttpRequest, template_id: UUID, payload: CitationIn):
    require_staff(request)
    row = library_service.add_template_citation(template_id, **payload.model_dump())
    return 201, citation_out(row)


@router.delete("/template-citations/{citation_id}", response={204: None})
def delete_template_citation(request: HttpRequest, citation_id: UUID):
    require_staff(request)
    library_service.delete_template_citation(citation_id)
    return 204, None


@router.put("/templates/{template_id}/blocks", response=TemplateDetailOut)
def set_template_blocks(
    request: HttpRequest, template_id: UUID, payload: TemplateBlocksIn
) -> TemplateDetailOut:
    require_staff(request)
    library_service.set_template_blocks(template_id, payload.block_ids, payload.sort_orders)
    return _template_detail(LetterTemplate.objects.get(pk=template_id))


# ---- P19b: a template as the editor sees it; the letterhead's seal ---------------------------


@router.get("/templates/{template_id}/letter", response=LetterOut)
def get_template_letter(request: HttpRequest, template_id: UUID) -> LetterOut:
    require_team(request)
    template = get_or_404(LetterTemplate, template_id, "template")
    return _template_letter_out(letter_service.template_view(template))


@router.put("/templates/{template_id}/letter", response=LetterOut)
def put_template_letter(
    request: HttpRequest, template_id: UUID, payload: LetterSaveIn
) -> LetterOut:
    require_staff(request)
    data = payload.model_dump()
    view = letter_service.save_template(
        template_id, pieces=data["pieces"], citations=data["citations"]
    )
    return _template_letter_out(view)


@router.get("/templates/{template_id}/html")
def get_template_html(request: HttpRequest, template_id: UUID):
    require_team(request)
    template = get_or_404(LetterTemplate, template_id, "template")
    html = letter_service.template_html(template, for_pdf=False)
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    # The page and the PDF are shown inside the editor, in a frame of our own origin.
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Cache-Control"] = "private, no-store"
    return response


@router.get("/templates/{template_id}/pdf")
def get_template_pdf(request: HttpRequest, template_id: UUID):
    require_team(request)
    template = get_or_404(LetterTemplate, template_id, "template")
    data = pdf.pdf_from_html(letter_service.template_html(template, for_pdf=True))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{safe_filename(template.name)}.pdf"'
    response["X-Page-Count"] = str(pdf.page_count(data))
    # The page and the PDF are shown inside the editor, in a frame of our own origin.
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Cache-Control"] = "private, no-store"
    return response


@router.post("/shells/{shell_id}/seal", response=ShellOut)
def put_seal(
    request: HttpRequest,
    shell_id: UUID,
    file: UploadedFile = File(...),  # noqa: B008 -- ninja's own idiom for a multipart file
) -> ShellOut:
    require_staff(request)
    row = letter_service.set_seal(shell_id, file.name or "seal", file.read())
    return _shell_out(row)


@router.get("/shells/{shell_id}/seal")
def get_seal(request: HttpRequest, shell_id: UUID):
    require_team(request)
    shell = get_or_404(LetterShell, shell_id, "letterhead")
    if not shell.seal_file:
        raise HttpError(404, "This letterhead has no seal.")
    mime = mimetypes.guess_type(shell.seal_file)[0] or "image/png"
    response = FileResponse(storage.open_stored(shell.seal_file), content_type=mime)
    response["Cache-Control"] = "private, max-age=300"
    return response


def _template_letter_out(view: letter_service.LetterView) -> LetterOut:
    return LetterOut(
        subject=view.subject,
        shell=LetterShellOut(**vars(view.shell)) if view.shell else None,
        pieces=[PieceOut(**vars(p)) for p in view.pieces],
        citations=[LetterCitationOut(**vars(c)) for c in view.citations],
        blocks=[LetterBlockOut(**b) for b in view.blocks],
    )
