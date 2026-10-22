from portal.drafting.models._base import DraftingModel
from portal.drafting.models.drafts import (
    LetterDraft,
    LetterDraftCitation,
    LetterDraftItem,
    LetterRecipient,
)
from portal.drafting.models.fork import ForkEvent
from portal.drafting.models.library import (
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

# Hand-maintained, and pinned at 14 by the drift test so a model nobody adds here stays visible
# instead of being silently uncovered.
DRAFTING_MODELS = [
    LetterShell,
    SharedBlock,
    LetterTemplate,
    LetterTemplateItem,
    LetterTemplateCitation,
    LetterTemplateBlock,
    LetterTemplateRevision,
    LetterDraft,
    LetterDraftItem,
    LetterDraftCitation,
    LetterRecipient,
    SharedBlockCitation,  # P19h
    LetterShellBlock,  # P19h
    ForkEvent,  # P21c
]

__all__ = [
    "DRAFTING_MODELS",
    "DraftingModel",
    "ForkEvent",
    "LetterDraft",
    "LetterDraftCitation",
    "LetterDraftItem",
    "LetterRecipient",
    "LetterShellBlock",
    "LetterShell",
    "LetterTemplate",
    "LetterTemplateBlock",
    "LetterTemplateCitation",
    "LetterTemplateItem",
    "LetterTemplateRevision",
    "SharedBlock",
    "SharedBlockCitation",
]
