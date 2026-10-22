"""The working half: a draft, its items and citations, and the recipients it will be filed to.

Draft creation is where the design's central rule is physically enforced. Items and citations are
COPIED here and are then independent: no column links a draft item to the template item it came
from, so nothing downstream can be tempted to reconcile the two, and a filing-time edit has
nowhere to flow back to. Shared blocks are resolved to plain text in the same step, so editing a
block afterwards cannot change a letter someone is midway through.
"""

from typing import Any
from uuid import UUID

from django.db import transaction

from portal.drafting.models import (
    LetterDraft,
    LetterDraftCitation,
    LetterDraftItem,
    LetterRecipient,
    LetterShell,
    LetterShellBlock,
    LetterTemplate,
)
from portal.drafting.services import library as library_service
from portal.drafting.services.common import (
    PROSE_ITEM_NUMBER,
    get_or_404,
    refusing_to_delete_what_was_filed_from,
    resolve_blocks,
    update_fields,
)
from portal.records.models import Agency, Request, RequestItem
from portal.records.services.common import NotFound, ServiceError

DRAFT_FIELDS = {"subject", "shell_id", "sensitivity"}
DRAFT_ITEM_FIELDS = {
    "item_number",
    "parent_item_id",
    "body",
    "sort_order",
    "provision",
    "kind",
    "body_rich",
}
RECIPIENT_FIELDS = {
    "channel",
    "status",
    "hold_reason",
    "agency_contact_id",
    "placeholder_overrides",
}
SETTABLE_STATUSES = (LetterRecipient.PENDING, LetterRecipient.HELD, LetterRecipient.SKIPPED)


DEFAULT_SENSITIVITY = "internal"


@transaction.atomic
def create_draft(
    *,
    subject: str | None = None,
    from_template_id: UUID | None = None,
    from_request_id: UUID | None = None,
    shell_id: UUID | None = None,
    sensitivity: str | None = None,
    created_by_user_id: UUID | None = None,
) -> LetterDraft:
    """Start a draft from a template, from a past request, or from nothing.

    `letter_drafts_one_source` allows at most one source; naming both is refused here with a
    sentence rather than left to the CHECK.

    `sensitivity=None` means the caller did not explicitly ask for one. Starting from a request
    inherits that request's own sensitivity (`records.requests.sensitivity` is
    `public | internal | privileged` and keys the privacy controls); a template carries no
    sensitivity of its own, so that path -- like starting from scratch -- falls back to
    `DEFAULT_SENSITIVITY`. An explicit `sensitivity` always wins over either fallback.
    """
    if from_template_id is not None and from_request_id is not None:
        raise ServiceError("A draft starts from a template or from a past request, not both.")

    if from_template_id is not None:
        return _draft_from_template(
            from_template_id,
            subject,
            shell_id,
            sensitivity or DEFAULT_SENSITIVITY,
            created_by_user_id,
        )
    if from_request_id is not None:
        return _draft_from_request(
            from_request_id, subject, shell_id, sensitivity, created_by_user_id
        )
    if not subject:
        raise ServiceError("A draft written from scratch needs a subject.")
    draft = LetterDraft.objects.create(
        subject=subject,
        shell_id=shell_id or _default_shell_id(),
        sensitivity=sensitivity or DEFAULT_SENSITIVITY,
        created_by_user_id=created_by_user_id,
    )
    _copy_shell_blocks(draft)
    return draft


def _copy_shell_blocks(draft: LetterDraft) -> None:
    """P19h: a letter written from scratch on a letterhead starts with the blocks the letterhead
    adds, as prose rows it owns, with the blocks' footnotes as its own citations."""
    if draft.shell_id is None:
        return
    links = list(
        LetterShellBlock.objects.filter(shell_id=draft.shell_id)
        .select_related("shared_block")
        .order_by("sort_order")
    )
    if not links:
        return
    blocks, citations = resolve_blocks(links, [])
    LetterDraftItem.objects.bulk_create(
        [
            LetterDraftItem(
                draft_id=draft.pk,
                item_number=PROSE_ITEM_NUMBER,
                kind="prose",
                body=block.body,
                body_rich=block.rich,
                sort_order=block.sort_order,
            )
            for block in blocks
        ]
    )
    LetterDraftCitation.objects.bulk_create(
        [
            LetterDraftCitation(
                draft_id=draft.pk,
                marker=cite.marker,
                citation_text=cite.citation_text,
                sort_order=cite.sort_order,
            )
            for cite in citations
        ]
    )


