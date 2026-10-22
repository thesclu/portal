"""The letter as the editor sees and saves it (P19b): one read of a draft or a template as an
ordered list of pieces with their editor documents, one write that upserts the pieces the editor
holds, and the page itself as HTML and as a PDF.

The plain `body` of every piece is derived from its document here, at save time, so the request's
text, search and the snapshot never read a document; they read words.
"""

import base64
import mimetypes
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from portal.drafting import render
from portal.drafting.models import (
    LetterDraft,
    LetterDraftCitation,
    LetterDraftItem,
    LetterShell,
    LetterTemplate,
    LetterTemplateCitation,
    LetterTemplateItem,
)
from portal.drafting.services.common import (
    get_or_404,
    is_prose,
    letter_of_draft,
    letter_of_template,
)
from portal.records import storage
from portal.records.services.common import ServiceError

KINDS = ("item", "prose")


@dataclass(frozen=True)
class Piece:
    id: UUID
    kind: str
    item_number: str
    parent_item_id: UUID | None
    sort_order: int
    provision: str | None
    body: str
    body_rich: Any


@dataclass(frozen=True)
class CitationPiece:
    id: UUID
    marker: str
    citation_text: str
    item_id: UUID | None
    sort_order: int


@dataclass(frozen=True)
class ShellView:
    id: UUID | None
    name: str
    letterhead: str
    opening_body: str
    opening_rich: Any
    closing_body: str
    closing_rich: Any
    font: str | None
    font_size_pt: int | None
    margin_inches: float | None
    seal_position: str | None
    has_seal: bool
    signer_name: str | None
    signer_title: str | None


@dataclass(frozen=True)
class LetterView:
    subject: str
    shell: ShellView | None
    pieces: list[Piece]
    citations: list[CitationPiece]
    blocks: list[dict[str, Any]]  # a template's shared blocks, in order (templates only)


def _piece(row: Any) -> Piece:
    return Piece(
        id=row.pk,
        kind="prose" if is_prose(row) else "item",
        item_number=row.item_number,
        parent_item_id=row.parent_item_id,
        sort_order=row.sort_order,
        provision=row.provision,
        body=row.body,
        body_rich=row.body_rich,
    )


def _citation(row: Any) -> CitationPiece:
    return CitationPiece(
        id=row.pk,
        marker=row.marker,
        citation_text=row.citation_text,
        item_id=row.item_id,
        sort_order=row.sort_order,
    )


def shell_view(shell: LetterShell | None) -> ShellView | None:
    if shell is None:
        return None
    return ShellView(
        id=shell.pk,
        name=shell.name,
        letterhead=shell.letterhead,
        opening_body=shell.opening_body,
        opening_rich=shell.opening_rich,
        closing_body=shell.closing_body,
        closing_rich=shell.closing_rich,
        font=shell.font,
        font_size_pt=shell.font_size_pt,
        margin_inches=float(shell.margin_inches) if shell.margin_inches is not None else None,
        seal_position=shell.seal_position,
        has_seal=bool(shell.seal_file),
        signer_name=shell.signer_name,
        signer_title=shell.signer_title,
    )


def draft_view(draft: LetterDraft) -> LetterView:
    rows = LetterDraftItem.objects.filter(draft_id=draft.pk).order_by(
        "sort_order", "item_number", "id"
    )
    cites = LetterDraftCitation.objects.filter(draft_id=draft.pk).order_by("sort_order", "marker")
    return LetterView(
        subject=draft.subject,
        shell=shell_view(draft.shell),
        pieces=[_piece(r) for r in rows],
        citations=[_citation(c) for c in cites],
        blocks=[],
    )


