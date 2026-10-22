"""Telling the open sockets that something changed (P22's instant delivery).

Every signed-in tab holds one websocket (`consumers.ChatConsumer`), which joins one Channels
group per conversation the person is a member of, plus one group for the person. A write sends
the group a NOTE that names what changed (a message id, or "your conversations changed"), never
the message itself: each socket then reads the message for its own person, so a request link is
resolved for that reader alone and nothing a reader may not see crosses the channel layer.

The note goes after the write's transaction commits (`transaction.on_commit`), so a socket never
reads a message that is not there yet, and a write that rolls back tells nobody. A channel layer
that is down (Redis stopped) is logged and swallowed: the write stands, and the clients' polling
fallback (`GET /core/chat/updates`) catches up.
"""

import logging
from collections.abc import Iterable
from uuid import UUID

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from portal.core.chat import access

logger = logging.getLogger(__name__)

NEW, EDITED, DELETED = "new", "edited", "deleted"


def channel_group(channel_id: UUID | str) -> str:
    return f"chat.c.{UUID(str(channel_id)).hex}"


def user_group(user_id: UUID | str) -> str:
    return f"chat.u.{UUID(str(user_id)).hex}"


def groups_for(user_id: UUID) -> set[str]:
    """The groups one person's socket belongs in: their own, and every conversation they are in."""
    return {user_group(user_id)} | {channel_group(c) for c in access.member_channel_ids(user_id)}


def _send(group: str, note: dict[str, str]) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group, note)
    except Exception:  # noqa: BLE001 -- the write stands; polling catches the clients up
        logger.exception("could not tell %s about %s", group, note.get("type"))


def message_changed(channel_id: UUID, message_id: UUID, event: str) -> None:
    """A message was posted, edited or deleted: tell its conversation once the write commits."""
    note = {
        "type": "chat.message",
        "channel_id": str(channel_id),
        "message_id": str(message_id),
        "event": event,
    }
    transaction.on_commit(lambda: _send(channel_group(channel_id), note))


def conversations_changed(user_ids: Iterable[UUID]) -> None:
    """These people joined, left, were added to or saw archived a conversation: their sockets
    re-read which groups they belong in, and their lists refresh."""
    people = sorted({str(u) for u in user_ids})

    def tell() -> None:
        for person in people:
            _send(user_group(person), {"type": "chat.conversations"})

    transaction.on_commit(tell)
