"""Request/response shapes for the drafting API. In/Out split, same as the records API's.

Nothing here echoes a database error. `RecipientOutcome.reason` is the service layer's own text
or a fixed constant -- never `str(IntegrityError)`, whose DETAIL line names every column of the
failing row.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Schema
from pydantic import field_validator

# ---- shells and shared blocks ----------------------------------------------


class ShellIn(Schema):
    name: str
    letterhead: str
    opening_body: str
    closing_body: str
    is_default: bool = False
    block_ids: list[UUID] = []  # P19h: the blocks this letterhead adds to every letter


class ShellPatchIn(Schema):
    block_ids: list[UUID] | None = None  # P19h
    name: str | None = None
    letterhead: str | None = None
    opening_body: str | None = None
    closing_body: str | None = None
    is_default: bool | None = None
    signer_name: str | None = None
    signer_title: str | None = None
    font_size_pt: int | None = None
    margin_mm: int | None = None
    # P19b: the editor's documents and the page's look.
    opening_rich: dict | None = None
    closing_rich: dict | None = None
    font: str | None = None
    margin_inches: float | None = None
    seal_position: str | None = None


class ShellOut(Schema):
    id: UUID
    name: str
    letterhead: str
    opening_body: str
    closing_body: str
    is_default: bool
    archived_at: datetime | None
    # drafting_db.0003 (P11): the PDF's signer and page furniture.
    signer_name: str | None = None
    signer_title: str | None = None
    font_size_pt: int | None = None
    margin_mm: int | None = None
    opening_rich: dict | None = None
    closing_rich: dict | None = None
    font: str | None = None
    margin_inches: float | None = None
    seal_position: str | None = None
    has_seal: bool = False
    block_ids: list[UUID] = []  # P19h: the blocks this letterhead adds


class BlockIn(Schema):
    name: str
    body: str
    body_rich: dict | None = None  # P19b: the editor's document beside the text


class BlockPatchIn(Schema):
    name: str | None = None
    body: str | None = None
    body_rich: dict | None = None


class BlockCitationIn(Schema):
    marker: str
    citation_text: str
    sort_order: int = 0


class BlockCitationOut(Schema):
    id: UUID
    marker: str
    citation_text: str
    sort_order: int


class BlockOut(Schema):
    id: UUID
    name: str
    body: str
    body_rich: dict | None = None
    archived_at: datetime | None
    updated_at: datetime | None = None
    # How many templates include it. Counted when read; nothing is stored.
    used_in: int = 0
    # P19h: how many letterheads add it, and its own footnotes.
    used_in_letterheads: int = 0
    citations: list[BlockCitationOut] = []


# ---- templates -------------------------------------------------------------


class TemplateIn(Schema):
    name: str
    jurisdiction: str | None = None
    legal_basis: str | None = None
    shell_id: UUID | None = None
    notes: str | None = None


class TemplatePatchIn(Schema):
    name: str | None = None
    jurisdiction: str | None = None
    legal_basis: str | None = None
    shell_id: UUID | None = None
    notes: str | None = None


class ItemIn(Schema):
    item_number: str
    body: str
    parent_item_id: UUID | None = None
    sort_order: int = 0
    # The rules-package provision this ask has its own clock under. A label, never a number.
    provision: str | None = None
    # P19b: a prose paragraph among the items, and the editor's document beside the text.
    kind: str = "item"
    body_rich: dict | None = None


class ItemPatchIn(Schema):
    item_number: str | None = None
    body: str | None = None
    parent_item_id: UUID | None = None
    sort_order: int | None = None
    provision: str | None = None
    kind: str | None = None
    body_rich: dict | None = None


class ItemOut(Schema):
    id: UUID
    item_number: str
    body: str
    parent_item_id: UUID | None
    sort_order: int
    provision: str | None = None
    kind: str = "item"
    body_rich: dict | None = None


# ---- P19b: the letter as the editor sees and saves it -------------------------------------


class PieceIn(Schema):
    id: UUID | None = None
    kind: str = "item"
    item_number: str = ""
    parent_item_id: UUID | None = None
    # For a new sub-item whose parent is new too: the parent's position in `pieces`.
    parent_index: int | None = None
    provision: str | None = None
    body: str = ""
    body_rich: dict | None = None


class PieceOut(Schema):
    id: UUID
    kind: str
    item_number: str
    parent_item_id: UUID | None
    sort_order: int
    provision: str | None
    body: str
    body_rich: dict | None


class LetterCitationIn(Schema):
    id: UUID | None = None
    marker: str
    citation_text: str
    item_id: UUID | None = None


class LetterCitationOut(Schema):
    id: UUID
    marker: str
    citation_text: str
    item_id: UUID | None
    sort_order: int


class LetterShellOut(Schema):
    id: UUID | None
    name: str
    letterhead: str
    opening_body: str
    opening_rich: dict | None
    closing_body: str
    closing_rich: dict | None
    font: str | None
    font_size_pt: int | None
    margin_inches: float | None
    seal_position: str | None
    has_seal: bool
    signer_name: str | None
    signer_title: str | None


class LetterBlockOut(Schema):
    id: UUID
    name: str
    body: str
    body_rich: dict | None
    sort_order: int


class LetterOut(Schema):
    subject: str
    shell: LetterShellOut | None
    pieces: list[PieceOut]
    citations: list[LetterCitationOut]
    blocks: list[LetterBlockOut]


class LetterSaveIn(Schema):
    """What the editor holds. A list given replaces the rows of that kind; one left out is kept."""

    subject: str | None = None
    pieces: list[PieceIn] | None = None
    citations: list[LetterCitationIn] | None = None


class CitationIn(Schema):
    marker: str
    citation_text: str
    item_id: UUID | None = None
    sort_order: int = 0


class CitationOut(Schema):
    id: UUID
    marker: str
    citation_text: str
    item_id: UUID | None
    sort_order: int


class TemplateBlocksIn(Schema):
    """The shared blocks a template pulls in. Blocks and items share one `sort_order` sequence,
    so `sort_orders` (one per block, same order) places each block among the items: before the
    item with that number, or after them all. Left out, the blocks take 0, 1, 2... as given."""

    block_ids: list[UUID]
    sort_orders: list[int] | None = None


class SaveTemplateIn(Schema):
    """The editor's Save. `summary` is the line on what changed."""

    summary: str


