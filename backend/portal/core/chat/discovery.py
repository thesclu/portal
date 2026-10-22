"""The legal director's read of every conversation, for discovery (P22; DECISIONS 37h, 42d).

owner, 2026-10-17: "every version of every message, deleted ones included, is kept, and only
the legal director (and the database) can read all chats, for discovery — not every admin".
The legal director is the one person named in Settings → Organisation (`org.legal_director`).
This is their only door to a conversation they are not in, and it is read-only: it returns the
full history (who was in it and when, every message with every version, deletions with who did
them and the words they removed, the files and the mentions) and writes an activity line each
time it is used, so the reading itself is on the record. Everyone else is refused, every
administrator included. No screen draws it (the route is listed in HANDOFF).

These conversations are plain; the end-to-end channels of 37h, and the director's keys for
them, are a later part.
"""

from typing import Any
from uuid import UUID

from django.db import transaction

from portal.core.chat import access, channels, render
from portal.core.models import (
    ChatAttachment,
    ChatChannel,
    ChatMembership,
    ChatMention,
    ChatMessage,
    ChatMessageVersion,
    User,
)
from portal.core.services import activity
from portal.records.actors import Actor
from portal.records.services.common import NotAllowed, NotFound

REFUSED = "Only the legal director reads team chat for discovery."


def require_legal_director(user: User) -> None:
    if not access.is_legal_director(user):
        raise NotAllowed(REFUSED)


@transaction.atomic
def index(user: User) -> list[dict[str, Any]]:
    """Every conversation there has ever been, archived and direct ones included."""
    require_legal_director(user)
    rows = list(ChatChannel.objects.select_related("created_by").order_by("created_at", "pk"))
    counts: dict[UUID, int] = {}
    for channel_id in ChatMessage.objects.values_list("channel_id", flat=True):
        counts[channel_id] = counts.get(channel_id, 0) + 1
    people: dict[UUID, set[UUID]] = {}
    for channel_id, user_id in ChatMembership.objects.values_list("channel_id", "user_id"):
        people.setdefault(channel_id, set()).add(user_id)
    activity.record(
        Actor.human(user),
        "chat_discovery_index",
        "Listed every team chat conversation for discovery",
        subject_type="chat",
        data={"conversations": len(rows)},
    )
    return [
        {
            "id": c.pk,
            "kind": c.kind,
            "name": c.name,
            "is_private": c.is_private,
            "encryption": c.encryption,
            "created_by": render.person(c.created_by),
            "created_at": c.created_at,
            "archived_at": c.archived_at,
            "messages": counts.get(c.pk, 0),
            "people_ever": len(people.get(c.pk, set())),
        }
        for c in rows
    ]


@transaction.atomic
def history(user: User, channel_id: UUID) -> dict[str, Any]:
    """One conversation, all of it."""
    require_legal_director(user)
    channel = ChatChannel.objects.select_related("created_by", "archived_by").filter(pk=channel_id)
    found = channel.first()
    if found is None:
        raise NotFound(access.NOT_FOUND)
    memberships = list(
        ChatMembership.objects.filter(channel=found)
        .select_related("user", "added_by")
        .order_by("joined_at", "pk")
    )
    messages = list(
        ChatMessage.objects.filter(channel=found)
        .select_related("author", "deleted_by")
        .order_by("created_at", "pk")
    )
    ids = [m.pk for m in messages]
    versions: dict[UUID, list[dict[str, Any]]] = {}
    for v in ChatMessageVersion.objects.filter(message_id__in=ids).select_related("by"):
        versions.setdefault(v.message_id, []).append(
            {"kind": v.kind, "body": v.body, "by": render.person(v.by), "at": v.at}
        )
    files: dict[UUID, list[dict[str, Any]]] = {}
    for a in ChatAttachment.objects.filter(message_id__in=ids).order_by("created_at"):
        assert a.message_id is not None
        files.setdefault(a.message_id, []).append(
            {
                "id": a.pk,
                "file_name": a.file_name,
                "content_type": a.content_type,
                "size": a.size,
                "sha256": a.sha256,
            }
        )
    mentions: dict[UUID, list[dict[str, Any]]] = {}
    for m in ChatMention.objects.filter(message_id__in=ids).select_related("user"):
        mentions.setdefault(m.message_id, []).append(render.person(m.user))
    activity.record(
        Actor.human(user),
        "chat_discovery_read",
        f"Read {channels.where(found)} for discovery",
        subject_type="chat_channel",
        subject_id=found.pk,
        data={"messages": len(messages)},
    )
    return {
        "id": found.pk,
        "kind": found.kind,
        "name": found.name,
        "is_private": found.is_private,
        "encryption": found.encryption,
        "created_by": render.person(found.created_by),
        "created_at": found.created_at,
        "archived_at": found.archived_at,
        "archived_by": render.person(found.archived_by) if found.archived_by else None,
        "memberships": [
            {
                "person": render.person(m.user),
                "joined_at": m.joined_at,
                "left_at": m.left_at,
                "added_by": render.person(m.added_by) if m.added_by else None,
            }
            for m in memberships
        ],
        "messages": [
            {
                "id": m.pk,
                "author": render.person(m.author),
                "created_at": m.created_at,
                "body": m.body,
                "edited_at": m.edited_at,
                "deleted_at": m.deleted_at,
                "deleted_by": render.person(m.deleted_by) if m.deleted_by else None,
                "versions": versions.get(m.pk, []),
                "attachments": files.get(m.pk, []),
                "mentions": mentions.get(m.pk, []),
            }
            for m in messages
        ],
    }


@transaction.atomic
def attachment(user: User, attachment_id: UUID) -> ChatAttachment:
    """Any file sent in any conversation, a deleted message's included."""
    require_legal_director(user)
    row = ChatAttachment.objects.filter(pk=attachment_id).first()
    if row is None:
        raise NotFound("No such file.")
    activity.record(
        Actor.human(user),
        "chat_discovery_file",
        f"Opened the file {row.file_name} from team chat for discovery",
        subject_type="chat_attachment",
        subject_id=row.pk,
        data={"channel_id": str(row.channel_id)},
    )
    return row
