"""Conversations: channels, private channels and direct messages, and the panel's list (P22).

Anyone signed in may create a channel (owner, 2026-10-16), public or private, and becomes its
first member. A public channel is joined by anyone; a private one only by being added by a
member. A direct conversation is between fixed people: the same people always open the same
conversation, and nobody joins or leaves it. Nothing is deleted: a channel is archived (by its
creator or an administrator) and stays readable to whoever could read it, with nothing more
written in it; leaving closes a membership row and joining again opens a new one.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.utils import timezone

from portal.core.chat import access, live, render
from portal.core.models import ChatChannel, ChatMembership, ChatMessage, User
from portal.core.services import activity
from portal.records.actors import Actor
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
NAME_RULE = (
    "A channel's name is letters, numbers, - and _, at most 80, starting with a letter or a number."
)


def normal_name(raw: str) -> str:
    """ "# Legal Review " → "legal-review": lower case, no leading #, spaces as hyphens."""
    text = (raw or "").strip().lstrip("#").strip().lower()
    text = re.sub(r"\s+", "-", text)
    return re.sub(r"-{2,}", "-", text)


def _join(channel: ChatChannel, user_id: UUID, *, by: UUID | None) -> ChatMembership:
    found = access.membership(channel.pk, user_id)
    if found is not None:
        return found
    return ChatMembership.objects.create(
        channel=channel, user_id=user_id, joined_at=timezone.now(), added_by_id=by
    )


def _public_name_taken(name: str) -> bool:
    return ChatChannel.objects.filter(
        kind=ChatChannel.CHANNEL, is_private=False, name__iexact=name
    ).exists()


@transaction.atomic
def create_channel(user: User, *, name: str, private: bool) -> ChatChannel:
    """A new channel, its maker its first member. A name is unique among PUBLIC channels only
    (P23a, QUESTIONS 30): whatever is tried, the answer never depends on a private channel, so it
    cannot tell anyone one exists. A private channel may share its name with other private ones,
    and a public one with private ones; neither may take the name of a public channel, which
    everyone can see anyway (the database holds public names unique, core.0031)."""
    clean = normal_name(name)
    if not NAME.match(clean):
        raise ServiceError(NAME_RULE)
    if _public_name_taken(clean):
        raise InvalidTransition(f"There is already a channel called #{clean}.")
    try:
        with transaction.atomic():
            channel = ChatChannel.objects.create(
                kind=ChatChannel.CHANNEL,
                name=clean,
                is_private=bool(private),
                encryption=ChatChannel.NONE,
                created_by=user,
            )
    except IntegrityError:
        raise InvalidTransition(f"There is already a channel called #{clean}.") from None
    _join(channel, user.pk, by=user.pk)
    activity.record(
        Actor.human(user),
        "chat_channel_created",
        f"Created the {'private ' if private else ''}channel #{clean}",
        subject_type="chat_channel",
        subject_id=channel.pk,
    )
    live.conversations_changed([user.pk])
    return channel


@transaction.atomic
def join(user: User, channel_id: UUID) -> ChatChannel:
    """Join a public channel. A private one is joined only by being added."""
    channel = access.readable(user, channel_id)
    if channel.kind != ChatChannel.CHANNEL or channel.is_private:
        if access.membership(channel.pk, user.pk) is None:
            raise NotFound(access.NOT_FOUND)
        return channel
    access.require_open(channel)
    _join(channel, user.pk, by=user.pk)
    live.conversations_changed([user.pk])
    return channel


@transaction.atomic
def leave(user: User, channel_id: UUID) -> ChatChannel:
    channel = access.readable(user, channel_id)
    if channel.kind == ChatChannel.DM:
        raise NotAllowed("A direct conversation keeps its people; it cannot be left.")
    found = access.membership(channel.pk, user.pk)
    if found is None:
        return channel
    ChatMembership.objects.filter(pk=found.pk).update(left_at=timezone.now())
    live.conversations_changed([user.pk])
    return channel


