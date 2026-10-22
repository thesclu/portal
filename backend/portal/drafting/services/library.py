"""The reusable half: shells, shared blocks, templates and their items, citations and blocks.

Archival, not deletion, for anything that has ever been filed from. The database enforces that
(letter_template_revisions' ON DELETE RESTRICT); `archive_template` is the door that exists so a
retired template can leave the picker without leaving the record.

"""

from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from portal.drafting import render
from portal.drafting.models import (
    LetterRecipient,
    LetterShell,
    LetterShellBlock,
    LetterTemplate,
    LetterTemplateBlock,
    LetterTemplateCitation,
    LetterTemplateItem,
    LetterTemplateRevision,
    SharedBlock,
    SharedBlockCitation,
)
from portal.drafting.services.common import (
    get_or_404,
    letter_of_template,
    refusing_to_delete_what_is_in_use,
    refusing_to_delete_what_was_filed_from,
    update_fields,
)
from portal.records.actors import Actor
from portal.records.services.common import NotFound, ServiceError, require_human

SHELL_FIELDS = {
    "name",
    "letterhead",
    "opening_body",
    "closing_body",
    "is_default",
    "archived_at",
    "signer_name",
    "signer_title",
    "font_size_pt",
    "margin_mm",
    "opening_rich",
    "closing_rich",
    "font",
    "margin_inches",
    "seal_position",
}
BLOCK_FIELDS = {"name", "body", "body_rich", "archived_at"}
TEMPLATE_FIELDS = {"name", "jurisdiction", "legal_basis", "shell_id", "notes", "archived_at"}
TEMPLATE_ITEM_FIELDS = {
    "item_number",
    "parent_item_id",
    "body",
    "sort_order",
    "provision",
    "kind",
    "body_rich",
}

# The summary a revision gets when it is minted because a letter was started from a template that
# had changed since it was last saved. Fixed, so such a revision is recognisable for what it is.
STARTED_SUMMARY = "Recorded when a letter was started from this template."


# ---- shells ----------------------------------------------------------------


@transaction.atomic
def create_shell(
    *, name: str, letterhead: str, opening_body: str, closing_body: str, is_default: bool = False
) -> LetterShell:
    if is_default:
        _clear_default_shell()
    return LetterShell.objects.create(
        name=name,
        letterhead=letterhead,
        opening_body=opening_body,
        closing_body=closing_body,
        is_default=is_default,
    )


@transaction.atomic
def update_shell(shell_id: UUID, **fields: Any) -> LetterShell:
    """`letter_shells_one_default` is a unique partial index, so promoting a second shell without
    demoting the first is a 23505, not a silent second default. Demote first."""
    if fields.get("is_default"):
        _clear_default_shell(except_id=shell_id)
    return update_fields(LetterShell, shell_id, fields, SHELL_FIELDS, "shell")


def _clear_default_shell(except_id: UUID | None = None) -> None:
    rows = LetterShell.objects.filter(is_default=True, archived_at__isnull=True)
    if except_id is not None:
        rows = rows.exclude(pk=except_id)
    rows.update(is_default=False, updated_at=timezone.now())


def delete_shell(shell_id: UUID) -> None:
    """A shell's 23503 means a template or draft still points at it -- never "filed from" (there
    is no revision-style history for a shell). See `refusing_to_delete_what_is_in_use`."""
    get_or_404(LetterShell, shell_id, "shell")
    with refusing_to_delete_what_is_in_use("shell"):
        LetterShell.objects.filter(pk=shell_id).delete()


# ---- shared blocks ---------------------------------------------------------


def create_block(*, name: str, body: str, body_rich: dict | None = None) -> SharedBlock:
    return SharedBlock.objects.create(name=name, body=body, body_rich=body_rich)


def update_block(block_id: UUID, **fields: Any) -> SharedBlock:
    return update_fields(SharedBlock, block_id, fields, BLOCK_FIELDS, "shared block")


