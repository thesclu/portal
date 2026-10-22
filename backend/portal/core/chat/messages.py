"""Messages: posting, editing, deleting, reading, mentions and files (P22).

Every text a message has had is a `ChatMessageVersion` row, written in the same transaction as
the change and never altered: `posted` when it is sent, `edited` with each new text, `deleted`
with the text it had when it was deleted and who deleted it. The message row keeps the current
text, which a deletion empties, so nothing but the versions (read only by the legal director's
discovery route) holds a deleted message's words. Readers see "edited" on an edited message and
"<name> deleted this message" where a deleted one was.

A mention is `@Full Name` (or `@First`) in the text, as the composer's picker writes it or as
someone types it, of a person who can read the conversation: anyone for a public channel, the
members for a private one or a direct message. Each is a `ChatMention` row and a notification
(the bell, and the mention email when the person's preference says so), naming who mentioned
them and where, never the words. An edit that adds a mention tells the new person; nobody is
told twice about one message.
"""

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.core.chat import access, channels, live
from portal.core.models import (
    ChatAttachment,
    ChatChannel,
    ChatMembership,
    ChatMention,
    ChatMessage,
    ChatMessageVersion,
    Notification,
    User,
)
from portal.core.services import activity, emails
from portal.core.services import notifications as core_notifications
from portal.core.services.settings import get_setting
from portal.records import storage
from portal.records.actors import Actor
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError

MAX_CHARS = 8000
PAGE = 50
MAX_PAGE = 200
UPDATES_LIMIT = 300
ATTACHMENT_SETTING = "chat.attachment_max_bytes"


def _text(body: str | None) -> str:
    text = (body or "").strip()
    if len(text) > MAX_CHARS:
        raise ServiceError(f"A message is at most {MAX_CHARS} characters.")
    return text


def _with_people(rows):
    return rows.select_related("author", "deleted_by")


def _readers(channel: ChatChannel) -> set[UUID] | None:
    """Who may read the conversation: None for "anyone signed in" (a public channel)."""
    if access.is_public(channel):
        return None
    return set(channels.members_of(channel.pk))


def mentioned_in(
    channel: ChatChannel, text: str, picked: Iterable[UUID], *, author: User
) -> list[User]:
    """The people a text mentions who may read the conversation, the author left out."""
    lowered = text.lower()
    typed = {u.pk for u in core_notifications.mentioned(text, exclude=author.pk)}
    candidates = set(picked) | typed
    if not candidates:
        return []
    readers = _readers(channel)
    out: list[User] = []
    for user in User.objects.filter(pk__in=candidates, is_active=True).order_by("name"):
        if user.pk == author.pk or (readers is not None and user.pk not in readers):
            continue
        full = (user.name or "").strip().lower()
        first = full.split(" ")[0] if full else ""
        if (
            user.pk in typed
            or (full and f"@{full}" in lowered)
            or (first and f"@{first}" in lowered)
        ):
            out.append(user)
    return out


def _tell(message: ChatMessage, channel: ChatChannel, people: Iterable[User], author: User) -> None:
    from portal.mail.services.system_mail import site_url

    where = channels.where(channel)
    link = f"/?chat={channel.pk}"
    brand = emails.brand()
    note = emails.Mention(
        sender=author.name,
        where=where,
        context=where if channel.kind == ChatChannel.CHANNEL else "Direct message",
        link=site_url(link),
    )
    text, html = emails.mention(note, brand)
    mail = core_notifications.Mail(subject=emails.mention_subject(note), text=text, html=html)
    for person in people:
        _, made = ChatMention.objects.get_or_create(message=message, user=person)
        if not made:
            continue
        core_notifications.notify(
            person.pk,
            Notification.MENTION,
            f"{author.name} mentioned you in {where}",
            link=link,
            subject_type="chat_message",
            subject_id=message.pk,
            actor_user_id=author.pk,
            mail=mail,
        )


def _mark_read(membership: ChatMembership, at: datetime) -> None:
    if membership.last_read_at is None or membership.last_read_at < at:
        ChatMembership.objects.filter(pk=membership.pk).update(last_read_at=at)


@transaction.atomic
def post(
    user: User,
    channel_id: UUID,
    *,
    body: str,
    attachment_ids: Iterable[UUID] = (),
    mention_ids: Iterable[UUID] = (),
) -> ChatMessage:
    channel = access.readable(user, channel_id)
    membership = access.require_member(user, channel)
    access.require_open(channel)
    text = _text(body)
    files = list(dict.fromkeys(attachment_ids))
    if not text and not files:
        raise ServiceError("Write something or attach a file.")
    now = timezone.now()
    message = ChatMessage.objects.create(
        channel=channel, author=user, body=text, created_at=now, changed_at=now
    )
    ChatMessageVersion.objects.create(
        message=message, kind=ChatMessageVersion.POSTED, body=text, by=user, at=now
    )
    if files:
        claimed = ChatAttachment.objects.filter(
            pk__in=files, channel=channel, uploaded_by=user, message__isnull=True
        ).update(message=message)
        if claimed != len(files):
            raise NotFound("A file you attached is not one you uploaded here, or was sent already.")
    _mark_read(membership, now)
    _tell(message, channel, mentioned_in(channel, text, mention_ids, author=user), user)
    live.message_changed(channel.pk, message.pk, live.NEW)
    return message


def _locked(user: User, message_id: UUID) -> tuple[ChatMessage, ChatChannel]:
    message = ChatMessage.objects.select_for_update().filter(pk=message_id).first()
    if message is None:
        raise NotFound("No such message.")
    channel = access.readable(user, message.channel_id)
    return message, channel