@transaction.atomic
def add_members(user: User, channel_id: UUID, user_ids: list[UUID]) -> ChatChannel:
    """A member adds people to a channel (the only way into a private one)."""
    channel = access.readable(user, channel_id)
    if channel.kind == ChatChannel.DM:
        raise NotAllowed("A direct conversation keeps its people; start another one instead.")
    access.require_member(user, channel)
    access.require_open(channel)
    wanted = set(user_ids)
    people = list(User.objects.filter(pk__in=wanted, is_active=True))
    if len(people) != len(wanted):
        raise NotFound("Someone you chose is not an active person in the portal.")
    added = [p.pk for p in people if access.membership(channel.pk, p.pk) is None]
    for person_id in added:
        _join(channel, person_id, by=user.pk)
    live.conversations_changed(added)
    return channel


@transaction.atomic
def archive(user: User, channel_id: UUID) -> ChatChannel:
    channel = access.readable(user, channel_id)
    if not access.may_archive(user, channel):
        raise NotAllowed("Only the person who made a channel, or an administrator, archives it.")
    if channel.archived_at is not None:
        return channel
    ChatChannel.objects.filter(pk=channel.pk).update(
        archived_at=timezone.now(), archived_by_id=user.pk
    )
    activity.record(
        Actor.human(user),
        "chat_channel_archived",
        f"Archived the channel #{channel.name}",
        subject_type="chat_channel",
        subject_id=channel.pk,
    )
    live.conversations_changed(members_of(channel.pk))
    channel.refresh_from_db()
    return channel


def _dm_key(people: set[UUID]) -> str:
    return ",".join(sorted(str(p) for p in people))


@transaction.atomic
def open_direct(user: User, user_ids: list[UUID]) -> ChatChannel:
    """The direct conversation between the caller and these people: found, or started."""
    people = set(user_ids) | {user.pk}
    if len(people) > 9:
        raise ServiceError("A direct conversation has at most nine people; make a channel.")
    found = set(User.objects.filter(pk__in=people, is_active=True).values_list("pk", flat=True))
    if found != people:
        raise NotFound("Someone you chose is not an active person in the portal.")
    key = _dm_key(people)
    channel = ChatChannel.objects.filter(kind=ChatChannel.DM, dm_key=key).first()
    if channel is not None:
        return channel
    try:
        with transaction.atomic():
            channel = ChatChannel.objects.create(
                kind=ChatChannel.DM,
                is_private=True,
                encryption=ChatChannel.NONE,
                dm_key=key,
                created_by=user,
            )
    except IntegrityError:
        # Someone opened the same conversation at the same moment: theirs is the one.
        return ChatChannel.objects.get(kind=ChatChannel.DM, dm_key=key)
    for person_id in people:
        _join(channel, person_id, by=user.pk)
    live.conversations_changed(people)
    return channel


def members_of(channel_id: UUID) -> list[UUID]:
    return list(
        ChatMembership.objects.filter(channel_id=channel_id, left_at__isnull=True).values_list(
            "user_id", flat=True
        )
    )


def member_people(channel_id: UUID) -> list[User]:
    return list(
        User.objects.filter(
            pk__in=ChatMembership.objects.filter(
                channel_id=channel_id, left_at__isnull=True
            ).values("user_id")
        ).order_by("name")
    )


# ---- what the panel lists ---------------------------------------------------------------------


@dataclass
class Summary:
    channel: ChatChannel
    is_member: bool
    member_count: int
    unread: int
    last_at: datetime | None
    preview: str | None
    preview_author: str | None
    people: list[User]  # a direct conversation's other people


