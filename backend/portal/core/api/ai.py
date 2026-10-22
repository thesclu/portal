"""P19d: the assistant's doors. Every draft route is scoped like the draft itself."""

from datetime import datetime
from typing import Any
from uuid import UUID

from django.http import HttpRequest
from django.utils import timezone
from ninja import Query, Router, Schema

from portal.core.ai import checks as checks_service
from portal.core.ai import contracts, service, tasks
from portal.core.ai.executors import DRAFTING, GENERAL, ExecutorError, NotConfigured, provider_for
from portal.core.api.users import require_admin
from portal.core.models import AiRun, DraftNote
from portal.drafting.api.permissions import require_draft_scope
from portal.records.api.permissions import require_team

router = Router(tags=["assistant"])


class AiCheckFixOut(Schema):
    mode: str  # 'word' or 'quotes'
    find: str = ""
    replace: str = ""


class AiCheckOut(Schema):
    kind: str
    title: str
    detail: str
    piece_id: UUID | None
    fix: AiCheckFixOut | None = None  # P19h


class AiSuggestionOut(Schema):
    id: UUID
    kind: str
    title: str
    text: str
    item_id: UUID | None
    replacement_text: str
    status: str
    created_at: datetime
    # P19h: who decided an applied or dismissed card, and when.
    decided_by_name: str | None = None
    decided_at: datetime | None = None


class DraftNoteOut(Schema):
    id: UUID
    author_name: str
    body: str
    created_at: datetime


class AiRailOut(Schema):
    checks: list[AiCheckOut]
    suggestions: list[AiSuggestionOut]
    notes: list[DraftNoteOut]
    assistant: str  # 'on', 'off' or 'privileged'
    decided: list[AiSuggestionOut] = []  # P19h: applied and dismissed, newest first


class AiAnswerOut(Schema):
    status: str  # 'ok', 'off', 'refused', 'failed'
    message: str = ""
    answer: str = ""
    run_id: UUID | None = None
    suggestions: list[AiSuggestionOut] = []


class AiAskIn(Schema):
    question: str


class DraftNoteIn(Schema):
    body: str


class AiPickIn(Schema):
    question: str


class AiPickMatchOut(Schema):
    kind: str  # 'master_letter' or 'past_request'
    id: str
    name: str
    why: str = ""


class AiPickOut(Schema):
    status: str
    message: str = ""
    run_id: UUID | None = None
    matches: list[AiPickMatchOut] = []
    proposed_items: list[str] = []
    note: str = ""


class AiRunOut(Schema):
    id: UUID
    task: str
    tier: str
    provider: str
    model: str
    status: str
    subject_type: str
    subject_id: UUID | None
    requested_by_name: str | None
    context_summary: dict[str, Any]
    tokens_in: int | None
    tokens_out: int | None
    error: str
    created_at: datetime
    finished_at: datetime | None


def _suggestion_out(row) -> AiSuggestionOut:
    return AiSuggestionOut(
        id=row.pk,
        kind=row.kind,
        title=row.title,
        text=row.text,
        item_id=row.item_id,
        replacement_text=row.replacement_text,
        status=row.status,
        created_at=row.created_at,
        decided_by_name=row.decided_by.name if row.decided_by_id else None,
        decided_at=row.decided_at,
    )


def _check_out(check) -> AiCheckOut:
    fix = check.fix
    return AiCheckOut(
        kind=check.kind,
        title=check.title,
        detail=check.detail,
        piece_id=check.piece_id,
        fix=AiCheckFixOut(mode=fix.mode, find=fix.find, replace=fix.replace) if fix else None,
    )


def _assistant_state(draft) -> str:
    if provider_for(DRAFTING) in ("off", "none", ""):
        return "off"
    if contracts.exclude_privileged() and draft.sensitivity == "privileged":
        return "privileged"
    return "on"


@router.get("/drafts/{draft_id}/rail", response=AiRailOut)
def get_rail(request: HttpRequest, draft_id: UUID) -> AiRailOut:
    draft = require_draft_scope(request, draft_id)
    notes = DraftNote.objects.filter(draft_id=draft_id, voided_at__isnull=True).select_related(
        "author"
    )
    return AiRailOut(
        checks=[_check_out(c) for c in checks_service.checks_for(draft)],
        suggestions=[_suggestion_out(s) for s in tasks.open_suggestions(draft_id)],
        decided=[_suggestion_out(s) for s in tasks.decided_suggestions(draft_id)],
        notes=[
            DraftNoteOut(id=n.pk, author_name=n.author.name, body=n.body, created_at=n.created_at)
            for n in notes
        ],
        assistant=_assistant_state(draft),
    )


