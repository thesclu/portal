"""Who may read, write in, join and archive a conversation (P22; owner, 2026-10-16/17).

  a public channel     read by anyone signed in, joined by anyone; written in by its members
  a private channel    read and written in by its members only; a member adds others
  a direct message     read and written in by its fixed people only
  the legal director   no more than anyone else here. Their reading of every conversation is a
                       separate, read-only route (`discovery.py`) that writes an activity line
                       each time; not every administrator, only the one person named in
                       Settings → Organisation (`org.legal_director`, P20h).

A conversation a person may not read is answered exactly like one that does not exist (404), so
the routes never confirm that a private channel or someone else's direct message exists.
Administrators get two things more, and only these: they may archive any channel and delete any
message (moderation; the deletion is a tombstone and the words are kept).
"""

from uuid import UUID

from portal.core.models import ChatChannel, ChatMembership, ChatMessage, User
from portal.core.services.identity import records_role_of
from portal.core.services.settings import get_setting
from portal.records import roles
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound

NOT_FOUND = "No such conversation."
LEGAL_DIRECTOR_SETTING = "org.legal_director"


def legal_director_id() -> UUID | None:
    """The person named in Settings → Organisation as the legal director, or None."""
    raw = str(get_setting(LEGAL_DIRECTOR_SETTING) or "").strip()
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def is_legal_director(user: User) -> bool:
    named = legal_director_id()
    return named is not None and bool(user.is_active) and user.pk == named


def is_admin(user: User) -> bool:
    """A records administrator: archives any channel, deletes any message, and is the one reader
    of who's online who sees which request someone is on (DECISIONS 33c)."""
    return records_role_of(user) == roles.ADMIN


def membership(channel_id: UUID, user_id: UUID) -> ChatMembership | None:
    """The person's open membership of the conversation, or None."""
    return ChatMembership.objects.filter(
        channel_id=channel_id, user_id=user_id, left_at__isnull=True
    ).first()


def member_channel_ids(user_id: UUID) -> set[UUID]:
    return set(
        ChatMembership.objects.filter(user_id=user_id, left_at__isnull=True).values_list(
            "channel_id", flat=True
        )
    )


def is_public(channel: ChatChannel) -> bool:
    return channel.kind == ChatChannel.CHANNEL and not channel.is_private


def can_read(user: User, channel: ChatChannel, *, member: bool | None = None) -> bool:
    """Through the ordinary routes. `member`, when the caller already knows it, saves a query."""
    if not user.is_active:
        return False
    if is_public(channel):
        return True
    if member is None:
        member = membership(channel.pk, user.pk) is not None
    return member


def readable(user: User, channel_id: UUID) -> ChatChannel:
    """The conversation, or the 404 a missing one gets."""
    channel = ChatChannel.objects.filter(pk=channel_id).first()
    if channel is None or not can_read(user, channel):
        raise NotFound(NOT_FOUND)
    return channel


def require_member(user: User, channel: ChatChannel) -> ChatMembership:
    found = membership(channel.pk, user.pk)
    if found is None:
        raise NotAllowed(f"Join #{channel.name} to write in it.")
    return found


def require_open(channel: ChatChannel) -> None:
    if channel.archived_at is not None:
        raise InvalidTransition("This channel is archived; nothing more can be written in it.")


def may_archive(user: User, channel: ChatChannel) -> bool:
    return channel.kind == ChatChannel.CHANNEL and (
        channel.created_by_id == user.pk or is_admin(user)
    )


def may_edit(user: User, message: ChatMessage) -> bool:
    return message.author_id == user.pk and message.deleted_at is None


def may_delete(user: User, message: ChatMessage) -> bool:
    return message.deleted_at is None and (message.author_id == user.pk or is_admin(user))