class TemplateOut(Schema):
    id: UUID
    name: str
    jurisdiction: str | None
    legal_basis: str | None
    shell_id: UUID | None
    notes: str | None
    archived_at: datetime | None
    created_at: datetime
    # How many filed letters came from any revision of it. Counted when read.
    filed_count: int = 0
    # P19e: the picker's "Contents" and "Last used" columns.
    item_count: int = 0
    last_used_at: datetime | None = None
    # P19h: the templates list's "Revision N".
    latest_revision: int | None = None
    # P21c: the organisation template in the drafting tool this row mirrors (its words are there).
    fork_template_id: UUID | None = None


class TemplateDetailOut(TemplateOut):
    items: list[ItemOut]
    citations: list[CitationOut]
    block_ids: list[UUID]
    # Where each block of `block_ids` sits in the sequence it shares with the items.
    block_sort_orders: list[int]
    # False when the template has changed since its newest revision: there is something to save.
    saved: bool


class RevisionOut(Schema):
    id: UUID
    template_id: UUID
    revision: int
    content: dict[str, Any]
    created_at: datetime
    # Who saved it and their line on what changed. Null on revisions from before P10.
    created_by_user_id: UUID | None = None
    created_by_name: str | None = None
    summary: str | None = None
    # Filed letters that point at this revision. Counted, never stored.
    filed_count: int = 0


# ---- drafts ----------------------------------------------------------------


class DraftIn(Schema):
    subject: str | None = None
    from_template_id: UUID | None = None
    from_request_id: UUID | None = None
    shell_id: UUID | None = None
    # None means "not specified" -- the service layer then inherits the source request's own
    # sensitivity, or falls back to DEFAULT_SENSITIVITY. A default of "internal" here would make
    # every draft explicitly ask for 'internal', which is exactly the bug this schema exists to
    # not reintroduce (F2: a draft started from a privileged request silently became internal).
    sensitivity: str | None = None
    # P23b (097 "File again?" · Why): why a request is being filed again. Only with
    # `from_request_id`; written as a note on that request, by the person starting the draft.
    refile_reason: str | None = None


class DraftPatchIn(Schema):
    subject: str | None = None
    shell_id: UUID | None = None
    sensitivity: str | None = None

    @field_validator("subject")
    @classmethod
    def _subject_not_explicitly_null(cls, value: str | None) -> str | None:
        """`letter_drafts.subject` is `NOT NULL`; the endpoint calls `model_dump(exclude_unset=
        True)`, so a caller who OMITS `subject` never reaches here (pydantic does not validate a
        field's own default), but one who sends `"subject": null` does -- and used to sail
        through as an unset value the ORM's `.update()` would then write as an actual NULL,
        surfacing as a bare `IntegrityError` instead of a clear 422. Reject it here, at the
        schema, before it becomes a database refusal."""
        if value is None:
            raise ValueError("subject cannot be null; omit it to leave it unchanged.")
        return value


class DraftOut(Schema):
    id: UUID
    subject: str
    shell_id: UUID | None
    source_template_id: UUID | None
    source_request_id: UUID | None
    # The template exactly as it stood when this letter was started.
    source_revision_id: UUID | None = None
    sensitivity: str
    created_at: datetime
    # P21c: the wave in the drafting tool this draft is (stage 2 frames its master), the master's
    # version as last heard of, and its open review comments (the Suggestions badge).
    fork_wave_id: UUID | None = None
    fork_master_document_id: UUID | None = None
    fork_master_version_number: int | None = None
    fork_suggestions: int = 0