def _copy_items(draft_id: UUID, source_rows, body_field: str) -> dict[UUID, LetterDraftItem]:
    """Copy items into a draft and remap their parent links onto the new rows.

    Two passes, so a child's parent already exists whatever order the rows arrive in. The remap is
    the point: a copied parent id is a NEW draft id, never the source's, because the copy is meant
    to be independent of what it came from. `body_field` is `body` on a template item and
    `description` on a records request item -- the only difference between the two sources.
    """
    rows = list(source_rows)
    copied = {
        row.pk: LetterDraftItem.objects.create(
            draft_id=draft_id,
            item_number=row.item_number,
            body=getattr(row, body_field),
            sort_order=row.sort_order,
            # Both sources carry one: a template item's, and a filed request item's.
            provision=row.provision,
        )
        for row in rows
    }
    for row in rows:
        if row.parent_item_id in copied:
            LetterDraftItem.objects.filter(pk=copied[row.pk].pk).update(
                parent_item_id=copied[row.parent_item_id].pk
            )
    return copied


def _default_shell_id() -> UUID | None:
    shell = LetterShell.objects.filter(is_default=True, archived_at__isnull=True).first()
    return shell.pk if shell else None


def _draft_from_template(
    template_id: UUID,
    subject: str | None,
    shell_id: UUID | None,
    sensitivity: str,
    created_by_user_id: UUID | None,
) -> LetterDraft:
    # `sensitivity` here is always already resolved (create_draft applies DEFAULT_SENSITIVITY
    # before calling in). Unlike a request, `LetterTemplate` carries no sensitivity of its own to
    # inherit -- checked for the same latent-default bug _draft_from_request had, and there is
    # nothing here that could be dead code in the same way.
    template = get_or_404(LetterTemplate, template_id, "template")
    # The template as it stands at this moment, frozen (or found already frozen) BEFORE anything
    # is copied, under the template's row lock. This is the revision the letter will point at
    # when it is reserved; nothing later mints one.
    revision = library_service.mint_or_reuse_revision(
        template.pk,
        created_by_user_id=created_by_user_id,
        summary=library_service.STARTED_SUMMARY,
    )
    draft = LetterDraft.objects.create(
        subject=subject or template.name,
        shell_id=shell_id or template.shell_id or _default_shell_id(),
        source_template_id=template.pk,
        source_revision_id=revision.pk,
        sensitivity=sensitivity,
        created_by_user_id=created_by_user_id,
    )

    # Everything below is copied from the REVISION, not re-read from the template's tables.
    # The revision was frozen under the template's row lock, but an item, a shared block or a
    # citation can be edited without touching that row, so a second read could differ from what
    # was just frozen and the letter would point at a revision that does not contain its text.
    sections = revision.content["sections"]
    # Resolved blocks keep the place they had in the template: their sort_order is the block's
    # own, in the one sequence blocks and items share, so the renderer puts each where it was.
    LetterDraftItem.objects.bulk_create(
        [
            LetterDraftItem(
                draft_id=draft.pk,
                item_number=PROSE_ITEM_NUMBER,
                body=section["body"],
                body_rich=section.get("rich"),
                sort_order=section["sort_order"],
            )
            for section in sections
            if section["kind"] == "block"
        ]
    )
    asks = [section for section in sections if section["kind"] == "item"]
    copied = {
        section["label"]: LetterDraftItem.objects.create(
            draft_id=draft.pk,
            item_number=section["label"],
            body=section["body"],
            sort_order=section["sort_order"],
            provision=section.get("provision") or None,
        )
        for section in asks
    }
    for section in asks:
        if section["parent"] in copied:
            LetterDraftItem.objects.filter(pk=copied[section["label"]].pk).update(
                parent_item_id=copied[section["parent"]].pk
            )
    LetterDraftCitation.objects.bulk_create(
        [
            LetterDraftCitation(
                draft_id=draft.pk,
                item_id=copied[section["parent"]].pk if section["parent"] in copied else None,
                marker=section["label"],
                citation_text=section["body"],
                sort_order=section["sort_order"],
            )
            for section in sections
            if section["kind"] == "citation"
        ]
    )
    return draft


