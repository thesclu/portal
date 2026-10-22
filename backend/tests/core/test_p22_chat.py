"""P22: team chat. Who reads what, edits and deletions kept, mentions, request links resolved per
reader, files and their size limit, who's online under DECISIONS 33c, the polling fallback, and
the legal director's discovery route. The live socket has its own module
(test_p22_chat_socket.py), because it needs real transactions."""

import json
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection, transaction
from django.test import Client
from django.utils import timezone
from portal.core.chat import channels as chat_channels
from portal.core.chat import messages as chat_messages
from portal.core.chat import render
from portal.core.models import (
    Activity,
    ChatAttachment,
    ChatChannel,
    ChatMention,
    ChatMessage,
    ChatMessageVersion,
    Notification,
    Presence,
    Setting,
)
from portal.core.services import notifications
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.services import directory
from portal.records.services import requests as requests_service

ROLES = ("admin", "staff", "staff_attorney", "limited_attorney", "intern", "requester")
SECRET = "Synthetic words about widgets"


def _make(email: str, name: str, role: str):
    return create_user(
        email=email,
        name=name,
        records_role=role,
        is_staff=role == "admin",
    )


@pytest.fixture
def people(db):
    """One person in each of the six roles, a channel maker, a second member, and the legal
    director (a staff attorney, named in settings). Names are synthetic."""
    out = {role: _make(f"chat-{role}@example.org", f"Chat {role.title()}", role) for role in ROLES}
    out["maker"] = _make("chat-maker@example.org", "Maker Person", "staff")
    out["member"] = _make("chat-member@example.org", "Member Person", "intern")
    out["director"] = _make("chat-director@example.org", "Director Person", "staff_attorney")
    Setting.objects.update_or_create(
        key="org.legal_director", defaults={"value": str(out["director"].pk)}
    )
    return out


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _post(client: Client, path: str, payload: dict | None = None):
    return client.post(path, data=json.dumps(payload or {}), content_type="application/json")


@pytest.fixture
def rooms(people):
    """A public channel, a private one and a direct conversation, each with a message."""
    maker, member = people["maker"], people["member"]
    public = chat_channels.create_channel(maker, name="General Talk", private=False)
    private = chat_channels.create_channel(maker, name="inner", private=True)
    chat_channels.add_members(maker, private.pk, [member.pk])
    dm = chat_channels.open_direct(maker, [member.pk])
    for room in (public, private, dm):
        chat_messages.post(maker, room.pk, body=f"{SECRET} in {room.pk}")
    return {"public": public, "private": private, "dm": dm}


# ---- access -----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_a_public_channel_is_read_and_joined_by_anyone_signed_in(people, rooms, role) -> None:
    client = _client(people[role])
    channel = rooms["public"]
    assert channel.name == "general-talk"
    page = client.get(f"/api/core/chat/channels/{channel.pk}/messages")
    assert page.status_code == 200
    assert SECRET in json.dumps(page.json())
    # Not a member yet: reading is allowed, writing is not.
    refused = _post(client, f"/api/core/chat/channels/{channel.pk}/messages", {"body": "hello"})
    assert refused.status_code == 403
    assert _post(client, f"/api/core/chat/channels/{channel.pk}/join").status_code == 200
    sent = _post(client, f"/api/core/chat/channels/{channel.pk}/messages", {"body": "hello"})
    assert sent.status_code == 201
    listed = client.get("/api/core/chat").json()
    assert [c["name"] for c in listed["channels"]] == ["general-talk"]


@pytest.mark.parametrize("role", (*ROLES, "director"))
@pytest.mark.parametrize("kind", ("private", "dm"))
def test_a_private_channel_or_a_direct_message_is_its_members_alone(
    people, rooms, role, kind
) -> None:
    """Every role, the administrator and the legal director included, is answered as if the
    conversation did not exist, by every ordinary route."""
    client = _client(people[role])
    room = rooms[kind]
    base = f"/api/core/chat/channels/{room.pk}"
    assert client.get(base).status_code == 404
    assert client.get(f"{base}/messages").status_code == 404
    assert _post(client, f"{base}/join").status_code == 404
    assert _post(client, f"{base}/messages", {"body": "let me in"}).status_code == 404
    assert _post(client, f"{base}/members", {"user_ids": [str(people[role].pk)]}).status_code == 404
    listed = json.dumps(client.get("/api/core/chat").json())
    assert str(room.pk) not in listed and SECRET not in listed
    updates = client.get("/api/core/chat/updates", {"since": "2000-01-01T00:00:00Z"}).json()
    assert updates["messages"] == []


