"""Team chat (P22), under /api/core/chat. Names no request: a request is only ever named inside a
message's text, and each reader gets it resolved for them (`portal/core/chat/render.py`), so every
route here is `NO_REQUEST` in the census.

  GET    /core/chat                                the panel: your channels and direct messages
  GET    /core/chat/browse                         public channels you have not joined
  POST   /core/chat/channels                       a new channel (name, private or not)
  GET    /core/chat/channels/{id}                  one conversation and its members
  POST   /core/chat/channels/{id}/join             join a public channel
  POST   /core/chat/channels/{id}/leave            leave a channel
  POST   /core/chat/channels/{id}/members          add people (the only way into a private one)
  POST   /core/chat/channels/{id}/archive          archive (its creator or an administrator)
  POST   /core/chat/channels/{id}/read             you have read it to now
  GET    /core/chat/channels/{id}/messages         a page of messages, oldest first
  POST   /core/chat/channels/{id}/messages         send one (text, files, mentions)
  POST   /core/chat/channels/{id}/attachments      upload a file for the next message
  PATCH  /core/chat/messages/{id}                  edit your message (a new version)
  DELETE /core/chat/messages/{id}                  delete it (a tombstone; the words are kept)
  GET    /core/chat/attachments/{id}               download a file
  POST   /core/chat/direct                         the direct conversation with these people
  GET    /core/chat/people                         everyone you can message or mention
  GET    /core/chat/online                         who's online, as DECISIONS 33c lets you see it
  GET    /core/chat/updates?since=                 what changed since then (the socket's fallback)
  GET    /core/chat/discovery                      legal director: every conversation
  GET    /core/chat/discovery/{id}                 legal director: one, with every version
  GET    /core/chat/discovery/attachments/{id}     legal director: any file

The live connection is a websocket at /api/core/chat/socket (`portal/core/chat/consumers.py`),
outside django-ninja; it carries the same `ChatMessageOut` as these routes.
"""

from datetime import datetime
from uuid import UUID

from django.http import FileResponse, HttpRequest
from django.utils import timezone
from ninja import File, Router, Status
from ninja.errors import HttpError
from ninja.files import UploadedFile

from portal.core.chat import access, channels, discovery, messages, render
from portal.core.chat.schemas import (
    ChatChannelDetailOut,
    ChatChannelIn,
    ChatChannelOut,
    ChatDiscoveryChannelOut,
    ChatDiscoveryHistoryOut,
    ChatEditIn,
    ChatFileOut,
    ChatMessageIn,
    ChatMessageOut,
    ChatOnlineOut,
    ChatOverviewOut,
    ChatPageOut,
    ChatPeopleIn,
    ChatPersonOut,
    ChatSeenOut,
    ChatUpdatesOut,
)
from portal.core.models import ChatAttachment, ChatChannel, ChatMessage, User
from portal.core.services import presence
from portal.records import roles, storage
from portal.records.api.permissions import records_role

router = Router(tags=["chat"])


def _me(request: HttpRequest) -> User:
    user = request.user
    if not isinstance(user, User):  # session auth has already refused anyone signed out
        raise HttpError(401, "Unauthorized")
    return user


def _person(user: User) -> ChatPersonOut:
    return ChatPersonOut(**render.person(user))


def channel_out(user: User, summary: channels.Summary) -> ChatChannelOut:
    channel = summary.channel
    open_ = channel.archived_at is None
    return ChatChannelOut(
        id=channel.pk,
        kind=channel.kind,
        name=channel.name,
        is_private=channel.is_private,
        encryption=channel.encryption,
        archived=not open_,
        is_member=summary.is_member,
        member_count=summary.member_count,
        unread=summary.unread,
        last_message_at=summary.last_at,
        preview=summary.preview,
        preview_author=summary.preview_author,
        people=[_person(u) for u in summary.people],
        can_post=summary.is_member and open_,
        can_archive=open_ and access.may_archive(user, channel),
    )


def messages_out(user: User, rows: list[ChatMessage]) -> list[ChatMessageOut]:
    return [ChatMessageOut(**row) for row in render.messages_out(user, rows)]


def _one(user: User, message: ChatMessage) -> ChatMessageOut:
    row = ChatMessage.objects.select_related("author", "deleted_by").get(pk=message.pk)
    return messages_out(user, [row])[0]