def _draft_from_request(
    request_id: UUID,
    subject: str | None,
    shell_id: UUID | None,
    sensitivity: str | None,
    created_by_user_id: UUID | None,
) -> LetterDraft:
    """A pure one-time copy of a past request: its subject, its sensitivity, and its numbered
    items.

    `requests.request_text` is deliberately not copied. It is the whole rendered letter -- shell,
    prose and asks already fused -- and nothing can decompose it back into the parts a draft is
    made of, so copying it would put every ask into the letter twice. The asks and the subject
    carry over; the drafter picks a shell.

    `sensitivity` inherits from the source request unless the caller explicitly named one --
    `sensitivity or source.sensitivity` only does that when `sensitivity` can actually be falsy,
    which needs `create_draft`'s default to be `None`, not `"internal"`. With `"internal"` as the
    default this line was unreachable dead code: a draft started from a `privileged` request came
    back `internal`, silently.
    """
    source = Request.objects.filter(pk=request_id).first()
    if source is None:
        raise NotFound(f"No request {request_id}.")
    draft = LetterDraft.objects.create(
        subject=subject or source.subject,
        shell_id=shell_id or _default_shell_id(),
        source_request_id=source.pk,
        sensitivity=sensitivity or source.sensitivity,
        created_by_user_id=created_by_user_id,
    )
    _copy_items(
        draft.pk,
        RequestItem.objects.filter(request_id=source.pk).order_by("sort_order", "item_number"),
        "description",
    )
    return draft


def update_draft(draft_id: UUID, **fields: Any) -> LetterDraft:
    return update_fields(LetterDraft, draft_id, fields, DRAFT_FIELDS, "draft")


def delete_draft(draft_id: UUID) -> None:
    """Genuinely deletable -- the only place in this system where that is true -- but only while
    nothing has been filed from it. The refusal comes from the schema's trigger, not from here."""
    get_or_404(LetterDraft, draft_id, "draft")
    with refusing_to_delete_what_was_filed_from("draft"):
        LetterDraft.objects.filter(pk=draft_id).delete()


# ---- a draft's children ----------------------------------------------------


def add_draft_item(
    draft_id: UUID,
    *,
    item_number: str,
    body: str,
    parent_item_id: UUID | None = None,
    sort_order: int = 0,
    provision: str | None = None,
    kind: str = "item",
    body_rich: dict | None = None,
) -> LetterDraftItem:
    get_or_404(LetterDraft, draft_id, "draft")
    return LetterDraftItem.objects.create(
        draft_id=draft_id,
        item_number=item_number,
        body=body,
        parent_item_id=parent_item_id,
        sort_order=sort_order,
        provision=(provision or "").strip() or None,
        kind=kind if kind in ("item", "prose") else "item",
        body_rich=body_rich,
    )


def update_draft_item(item_id: UUID, **fields: Any) -> LetterDraftItem:
    return update_fields(LetterDraftItem, item_id, fields, DRAFT_ITEM_FIELDS, "draft item")


def delete_draft_item(item_id: UUID) -> None:
    get_or_404(LetterDraftItem, item_id, "draft item")
    LetterDraftItem.objects.filter(pk=item_id).delete()


def add_draft_citation(
    draft_id: UUID,
    *,
    marker: str,
    citation_text: str,
    item_id: UUID | None = None,
    sort_order: int = 0,
) -> LetterDraftCitation:
    get_or_404(LetterDraft, draft_id, "draft")
    return LetterDraftCitation.objects.create(
        draft_id=draft_id,
        marker=marker,
        citation_text=citation_text,
        item_id=item_id,
        sort_order=sort_order,
    )


def delete_draft_citation(citation_id: UUID) -> None:
    get_or_404(LetterDraftCitation, citation_id, "draft citation")
    LetterDraftCitation.objects.filter(pk=citation_id).delete()


# ---- recipients ------------------------------------------------------------


def _cannot_go_out(agency: Agency, channel: str | None) -> str | None:
    """Why a letter to this agency by this channel cannot go out as things stand, or None."""
    if channel is None:
        return NO_FILING_METHOD
    if channel == "email" and not (
        agency.default_request_email and agency.request_email_verified_at
    ):
        return NO_VERIFIED_EMAIL
    return None


def _require_text_overrides(overrides: dict[str, Any] | None) -> None:
    for name, value in (overrides or {}).items():
        if value is not None and not isinstance(value, str):
            raise ServiceError(f"The value given for {name} must be text.")


NO_FILING_METHOD = "This agency has no filing method on record; choose how this letter goes."
NO_VERIFIED_EMAIL = "This agency files by email and has no verified address."
# What a recipient's channel is stored as while nobody has said how the letter goes. The column
# is NOT NULL; the recipient arrives held, with NO_FILING_METHOD as the reason, so this value is
# never acted on.
UNDECIDED_CHANNEL = "email"