def _answer(callable_) -> AiAnswerOut:
    try:
        answer = callable_()
    except NotConfigured as exc:
        return AiAnswerOut(status="off", message=str(exc))
    except contracts.Refused as exc:
        return AiAnswerOut(status="refused", message=str(exc))
    except (ExecutorError, service.OverLimit) as exc:
        return AiAnswerOut(status="failed", message=str(exc))
    return AiAnswerOut(
        status="ok",
        answer=answer.answer,
        run_id=answer.run_id,
        suggestions=[_suggestion_out(s) for s in answer.suggestions],
    )


@router.post("/drafts/{draft_id}/suggest", response=AiAnswerOut)
def suggest(request: HttpRequest, draft_id: UUID) -> AiAnswerOut:
    draft = require_draft_scope(request, draft_id)
    return _answer(lambda: tasks.suggest_letter(request.user, draft))


@router.post("/drafts/{draft_id}/ask", response=AiAnswerOut)
def ask(request: HttpRequest, draft_id: UUID, payload: AiAskIn) -> AiAnswerOut:
    draft = require_draft_scope(request, draft_id)
    question = payload.question.strip()
    if not question:
        return AiAnswerOut(status="failed", message="Ask something.")
    return _answer(lambda: tasks.ask_letter(request.user, draft, question))


@router.post("/drafts/{draft_id}/notes", response=DraftNoteOut)
def add_note(request: HttpRequest, draft_id: UUID, payload: DraftNoteIn) -> DraftNoteOut:
    require_draft_scope(request, draft_id)
    body = payload.body.strip()
    if not body:
        from ninja.errors import HttpError

        raise HttpError(422, "A note needs words.")
    row = DraftNote.objects.create(draft_id=draft_id, author=request.user, body=body)
    return DraftNoteOut(
        id=row.pk, author_name=request.user.name, body=row.body, created_at=row.created_at
    )


@router.post("/notes/{note_id}/void", response={204: None})
def void_note(request: HttpRequest, note_id: UUID):
    require_team(request)
    row = DraftNote.objects.filter(pk=note_id).first()
    if row is None:
        from ninja.errors import HttpError

        raise HttpError(404, "No such note.")
    require_draft_scope(request, row.draft_id)
    DraftNote.objects.filter(pk=note_id).update(voided_at=timezone.now())
    return 204, None


@router.post("/suggestions/{suggestion_id}/apply", response=AiSuggestionOut)
def apply_suggestion(request: HttpRequest, suggestion_id: UUID) -> AiSuggestionOut:
    require_team(request)
    row = tasks.AiSuggestion.objects.filter(pk=suggestion_id).first()
    if row is not None:
        require_draft_scope(request, row.draft_id)
    return _suggestion_out(tasks.decide_suggestion(request.user, suggestion_id, applied=True))


@router.post("/suggestions/{suggestion_id}/dismiss", response=AiSuggestionOut)
def dismiss_suggestion(request: HttpRequest, suggestion_id: UUID) -> AiSuggestionOut:
    require_team(request)
    row = tasks.AiSuggestion.objects.filter(pk=suggestion_id).first()
    if row is not None:
        require_draft_scope(request, row.draft_id)
    return _suggestion_out(tasks.decide_suggestion(request.user, suggestion_id, applied=False))


@router.post("/pick", response=AiPickOut)
def pick(request: HttpRequest, payload: AiPickIn) -> AiPickOut:
    require_team(request)
    question = payload.question.strip()
    if not question:
        return AiPickOut(status="failed", message="Say what you want to find out.")
    try:
        found = tasks.pick_letter(request.user, question)
    except NotConfigured as exc:
        return AiPickOut(status="off", message=str(exc))
    except (ExecutorError, service.OverLimit) as exc:
        return AiPickOut(status="failed", message=str(exc))
    return AiPickOut(status="ok", **found)


@router.get("/runs", response=list[AiRunOut])
def list_runs(request: HttpRequest, limit: int = Query(100, ge=1, le=500)) -> list[AiRunOut]:
    """Administrators: the sent-data log, newest first."""
    require_admin(request)
    rows = AiRun.objects.select_related("requested_by").order_by("-created_at")[:limit]
    return [
        AiRunOut(
            id=r.pk,
            task=r.task,
            tier=r.tier,
            provider=r.provider,
            model=r.model,
            status=r.status,
            subject_type=r.subject_type,
            subject_id=r.subject_id,
            requested_by_name=r.requested_by.name if r.requested_by_id else None,
            context_summary=r.context_summary,
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            error=r.error,
            created_at=r.created_at,
            finished_at=r.finished_at,
        )
        for r in rows
    ]


__all__ = ["GENERAL", "router"]