@pytest.mark.parametrize("kind", ("private", "dm"))
def test_members_read_and_write_their_conversation(people, rooms, kind) -> None:
    client = _client(people["member"])
    base = f"/api/core/chat/channels/{rooms[kind].pk}"
    assert SECRET in json.dumps(client.get(f"{base}/messages").json())
    assert _post(client, f"{base}/messages", {"body": "a reply"}).status_code == 201
    detail = client.get(base).json()
    assert {m["name"] for m in detail["members"]} == {"Maker Person", "Member Person"}


def test_a_member_adds_someone_to_a_private_channel_and_a_dm_keeps_its_people(
    people, rooms
) -> None:
    member = _client(people["member"])
    added = _post(
        member,
        f"/api/core/chat/channels/{rooms['private'].pk}/members",
        {"user_ids": [str(people["staff"].pk)]},
    )
    assert added.status_code == 200
    assert (
        _client(people["staff"]).get(f"/api/core/chat/channels/{rooms['private'].pk}").status_code
        == 200
    )
    dm = f"/api/core/chat/channels/{rooms['dm'].pk}"
    assert _post(member, f"{dm}/leave").status_code == 403
    assert (
        _post(member, f"{dm}/members", {"user_ids": [str(people["staff"].pk)]}).status_code == 403
    )
    # The same two people always find the same conversation.
    again = _post(member, "/api/core/chat/direct", {"user_ids": [str(people["maker"].pk)]})
    assert again.json()["channel"]["id"] == str(rooms["dm"].pk)


def test_leaving_keeps_the_membership_row_and_joining_again_opens_a_new_one(people, rooms) -> None:
    client = _client(people["staff"])
    base = f"/api/core/chat/channels/{rooms['public'].pk}"
    _post(client, f"{base}/join")
    _post(client, f"{base}/leave")
    _post(client, f"{base}/join")
    rows = rooms["public"].memberships.filter(user=people["staff"]).order_by("joined_at")
    assert [r.left_at is None for r in rows] == [False, True]


def test_channel_names_are_normal_and_unique_and_nothing_sets_e2ee(people) -> None:
    client = _client(people["requester"])
    made = _post(
        client, "/api/core/chat/channels", {"name": "# Records Team", "encryption": "e2ee"}
    )
    assert made.status_code == 201
    body = made.json()
    assert body["name"] == "records-team" and body["encryption"] == "none"
    assert ChatChannel.objects.get(pk=body["id"]).encryption == ChatChannel.NONE
    assert _post(client, "/api/core/chat/channels", {"name": "RECORDS-TEAM"}).status_code == 409
    assert _post(client, "/api/core/chat/channels", {"name": "no/slashes"}).status_code == 422
    assert set(ChatChannel.objects.values_list("encryption", flat=True)) == {"none"}


def test_archiving_is_the_makers_or_an_administrators_and_stops_writing(people, rooms) -> None:
    base = f"/api/core/chat/channels/{rooms['public'].pk}"
    staff = _client(people["staff"])
    _post(staff, f"{base}/join")
    assert _post(staff, f"{base}/archive").status_code == 403
    assert _post(_client(people["admin"]), f"{base}/archive").status_code == 200
    assert _post(staff, f"{base}/messages", {"body": "late"}).status_code == 409
    assert staff.get(f"{base}/messages").status_code == 200
    assert Activity.objects.filter(action="chat_channel_archived").count() == 1


# ---- edits and deletions are kept -------------------------------------------------------------