@transaction.atomic
def edit(
    user: User, message_id: UUID, *, body: str, mention_ids: Iterable[UUID] = ()
) -> ChatMessage:
    message, channel = _locked(user, message_id)
    if message.deleted_at is not None:
        raise InvalidTransition("This message was deleted.")
    if not access.may_edit(user, message):
        raise NotAllowed("Only the person who wrote a message edits it.")
    access.require_member(user, channel)
    access.require_open(channel)
    text = _text(body)
    if not text and not ChatAttachment.objects.filter(message=message).exists():
        raise ServiceError("A message cannot be empty; delete it instead.")
    if text == message.body:
        return message
    now = timezone.now()
    ChatMessage.objects.filter(pk=message.pk).update(body=text, edited_at=now, changed_at=now)
    ChatMessageVersion.objects.create(
        message=message, kind=ChatMessageVersion.EDITED, body=text, by=user, at=now
    )
    message.refresh_from_db()
    _tell(message, channel, mentioned_in(channel, text, mention_ids, author=user), user)
    live.message_changed(channel.pk, message.pk, live.EDITED)
    return message


@transaction.atomic
def delete(user: User, message_id: UUID) -> ChatMessage:
    """A tombstone: the message stays, empty, with who deleted it; its words stay in the version
    written now, for the legal director."""
    message, channel = _locked(user, message_id)
    if message.deleted_at is not None:
        return message
    if not access.may_delete(user, message):
        raise NotAllowed("Only the person who wrote a message, or an administrator, deletes it.")
    now = timezone.now()
    ChatMessageVersion.objects.create(
        message=message, kind=ChatMessageVersion.DELETED, body=message.body, by=user, at=now
    )
    ChatMessage.objects.filter(pk=message.pk).update(
        body="", deleted_at=now, deleted_by=user, changed_at=now
    )
    if message.author_id != user.pk:
        activity.record(
            Actor.human(user),
            "chat_message_deleted",
            f"Deleted a message by someone else in {channels.where(channel)}",
            subject_type="chat_message",
            subject_id=message.pk,
            data={"channel_id": str(channel.pk), "author_id": str(message.author_id)},
        )
    message.refresh_from_db()
    live.message_changed(channel.pk, message.pk, live.DELETED)
    return message


@transaction.atomic
def mark_read(user: User, channel_id: UUID) -> None:
    channel = access.readable(user, channel_id)
    found = access.membership(channel.pk, user.pk)
    if found is not None:
        _mark_read(found, timezone.now())


def page(
    user: User, channel_id: UUID, *, before: datetime | None = None, limit: int = PAGE
) -> tuple[ChatChannel, list[ChatMessage], bool]:
    """Up to `limit` messages before `before` (the newest when it is empty), oldest first, and
    whether there are older ones."""
    channel = access.readable(user, channel_id)
    count = max(1, min(int(limit or PAGE), MAX_PAGE))
    rows = _with_people(ChatMessage.objects.filter(channel=channel))
    if before is not None:
        rows = rows.filter(created_at__lt=before)
    newest = list(rows.order_by("-created_at", "-pk")[: count + 1])
    more = len(newest) > count
    return channel, list(reversed(newest[:count])), more


def updates(user: User, since: datetime) -> list[ChatMessage]:
    """Every message that changed after `since` in a conversation the person is in: what the
    polling fallback asks for while the socket is down."""
    return list(
        _with_people(
            ChatMessage.objects.filter(
                channel_id__in=access.member_channel_ids(user.pk), changed_at__gt=since
            )
        ).order_by("changed_at", "pk")[:UPDATES_LIMIT]
    )


# ---- files ------------------------------------------------------------------------------------


def max_attachment_bytes() -> int:
    return int(get_setting(ATTACHMENT_SETTING) or 0)


def _megabytes(size: int) -> str:
    value = size / (1024 * 1024)
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}".rstrip("0").rstrip(".")


@transaction.atomic
def upload(
    user: User,
    channel_id: UUID,
    *,
    file_name: str,
    chunks: Iterable[bytes],
    declared_type: str | None = None,
) -> ChatAttachment:
    """Store a file for a message about to be sent. Refused past `chat.attachment_max_bytes`
    (Settings → Organisation) before anything is stored."""
    channel = access.readable(user, channel_id)
    access.require_member(user, channel)
    access.require_open(channel)
    limit = max_attachment_bytes()
    name = storage.safe_filename(file_name)
    try:
        stored = storage.store_chat_file(channel.pk, name, chunks, max_bytes=limit)
    except storage.UploadTooLarge:
        raise ServiceError(
            f"{name} is larger than {_megabytes(limit)} MB, the most a chat message may carry."
        ) from None
    return ChatAttachment.objects.create(
        channel=channel,
        uploaded_by=user,
        file_name=name,
        content_type=storage.guess_mime(name, declared_type) or "",
        size=stored.size,
        sha256=stored.sha256,
        storage_name=stored.storage_name,
    )


def attachment_for(user: User, attachment_id: UUID) -> ChatAttachment:
    """A file the person may download: in a conversation they may read, on a message that was
    not deleted, or not yet sent and their own. Anything else is the same 404."""
    row = ChatAttachment.objects.select_related("message").filter(pk=attachment_id).first()
    missing = NotFound("No such file.")
    if row is None:
        raise missing
    channel = ChatChannel.objects.filter(pk=row.channel_id).first()
    if channel is None or not access.can_read(user, channel):
        raise missing
    if row.message is None:
        if row.uploaded_by_id != user.pk:
            raise missing
    elif row.message.deleted_at is not None:
        raise missing
    return row
