"""What every drafting service shares: the deletion-refusal translation and the letter builders.

Errors are the records service layer's own `ServiceError` family, imported rather than
reinvented. The API's exception handlers are registered on those classes for the whole NinjaAPI,
so raising them here is what makes a drafting endpoint answer 404/403/409/422 like a records one.

No `writable()` anywhere in this package. That guard belongs to the records ledger; drafting
models are not `LedgerModel` and copying the idiom would claim scope this layer does not need.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from portal.drafting import render
from portal.drafting.models import (
    LetterDraft,
    LetterDraftCitation,
    LetterDraftItem,
    LetterTemplate,
    LetterTemplateCitation,
    LetterTemplateItem,
)
from portal.records.services.common import InvalidTransition, NotFound, ServiceError

# The two ways the database refuses a deletion here, and they are not the same mechanism:
# 23001 (restrict_violation) is raised by name inside the schema's two hand-written BEFORE DELETE
# triggers; 23503 (foreign_key_violation) comes from letter_template_revisions' ON DELETE RESTRICT
# on its template. Anything that wants to tell "refused because it has been filed from" apart from
# a genuine constraint bug has to catch both.
DELETE_REFUSED_SQLSTATES = frozenset({"23001", "23503"})

# A shell or a shared block also throws 23503 on delete -- letter_templates.shell_id,
# letter_drafts.shell_id and letter_template_blocks.shared_block_id all reference them with no
# ON DELETE action -- but that FK means something different from the templates/drafts/recipients
# case above: it fires because a template or draft still POINTS at the row, never because
# anything was filed from the shell or block itself (nothing files "from" a shell). Reusing
# `refusing_to_delete_what_was_filed_from`'s message here was wrong twice over: nothing was
# filed, and neither table has an `archived_at` door in the API to send the caller to (unlike
# `archive_template`, no `archive_shell`/`archive_block` exists) -- so "Archive it instead" was a
# door that does not exist.
IN_USE_SQLSTATE = "23503"

# A draft has no blocks table -- the schema gives it only items and citations -- but shared blocks
# must land somewhere at draft creation or editing one would still change a letter someone is
# midway through. They land in letter_draft_items under this item_number: prose that belongs in
# the letter but is not one of the numbered asks. Filing sends only the numbered asks to
# records.request_items, because those are what an agency answers and what a deadline attaches to.
PROSE_ITEM_NUMBER = ""


@contextmanager
def refusing_to_delete_what_was_filed_from(what: str) -> Iterator[None]:
    """Turn the database's two deletion refusals into one sentence a person can act on.

    The savepoint is not optional: catching an IntegrityError without one leaves the surrounding
    transaction unusable, and under ATOMIC_REQUESTS there is always a surrounding transaction.
    Same shape `records.services.sources.register_source` uses for its own caught IntegrityError.

    Anything that is not one of the two deletion refusals is re-raised untouched, so it reaches
    the API's IntegrityError handler and gets logged with a traceback rather than being disguised
    as an ordinary refusal.
    """
    try:
        with transaction.atomic():
            yield
    except IntegrityError as exc:
        if getattr(exc.__cause__, "sqlstate", None) not in DELETE_REFUSED_SQLSTATES:
            raise
        raise InvalidTransition(
            f"This {what} is part of the record and cannot be deleted: a request was reserved or "
            "filed from it, or a version of it was saved. Archive it instead, so what came from "
            "it keeps pointing at something."
        ) from exc


@contextmanager
def refusing_to_delete_what_is_in_use(what: str) -> Iterator[None]:
    """Turn a shell's or a shared block's 23503 into a sentence that describes what actually
    happened: some template or draft still references this row. Distinct from
    `refusing_to_delete_what_was_filed_from` -- see `IN_USE_SQLSTATE`'s comment for why the two
    must not share a message.
    """
    try:
        with transaction.atomic():
            yield
    except IntegrityError as exc:
        if getattr(exc.__cause__, "sqlstate", None) != IN_USE_SQLSTATE:
            raise
        raise InvalidTransition(
            f"This {what} is still used by a template or a draft and cannot be deleted. Remove "
            "it from whatever is using it first."
        ) from exc


def get_or_404(model: Any, pk: Any, what: str) -> Any:
    row = model.objects.filter(pk=pk).first()
    if row is None:
        raise NotFound(f"No {what} {pk}.")
    return row


def update_fields(model: Any, pk: Any, fields: dict[str, Any], allowed: set[str], what: str) -> Any:
    """The one edit door for every drafting row: refuse unknown fields, then `QuerySet.update()`.

    Never `.save()` on an existing row -- P2's standing constraint. It applies for a different
    reason here than in records (there are no ungranted cache columns to be refused on) but the
    same habit avoids a full-column UPDATE overwriting a field a concurrent editor just changed.
    `updated_at` is stamped only on the tables that have one.
    """
    unknown = set(fields) - allowed
    if unknown:
        raise ServiceError(f"{what}: not editable: {sorted(unknown)}.")
    get_or_404(model, pk, what)
    if fields:
        if "updated_at" in {f.name for f in model._meta.concrete_fields}:
            fields = {**fields, "updated_at": timezone.now()}
        model.objects.filter(pk=pk).update(**fields)
    return model.objects.get(pk=pk)


def _shell_of(row: LetterTemplate | LetterDraft) -> render.Shell | None:
    shell = row.shell
    if shell is None:
        return None
    return render.Shell(
        letterhead=shell.letterhead,
        opening_body=shell.opening_body,
        closing_body=shell.closing_body,
        opening_rich=shell.opening_rich,
        closing_rich=shell.closing_rich,
    )


def _items(rows: Sequence[Any]) -> list[render.Item]:
    numbers = {row.pk: row.item_number for row in rows}
    return [
        render.Item(
            number=row.item_number,
            body=row.body,
            parent_number=numbers.get(row.parent_item_id, ""),
            sort_order=row.sort_order,
            provision=row.provision or "",
            rich=row.body_rich,
        )
        for row in rows
        if not is_prose(row)
    ]


def is_prose(row: Any) -> bool:
    """A prose row: a paragraph of the letter in the items' order, numbered by nothing. Either
    the P19b `kind`, or the older convention of an empty item number."""
    return getattr(row, "kind", "item") == "prose" or row.item_number == PROSE_ITEM_NUMBER


def _citations(rows: Sequence[Any], numbers: dict[Any, str]) -> list[render.Citation]:
    return [
        render.Citation(
            marker=row.marker,
            citation_text=row.citation_text,
            item_number=numbers.get(row.item_id, ""),
            sort_order=row.sort_order,
        )
        for row in rows
    ]


def resolve_blocks(
    links: Sequence[Any], citations: Sequence[render.Citation]
) -> tuple[list[render.Block], list[render.Citation]]:
    """P19h: shared blocks as letter pieces, each block's own footnotes joined to the letter's
    after `citations` and renumbered to follow them (the block's `[n]` references and its
    `footnoteRef` nodes rewritten to match). `links` are rows with `shared_block` and
    `sort_order`, in the order the blocks sit in the letter."""
    blocks: list[render.Block] = []
    out = list(citations)

    def next_marker() -> str:
        numbers = [int(c.marker) for c in out if c.marker.isdigit()]
        return str(max(numbers, default=0) + 1)

    for link in links:
        block = link.shared_block
        mapping: dict[str, str] = {}
        for cite in block.citations.order_by("sort_order", "marker"):
            new = next_marker()
            mapping[cite.marker] = new
            out.append(
                render.Citation(marker=new, citation_text=cite.citation_text, sort_order=len(out))
            )
        body, rich = render.renumber_footnotes(block.body, block.body_rich, mapping)
        blocks.append(
            render.Block(name=block.name, body=body, sort_order=link.sort_order, rich=rich)
        )
    return blocks, out


def letter_of_template(template: LetterTemplate) -> render.Letter:
    """The template's current state as a `Letter` -- what gets frozen into a revision."""
    items = list(
        LetterTemplateItem.objects.filter(template_id=template.pk).order_by(
            "sort_order", "item_number"
        )
    )
    numbers = {row.pk: row.item_number for row in items}
    citations = list(
        LetterTemplateCitation.objects.filter(template_id=template.pk).order_by(
            "sort_order", "marker"
        )
    )
    blocks, all_citations = resolve_blocks(
        template.template_blocks.select_related("shared_block").order_by("sort_order"),
        _citations(citations, numbers),
    )
    return render.letter_from_parts(
        subject=template.name,
        shell=_shell_of(template),
        items=_items(items),
        citations=all_citations,
        blocks=blocks
        + [
            render.Block(name="", body=row.body, sort_order=row.sort_order, rich=row.body_rich)
            for row in items
            if is_prose(row)
        ],
        meta={
            k: v
            for k, v in (
                ("jurisdiction", template.jurisdiction or ""),
                ("legal_basis", template.legal_basis or ""),
                ("template_name", template.name),
            )
            if v
        },
    )


