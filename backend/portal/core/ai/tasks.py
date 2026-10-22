"""The assistant's tasks (P19d): what each asks for, on which tier, and what it does with the
answer. The system instructions say what the assistant is: a reader that proposes; a person
decides. No instruction here supplies legal content; the model is asked to cite only what it
was given or what its tools returned, and to say when it is unsure.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.utils import timezone

from portal.core.ai import contracts, service
from portal.core.ai.executors import DRAFTING, GENERAL
from portal.core.models import AiRun, AiSuggestion, User
from portal.drafting.models import LetterDraft, LetterTemplate
from portal.records.actors import Actor
from portal.records.models import Request, Source
from portal.records.services import sources as sources_service

SUGGESTION_KINDS = ("scope", "likely_fight", "house_style", "citation", "wording", "other")

ASSISTANT_SYSTEM = (
    "You help a small civil-liberties organisation draft public-records requests. You read what "
    "you are given and propose; a person decides. Be specific and brief. Cite only authorities "
    "that appear in what you were given or that your tools returned; if you are not sure, say "
    "so rather than guess. Never claim a deadline or a legal rule that was not given to you."
)
CHECK_SYSTEM = (
    "You sort incoming mail for a records-request team. Judge only from what you are given. "
    "Answer in JSON exactly as asked."
)

SUGGEST_SCHEMA = {
    "suggestions": [
        {
            "piece_id": "the id of the piece this is about, or null",
            "kind": "one of scope, likely_fight, house_style, citation, wording, other",
            "title": "a short title",
            "text": "what you noticed and why it matters, in two sentences at most",
            "replacement_text": "the full replacement wording for that piece, or an empty string",
        }
    ]
}
ASK_SCHEMA = {"answer": "your answer in plain words", **SUGGEST_SCHEMA}
PICK_SCHEMA = {
    "matches": [
        {
            "kind": "master_letter or past_request",
            "id": "the id as given",
            "name": "its name",
            "why": "one sentence",
        }
    ],
    "proposed_items": ["an item to add, in the letter's own wording"],
    "note": "anything the person should know, or an empty string",
}
INBOUND_SCHEMA = {
    "relates": "yes, no or unsure: does this message concern the request it landed on",
    "confidence": "a number from 0 to 1",
    "kind": (
        "one of acknowledgment, determination, extension, fee_notice, production, "
        "clarification, other"
    ),
    "agency_reference": "the agency's own reference number if one appears, else an empty string",
    "mentioned_dates": ["dates the message names that may matter, as written"],
    "reason": "one sentence",
}


@dataclass(frozen=True)
class Answer:
    run_id: UUID
    answer: str
    suggestions: list[AiSuggestion]


def _save_suggestions(
    run: AiRun, draft_id: UUID, data: dict[str, Any] | None
) -> list[AiSuggestion]:
    rows: list[AiSuggestion] = []
    for raw in (data or {}).get("suggestions") or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "other")
        if kind not in SUGGESTION_KINDS:
            kind = "other"
        piece = raw.get("piece_id")
        try:
            item_id = UUID(str(piece)) if piece else None
        except ValueError:
            item_id = None
        title = str(raw.get("title") or "").strip()[:200]
        text = str(raw.get("text") or "").strip()
        if not title and not text:
            continue
        rows.append(
            AiSuggestion.objects.create(
                run=run,
                draft_id=draft_id,
                item_id=item_id,
                kind=kind,
                title=title or kind.replace("_", " ").capitalize(),
                text=text,
                replacement_text=str(raw.get("replacement_text") or "").strip(),
            )
        )
    return rows


def suggest_letter(user: User, draft: LetterDraft) -> Answer:
    """Cards about the letter as it stands. Open cards from earlier runs are kept."""
    context = contracts.letter_context(draft)
    prompt = (
        "Read the letter. Propose at most six improvements as suggestions: scope that is too "
        "narrow or too broad for what the subject suggests, items an agency of this kind is "
        "likely to fight and why, house style, citations that are missing or inconsistent with "
        "the ones given, and wording. Each suggestion names the piece it is about by id."
    )
    run, result = service.run(
        "suggest_letter",
        DRAFTING,
        context,
        system=ASSISTANT_SYSTEM,
        prompt=prompt,
        schema=SUGGEST_SCHEMA,
        subject_type="draft",
        subject_id=draft.pk,
        requested_by=user,
    )
    return Answer(run.pk, "", _save_suggestions(run, draft.pk, result.data))


def ask_letter(user: User, draft: LetterDraft, question: str) -> Answer:
    context = contracts.letter_context(draft, question=question)
    prompt = (
        "Answer the question about this letter in plain words. If answering means changing the "
        "letter, add suggestions naming the piece by id; otherwise leave the list empty."
    )
    run, result = service.run(
        "ask_letter",
        DRAFTING,
        context,
        system=ASSISTANT_SYSTEM,
        prompt=prompt,
        schema=ASK_SCHEMA,
        subject_type="draft",
        subject_id=draft.pk,
        requested_by=user,
    )
    answer = str((result.data or {}).get("answer") or result.text or "").strip()
    return Answer(run.pk, answer, _save_suggestions(run, draft.pk, result.data))


def pick_letter(user: User, question: str) -> dict[str, Any]:
    """Stage 1's "Ask the assistant": which master letter or past request fits, and what to
    add."""
    from portal.core.services.identity import records_role_of
    from portal.drafting.services.library import list_templates
    from portal.records.services import requests as requests_service
    from portal.records.services.access import Viewer, scope

    templates = [
        {
            "id": str(t.pk),
            "name": t.name,
            "jurisdiction": t.jurisdiction,
            "legal_basis": t.legal_basis,
            "items": [i.body[:200] for i in t.items.order_by("sort_order")[:10]],
        }
        for t in list_templates()
    ]
    viewer = Viewer(user_id=user.pk, role=records_role_of(user))
    recent = scope(viewer, requests_service.docket(sort="newest"))[:40]
    past = [
        {
            "id": str(r.pk),
            "tracking_number": r.tracking_number,
            "subject": r.subject,
            "agency": r.agency.name,
            "status": r.current_status_id,
        }
        for r in recent
    ]
    context = contracts.pick_context(question, templates, past)
    prompt = (
        "The person says what they want to find out. Say which master letters or past requests "
        "fit best (at most three), and propose items to add, in the letter's own style. Do not "
        "invent citations."
    )
    run, result = service.run(
        "pick_letter",
        DRAFTING,
        context,
        system=ASSISTANT_SYSTEM,
        prompt=prompt,
        schema=PICK_SCHEMA,
        subject_type="question",
        requested_by=user,
    )
    data = result.data or {}
    return {
        "run_id": str(run.pk),
        "matches": [
            {
                "kind": str(m.get("kind") or ""),
                "id": str(m.get("id") or ""),
                "name": str(m.get("name") or ""),
                "why": str(m.get("why") or ""),
            }
            for m in data.get("matches") or []
            if isinstance(m, dict) and m.get("id")
        ][:3],
        "proposed_items": [str(i) for i in data.get("proposed_items") or []][:8],
        "note": str(data.get("note") or ""),
    }


ASSISTANT_ACTOR = "assistant"


def check_inbound(source_id: UUID) -> AiRun | None:
    """The constant check on mail that came in: does it relate to the request it landed on; what
    kind is it; the agency's reference. A clear mismatch flags the message for a person; the
    rest is kept on the run for the Inbox to show. Never moves a status."""
    source = Source.objects.filter(pk=source_id).first()
    if source is None:
        return None
    context = contracts.inbound_context(source)
    if context is None:
        return None
    prompt = (
        "Decide whether the message concerns the request it was filed under, what kind of "
        "message it is, and pull out the agency's reference number and any dates it names."
    )
    run, result = service.run(
        "check_inbound",
        GENERAL,
        context,
        system=CHECK_SYSTEM,
        prompt=prompt,
        schema=INBOUND_SCHEMA,
        subject_type="source",
        subject_id=source.pk,
    )
    data = result.data or {}
    relates = str(data.get("relates") or "").lower()
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if relates == "no" and confidence >= 0.6 and not source.flagged_for_review:
        reason = str(data.get("reason") or "").strip()[:300]
        sources_service.flag_source(
            Actor.for_system(ASSISTANT_ACTOR),
            source.pk,
            "The assistant thinks this message may not belong to the request it landed on."
            + (f" {reason}" if reason else ""),
            reason="assistant",
        )
    return run


def decide_suggestion(user: User, suggestion_id: UUID, *, applied: bool) -> AiSuggestion:
    from portal.core.services import activity
    from portal.records.services.common import InvalidTransition, NotFound

    row = AiSuggestion.objects.filter(pk=suggestion_id).first()
    if row is None:
        raise NotFound(f"No suggestion {suggestion_id}.")
    if row.status != AiSuggestion.OPEN:
        raise InvalidTransition("This suggestion has already been decided.")
    status = AiSuggestion.APPLIED if applied else AiSuggestion.DISMISSED
    AiSuggestion.objects.filter(pk=suggestion_id).update(
        status=status, decided_by=user, decided_at=timezone.now()
    )
    if applied:
        activity.record(
            Actor.human(user),
            "suggestion_applied",
            f"Applied the assistant's suggestion: {row.title[:80]}",
            subject_type="draft",
            subject_id=row.draft_id,
            data={"suggestion_id": str(row.pk), "kind": row.kind},
        )
    return AiSuggestion.objects.get(pk=suggestion_id)


def open_suggestions(draft_id: UUID) -> list[AiSuggestion]:
    return list(AiSuggestion.objects.filter(draft_id=draft_id, status=AiSuggestion.OPEN))


def decided_suggestions(draft_id: UUID, limit: int = 20) -> list[AiSuggestion]:
    """P19h: the cards a person has applied or dismissed, newest decision first."""
    return list(
        AiSuggestion.objects.filter(draft_id=draft_id)
        .exclude(status=AiSuggestion.OPEN)
        .select_related("decided_by")
        .order_by("-decided_at")[:limit]
    )


def template_of(draft: LetterDraft) -> LetterTemplate | None:
    return draft.source_template if draft.source_template_id else None


def request_of(request_id: UUID) -> Request | None:
    return Request.objects.filter(pk=request_id).first()