def template_view(template: LetterTemplate) -> LetterView:
    rows = LetterTemplateItem.objects.filter(template_id=template.pk).order_by(
        "sort_order", "item_number", "id"
    )
    cites = LetterTemplateCitation.objects.filter(template_id=template.pk).order_by(
        "sort_order", "marker"
    )
    blocks = [
        {
            "id": link.shared_block_id,
            "name": link.shared_block.name,
            "body": link.shared_block.body,
            "body_rich": link.shared_block.body_rich,
            "sort_order": link.sort_order,
        }
        for link in template.template_blocks.select_related("shared_block").order_by("sort_order")
    ]
    return LetterView(
        subject=template.name,
        shell=shell_view(template.shell),
        pieces=[_piece(r) for r in rows],
        citations=[_citation(c) for c in cites],
        blocks=blocks,
    )


def _check_piece(piece: dict[str, Any]) -> dict[str, Any]:
    kind = piece.get("kind") or "item"
    if kind not in KINDS:
        raise ServiceError(f"A piece is one of {', '.join(KINDS)}.")
    rich = piece.get("body_rich")
    if rich is not None and not isinstance(rich, dict):
        raise ServiceError("A piece's document is an object.")
    body = str(piece.get("body") or "")
    if rich:
        body = render.rich_text(rich)
    number = str(piece.get("item_number") or "").strip()
    if kind == "item" and not number:
        raise ServiceError("An item needs a number.")
    if kind == "prose":
        number = ""
    return {
        "kind": kind,
        "item_number": number,
        "parent_item_id": piece.get("parent_item_id"),
        "sort_order": int(piece.get("sort_order") or 0),
        "provision": (piece.get("provision") or "").strip() or None,
        "body": body,
        "body_rich": rich,
    }


def _check_citation(cite: dict[str, Any]) -> dict[str, Any]:
    marker = str(cite.get("marker") or "").strip()
    text = str(cite.get("citation_text") or "").strip()
    if not marker or not text:
        raise ServiceError("A citation needs its marker and its text.")
    return {
        "marker": marker,
        "citation_text": text,
        "item_id": cite.get("item_id"),
        "sort_order": int(cite.get("sort_order") or 0),
    }


def _upsert(
    model: Any,
    owner_field: str,
    owner_id: UUID,
    given: list[dict[str, Any]],
    check: Any,
    *,
    keep_order: bool = True,
) -> list[Any]:
    """The rows the editor holds become the rows that exist: known ids are updated, new ones
    are made, ids not sent are deleted (a draft is scratch; DELETE is granted on drafting)."""
    existing = {row.pk: row for row in model.objects.filter(**{owner_field: owner_id})}
    seen: set[UUID] = set()
    out = []
    for position, raw in enumerate(given):
        fields = check(raw)
        if keep_order:
            fields["sort_order"] = position
        pk = raw.get("id")
        if pk is not None and pk in existing:
            model.objects.filter(pk=pk).update(**fields)
            seen.add(pk)
            out.append(model.objects.get(pk=pk))
        else:
            row = model.objects.create(**{owner_field: owner_id}, **fields)
            seen.add(row.pk)
            out.append(row)
    stale = set(existing) - seen
    if stale:
        model.objects.filter(pk__in=stale).delete()
    return out


