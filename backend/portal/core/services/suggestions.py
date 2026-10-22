"""A requester's suggestion (P13): what they want to find out, which agency if they know, why.
Staff start a letter from it or decline it with a reason the requester can read."""

from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.core.models import Suggestion, User
from portal.records.services.common import InvalidTransition, NotFound, ServiceError


def suggest(user: User, *, what: str, agency_hint: str = "", why: str = "") -> Suggestion:
    if not what.strip():
        raise ServiceError("Say what you want to find out.")
    return Suggestion.objects.create(
        what=what.strip(), agency_hint=agency_hint.strip(), why=why.strip(), requester=user
    )


def _open(suggestion_id: UUID) -> Suggestion:
    row = Suggestion.objects.select_for_update().filter(pk=suggestion_id).first()
    if row is None:
        raise NotFound(f"No suggestion {suggestion_id}.")
    if row.status != Suggestion.SUGGESTED:
        raise InvalidTransition(f"This suggestion was already {row.status}.")
    return row


@transaction.atomic
def decline(user: User, suggestion_id: UUID, note: str) -> Suggestion:
    row = _open(suggestion_id)
    if not note.strip():
        raise ServiceError("Tell the requester why.")
    Suggestion.objects.filter(pk=row.pk).update(
        status=Suggestion.DECLINED,
        decline_note=note.strip(),
        decided_by=user,
        decided_at=timezone.now(),
    )
    return Suggestion.objects.get(pk=row.pk)


@transaction.atomic
def start(user: User, suggestion_id: UUID) -> Suggestion:
    """Take the suggestion up: a letter draft in the staff member's name, with the suggestion's
    words as its subject and first item, so nothing a requester typed is lost or rewritten."""
    from portal.drafting.services import drafts as drafts_service

    row = _open(suggestion_id)
    draft = drafts_service.create_draft(subject=row.what, created_by_user_id=user.pk)
    drafts_service.add_draft_item(draft.pk, item_number="1", body=row.what, sort_order=1)
    Suggestion.objects.filter(pk=row.pk).update(
        status=Suggestion.STARTED, decided_by=user, decided_at=timezone.now(), draft_id=draft.pk
    )
    return Suggestion.objects.get(pk=row.pk)


def mine(user: User) -> list[Suggestion]:
    return list(Suggestion.objects.filter(requester=user).order_by("-created_at"))


def pending() -> list[Suggestion]:
    return list(Suggestion.objects.filter(status=Suggestion.SUGGESTED).order_by("created_at"))