def _file(row: ChatAttachment) -> FileResponse:
    try:
        handle = storage.open_stored(row.storage_name)
    except FileNotFoundError:
        raise HttpError(404, "The file is missing from storage.") from None
    response = FileResponse(handle, content_type=row.content_type or "application/octet-stream")
    name = storage.safe_filename(row.file_name)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


# ---- the panel --------------------------------------------------------------------------------


@router.get("", response=ChatOverviewOut)
def overview(request: HttpRequest) -> ChatOverviewOut:
    user = _me(request)
    mine, direct = channels.overview(user)
    return ChatOverviewOut(
        channels=[channel_out(user, s) for s in mine],
        direct=[channel_out(user, s) for s in direct],
        unread=sum(s.unread for s in mine) + sum(s.unread for s in direct),
    )


@router.get("/browse", response=list[ChatChannelOut])
def browse(request: HttpRequest) -> list[ChatChannelOut]:
    user = _me(request)
    return [channel_out(user, s) for s in channels.browse(user)]


@router.post("/channels", response={201: ChatChannelOut})
def create_channel(request: HttpRequest, payload: ChatChannelIn) -> Status:
    user = _me(request)
    channel = channels.create_channel(user, name=payload.name, private=payload.private)
    return Status(201, channel_out(user, channels.summary(user, channel)))


def _detail(user: User, channel: ChatChannel) -> ChatChannelDetailOut:
    return ChatChannelDetailOut(
        channel=channel_out(user, channels.summary(user, channel)),
        members=[_person(u) for u in channels.member_people(channel.pk)],
    )


@router.get("/channels/{channel_id}", response=ChatChannelDetailOut)
def channel_detail(request: HttpRequest, channel_id: UUID) -> ChatChannelDetailOut:
    user = _me(request)
    return _detail(user, access.readable(user, channel_id))


@router.post("/channels/{channel_id}/join", response=ChatChannelDetailOut)
def join(request: HttpRequest, channel_id: UUID) -> ChatChannelDetailOut:
    user = _me(request)
    return _detail(user, channels.join(user, channel_id))


@router.post("/channels/{channel_id}/leave", response=ChatChannelDetailOut)
def leave(request: HttpRequest, channel_id: UUID) -> ChatChannelDetailOut:
    user = _me(request)
    return _detail(user, channels.leave(user, channel_id))


@router.post("/channels/{channel_id}/members", response=ChatChannelDetailOut)
def add_members(
    request: HttpRequest, channel_id: UUID, payload: ChatPeopleIn
) -> ChatChannelDetailOut:
    user = _me(request)
    return _detail(user, channels.add_members(user, channel_id, payload.user_ids))


@router.post("/channels/{channel_id}/archive", response=ChatChannelDetailOut)
def archive(request: HttpRequest, channel_id: UUID) -> ChatChannelDetailOut:
    user = _me(request)
    return _detail(user, channels.archive(user, channel_id))


@router.post("/channels/{channel_id}/read", response={204: None})
def mark_read(request: HttpRequest, channel_id: UUID) -> Status:
    messages.mark_read(_me(request), channel_id)
    return Status(204, None)


# ---- messages ---------------------------------------------------------------------------------


@router.get("/channels/{channel_id}/messages", response=ChatPageOut)
def message_page(
    request: HttpRequest, channel_id: UUID, before: datetime | None = None, limit: int = 50
) -> ChatPageOut:
    user = _me(request)
    channel, rows, more = messages.page(user, channel_id, before=before, limit=limit)
    return ChatPageOut(
        channel=channel_out(user, channels.summary(user, channel)),
        messages=messages_out(user, rows),
        more=more,
    )


@router.post("/channels/{channel_id}/messages", response={201: ChatMessageOut})
def send(request: HttpRequest, channel_id: UUID, payload: ChatMessageIn) -> Status:
    user = _me(request)
    message = messages.post(
        user,
        channel_id,
        body=payload.body,
        attachment_ids=payload.attachment_ids,
        mention_ids=payload.mention_ids,
    )
    return Status(201, _one(user, message))


@router.post("/channels/{channel_id}/attachments", response={201: ChatFileOut})
def upload(
    request: HttpRequest,
    channel_id: UUID,
    file: UploadedFile = File(...),  # noqa: B008 -- ninja's own idiom for a multipart file
) -> Status:
    row = messages.upload(
        _me(request),
        channel_id,
        file_name=file.name or "file",
        chunks=file.chunks(),
        declared_type=file.content_type,
    )
    return Status(
        201,
        ChatFileOut(
            id=row.pk, file_name=row.file_name, content_type=row.content_type, size=row.size
        ),
    )