def test_an_edit_shows_edited_and_keeps_every_version(people, rooms) -> None:
    maker = _client(people["maker"])
    channel = rooms["public"]
    sent = _post(maker, f"/api/core/chat/channels/{channel.pk}/messages", {"body": "first"}).json()
    for text in ("second", "third"):
        edited = maker.patch(
            f"/api/core/chat/messages/{sent['id']}",
            data=json.dumps({"body": text}),
            content_type="application/json",
        )
        assert edited.status_code == 200
    body = edited.json()
    assert body["edited"] is True and body["parts"] == [
        {"kind": "text", "text": "third"}
        | {
            "to": None,
            "href": None,
            "request_id": None,
            "title": None,
            "user_id": None,
        }
    ]
    versions = ChatMessageVersion.objects.filter(message_id=sent["id"]).order_by("at", "pk")
    assert [(v.kind, v.body) for v in versions] == [
        ("posted", "first"),
        ("edited", "second"),
        ("edited", "third"),
    ]
    # Somebody else reads "edited" and the words, never the text field meant for the author.
    other = _client(people["staff"]).get(f"/api/core/chat/channels/{channel.pk}/messages").json()
    mine = next(m for m in other["messages"] if m["id"] == sent["id"])
    assert mine["edited"] is True and mine["body"] is None and mine["can_edit"] is False
    # Only the author edits.
    refused = _client(people["admin"]).patch(
        f"/api/core/chat/messages/{sent['id']}",
        data=json.dumps({"body": "admin words"}),
        content_type="application/json",
    )
    assert refused.status_code == 403


def test_a_deletion_leaves_a_tombstone_and_keeps_the_words_for_the_legal_director_alone(
    people, rooms
) -> None:
    channel = rooms["public"]
    maker = _client(people["maker"])
    words = "Synthetic words nobody else will read again"
    sent = _post(maker, f"/api/core/chat/channels/{channel.pk}/messages", {"body": words}).json()
    assert (
        _client(people["staff"]).delete(f"/api/core/chat/messages/{sent['id']}").status_code == 403
    )
    gone = maker.delete(f"/api/core/chat/messages/{sent['id']}")
    assert gone.status_code == 200
    body = gone.json()
    assert body["deleted"] is True and body["deleted_by"]["name"] == "Maker Person"
    assert body["parts"] == [] and body["body"] is None and body["attachments"] == []
    # Every ordinary reader, the administrator included, sees the tombstone and not the words.
    for role in ("admin", "staff", "requester"):
        page = _client(people[role]).get(f"/api/core/chat/channels/{channel.pk}/messages")
        assert words not in json.dumps(page.json())
        row = next(m for m in page.json()["messages"] if m["id"] == sent["id"])
        assert row["deleted"] and row["deleted_by"]["name"] == "Maker Person"
    assert words not in json.dumps(maker.get("/api/core/chat").json())
    # The row is still there, empty; the words are in its versions.
    message = ChatMessage.objects.get(pk=sent["id"])
    assert message.body == "" and message.deleted_at is not None
    kinds = list(message.versions.values_list("kind", "body"))
    assert kinds == [("posted", words), ("deleted", words)]
    history = _client(people["director"]).get(f"/api/core/chat/discovery/{channel.pk}").json()
    found = next(m for m in history["messages"] if m["id"] == sent["id"])
    assert found["deleted_by"]["name"] == "Maker Person"
    assert [v["kind"] for v in found["versions"]] == ["posted", "deleted"]
    assert words in json.dumps(found)


def test_an_administrator_deletes_someone_elses_message_on_the_record(people, rooms) -> None:
    channel = rooms["public"]
    sent = chat_messages.post(people["maker"], channel.pk, body="moderate me")
    admin = _client(people["admin"])
    body = admin.delete(f"/api/core/chat/messages/{sent.pk}").json()
    assert body["deleted_by"]["name"] == "Chat Admin"
    assert Activity.objects.filter(action="chat_message_deleted", subject_id=sent.pk).exists()


