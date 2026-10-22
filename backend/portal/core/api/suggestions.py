"""P13: a requester suggests a request; staff start a letter from it or decline it."""

from datetime import datetime
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from portal.core.models import Suggestion
from portal.core.services import suggestions as suggestions_service
from portal.records import roles
from portal.records.api.permissions import records_role, require_staff

router = Router(tags=["suggestions"])


class SuggestionOut(Schema):
    id: UUID
    what: str
    agency_hint: str
    why: str
    requester_user_id: UUID
    requester_name: str
    created_at: datetime
    status: str
    decline_note: str
    decided_at: datetime | None
    draft_id: UUID | None


class SuggestionIn(Schema):
    what: str
    agency_hint: str = ""
    why: str = ""


class DeclineIn(Schema):
    note: str


def _out(row: Suggestion) -> SuggestionOut:
    return SuggestionOut(
        id=row.pk,
        what=row.what,
        agency_hint=row.agency_hint,
        why=row.why,
        requester_user_id=row.requester_id,
        requester_name=row.requester.name,
        created_at=row.created_at,
        status=row.status,
        decline_note=row.decline_note,
        decided_at=row.decided_at,
        draft_id=row.draft_id,
    )


def _require_records_user(request: HttpRequest) -> str:
    role = records_role(request)
    if role is None:
        raise HttpError(403, "No records access.")
    return role


@router.get("", response=list[SuggestionOut])
def list_suggestions(request: HttpRequest, pending: bool = False) -> list[SuggestionOut]:
    """A requester sees their own; admins, staff and staff attorneys see every open one
    (`pending`) or everything; a limited-scope attorney or an intern sees their own too (P20h:
    taking a suggestion up is staff's)."""
    role = _require_records_user(request)
    if role not in roles.FULL_STAFF:
        return [_out(r) for r in suggestions_service.mine(request.user)]
    rows = (
        suggestions_service.pending()
        if pending
        else list(Suggestion.objects.order_by("-created_at")[:200])
    )
    return [_out(r) for r in rows]


@router.post("", response={201: SuggestionOut})
def suggest(request: HttpRequest, payload: SuggestionIn):
    _require_records_user(request)
    row = suggestions_service.suggest(
        request.user, what=payload.what, agency_hint=payload.agency_hint, why=payload.why
    )
    return 201, _out(row)


@router.post("/{suggestion_id}/decline", response=SuggestionOut)
def decline(request: HttpRequest, suggestion_id: UUID, payload: DeclineIn) -> SuggestionOut:
    require_staff(request)
    return _out(suggestions_service.decline(request.user, suggestion_id, payload.note))


@router.post("/{suggestion_id}/start", response=SuggestionOut)
def start(request: HttpRequest, suggestion_id: UUID) -> SuggestionOut:
    """Take it up: a letter draft in the caller's name, opened at `/drafts/{draft_id}`."""
    require_staff(request)
    return _out(suggestions_service.start(request.user, suggestion_id))