def delete_block(block_id: UUID) -> None:
    """Same as `delete_shell`: a shared block's 23503 comes from `letter_template_blocks` (or,
    P19h, `letter_shell_blocks`) still pulling it in, not from anything having been filed. Its
    own footnotes go with it (ON DELETE CASCADE)."""
    get_or_404(SharedBlock, block_id, "shared block")
    with refusing_to_delete_what_is_in_use("shared block"):
        SharedBlockCitation.objects.filter(shared_block_id=block_id).delete()
        SharedBlock.objects.filter(pk=block_id).delete()


# ---- P19h: a block's own footnotes; a letterhead's own blocks -------------------------------


def block_citations(block_id: UUID) -> list[SharedBlockCitation]:
    return list(
        SharedBlockCitation.objects.filter(shared_block_id=block_id).order_by(
            "sort_order", "marker"
        )
    )


def add_block_citation(
    block_id: UUID, *, marker: str, citation_text: str, sort_order: int = 0
) -> SharedBlockCitation:
    get_or_404(SharedBlock, block_id, "shared block")
    marker = marker.strip()
    if not marker:
        raise ServiceError("A footnote needs a marker.")
    if not citation_text.strip():
        raise ServiceError("A footnote needs its citation.")
    if SharedBlockCitation.objects.filter(shared_block_id=block_id, marker=marker).exists():
        raise ServiceError(f"This block already has a footnote {marker}.")
    return SharedBlockCitation.objects.create(
        shared_block_id=block_id, marker=marker, citation_text=citation_text, sort_order=sort_order
    )


def delete_block_citation(citation_id: UUID) -> None:
    get_or_404(SharedBlockCitation, citation_id, "block footnote")
    SharedBlockCitation.objects.filter(pk=citation_id).delete()


def shell_block_ids(shell_id: UUID) -> list[UUID]:
    return list(
        LetterShellBlock.objects.filter(shell_id=shell_id)
        .order_by("sort_order")
        .values_list("shared_block_id", flat=True)
    )


def set_shell_blocks(shell_id: UUID, block_ids: list[UUID]) -> list[LetterShellBlock]:
    """The shared blocks a letterhead adds to every letter started on it, in this order."""
    get_or_404(LetterShell, shell_id, "shell")
    if len(set(block_ids)) != len(block_ids):
        raise ServiceError("A letterhead adds each shared block at most once.")
    for block_id in block_ids:
        get_or_404(SharedBlock, block_id, "shared block")
    LetterShellBlock.objects.filter(shell_id=shell_id).delete()
    LetterShellBlock.objects.bulk_create(
        [
            LetterShellBlock(shell_id=shell_id, shared_block_id=block_id, sort_order=position)
            for position, block_id in enumerate(block_ids)
        ]
    )
    return list(LetterShellBlock.objects.filter(shell_id=shell_id).order_by("sort_order"))


def latest_revision_number(template_id: UUID) -> int | None:
    newest = (
        LetterTemplateRevision.objects.filter(template_id=template_id).order_by("-revision").first()
    )
    return newest.revision if newest else None


# ---- templates -------------------------------------------------------------


def create_template(
    *,
    name: str,
    jurisdiction: str | None = None,
    legal_basis: str | None = None,
    shell_id: UUID | None = None,
    notes: str | None = None,
    created_by_user_id: UUID | None = None,
) -> LetterTemplate:
    template = LetterTemplate.objects.create(
        name=name,
        jurisdiction=jurisdiction,
        legal_basis=legal_basis,
        shell_id=shell_id,
        notes=notes,
        created_by_user_id=created_by_user_id,
    )
    # P19h: a template created on a letterhead starts with the blocks the letterhead adds. The
    # list is the template's own from here on; the letterhead is not read again.
    if shell_id is not None:
        inherited = shell_block_ids(shell_id)
        if inherited:
            set_template_blocks(template.pk, inherited)
    return template


