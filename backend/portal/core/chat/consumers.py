"""The websocket every signed-in tab holds open for team chat (P22's instant delivery).

    ws(s)://<site>/api/core/chat/socket

Authenticated by the session cookie (Channels' `AuthMiddlewareStack`), behind
`AllowedHostsOriginValidator` so another site's page cannot open it with the person's cookie
(`portal/asgi.py`). A socket with nobody signed in is refused at the handshake, and so is one for
a person the new-user setup holds (P23a: the same rule as every API route, `core.services.setup.
required`); a socket opened before an agreement's new version was published stops being pushed
messages until the person has agreed.

On connect the socket joins the person's own group and one group per conversation they are a
member of (`live.groups_for`). A note on a conversation's group ("message X was posted, edited or
deleted") makes it read that message FOR ITS OWN PERSON, in a transaction with that person's
row-level context, and send it as `{"type": "message", "event": ..., "message": ChatMessageOut}`;
a request link inside it is resolved for that reader only. A note on the person's group ("your
conversations changed") makes it re-join its groups and send `{"type": "channels"}` so the list
refreshes. It also answers `{"type": "ping"}` with `{"type": "pong"}`. The client sends nothing
else; everything it writes goes through the HTTP routes.

Only members are pushed to: someone reading a public channel they have not joined sees new
messages when they reload it, and joins to be told live.
"""

from typing import Any
from uuid import UUID

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db import transaction

from portal.core.chat import access, live, render
from portal.core.chat.schemas import ChatMessageOut
from portal.core.models import ChatMessage, User
from portal.core.services import setup as setup_service


def message_for(user_id: UUID, message_id: UUID) -> dict[str, Any] | None:
    """The message as this person sees it, or None when they are no longer in its conversation
    (or no longer active, or held by the new-user setup). Runs with the person's row-level
    context, as an API request does."""
    from portal.core.rls import set_web_context

    with transaction.atomic():
        user = User.objects.filter(pk=user_id, is_active=True).first()
        if user is None or setup_service.required(user):
            return None
        set_web_context(user)
        message = (
            ChatMessage.objects.select_related("author", "deleted_by", "channel")
            .filter(pk=message_id)
            .first()
        )
        if message is None or access.membership(message.channel_id, user.pk) is None:
            return None
        row = render.messages_out(user, [message])[0]
        return ChatMessageOut(**row).model_dump(mode="json")


class ChatConsumer(AsyncJsonWebsocketConsumer):
    user_id: UUID
    joined: set[str]

    async def connect(self) -> None:
        user = self.scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
            await self.close()
            return
        if await database_sync_to_async(setup_service.required)(user):
            await self.close()
            return
        self.user_id = user.pk
        self.joined = set()
        await self.accept()
        await self._join_groups()
        await self.send_json({"type": "hello"})

    async def disconnect(self, code: int) -> None:
        for group in getattr(self, "joined", set()):
            await self.channel_layer.group_discard(group, self.channel_name)
        self.joined = set()

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        if isinstance(content, dict) and content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def _join_groups(self) -> None:
        wanted = await database_sync_to_async(live.groups_for)(self.user_id)
        for group in wanted - self.joined:
            await self.channel_layer.group_add(group, self.channel_name)
        for group in self.joined - wanted:
            await self.channel_layer.group_discard(group, self.channel_name)
        self.joined = wanted

    # ---- notes from the channel layer (`live.py`) -------------------------------------------

    async def chat_message(self, event: dict[str, str]) -> None:
        payload = await database_sync_to_async(message_for)(self.user_id, UUID(event["message_id"]))
        if payload is None:
            await self._join_groups()
            return
        await self.send_json({"type": "message", "event": event["event"], "message": payload})

    async def chat_conversations(self, event: dict[str, str]) -> None:
        await self._join_groups()
        await self.send_json({"type": "channels"})
