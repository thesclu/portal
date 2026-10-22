"""The working half: a draft, its items and citations, and the recipients that are the seam.

Draft items and citations are copies of a template's, and then completely independent. There is
deliberately no column linking one back to the template item it came from: without that column
nothing can ever be tempted to reconcile them, which is how "filing-time edits never flow back to
the template" is enforced -- by absence, not by a rule someone has to remember.
"""

import uuid

from django.db import models
from django.utils import timezone

from portal.drafting.models._base import DraftingModel
from portal.drafting.models.library import LetterShell, LetterTemplate, LetterTemplateRevision
from portal.records.models import Agency, AgencyContact, RecordsUser, Request


class LetterDraft(DraftingModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.TextField()
    shell = models.ForeignKey(
        LetterShell,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="shell_id",
        related_name="drafts",
    )
    # Provenance, and at most one of the two (letter_drafts_one_source). source_template_id is
    # also what the revision is stamped from at filing.
    source_template = models.ForeignKey(
        LetterTemplate,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="source_template_id",
        related_name="drafts",
    )
    source_request = models.ForeignKey(
        Request,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="source_request_id",
        related_name="+",
    )
    sensitivity = models.TextField(default="internal")
    # The template exactly as it stood when this letter was started (drafting_db.0002). Copied to
    # each recipient's template_revision_id at reservation; filing mints nothing.
    source_revision = models.ForeignKey(
        LetterTemplateRevision,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="source_revision_id",
        related_name="drafts",
    )
    created_by_user = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="created_by_user_id",
        related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)
    # drafting_db.0006 (P21c): the wave in the drafting tool this draft is, its folder, and its
    # master letter with the version the portal last heard of. Set together, at stage 1's "Use",
    # only while the drafting tool is in use; a draft without them is the portal editor's.
    fork_wave_id = models.UUIDField(null=True, blank=True, unique=True)
    fork_folder_id = models.UUIDField(null=True, blank=True)
    fork_master_document_id = models.UUIDField(null=True, blank=True, unique=True)
    fork_master_version_id = models.UUIDField(null=True, blank=True)
    fork_master_version_number = models.IntegerField(null=True, blank=True)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_drafts'

    def __str__(self) -> str:
        return self.subject


class LetterDraftItem(DraftingModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.ForeignKey(
        LetterDraft, on_delete=models.DO_NOTHING, db_column="draft_id", related_name="items"
    )
    item_number = models.TextField()
    parent_item = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="parent_item_id",
        related_name="children",
    )
    body = models.TextField()
    sort_order = models.IntegerField(default=0)
    # See LetterTemplateItem.provision. Copied from the template at draft creation, editable here.
    provision = models.TextField(null=True, blank=True)
    # drafting_db.0004 (P19b): a prose paragraph sits among the items, numbered by nothing; the
    # editor's document is kept beside the plain text.
    kind = models.TextField(default="item")
    body_rich = models.JSONField(null=True, blank=True)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_draft_items'

    def __str__(self) -> str:
        return f"{self.item_number}: {self.body[:60]}"


class LetterDraftCitation(DraftingModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.ForeignKey(
        LetterDraft, on_delete=models.DO_NOTHING, db_column="draft_id", related_name="citations"
    )
    item = models.ForeignKey(
        LetterDraftItem,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="item_id",
        related_name="citations",
    )
    marker = models.TextField()
    citation_text = models.TextField()
    sort_order = models.IntegerField(default=0)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_draft_citations'

    def __str__(self) -> str:
        return f"{self.marker} {self.citation_text[:60]}"


class LetterRecipient(DraftingModel):
    """One agency this draft is going to -- and the only place the request -> draft -> revision
    link is recorded, because records has no back-pointer by design."""

    PENDING = "pending"
    HELD = "held"
    FILED = "filed"
    SKIPPED = "skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.ForeignKey(
        LetterDraft, on_delete=models.DO_NOTHING, db_column="draft_id", related_name="recipients"
    )
    agency = models.ForeignKey(
        Agency, on_delete=models.DO_NOTHING, db_column="agency_id", related_name="+"
    )
    agency_contact = models.ForeignKey(
        AgencyContact,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="agency_contact_id",
        related_name="+",
    )
    channel = models.TextField()
    status = models.TextField(default=PENDING)
    hold_reason = models.TextField(null=True, blank=True)
    # Per-agency exceptions only. The defaults resolve from the agency directory at render time.
    placeholder_overrides = models.JSONField(null=True, blank=True)
    request = models.ForeignKey(
        Request,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="request_id",
        related_name="+",
    )
    template_revision = models.ForeignKey(
        LetterTemplateRevision,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="template_revision_id",
        related_name="recipients",
    )
    filed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    # drafting_db.0006 (P21c): this agency's letter in the drafting tool (made by the fork's merge
    # at reserve) and the version the portal last heard of; and the SHA-256 of the .docx the
    # reserved request's words were last written from.
    fork_document_id = models.UUIDField(null=True, blank=True, unique=True)
    fork_version_id = models.UUIDField(null=True, blank=True)
    fork_version_number = models.IntegerField(null=True, blank=True)
    fork_text_sha256 = models.TextField(null=True, blank=True)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_recipients'

    def __str__(self) -> str:
        return f"{self.agency_id} ({self.status})"
