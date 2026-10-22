"""The reusable half: shells, shared blocks, templates and their children, and the revisions.

`portal_app`'s search_path is `core, records, public` and does not include `drafting`, so every
db_table here is written schema-qualified the way the records models are.

Every FK uses DO_NOTHING. The SQL already declares what each one does on delete -- cascade for a
template's own children, RESTRICT from revisions, NO ACTION elsewhere -- and letting Postgres run
that keeps one authority for deletion instead of two that can disagree. It also matters for
correctness: a Django-level CASCADE from a revision to its template would try to DELETE a row
`portal_app` has no DELETE grant on, so a refusal that should read as "this template has been
filed from" would arrive as a permission error instead.
"""

import uuid

from django.db import models
from django.utils import timezone

from portal.drafting.models._base import DraftingModel
from portal.records.models import RecordsUser


class LetterShell(DraftingModel):
    """The house furniture -- letterhead, opening, closing. Changes on a different clock from a
    template: the shell is org identity, the template is subject matter."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    letterhead = models.TextField()
    opening_body = models.TextField()
    closing_body = models.TextField()
    is_default = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)
    # drafting_db.0003 (P11): the PDF's signer and page furniture.
    signer_name = models.TextField(null=True, blank=True)
    signer_title = models.TextField(null=True, blank=True)
    font_size_pt = models.IntegerField(null=True, blank=True)
    margin_mm = models.IntegerField(null=True, blank=True)
    # drafting_db.0004 (P19b): the editor's documents beside the text, and the page's look.
    opening_rich = models.JSONField(null=True, blank=True)
    closing_rich = models.JSONField(null=True, blank=True)
    font = models.TextField(null=True, blank=True)
    margin_inches = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    seal_position = models.TextField(null=True, blank=True)
    seal_file = models.TextField(null=True, blank=True)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_shells'

    def __str__(self) -> str:
        return self.name


class SharedBlock(DraftingModel):
    """Reusable prose pulled into many templates -- and resolved to plain text the moment a draft
    is created, so editing one never changes a letter someone is midway through."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    body = models.TextField()
    body_rich = models.JSONField(null=True, blank=True)  # drafting_db.0004 (P19b)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."shared_blocks'

    def __str__(self) -> str:
        return self.name


class SharedBlockCitation(DraftingModel):
    """P19h (`drafting_db.0005`): a shared block's own footnote. Kept with the block, so every
    template that pulls the block in cites the same thing; renumbered after the template's own
    citations when a letter is assembled."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shared_block = models.ForeignKey(
        SharedBlock,
        on_delete=models.DO_NOTHING,
        db_column="shared_block_id",
        related_name="citations",
    )
    marker = models.TextField()
    citation_text = models.TextField()
    sort_order = models.IntegerField(default=0)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."shared_block_citations'

    def __str__(self) -> str:
        return f"{self.marker} {self.citation_text[:60]}"


class LetterShellBlock(DraftingModel):
    """P19h (`drafting_db.0005`): a shared block a letterhead adds to every letter started on
    it. Join row with a composite key, like `LetterTemplateBlock`."""

    pk = models.CompositePrimaryKey("shell", "shared_block")
    shell = models.ForeignKey(
        LetterShell,
        on_delete=models.DO_NOTHING,
        db_column="shell_id",
        related_name="shell_blocks",
    )
    shared_block = models.ForeignKey(
        SharedBlock,
        on_delete=models.DO_NOTHING,
        db_column="shared_block_id",
        related_name="shell_links",
    )
    sort_order = models.IntegerField(default=0)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_shell_blocks'


class LetterTemplate(DraftingModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    jurisdiction = models.TextField(null=True, blank=True)
    legal_basis = models.TextField(null=True, blank=True)
    shell = models.ForeignKey(
        LetterShell,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="shell_id",
        related_name="templates",
    )
    notes = models.TextField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
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
    # drafting_db.0006 (P21c): the organisation template in the drafting tool this row mirrors.
    fork_template_id = models.UUIDField(null=True, blank=True, unique=True)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_templates'

    def __str__(self) -> str:
        return self.name


class LetterTemplateItem(DraftingModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        LetterTemplate,
        on_delete=models.DO_NOTHING,
        db_column="template_id",
        related_name="items",
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
    # The rules-package provision this ask has its own clock under (drafting_db.0002). A label,
    # carried to records.request_items.provision at reservation; never a number.
    provision = models.TextField(null=True, blank=True)
    # drafting_db.0004 (P19b): a prose paragraph sits among the items, numbered by nothing; the
    # editor's document is kept beside the plain text.
    kind = models.TextField(default="item")
    body_rich = models.JSONField(null=True, blank=True)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_template_items'

    def __str__(self) -> str:
        return f"{self.item_number}: {self.body[:60]}"


class LetterTemplateCitation(DraftingModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        LetterTemplate,
        on_delete=models.DO_NOTHING,
        db_column="template_id",
        related_name="citations",
    )
    item = models.ForeignKey(
        LetterTemplateItem,
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
        db_table = 'drafting"."letter_template_citations'

    def __str__(self) -> str:
        return f"{self.marker} {self.citation_text[:60]}"


class LetterTemplateBlock(DraftingModel):
    """Join row. The table's real key is (template_id, shared_block_id), which Django can only
    name through CompositePrimaryKey -- a virtual field, so it stays out of `concrete_fields`
    and the column-set drift test is unaffected by it."""

    pk = models.CompositePrimaryKey("template", "shared_block")
    template = models.ForeignKey(
        LetterTemplate,
        on_delete=models.DO_NOTHING,
        db_column="template_id",
        related_name="template_blocks",
    )
    shared_block = models.ForeignKey(
        SharedBlock,
        on_delete=models.DO_NOTHING,
        db_column="shared_block_id",
        related_name="template_blocks",
    )
    sort_order = models.IntegerField(default=0)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_template_blocks'


class LetterTemplateRevision(DraftingModel):
    """Write-once history. Minted when a template is SAVED and when a letter is STARTED from one
    (drafting_db.0002; DECISIONS, "Design v2"), never by filing. `content` is the whole frozen
    structure, so the row stays readable years later without joining anything that has since
    changed. `created_by_user` and `summary` say who saved it and what changed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        LetterTemplate,
        on_delete=models.DO_NOTHING,
        db_column="template_id",
        related_name="revisions",
    )
    revision = models.IntegerField()
    content = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by_user = models.ForeignKey(
        RecordsUser,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column="created_by_user_id",
        related_name="+",
    )
    summary = models.TextField(null=True, blank=True)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."letter_template_revisions'
        ordering = ["-revision"]

    def __str__(self) -> str:
        return f"rev {self.revision}"