def update_template(template_id: UUID, **fields: Any) -> LetterTemplate:
    return update_fields(LetterTemplate, template_id, fields, TEMPLATE_FIELDS, "template")


def archive_template(template_id: UUID, *, archived: bool = True) -> LetterTemplate:
    """Retirement. A template that has been filed from has forward value -- it is the thing you
    reuse -- so getting rid of it means taking it out of the picker, not erasing it."""
    return update_fields(
        LetterTemplate,
        template_id,
        {"archived_at": timezone.now() if archived else None},
        TEMPLATE_FIELDS,
        "template",
    )


def delete_template(template_id: UUID) -> None:
    get_or_404(LetterTemplate, template_id, "template")
    with refusing_to_delete_what_was_filed_from("template"):
        LetterTemplate.objects.filter(pk=template_id).delete()


def list_templates(*, include_archived: bool = False) -> list[LetterTemplate]:
    rows = LetterTemplate.objects.all()
    if not include_archived:
        rows = rows.filter(archived_at__isnull=True)
    return list(rows.order_by("name"))


# ---- a template's children -------------------------------------------------


def add_template_item(
    template_id: UUID,
    *,
    item_number: str,
    body: str,
    parent_item_id: UUID | None = None,
    sort_order: int = 0,
    provision: str | None = None,
    kind: str = "item",
    body_rich: dict | None = None,
) -> LetterTemplateItem:
    get_or_404(LetterTemplate, template_id, "template")
    return LetterTemplateItem.objects.create(
        template_id=template_id,
        item_number=item_number,
        body=body,
        parent_item_id=parent_item_id,
        sort_order=sort_order,
        provision=(provision or "").strip() or None,
        kind=kind if kind in ("item", "prose") else "item",
        body_rich=body_rich,
    )


def update_template_item(item_id: UUID, **fields: Any) -> LetterTemplateItem:
    return update_fields(LetterTemplateItem, item_id, fields, TEMPLATE_ITEM_FIELDS, "template item")


def delete_template_item(item_id: UUID) -> None:
    get_or_404(LetterTemplateItem, item_id, "template item")
    LetterTemplateItem.objects.filter(pk=item_id).delete()


def add_template_citation(
    template_id: UUID,
    *,
    marker: str,
    citation_text: str,
    item_id: UUID | None = None,
    sort_order: int = 0,
) -> LetterTemplateCitation:
    get_or_404(LetterTemplate, template_id, "template")
    return LetterTemplateCitation.objects.create(
        template_id=template_id,
        marker=marker,
        citation_text=citation_text,
        item_id=item_id,
        sort_order=sort_order,
    )


def delete_template_citation(citation_id: UUID) -> None:
    get_or_404(LetterTemplateCitation, citation_id, "template citation")
    LetterTemplateCitation.objects.filter(pk=citation_id).delete()


@transaction.atomic
def set_template_blocks(
    template_id: UUID, block_ids: list[UUID], sort_orders: list[int] | None = None
) -> list[LetterTemplateBlock]:
    """Replace the whole list in one call.

    Blocks and items share one `sort_order` sequence (P10), so a block can sit between two asks or
    after the last one. `sort_orders`, one per block, says where each goes in that sequence: a
    block with the `sort_order` of an item comes just before it, and one larger than every item's
    comes after them all. Left out, the blocks take the positions just BELOW zero, in the order
    given (-2, -1 for two blocks): ahead of every item, whose own positions start at zero. That is
    where all prose sat before P10, so a caller that does not place blocks gets what it always got.
    """
    get_or_404(LetterTemplate, template_id, "template")
    if len(set(block_ids)) != len(block_ids):
        raise ServiceError("A template pulls in each shared block at most once.")
    if sort_orders is None:
        sort_orders = list(range(-len(block_ids), 0))
    if len(sort_orders) != len(block_ids):
        raise ServiceError("sort_orders must give one position for each block, in the same order.")
    LetterTemplateBlock.objects.filter(template_id=template_id).delete()
    LetterTemplateBlock.objects.bulk_create(
        [
            LetterTemplateBlock(
                template_id=template_id, shared_block_id=block_id, sort_order=position
            )
            for position, block_id in zip(sort_orders, block_ids, strict=True)
        ]
    )
    return list(LetterTemplateBlock.objects.filter(template_id=template_id).order_by("sort_order"))