@router.patch("/messages/{message_id}", response=ChatMessageOut)
def edit(request: HttpRequest, message_id: UUID, payload: ChatEditIn) -> ChatMessageOut:
    user = _me(request)
    message = messages.edit(user, message_id, body=payload.body, mention_ids=payload.mention_ids)
    return _one(user, message)


@router.delete("/messages/{message_id}", response=ChatMessageOut)
def delete(request: HttpRequest, message_id: UUID) -> ChatMessageOut:
    user = _me(request)
    return _one(user, messages.delete(user, message_id))


@router.get("/attachments/{attachment_id}")
def download(request: HttpRequest, attachment_id: UUID) -> FileResponse:
    return _file(messages.attachment_for(_me(request), attachment_id))


# ---- people -----------------------------------------------------------------------------------


@router.post("/direct", response=ChatChannelDetailOut)
def direct(request: HttpRequest, payload: ChatPeopleIn) -> ChatChannelDetailOut:
    user = _me(request)
    return _detail(user, channels.open_direct(user, payload.user_ids))


@router.get("/people", response=list[ChatPersonOut])
def people(request: HttpRequest) -> list[ChatPersonOut]:
    """Every active person, for the new-message picker and @mentions: names only."""
    _me(request)
    return [_person(u) for u in User.objects.filter(is_active=True).order_by("name")]


@router.get("/online", response=ChatOnlineOut)
def online(request: HttpRequest) -> ChatOnlineOut:
    """Who's online inside the panel (DECISIONS 32, 33c): everyone else seen lately, online first.
    Everyone is told where people are by destination only; an administrator is told the
    request. A requester is told nobody, as P19a's list told them nobody."""
    user = _me(request)
    if records_role(request) not in roles.TEAM:
        return ChatOnlineOut(online=0, away=0, people=[])
    rows = [r for r in presence.online() if r.user.pk != user.pk]
    admin = access.is_admin(user)
    seen_requests = presence.requests_seen(rows) if admin else {}
    now = timezone.now()
    out: list[ChatSeenOut] = []
    for row in rows:
        where = presence.where(row, admin=admin, requests=seen_requests)
        out.append(
            ChatSeenOut(
                user_id=row.user.pk,
                name=row.user.name,
                initials=presence.initials(row.user.name),
                state=row.state,
                where=where.text,
                on_request=where.on_request,
                request_id=UUID(where.request_id) if where.request_id else None,
                request_label=where.request_label,
                seen_at=row.seen_at,
                minutes_since=max(0, int((now - row.seen_at).total_seconds() // 60)),
            )
        )
    return ChatOnlineOut(
        online=sum(1 for p in out if p.state == presence.ONLINE),
        away=sum(1 for p in out if p.state == presence.AWAY),
        people=out,
    )


@router.get("/updates", response=ChatUpdatesOut)
def updates(request: HttpRequest, since: datetime) -> ChatUpdatesOut:
    """While the socket is down the client asks this every few seconds, with the `now` of its last
    answer (less a little, and it drops what it already has)."""
    user = _me(request)
    now = timezone.now()
    rows = messages.updates(user, since)
    return ChatUpdatesOut(
        now=now, messages=messages_out(user, rows), unread=channels.unread_total(user)
    )


# ---- the legal director, for discovery (no screen draws it) -------------------------------------


@router.get("/discovery", response=list[ChatDiscoveryChannelOut])
def discovery_index(request: HttpRequest) -> list[ChatDiscoveryChannelOut]:
    return [ChatDiscoveryChannelOut(**row) for row in discovery.index(_me(request))]


@router.get("/discovery/{channel_id}", response=ChatDiscoveryHistoryOut)
def discovery_history(request: HttpRequest, channel_id: UUID) -> ChatDiscoveryHistoryOut:
    return ChatDiscoveryHistoryOut(**discovery.history(_me(request), channel_id))


@router.get("/discovery/attachments/{attachment_id}")
def discovery_file(request: HttpRequest, attachment_id: UUID) -> FileResponse:
    return _file(discovery.attachment(_me(request), attachment_id))