def _summaries(user: User, channels: Sequence[ChatChannel], member_of: set[UUID]) -> list[Summary]:
    if not channels:
        return []
    ids = [c.pk for c in channels]
    counts = dict(
        ChatMembership.objects.filter(channel_id__in=ids, left_at__isnull=True)
        .values("channel_id")
        .annotate(n=Count("pk"))
        .values_list("channel_id", "n")
    )
    read_marks = {
        m.channel_id: (m.last_read_at or m.joined_at)
        for m in ChatMembership.objects.filter(
            channel_id__in=ids, user_id=user.pk, left_at__isnull=True
        )
    }
    unread: dict[UUID, int] = {}
    if read_marks:
        since = Q()
        for channel_id, mark in read_marks.items():
            since |= Q(channel_id=channel_id, created_at__gt=mark)
        unread = dict(
            ChatMessage.objects.filter(since, deleted_at__isnull=True)
            .exclude(author_id=user.pk)
            .values("channel_id")
            .annotate(n=Count("pk"))
            .values_list("channel_id", "n")
        )
    latest_ids = (
        ChatChannel.objects.filter(pk__in=ids)
        .annotate(
            last_id=Subquery(
                ChatMessage.objects.filter(channel_id=OuterRef("pk"))
                .order_by("-created_at", "-pk")
                .values("pk")[:1]
            )
        )
        .values_list("pk", "last_id")
    )
    last_by_channel = {c: m for c, m in latest_ids if m is not None}
    latest = {
        m.pk: m
        for m in ChatMessage.objects.filter(pk__in=last_by_channel.values()).select_related(
            "author", "deleted_by"
        )
    }
    rendered = {row["id"]: row for row in render.messages_out(user, list(latest.values()))}
    dm_people: dict[UUID, list[User]] = {}
    dms = [c.pk for c in channels if c.kind == ChatChannel.DM]
    if dms:
        rows = (
            ChatMembership.objects.filter(channel_id__in=dms, left_at__isnull=True)
            .exclude(user_id=user.pk)
            .select_related("user")
            .order_by("user__name")
        )
        for row in rows:
            dm_people.setdefault(row.channel_id, []).append(row.user)
    out: list[Summary] = []
    for channel in channels:
        message_id = last_by_channel.get(channel.pk)
        message = latest.get(message_id) if message_id else None
        text: str | None = None
        author: str | None = None
        if message is not None:
            shown = rendered[message.pk]
            if shown["deleted"]:
                who = shown["deleted_by"]["name"] if shown["deleted_by"] else message.author.name
                text = f"{who} deleted this message"
            else:
                text = render.preview(shown["parts"]) or (
                    "Sent a file" if shown["attachments"] else ""
                )
            author = message.author.name
        out.append(
            Summary(
                channel=channel,
                is_member=channel.pk in member_of,
                member_count=counts.get(channel.pk, 0),
                unread=unread.get(channel.pk, 0),
                last_at=message.created_at if message else None,
                preview=text,
                preview_author=author,
                people=dm_people.get(channel.pk, []),
            )
        )
    return out


def overview(user: User) -> tuple[list[Summary], list[Summary]]:
    """The panel's two lists: the channels the person is in (oldest first, as 018 lists them),
    then their direct conversations, the most recent first. Archived channels are left out."""
    member_of = access.member_channel_ids(user.pk)
    rows = list(
        ChatChannel.objects.filter(pk__in=member_of)
        .annotate(last=Max("messages__created_at"))
        .order_by("created_at", "pk")
    )
    channels = [c for c in rows if c.kind == ChatChannel.CHANNEL and c.archived_at is None]
    direct = [c for c in rows if c.kind == ChatChannel.DM]
    direct.sort(key=lambda c: c.last or c.created_at, reverse=True)
    return _summaries(user, channels, member_of), _summaries(user, direct, member_of)


def browse(user: User) -> list[Summary]:
    """Public channels the person is not in yet, to join (by name)."""
    member_of = access.member_channel_ids(user.pk)
    rows = list(
        ChatChannel.objects.filter(
            kind=ChatChannel.CHANNEL, is_private=False, archived_at__isnull=True
        )
        .exclude(pk__in=member_of)
        .order_by("name")
    )
    return _summaries(user, rows, member_of)


def summary(user: User, channel: ChatChannel) -> Summary:
    member_of = access.member_channel_ids(user.pk)
    return _summaries(user, [channel], member_of)[0]


def unread_total(user: User) -> int:
    channels, direct = overview(user)
    return sum(s.unread for s in channels) + sum(s.unread for s in direct)


def where(channel: ChatChannel) -> str:
    """What a notification, an email or an activity line calls a conversation: never its words."""
    return f"#{channel.name}" if channel.kind == ChatChannel.CHANNEL else "a direct message"
