"""Saved replies: short texts a person inserts into a message. The portal's own, not the agency's;
nothing here is legal content."""

from uuid import UUID

from django.utils import timezone

from portal.mail.models import SavedReply
from portal.records.actors import Actor
from portal.records.services.common import NotFound, ServiceError, require_human


def list_replies(*, include_archived: bool = False) -> list[SavedReply]:
    rows = SavedReply.objects.all()
    if not include_archived:
        rows = rows.filter(archived_at__isnull=True)
    return list(rows.order_by("name"))


def create_reply(actor: Actor, *, name: str, body: str) -> SavedReply:
    user_id = require_human(actor, "Saving a reply")
    if not name.strip() or not body.strip():
        raise ServiceError("A saved reply needs a name and a body.")
    return SavedReply.objects.create(name=name.strip(), body=body, created_by_id=user_id)


def update_reply(
    actor: Actor,
    reply_id: UUID,
    *,
    name: str | None = None,
    body: str | None = None,
    archived: bool | None = None,
) -> SavedReply:
    require_human(actor, "Editing a saved reply")
    reply = SavedReply.objects.filter(pk=reply_id).first()
    if reply is None:
        raise NotFound(f"No saved reply {reply_id}.")
    changes: dict = {"updated_at": timezone.now()}
    if name is not None:
        if not name.strip():
            raise ServiceError("A saved reply needs a name.")
        changes["name"] = name.strip()
    if body is not None:
        changes["body"] = body
    if archived is not None:
        changes["archived_at"] = timezone.now() if archived else None
    SavedReply.objects.filter(pk=reply_id).update(**changes)
    return SavedReply.objects.get(pk=reply_id)


def delete_reply(actor: Actor, reply_id: UUID) -> None:
    require_human(actor, "Deleting a saved reply")
    reply = SavedReply.objects.filter(pk=reply_id).first()
    if reply is None:
        raise NotFound(f"No saved reply {reply_id}.")
    reply.delete()