@transaction.atomic
def save_draft(
    draft_id: UUID,
    *,
    subject: str | None = None,
    pieces: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> LetterView:
    draft = get_or_404(LetterDraft, draft_id, "draft")
    if subject is not None:
        LetterDraft.objects.filter(pk=draft_id).update(subject=subject.strip() or draft.subject)
    if pieces is not None:
        rows = _upsert(LetterDraftItem, "draft_id", draft_id, pieces, _check_piece)
        _repoint_parents(LetterDraftItem, rows, pieces)
    if citations is not None:
        _upsert(
            LetterDraftCitation, "draft_id", draft_id, citations, _check_citation, keep_order=True
        )
    return draft_view(LetterDraft.objects.get(pk=draft_id))


@transaction.atomic
def save_template(
    template_id: UUID,
    *,
    pieces: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> LetterView:
    template = get_or_404(LetterTemplate, template_id, "template")
    if pieces is not None:
        rows = _upsert(LetterTemplateItem, "template_id", template_id, pieces, _check_piece)
        _repoint_parents(LetterTemplateItem, rows, pieces)
    if citations is not None:
        _upsert(
            LetterTemplateCitation,
            "template_id",
            template_id,
            citations,
            _check_citation,
            keep_order=True,
        )
    return template_view(LetterTemplate.objects.get(pk=template.pk))


def _repoint_parents(model: Any, rows: list[Any], given: list[dict[str, Any]]) -> None:
    """A new sub-item names its parent by the parent's position when the parent is new too
    (`parent_index`); resolve that to the row the parent became."""
    for row, raw in zip(rows, given, strict=False):
        index = raw.get("parent_index")
        if index is not None and 0 <= int(index) < len(rows):
            model.objects.filter(pk=row.pk).update(parent_item_id=rows[int(index)].pk)


# ---- the page --------------------------------------------------------------------------------


def page_layout(shell: LetterShell | None) -> render.PageLayout:
    if shell is None:
        return render.PageLayout()
    return render.PageLayout(
        font=shell.font or "Times New Roman",
        font_size_pt=shell.font_size_pt or 12,
        margin_inches=float(shell.margin_inches) if shell.margin_inches is not None else 1.0,
        seal_position=shell.seal_position or "left",
        seal_data_uri=seal_data_uri(shell),
        signer_name=shell.signer_name,
        signer_title=shell.signer_title,
    )


def seal_data_uri(shell: LetterShell) -> str | None:
    if not shell.seal_file:
        return None
    try:
        with storage.open_stored(shell.seal_file) as fh:
            data = fh.read()
    except FileNotFoundError:
        return None
    mime = mimetypes.guess_type(shell.seal_file)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def set_seal(shell_id: UUID, filename: str, data: bytes) -> LetterShell:
    """The seal is a stored image named on the letterhead; replacing it replaces the name."""
    shell = get_or_404(LetterShell, shell_id, "letterhead")
    mime = mimetypes.guess_type(filename)[0] or ""
    if not mime.startswith("image/"):
        raise ServiceError("The seal is an image (PNG, JPEG or SVG).")
    if len(data) > 4 * 1024 * 1024:
        raise ServiceError("The seal image is larger than four megabytes.")
    name = default_storage.save(
        f"seals/{shell_id}/{storage.safe_filename(filename)}", ContentFile(data)
    )
    old = shell.seal_file
    LetterShell.objects.filter(pk=shell_id).update(seal_file=name)
    if old and old != name:
        storage.delete_stored([old])
    return LetterShell.objects.get(pk=shell_id)


def draft_html(draft: LetterDraft, placeholders: dict[str, str], *, for_pdf: bool) -> str:
    return render.render_html(
        letter_of_draft(draft), placeholders, page_layout(draft.shell), for_pdf=for_pdf
    )


def template_html(
    template: LetterTemplate, placeholders: dict[str, str] | None = None, *, for_pdf: bool
) -> str:
    return render.render_html(
        letter_of_template(template),
        placeholders or {},
        page_layout(template.shell),
        for_pdf=for_pdf,
    )


def margin_value(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def letter_filename(recipient: Any) -> str:
    """`SCLU_CPRA_Agency-Short_Subject_2026-09-08.pdf`: the organisation's short name, the law,
    the agency, the subject and the date, as the design names the file (P19e). The date is the
    agency's, as the letter's own `{{FILING_DATE}}` is (P27: it was UTC's, a day ahead of the
    letter every evening in California)."""
    import re as _re
    from datetime import UTC, datetime

    from portal.core.services.settings import org_short_name
    from portal.drafting.services.filing import letter_date

    def slug(text: str, limit: int) -> str:
        cleaned = _re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-")
        return cleaned[:limit].strip("-") or "letter"

    org = org_short_name() or "Letter"
    agency = recipient.agency
    draft = recipient.draft
    law = agency.records_law or "Records"
    when = letter_date(agency, recipient.filed_at or datetime.now(tz=UTC))
    return (
        f"{slug(org, 24)}_{slug(law, 24)}_{slug(agency.short_name or agency.name, 40)}_"
        f"{slug(draft.subject, 40)}_{when:%Y-%m-%d}.pdf"
    )