def revisions_of(template_id: UUID) -> list[LetterTemplateRevision]:
    return list(LetterTemplateRevision.objects.filter(template_id=template_id))


# ---- revisions: minted on save, and when a letter is started ---------------------------------


@transaction.atomic
def mint_or_reuse_revision(
    template_id: UUID,
    *,
    created_by_user_id: UUID | None,
    summary: str | None,
    replace_unsaved: bool = False,
) -> LetterTemplateRevision:
    """Freeze the template's current state, unless that exact state is the newest one frozen.

    Called from two places and from nowhere else (DECISIONS, "Design v2"): `save_template`, which
    is the editor's Save, and `drafts.create_draft` when a letter is started from a template.
    Filing mints nothing. So a filed letter points at the template exactly as it stood when the
    letter was STARTED, and fixing a template afterwards cannot make an earlier letter look as if
    it carried the fix.

    `select_for_update` on the template row because `revision = max + 1` is a check-then-act: two
    saves at once would otherwise compute the same number.

    "That exact state" is `content` equality with the NEWEST revision only. The snapshot's array
    order is total and stable (see render.py), so an unedited template re-serialises to the same
    structure and reuses its revision. An edit followed by a revert mints a new one rather than
    finding the older identical row; that is accepted.
    """
    template = LetterTemplate.objects.select_for_update().filter(pk=template_id).first()
    if template is None:
        raise NotFound(f"No template {template_id}.")
    content = render.snapshot(letter_of_template(template))
    newest = (
        LetterTemplateRevision.objects.filter(template_id=template_id).order_by("-revision").first()
    )
    if newest is not None and newest.content == content:
        # `replace_unsaved` is Save's: the newest revision was recorded because a letter was
        # started, under somebody else's name and the fixed summary. Returning it would throw the
        # saver's name and their line on what changed away, for good, since a revision is
        # write-once. So Save records its own, with the same content.
        if not (replace_unsaved and newest.summary in (STARTED_SUMMARY, None)):
            return newest
    return LetterTemplateRevision.objects.create(
        template_id=template_id,
        revision=(newest.revision + 1) if newest else 1,
        content=content,
        created_by_user_id=created_by_user_id,
        summary=(summary or "").strip() or None,
    )


def save_template(actor: Actor, template_id: UUID, summary: str) -> LetterTemplateRevision:
    """The editor's Save: record the template as it now stands, with who saved it and a line on
    what changed. Editing a template writes no history; saving does. Saving a template that has
    not changed since it was last SAVED returns that revision and writes nothing. If its newest
    revision was only recorded because a letter was started, Save records its own."""
    user_id = require_human(actor, "Saving a template")
    if not summary or not summary.strip():
        raise ServiceError("Saving a template needs a line saying what changed.")
    return mint_or_reuse_revision(
        template_id, created_by_user_id=user_id, summary=summary, replace_unsaved=True
    )


def filing_counts(template_id: UUID) -> dict[UUID, int]:
    """For each revision of this template, how many filed letters point at it. A count over rows
    that exist; nothing is stored."""
    rows = (
        LetterRecipient.objects.filter(
            template_revision__template_id=template_id, status=LetterRecipient.FILED
        )
        .values_list("template_revision_id")
        .annotate(n=Count("id"))
    )
    return dict(rows)


def last_used_at(template_id: UUID):
    """When a letter from any revision of this template was last filed; None when never."""
    return (
        LetterRecipient.objects.filter(
            template_revision__template_id=template_id, status=LetterRecipient.FILED
        )
        .order_by("-filed_at")
        .values_list("filed_at", flat=True)
        .first()
    )