def add_recipient(
    draft_id: UUID,
    *,
    agency_id: UUID,
    channel: str | None = None,
    agency_contact_id: UUID | None = None,
    hold_reason: str | None = None,
    placeholder_overrides: dict[str, Any] | None = None,
) -> LetterRecipient:
    """Add an agency to the draft. The channel defaults from the agency's own filing method.

    A recipient that cannot go out as it stands arrives `held` with a reason rather than silently
    missing from the wave or failing at the last step: an agency with no filing method (and no
    channel named here), or one that files by email and has no verified address. A `hold_reason`
    given by the caller wins over both.
    """
    get_or_404(LetterDraft, draft_id, "draft")
    agency = get_or_404(Agency, agency_id, "agency")
    _require_text_overrides(placeholder_overrides)
    chosen = channel or agency.filing_method
    hold_reason = (hold_reason or "").strip() or _cannot_go_out(agency, chosen)
    return LetterRecipient.objects.create(
        draft_id=draft_id,
        agency_id=agency_id,
        agency_contact_id=agency_contact_id,
        channel=chosen or UNDECIDED_CHANNEL,
        status=LetterRecipient.HELD if hold_reason else LetterRecipient.PENDING,
        hold_reason=hold_reason,
        placeholder_overrides=placeholder_overrides,
    )


@transaction.atomic
def update_recipient(recipient_id: UUID, **fields: Any) -> LetterRecipient:
    """Edit a recipient. The row is locked first: every rule below reads the row and then writes
    it, and reservation does the same under the same lock, so the two cannot interleave."""
    row = LetterRecipient.objects.select_for_update().filter(pk=recipient_id).first()
    if row is None:
        raise NotFound(f"No recipient {recipient_id}.")
    if row.status == LetterRecipient.FILED:
        raise ServiceError("This recipient has been filed; its letter is already out the door.")
    _require_text_overrides(fields.get("placeholder_overrides"))
    if row.request_id is not None:
        # Reserved: the letter is frozen in its request, and its wording includes who it is
        # addressed to and how it says it was sent. Nothing about it can change from here; what
        # can still happen to it is filing it or skipping it.
        frozen = set(fields) & {"agency_contact_id", "placeholder_overrides", "channel"}
        if frozen:
            raise ServiceError(
                "This recipient's letter is already reserved, so its wording is fixed: "
                f"{sorted(frozen)} can no longer change."
            )
        if "status" in fields and fields["status"] != row.status:
            raise ServiceError(
                "This recipient's letter is already reserved. It can be filed, or skipped with "
                "the skip action (which withdraws its request and needs a note); its request "
                "sitting in 'pending filing' is what holding it means now."
            )
    status = fields.get("status", row.status)
    if status not in SETTABLE_STATUSES:
        raise ServiceError(
            f"A recipient's status is set to one of {SETTABLE_STATUSES}; 'filed' happens only by "
            "being filed, which is what mints its request."
        )
    if "hold_reason" in fields:
        fields["hold_reason"] = (fields["hold_reason"] or "").strip() or None
    # Read hold_reason the way the UPDATE will: an explicit null in `fields` wins over the row.
    reason = fields["hold_reason"] if "hold_reason" in fields else row.hold_reason
    if status == LetterRecipient.HELD and not reason:
        raise ServiceError("A held recipient has to say why it is held.")
    if status == LetterRecipient.PENDING and row.status == LetterRecipient.HELD:
        # Leaving hold. The two reasons a recipient ARRIVES held are facts about the agency, not
        # opinions, so setting the status back does not make them go away: the channel has to
        # have been chosen, and an email letter still needs a verified address.
        undecided = row.hold_reason == NO_FILING_METHOD and "channel" not in fields
        agency = row.agency
        channel = (
            None
            if undecided and agency.filing_method is None
            else (fields.get("channel") or (agency.filing_method if undecided else row.channel))
        )
        blocked = _cannot_go_out(agency, channel)
        if blocked:
            raise ServiceError(blocked)
        fields = {**fields, "channel": channel, "hold_reason": None}
    return update_fields(LetterRecipient, recipient_id, fields, RECIPIENT_FIELDS, "recipient")


def delete_recipient(recipient_id: UUID) -> None:
    get_or_404(LetterRecipient, recipient_id, "recipient")
    with refusing_to_delete_what_was_filed_from("recipient"):
        LetterRecipient.objects.filter(pk=recipient_id).delete()
