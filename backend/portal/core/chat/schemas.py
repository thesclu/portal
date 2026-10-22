"""Team chat's shapes, for the HTTP routes and the socket alike (P22). Every class name starts with
`Chat` so the OpenAPI schema never has two of a name."""

from datetime import datetime
from uuid import UUID

from ninja import Schema


class ChatPersonOut(Schema):
    id: UUID
    name: str
    initials: str


class ChatPartOut(Schema):
    """One piece of a message as the reader may see it. `kind` is text | mention | request | page
    | url. A request the reader may not see is `{kind: request, text: "a request"}` and nothing
    else: no id, no title, no link."""

    kind: str
    text: str
    to: str | None = None
    href: str | None = None
    request_id: UUID | None = None
    title: str | None = None
    user_id: UUID | None = None


class ChatFileOut(Schema):
    id: UUID
    file_name: str
    content_type: str
    size: int


class ChatMessageOut(Schema):
    id: UUID
    channel_id: UUID
    author: ChatPersonOut
    created_at: datetime
    changed_at: datetime
    edited: bool
    edited_at: datetime | None
    deleted: bool
    deleted_at: datetime | None
    # Who deleted it ("<name> deleted this message"); the words are gone from every reader.
    deleted_by: ChatPersonOut | None
    parts: list[ChatPartOut]
    # The text as written: only to its author (to edit it), never once deleted.
    body: str | None
    attachments: list[ChatFileOut]
    mine: bool
    mentions_me: bool
    can_edit: bool
    can_delete: bool


class ChatChannelOut(Schema):
    id: UUID
    kind: str
    name: str
    is_private: bool
    encryption: str
    archived: bool
    is_member: bool
    member_count: int
    unread: int
    last_message_at: datetime | None
    preview: str | None
    preview_author: str | None
    # A direct conversation's other people (empty for a channel).
    people: list[ChatPersonOut]
    can_post: bool
    can_archive: bool


class ChatOverviewOut(Schema):
    channels: list[ChatChannelOut]
    direct: list[ChatChannelOut]
    unread: int


class ChatChannelDetailOut(Schema):
    channel: ChatChannelOut
    members: list[ChatPersonOut]


class ChatPageOut(Schema):
    channel: ChatChannelOut
    messages: list[ChatMessageOut]
    more: bool


class ChatUpdatesOut(Schema):
    now: datetime
    messages: list[ChatMessageOut]
    unread: int


class ChatChannelIn(Schema):
    name: str
    private: bool = False


class ChatMessageIn(Schema):
    body: str = ""
    attachment_ids: list[UUID] = []
    mention_ids: list[UUID] = []


class ChatEditIn(Schema):
    body: str
    mention_ids: list[UUID] = []


class ChatPeopleIn(Schema):
    user_ids: list[UUID]


class ChatSeenOut(Schema):
    """One person in who's online, as the reader may be told it (DECISIONS 33c)."""

    user_id: UUID
    name: str
    initials: str
    state: str
    # "Docket", "Inbox · Flagged", "A request"; an administrator reads "REQ-… · Agency".
    where: str
    on_request: bool
    # Administrators only: the request they are on.
    request_id: UUID | None
    request_label: str | None
    seen_at: datetime
    # How long since they were last seen, in whole minutes (shown for someone away).
    minutes_since: int


class ChatOnlineOut(Schema):
    online: int
    away: int
    people: list[ChatSeenOut]


class ChatDiscoveryChannelOut(Schema):
    id: UUID
    kind: str
    name: str
    is_private: bool
    encryption: str
    created_by: ChatPersonOut
    created_at: datetime
    archived_at: datetime | None
    messages: int
    people_ever: int


class ChatDiscoveryMembershipOut(Schema):
    person: ChatPersonOut
    joined_at: datetime
    left_at: datetime | None
    added_by: ChatPersonOut | None


class ChatDiscoveryVersionOut(Schema):
    kind: str
    body: str
    by: ChatPersonOut
    at: datetime


class ChatDiscoveryFileOut(Schema):
    id: UUID
    file_name: str
    content_type: str
    size: int
    sha256: str


class ChatDiscoveryMessageOut(Schema):
    id: UUID
    author: ChatPersonOut
    created_at: datetime
    body: str
    edited_at: datetime | None
    deleted_at: datetime | None
    deleted_by: ChatPersonOut | None
    versions: list[ChatDiscoveryVersionOut]
    attachments: list[ChatDiscoveryFileOut]
    mentions: list[ChatPersonOut]


class ChatDiscoveryHistoryOut(Schema):
    id: UUID
    kind: str
    name: str
    is_private: bool
    encryption: str
    created_by: ChatPersonOut
    created_at: datetime
    archived_at: datetime | None
    archived_by: ChatPersonOut | None
    memberships: list[ChatDiscoveryMembershipOut]
    messages: list[ChatDiscoveryMessageOut]