def draft_item_rows(draft_id: Any) -> list[LetterDraftItem]:
    """A draft's item rows, asks and resolved prose alike, read ONCE. Reservation builds both the
    letter's text and the request's items from one read of these, so an edit landing in between
    cannot make the two disagree."""
    return list(
        LetterDraftItem.objects.filter(draft_id=draft_id).order_by(
            "sort_order", "item_number", "id"
        )
    )


def letter_of_draft(
    draft: LetterDraft, rows: Sequence[LetterDraftItem] | None = None
) -> render.Letter:
    """The draft's current state as a `Letter` -- what actually goes out.

    Rows carrying PROSE_ITEM_NUMBER come back as blocks rather than items, so resolved prose sits
    where it sat in the template instead of being renumbered into the asks.
    """
    if rows is None:
        rows = draft_item_rows(draft.pk)
    asks = [row for row in rows if not is_prose(row)]
    numbers = {row.pk: row.item_number for row in rows}
    citations = list(
        LetterDraftCitation.objects.filter(draft_id=draft.pk).order_by("sort_order", "marker")
    )
    return render.letter_from_parts(
        subject=draft.subject,
        shell=_shell_of(draft),
        items=_items(asks),
        citations=_citations(citations, numbers),
        blocks=[
            render.Block(name="", body=row.body, sort_order=row.sort_order, rich=row.body_rich)
            for row in rows
            if is_prose(row)
        ],
    )