class RequestDraftOut(Schema):
    """The draft a reserved request was made from, and the letter (recipient) that reserved it:
    where the request page's "Review & file" (076) goes."""

    draft_id: UUID
    recipient_id: UUID


class RecipientIn(Schema):
    agency_id: UUID
    # Left out, it is the agency's own filing method.
    channel: str | None = None
    agency_contact_id: UUID | None = None
    hold_reason: str | None = None
    placeholder_overrides: dict[str, Any] | None = None


class RecipientPatchIn(Schema):
    channel: str | None = None
    status: str | None = None
    hold_reason: str | None = None
    agency_contact_id: UUID | None = None
    placeholder_overrides: dict[str, Any] | None = None


class RecipientOut(Schema):
    id: UUID
    draft_id: UUID
    agency_id: UUID
    agency_contact_id: UUID | None
    channel: str
    status: str
    hold_reason: str | None
    placeholder_overrides: dict[str, Any] | None
    request_id: UUID | None
    template_revision_id: UUID | None
    filed_at: datetime | None
    # Its request, once reserved: enough to show "REQ-... reserved", "Pending filing", "Withdrawn".
    tracking_number: str | None = None
    request_status: str | None = None
    # P19e: what the PDF is called when downloaded or uploaded to a form.
    letter_filename: str = ""
    request_filed_at: datetime | None = None
    # P21c: this agency's letter in the drafting tool (made at reserve), its version as last heard
    # of, and its open review comments.
    fork_document_id: UUID | None = None
    fork_version_number: int | None = None
    fork_suggestions: int = 0


class ReserveIn(Schema):
    requester_user_id: UUID | None = None


class FileRecipientIn(Schema):
    """Mark one letter as sent. `filed_at` defaults to now, `channel` to the recipient's own."""

    filed_at: datetime | None = None
    channel: str | None = None
    agency_reference_number: str | None = None
    request_provision: str | None = None
    requester_user_id: UUID | None = None
    # P19e: the carrier's tracking number when the letter went by post; kept on the filing event.
    tracking_number: str | None = None


class SkipIn(Schema):
    """`note` is required once the recipient is reserved: skipping then withdraws its request."""

    note: str | None = None


class DraftDetailOut(DraftOut):
    items: list[ItemOut]
    citations: list[CitationOut]
    recipients: list[RecipientOut]


class PreviewOut(Schema):
    # Null for "Preview as", which runs before the agency is a recipient.
    recipient_id: UUID | None = None
    text: str
    # Placeholders that would still be standing in the letter if it went out now.
    unresolved: list[str]


class FileDraftIn(Schema):
    filed_at: datetime | None = None
    requester_user_id: UUID | None = None
    recipient_ids: list[UUID] | None = None
    request_provision: str | None = None


class OutcomeOut(Schema):
    recipient_id: UUID
    agency_id: UUID | None
    filed: bool
    request_id: UUID | None
    tracking_number: str | None
    reason: str | None


class FileDraftOut(Schema):
    draft_id: UUID
    revision_id: UUID | None
    revision: int | None
    filed_count: int
    outcomes: list[OutcomeOut]


# ---- P21c: the drafting tool ---------------------------------------------------------------


class ForkStatusOut(Schema):
    """Whether letters are written in the drafting tool now, and where its pages are (for the
    frames and their message origin checks). The addresses only while it is in use."""

    in_use: bool
    web_url: str = ""
    web_origin: str = ""


class ForkTemplateIn(Schema):
    """`sclu-templates/1`'s "template" message, as the Templates frame posted it."""

    template_id: UUID
    name: str
    archived: bool = False
    event: str = "saved"


class ForkMergeFailureOut(Schema):
    recipient_id: UUID
    reason: str


class ForkMergeOut(Schema):
    made: list[UUID]
    failed: list[ForkMergeFailureOut]


class ForkValuesOut(Schema):
    """One agency's merge values, for stage 2's "Preview as" before its letter exists
    (`sclu-letter/1` "preview")."""

    agency_id: UUID
    label: str
    values: dict[str, str]


class ForkLetterOut(Schema):
    """A request's letter in the drafting tool, for its Files tab."""

    recipient_id: UUID
    draft_id: UUID
    document_id: UUID
    version_number: int | None
    filename: str
    revised_at: datetime | None
    open_suggestions: int
    verification: dict[str, Any] | None
    filed: bool


class ForkLettersOut(Schema):
    in_use: bool
    letters: list[ForkLetterOut]


class WebhookAckOut(Schema):
    status: str