@pytest.mark.app_role
def test_the_database_refuses_to_remove_or_rewrite_chat_history(people, rooms) -> None:
    message = ChatMessage.objects.filter(channel=rooms["public"]).first()
    version = ChatMessageVersion.objects.filter(message=message).first()
    for statement, args in (
        ("DELETE FROM core.chat_messages WHERE id = %s", [message.pk]),
        ("UPDATE core.chat_message_versions SET body = 'x' WHERE id = %s", [version.pk]),
        ("DELETE FROM core.chat_message_versions WHERE id = %s", [version.pk]),
        ("DELETE FROM core.chat_channels WHERE id = %s", [rooms["public"].pk]),
        ("DELETE FROM core.chat_memberships WHERE channel_id = %s", [rooms["public"].pk]),
    ):
        with pytest.raises(DatabaseError, match="kept forever"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(statement, args)


# ---- mentions ---------------------------------------------------------------------------------


@pytest.fixture
def mail(monkeypatch):
    sent: list[tuple] = []

    def fake(to, subject, text, html=None):
        sent.append((to, subject, text, html))

    monkeypatch.setattr("portal.mail.services.system_mail.send_system_email", fake)
    return sent


def test_a_mention_rings_the_bell_and_emails_who_and_where_but_never_the_words(
    people, rooms, mail
) -> None:
    channel = rooms["public"]
    words = "@Chat Staff please look at the synthetic widgets"
    chat_messages.post(people["maker"], channel.pk, body=words, mention_ids=[people["staff"].pk])
    told = Notification.objects.get(user=people["staff"], kind="mention")
    assert told.text == "Maker Person mentioned you in #general-talk"
    assert told.link == f"/?chat={channel.pk}"
    assert ChatMention.objects.filter(user=people["staff"]).count() == 1
    assert len(mail) == 1
    to, subject, text, html = mail[0]
    assert to == "chat-staff@example.org" and subject == "Maker mentioned you in #general-talk"
    assert "Reply in the portal" in html and "Maker mentioned you" in html
    assert "widgets" not in text and "widgets" not in html
    # The bell says so in the daily summary until it is opened.
    summary = notifications.summary_data(people["staff"])
    assert summary is not None
    assert [what for what, _ in summary.mentions] == [told.text]
    text, html = notifications.emails.daily_summary(summary, notifications.emails.brand())
    assert "Mentions" in text and told.text in html


def test_a_mention_reaches_only_people_who_can_read_the_conversation(people, rooms, mail) -> None:
    private = rooms["private"]
    chat_messages.post(
        people["maker"],
        private.pk,
        body="@Chat Staff and @Member Person",
        mention_ids=[people["staff"].pk, people["member"].pk],
    )
    assert not Notification.objects.filter(user=people["staff"], kind="mention").exists()
    assert Notification.objects.filter(user=people["member"], kind="mention").count() == 1


def test_an_edit_that_adds_a_mention_tells_the_new_person_once(people, rooms, mail) -> None:
    channel = rooms["public"]
    message = chat_messages.post(people["maker"], channel.pk, body="hello")
    chat_messages.edit(people["maker"], message.pk, body="hello @Chat Intern")
    chat_messages.edit(people["maker"], message.pk, body="hello again @Chat Intern")
    assert Notification.objects.filter(user=people["intern"], kind="mention").count() == 1
    assert ChatMention.objects.filter(message=message).count() == 1


# ---- links to requests ------------------------------------------------------------------------


@pytest.fixture
def requests_named(people):
    agency = directory.create_agency(
        Actor.human(people["admin"]),
        name="Test Agency Chat",
        jurisdiction="Testland",
        records_law="TESTLAW",
    )

    def reserve(subject: str, sensitivity: str):
        return requests_service.reserve_request(
            Actor.human(people["admin"]),
            agency_id=agency.pk,
            requester_user_id=people["admin"].pk,
            subject=subject,
            request_text="Body.",
            sensitivity=sensitivity,
        )

    return {
        "sealed": reserve("Sealed synthetic widgets", "privileged"),
        "ordinary": reserve("Ordinary synthetic widgets", "internal"),
    }


def test_a_request_link_shows_its_title_only_to_a_reader_who_may_see_the_request(
    people, rooms, requests_named
) -> None:
    sealed, ordinary = requests_named["sealed"], requests_named["ordinary"]
    channel = rooms["public"]
    body = (
        f"Filed {sealed.tracking_number}, see /requests/{sealed.pk}, and "
        f"{ordinary.tracking_number}; also REQ-1999-000001 and /docket."
    )
    sent = chat_messages.post(people["maker"], channel.pk, body=body)

    def parts(role):
        page = _client(people[role]).get(f"/api/core/chat/channels/{channel.pk}/messages")
        row = next(m for m in page.json()["messages"] if m["id"] == str(sent.pk))
        return page, [p for p in row["parts"] if p["kind"] != "text"]

    page, seen = parts("admin")
    assert [p["text"] for p in seen] == [
        sealed.tracking_number,
        sealed.tracking_number,
        ordinary.tracking_number,
        "a request",
        "/docket",
    ]
    assert seen[0]["title"] == "Sealed synthetic widgets · Test Agency Chat"
    assert seen[1]["to"] == f"/requests/{sealed.pk}"
    assert seen[4] == seen[4] | {"kind": "page", "to": "/docket"}
    # Staff are off the privileged request's list: the sealed one is "a request" both times,
    # with nothing that names it; the ordinary one they may see.
    page, seen = parts("staff")
    assert [p["text"] for p in seen] == [
        "a request",
        "a request",
        ordinary.tracking_number,
        "a request",
        "/docket",
    ]
    assert all(p["request_id"] is None and p["title"] is None for p in seen[:2])
    dumped = json.dumps(page.json())
    assert sealed.tracking_number not in dumped and str(sealed.pk) not in dumped
    assert "Sealed synthetic widgets" not in dumped
    # A requester sees neither: a number that names no request reads the same as a hidden one.
    page, seen = parts("requester")
    assert [p["text"] for p in seen[:4]] == ["a request"] * 4
    assert ordinary.tracking_number not in json.dumps(page.json())
    # The conversation list's one-line preview is built the same way.
    _post(_client(people["staff"]), f"/api/core/chat/channels/{channel.pk}/join")
    listed = json.dumps(_client(people["staff"]).get("/api/core/chat").json())
    assert sealed.tracking_number not in listed


def test_tokens_find_links_mentions_and_numbers_and_leave_the_rest_as_text() -> None:
    from portal.core.chat.render import tokens

    found = tokens(
        "and/or /tmp/x see /inbox?bucket=flagged, https://example.org/a. REQ-2026-000001!"
    )
    assert [(t.kind, t.text) for t in found] == [
        ("path", "/inbox?bucket=flagged"),
        ("url", "https://example.org/a"),
        ("tracking", "REQ-2026-000001"),
    ]
    assert render.preview([{"text": "a " * 200}]).endswith("…")


# ---- files ------------------------------------------------------------------------------------


def test_attachments_respect_the_size_setting_and_follow_their_message(
    people, rooms, media
) -> None:
    Setting.objects.update_or_create(key="chat.attachment_max_bytes", defaults={"value": 10})
    channel = rooms["private"]
    maker = _client(people["maker"])
    base = f"/api/core/chat/channels/{channel.pk}"
    too_big = maker.post(
        f"{base}/attachments", {"file": SimpleUploadedFile("big.txt", b"x" * 11, "text/plain")}
    )
    assert too_big.status_code == 422 and "larger than" in too_big.json()["detail"]
    assert not ChatAttachment.objects.exists()
    fits = maker.post(
        f"{base}/attachments", {"file": SimpleUploadedFile("../fits.txt", b"x" * 10, "text/plain")}
    )
    assert fits.status_code == 201
    file_id = fits.json()["id"]
    assert fits.json()["file_name"] == "fits.txt"
    # Not sent yet: only its uploader may fetch it.
    member = _client(people["member"])
    assert member.get(f"/api/core/chat/attachments/{file_id}").status_code == 404
    # Someone else cannot send it.
    stolen = _post(member, f"{base}/messages", {"body": "mine", "attachment_ids": [file_id]})
    assert stolen.status_code == 404
    sent = _post(maker, f"{base}/messages", {"body": "", "attachment_ids": [file_id]}).json()
    assert [a["file_name"] for a in sent["attachments"]] == ["fits.txt"]
    got = member.get(f"/api/core/chat/attachments/{file_id}")
    assert got.status_code == 200 and b"".join(got.streaming_content) == b"x" * 10
    assert "attachment;" in got["Content-Disposition"]
    assert _client(people["admin"]).get(f"/api/core/chat/attachments/{file_id}").status_code == 404
    # A deleted message takes its files from every reader but the legal director.
    maker.delete(f"/api/core/chat/messages/{sent['id']}")
    assert member.get(f"/api/core/chat/attachments/{file_id}").status_code == 404
    director = _client(people["director"])
    assert director.get(f"/api/core/chat/discovery/attachments/{file_id}").status_code == 200


# ---- the legal director, for discovery --------------------------------------------------------


@pytest.mark.parametrize("role", (*ROLES, "maker"))
def test_discovery_is_refused_to_everyone_but_the_legal_director(people, rooms, role) -> None:
    client = _client(people[role])
    assert client.get("/api/core/chat/discovery").status_code == 403
    assert client.get(f"/api/core/chat/discovery/{rooms['dm'].pk}").status_code == 403
    file_id = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/core/chat/discovery/attachments/{file_id}").status_code == 403
    assert not Activity.objects.filter(action__startswith="chat_discovery").exists()


def test_the_legal_director_reads_everything_and_each_read_is_on_the_record(people, rooms) -> None:
    director = _client(people["director"])
    index = director.get("/api/core/chat/discovery").json()
    assert {row["id"] for row in index} == {str(r.pk) for r in rooms.values()}
    history = director.get(f"/api/core/chat/discovery/{rooms['dm'].pk}").json()
    assert history["kind"] == "dm" and SECRET in json.dumps(history)
    assert {m["person"]["name"] for m in history["memberships"]} == {
        "Maker Person",
        "Member Person",
    }
    director.get(f"/api/core/chat/discovery/{rooms['dm'].pk}")
    lines = Activity.objects.filter(actor=people["director"]).values_list("action", flat=True)
    assert sorted(lines) == ["chat_discovery_index", "chat_discovery_read", "chat_discovery_read"]
    # The same person, through the ordinary routes, is just someone not in the conversation.
    assert director.get(f"/api/core/chat/channels/{rooms['dm'].pk}/messages").status_code == 404
    # Naming somebody else takes it away from them.
    Setting.objects.filter(key="org.legal_director").update(value=str(people["admin"].pk))
    assert director.get("/api/core/chat/discovery").status_code == 403


# ---- who's online (DECISIONS 33c) -------------------------------------------------------------


def test_whos_online_names_a_request_to_administrators_alone(people, requests_named) -> None:
    sealed = requests_named["sealed"]
    now = timezone.now()
    Presence.objects.create(
        user=people["staff"], path=f"/requests/{sealed.pk}", label="Docket › Sealed", seen_at=now
    )
    Presence.objects.create(
        user=people["intern"],
        path="/inbox?bucket=flagged",
        label="Inbox › Flagged",
        seen_at=now - timedelta(minutes=14),
    )
    seen = _client(people["admin"]).get("/api/core/chat/online").json()
    staff = next(p for p in seen["people"] if p["name"] == "Chat Staff")
    assert staff["where"] == f"{sealed.tracking_number} · Test Agency Chat"
    assert staff["request_label"] == sealed.tracking_number
    assert staff["request_id"] == str(sealed.pk)
    intern = next(p for p in seen["people"] if p["name"] == "Chat Intern")
    assert intern["state"] == "away" and intern["minutes_since"] == 14
    assert intern["where"] == "Inbox · Flagged"
    for role in ("staff_attorney", "limited_attorney", "intern"):
        body = _client(people[role]).get("/api/core/chat/online").json()
        dumped = json.dumps(body)
        assert str(sealed.pk) not in dumped and sealed.tracking_number not in dumped
        assert "Sealed" not in dumped
        found = next(p for p in body["people"] if p["name"] == "Chat Staff")
        assert found["where"] == "A request" and found["on_request"] is True
        assert found["request_id"] is None and found["request_label"] is None
    # Nobody is listed to themselves; a requester is told nobody.
    mine = _client(people["staff"]).get("/api/core/chat/online").json()
    assert "Chat Staff" not in [p["name"] for p in mine["people"]]
    assert _client(people["requester"]).get("/api/core/chat/online").json()["people"] == []


def test_the_presence_list_names_a_request_only_to_admins_and_to_people_on_it(
    people, requests_named
) -> None:
    sealed, ordinary = requests_named["sealed"], requests_named["ordinary"]
    staff, attorney = _client(people["staff"]), _client(people["staff_attorney"])
    _post(staff, "/api/core/presence", {"path": f"/requests/{sealed.pk}", "label": "Docket › S"})
    listed = _post(attorney, "/api/core/presence", {"path": f"/requests/{ordinary.pk}"}).json()
    other = next(p for p in listed["people"] if p["name"] == "Chat Staff")
    assert other["path"] == "/requests" and other["label"] == "A request"
    # On the same request as the caller: kept, so "Viewing now" still works.
    _post(staff, "/api/core/presence", {"path": f"/requests/{ordinary.pk}", "label": "Docket › O"})
    listed = attorney.get("/api/core/presence").json()
    other = next(p for p in listed["people"] if p["name"] == "Chat Staff")
    assert other["path"] == f"/requests/{ordinary.pk}" and other["label"] == "A request"
    admin = _client(people["admin"]).get("/api/core/presence").json()
    other = next(p for p in admin["people"] if p["name"] == "Chat Staff")
    assert other["label"] == "Docket › O"


# ---- the polling fallback ---------------------------------------------------------------------


def test_updates_return_what_changed_in_your_conversations_since_then(people, rooms) -> None:
    member = _client(people["member"])
    first = member.get("/api/core/chat/updates", {"since": "2000-01-01T00:00:00Z"}).json()
    assert {m["channel_id"] for m in first["messages"]} == {
        str(rooms["private"].pk),
        str(rooms["dm"].pk),
    }
    assert first["unread"] == 2
    since = first["now"]
    message = chat_messages.post(people["maker"], rooms["dm"].pk, body="new one")
    chat_messages.post(people["maker"], rooms["public"].pk, body="not yours")
    later = member.get("/api/core/chat/updates", {"since": since}).json()
    assert [m["id"] for m in later["messages"]] == [str(message.pk)]
    chat_messages.delete(people["maker"], message.pk)
    last = member.get("/api/core/chat/updates", {"since": later["now"]}).json()
    assert [m["deleted"] for m in last["messages"]] == [True]
    # Reading clears the count.
    _post(member, f"/api/core/chat/channels/{rooms['dm'].pk}/read")
    _post(member, f"/api/core/chat/channels/{rooms['private'].pk}/read")
    assert member.get("/api/core/chat").json()["unread"] == 0


def test_the_overview_lists_channels_then_direct_messages_with_unread_and_previews(
    people, rooms
) -> None:
    body = _client(people["member"]).get("/api/core/chat").json()
    assert [c["name"] for c in body["channels"]] == ["inner"]
    assert [c["unread"] for c in body["channels"]] == [1]
    [dm] = body["direct"]
    assert dm["kind"] == "dm" and [p["name"] for p in dm["people"]] == ["Maker Person"]
    assert dm["preview"].startswith(SECRET) and dm["unread"] == 1
    browse = _client(people["member"]).get("/api/core/chat/browse").json()
    assert [c["name"] for c in browse] == ["general-talk"]
    people_listed = _client(people["requester"]).get("/api/core/chat/people").json()
    assert "Maker Person" in [p["name"] for p in people_listed]


# ---- the mention email and the two entry points -----------------------------------------------


def test_the_mention_email_is_escaped_and_draws_no_quote_for_chat() -> None:
    from portal.core.services import emails

    brand = emails.Brand(
        short_name="TST", portal="TST Portal", logo_url="https://x/l.png", home_url="https://x/"
    )
    note = emails.Mention(
        sender="Seed <b>Bold</b>", where="#seed-room", context="#seed-room", link="https://x/?c=1"
    )
    text, html = emails.mention(note, brand)
    assert "<b>Bold</b>" not in html and "&lt;b&gt;Bold&lt;/b&gt;" in html
    assert "Seed &lt;b&gt;Bold&lt;/b&gt; in TST Portal" in html and "Reply in the portal" in html
    assert "background:#f4f6f9" not in html  # no quote box without a quote
    assert emails.mention_subject(note) == "Seed mentioned you in #seed-room"
    assert "Reply in the portal: https://x/?c=1" in text
    quoted = emails.mention_card(
        emails.Mention(sender="A B", where="w", context="c", link="l", quote="said <this>"),
        brand,
    )
    assert "“said &lt;this&gt;”" in quoted


def test_the_asgi_application_serves_the_socket_and_the_wsgi_path_still_imports() -> None:
    from channels.routing import ProtocolTypeRouter
    from portal import asgi, wsgi

    assert isinstance(asgi.application, ProtocolTypeRouter)
    assert set(asgi.application.application_mapping) == {"http", "websocket"}
    assert callable(wsgi.application)
