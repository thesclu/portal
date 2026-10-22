"""Request links (BUILD_PLAN P12): one request named as a duplicate of, or a follow-on to,
another, with a reason. Directional: `from` is the duplicate / the follow-on, `to` is the original.
Voided, never deleted; a voided link can be made again. Status is untouched: naming a duplicate
records a fact about two requests and decides nothing about either (a duplicate that should not
stay open is withdrawn through its own door, with its own note).

"Same campaign" is not a kind: campaigns are not built. "Filed with another agency" is not a
link either; it is `letter_drafts.source_request_id` in the drafting schema.
"""

from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from portal.records.actors import Actor
from portal.records.models import LinkKind, Request, RequestLink, writable
from portal.records.services.common import NotFound, ServiceError, lock_request, require_human


@transaction.atomic
def link_requests(
    actor: Actor, from_request_id: UUID, to_request_id: UUID, *, kind: str, reason: str
) -> RequestLink:
    user_id = require_human(actor, "Linking requests")
    if kind not in LinkKind.values:
        raise ServiceError(f"Unknown link kind {kind!r}; expected one of {LinkKind.values}.")
    if not reason or not reason.strip():
        raise ServiceError("A link needs a reason.")
    if from_request_id == to_request_id:
        raise ServiceError("A request cannot be linked to itself.")
    lock_request(from_request_id)
    if not Request.objects.filter(pk=to_request_id).exists():
        raise NotFound(f"No request {to_request_id}.")
    if RequestLink.objects.filter(
        from_request_id=from_request_id, to_request_id=to_request_id, kind=kind, voided_at=None
    ).exists():
        raise ServiceError("That link already exists.")
    with writable():
        return RequestLink.objects.create(
            from_request_id=from_request_id,
            to_request_id=to_request_id,
            kind=kind,
            reason=reason.strip(),
            created_by_id=user_id,
        )


@transaction.atomic
def void_link(actor: Actor, link_id: UUID, reason: str) -> RequestLink:
    require_human(actor, "Voiding a link")
    if not reason or not reason.strip():
        raise ServiceError("Voiding a link needs a reason.")
    link = RequestLink.objects.select_for_update().filter(pk=link_id).first()
    if link is None:
        raise NotFound(f"No link {link_id}.")
    if link.voided_at is None:
        RequestLink.objects.filter(pk=link_id).update(
            voided_at=timezone.now(), void_reason=reason.strip()
        )
        link.refresh_from_db()
    return link


def links_of(request_id: UUID) -> list[RequestLink]:
    """Both directions, live only, oldest first."""
    return list(
        RequestLink.objects.filter(
            Q(from_request_id=request_id) | Q(to_request_id=request_id), voided_at__isnull=True
        )
        .select_related("from_request", "to_request")
        .order_by("created_at")
    )
