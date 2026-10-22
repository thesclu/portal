"""What each task may be given (DECISIONS 27): a written contract per task, assembled by code.

A context is a dict of named parts. `summary` is what the run log keeps of it: each part's
name, its size and a short hash, never its text (unless `ai.keep_prompts` is on, in which case
the prompt itself is kept on the run). A privileged request or draft is never put in a context
while `ai.exclude_privileged` is on: `Refused` is raised before anything is assembled.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from portal.core.services.settings import get_setting
from portal.drafting import render
from portal.drafting.models import LetterDraft, LetterRecipient
from portal.drafting.services.common import letter_of_draft
from portal.records.models import Request, Source
from portal.records.services import agency_record
from portal.records.services.common import ServiceError

MAX_TEXT = 12000
MAX_EXCERPT = 4000


class Refused(ServiceError):
    """The contract forbids this run (privileged subject, or the assistant is off)."""


@dataclass
class Context:
    task: str
    parts: dict[str, Any] = field(default_factory=dict)

    def put(self, name: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        self.parts[name] = value

    def text(self) -> str:
        """The parts as the model reads them: named, in order, as JSON for structured parts."""
        chunks = []
        for name, value in self.parts.items():
            body = value if isinstance(value, str) else json.dumps(value, indent=1, default=str)
            chunks.append(f"### {name}\n{body}")
        return "\n\n".join(chunks)

    def summary(self) -> dict[str, Any]:
        out = {}
        for name, value in self.parts.items():
            body = value if isinstance(value, str) else json.dumps(value, default=str)
            out[name] = {
                "chars": len(body),
                "sha256": hashlib.sha256(body.encode()).hexdigest()[:12],
            }
        return out


def exclude_privileged() -> bool:
    return bool(get_setting("ai.exclude_privileged", True))


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …"


def letter_context(draft: LetterDraft, *, question: str | None = None) -> Context:
    """Drafting and critique: the letter as it stands with its placeholders standing, its
    pieces by id, its citations, the template's lineage, the agencies it is going to and their
    response record, and the question. Notes are never included."""
    if exclude_privileged() and draft.sensitivity == "privileged":
        raise Refused("This draft is privileged; the assistant is not given privileged drafts.")
    ctx = Context("letter")
    letter = letter_of_draft(draft)
    ctx.put("subject", draft.subject)
    if draft.source_template_id:
        template = draft.source_template
        ctx.put(
            "template",
            {
                "name": template.name,
                "jurisdiction": template.jurisdiction,
                "legal_basis": template.legal_basis,
            },
        )
    ctx.put("letter_text", _clip(render.render_text(letter), MAX_TEXT))
    from portal.drafting.services import letter as letter_service

    ctx.put(
        "pieces",
        [
            {
                "id": str(p.id),
                "kind": p.kind,
                "number": p.item_number,
                "text": _clip(p.body, 2000),
            }
            for p in letter_service.draft_view(draft).pieces
        ],
    )
    ctx.put(
        "citations",
        [{"marker": c.marker, "text": c.citation_text} for c in letter.citations],
    )
    agencies = []
    for recipient in LetterRecipient.objects.filter(draft_id=draft.pk).select_related("agency"):
        agency = recipient.agency
        record = agency_record.response_record(
            agency,
            list(Request.objects.filter(agency_id=agency.pk).values_list("pk", flat=True)),
        )
        agencies.append(
            {
                "name": agency.name,
                "jurisdiction": agency.jurisdiction,
                "records_law": agency.records_law,
                "files_by": recipient.channel,
                "response_record": {
                    "median_days_to_determination": record.median_days_to_determination,
                    "determinations_on_time": record.determinations_on_time,
                    "determinations_total": record.determinations_total,
                    "extensions_taken": record.extensions_taken,
                },
            }
        )
    ctx.put("agencies", agencies)
    if question:
        ctx.put("question", _clip(question, 2000))
    return ctx


def inbound_context(source: Source) -> Context | None:
    """The constant check: what came in, and the request it landed on. None when the request is
    privileged and the contract excludes it, or when the message landed nowhere."""
    from portal.records.models import Event

    event = (
        Event.objects.filter(source_id=source.pk, voided_at__isnull=True)
        .select_related("request__agency")
        .order_by("occurred_at")
        .first()
    )
    if event is None:
        return None
    request = event.request
    if exclude_privileged() and request.sensitivity == "privileged":
        return None
    meta = source.raw_metadata or {}
    ctx = Context("inbound")
    ctx.put(
        "message",
        {
            "from": meta.get("from") or meta.get("From") or "",
            "subject": meta.get("subject") or meta.get("Subject") or "",
            "excerpt": _clip(event.display_body or "", MAX_EXCERPT),
        },
    )
    ctx.put(
        "request",
        {
            "tracking_number": request.tracking_number,
            "subject": request.subject,
            "agency": request.agency.name,
            "agency_email": request.agency.default_request_email or "",
            "items": [
                i.description[:300]
                for i in request.items.order_by("sort_order", "item_number")[:12]
            ],
            "status": request.current_status_id,
        },
    )
    return ctx


def pick_context(
    question: str, templates: list[dict[str, Any]], past: list[dict[str, Any]]
) -> Context:
    ctx = Context("pick")
    ctx.put("question", _clip(question, 2000))
    ctx.put("master_letters", templates)
    ctx.put("past_requests", past)
    return ctx
