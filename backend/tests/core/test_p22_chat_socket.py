"""P22: the live socket, through the real ASGI application with Channels' test communicator.

These need `django_db(transaction=True)`: the consumer reads the database from its own thread,
which only sees committed rows, and the write's note goes out on commit. People are made with
addresses unique to each test because the records tables are not flushed between transactional
tests (they are not Django's)."""

import uuid

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import Client
from portal.asgi import application
from portal.core.chat import channels as chat_channels
from portal.core.chat import messages as chat_messages
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.services import directory
from portal.records.services import requests as requests_service

# As the other threaded tests: `available_apps` makes the flush after each test cascade, because
# records.users references core.users.
pytestmark = pytest.mark.django_db(
    transaction=True,
    available_apps=[
        "portal.core",
        "portal.records",
        "portal.records_db",
        "django.contrib.sessions",
    ],
)
SOCKET = "/api/core/chat/socket"


@pytest.fixture(autouse=True)
def _memory_layer(settings):
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def _person(name: str, role: str):
    tag = uuid.uuid4().hex[:8]
    return create_user(
        email=f"socket-{tag}@example.org",
        name=name,
        records_role=role,
        is_staff=role == "admin",
    )


def _cookie(user) -> str:
    client = Client()
    client.force_login(user)
    return client.cookies["sessionid"].value


async def _open(cookie: str | None, origin: bytes = b"http://localhost"):
    headers = [(b"origin", origin)]
    if cookie:
        headers.append((b"cookie", f"sessionid={cookie}".encode()))
    socket = WebsocketCommunicator(application, SOCKET, headers=headers)
    connected, _ = await socket.connect()
    if connected:
        assert (await socket.receive_json_from(timeout=3)) == {"type": "hello"}
    return socket, connected


def test_the_socket_pushes_new_edited_and_deleted_messages_to_members_and_not_to_others() -> None:
    maker = _person("Socket Maker", "staff")
    member = _person("Socket Member", "intern")
    outsider = _person("Socket Outsider", "admin")
    room = chat_channels.create_channel(maker, name=f"room-{uuid.uuid4().hex[:6]}", private=True)
    chat_channels.add_members(maker, room.pk, [member.pk])
    member_cookie, outsider_cookie = _cookie(member), _cookie(outsider)

    async def run() -> None:
        inside, ok = await _open(member_cookie)
        outside, ok_too = await _open(outsider_cookie)
        assert ok and ok_too
        message = await database_sync_to_async(chat_messages.post)(
            maker, room.pk, body="Synthetic live words"
        )
        got = await inside.receive_json_from(timeout=3)
        assert got["type"] == "message" and got["event"] == "new"
        assert got["message"]["id"] == str(message.pk)
        assert got["message"]["parts"][0]["text"] == "Synthetic live words"
        assert got["message"]["author"]["name"] == "Socket Maker"
        assert got["message"]["body"] is None  # the raw text is its author's alone
        assert await outside.receive_nothing(timeout=0.5)

        await database_sync_to_async(chat_messages.edit)(maker, message.pk, body="Edited words")
        got = await inside.receive_json_from(timeout=3)
        assert got["event"] == "edited" and got["message"]["edited"] is True
        await database_sync_to_async(chat_messages.delete)(maker, message.pk)
        got = await inside.receive_json_from(timeout=3)
        assert got["event"] == "deleted" and got["message"]["deleted"] is True
        assert got["message"]["parts"] == [] and "Edited words" not in str(got)
        assert await outside.receive_nothing(timeout=0.5)

        # Pinged, it answers.
        await inside.send_json_to({"type": "ping"})
        assert await inside.receive_json_from(timeout=3) == {"type": "pong"}
        await inside.disconnect()
        await outside.disconnect()

    async_to_sync(run)()


def test_someone_added_to_a_conversation_is_told_and_then_hears_it() -> None:
    maker = _person("Socket Opener", "staff")
    later = _person("Socket Later", "requester")
    later_cookie = _cookie(later)

    async def run() -> None:
        socket, ok = await _open(later_cookie)
        assert ok
        room = await database_sync_to_async(chat_channels.open_direct)(maker, [later.pk])
        assert await socket.receive_json_from(timeout=3) == {"type": "channels"}
        message = await database_sync_to_async(chat_messages.post)(maker, room.pk, body="hello")
        got = await socket.receive_json_from(timeout=3)
        assert got["message"]["id"] == str(message.pk)
        await socket.disconnect()

    async_to_sync(run)()


def test_a_request_link_pushed_live_is_resolved_for_each_reader() -> None:
    admin = _person("Socket Admin", "admin")
    staff = _person("Socket Staff", "staff")
    agency = directory.create_agency(
        Actor.human(admin),
        name="Test Agency Socket",
        jurisdiction="Testland",
        records_law="TESTLAW",
    )
    sealed = requests_service.reserve_request(
        Actor.human(admin),
        agency_id=agency.pk,
        requester_user_id=admin.pk,
        subject="Sealed socket widgets",
        request_text="Body.",
        sensitivity="privileged",
    )
    room = chat_channels.create_channel(admin, name=f"links-{uuid.uuid4().hex[:6]}", private=False)
    chat_channels.join(staff, room.pk)
    admin_cookie, staff_cookie = _cookie(admin), _cookie(staff)

    async def run() -> None:
        theirs, _ = await _open(admin_cookie)
        mine, _ = await _open(staff_cookie)
        await database_sync_to_async(chat_messages.post)(
            admin, room.pk, body=f"Filed {sealed.tracking_number}"
        )
        for_admin = await theirs.receive_json_from(timeout=3)
        for_staff = await mine.receive_json_from(timeout=3)
        assert for_admin["message"]["parts"][1]["text"] == sealed.tracking_number
        assert for_staff["message"]["parts"][1] == {
            "kind": "request",
            "text": "a request",
            "to": None,
            "href": None,
            "request_id": None,
            "title": None,
            "user_id": None,
        }
        assert sealed.tracking_number not in str(for_staff)
        await theirs.disconnect()
        await mine.disconnect()

    async_to_sync(run)()


def test_nobody_signed_in_and_another_sites_page_are_refused() -> None:
    person = _person("Socket Person", "staff")
    cookie = _cookie(person)

    async def run() -> None:
        _, ok = await _open(None)
        assert ok is False
        _, ok = await _open(cookie, origin=b"https://elsewhere.example.org")
        assert ok is False
        socket, ok = await _open(cookie)
        assert ok is True
        await socket.disconnect()

    async_to_sync(run)()


# ---- P23a: the new-user setup holds the socket too ----------------------------------------------


@pytest.mark.unfinished_setup
def test_a_person_the_setup_holds_cannot_open_the_socket() -> None:
    """The same rule as every API route (`core.services.setup.required`): not finished, no
    socket; finished, the socket opens."""
    from django.utils import timezone
    from portal.core.models import User

    person = _person("Socket Newcomer", "intern")
    cookie = _cookie(person)

    async def run(expect: bool) -> None:
        socket, ok = await _open(cookie)
        assert ok is expect
        if ok:
            await socket.disconnect()

    async_to_sync(run)(False)
    User.objects.filter(pk=person.pk).update(setup_completed_at=timezone.now())
    async_to_sync(run)(True)


def test_a_socket_open_before_a_new_agreement_version_is_not_pushed_messages_until_agreed() -> None:
    """Held after the socket opened (an agreement's new version published): the socket is told
    nothing about messages until the person has agreed. Polling would be refused too."""
    from portal.core.models import Agreement, AgreementAcceptance
    from portal.core.services import agreements

    maker = _person("Socket Speaker", "staff")
    listener = _person("Socket Listener", "staff")
    publisher = _person("Socket Publisher", "admin")
    room = chat_channels.create_channel(maker, name=f"held-{uuid.uuid4().hex[:6]}", private=True)
    chat_channels.add_members(maker, room.pk, [listener.pk])
    cookie = _cookie(listener)

    async def run() -> None:
        socket, ok = await _open(cookie)
        assert ok
        new = await database_sync_to_async(agreements.publish)(
            publisher, Agreement.CONFIDENTIALITY, f"**Synthetic.** Version {uuid.uuid4().hex}."
        )
        await database_sync_to_async(chat_messages.post)(maker, room.pk, body="Not for now")
        assert await socket.receive_nothing(timeout=0.5)
        await database_sync_to_async(_accept_for_test)(listener, new)
        message = await database_sync_to_async(chat_messages.post)(maker, room.pk, body="Now")
        got = await socket.receive_json_from(timeout=3)
        assert got["message"]["id"] == str(message.pk)
        await socket.disconnect()

    def _accept_for_test(user, agreement) -> None:
        AgreementAcceptance.objects.create(
            agreement=agreement,
            user=user,
            signed_name=user.name,
            signature_storage_name="test://no-signature-file",
            signature_content_type="image/png",
            signature_sha256="0" * 64,
            pdf_storage_name="test://no-pdf",
            pdf_sha256="0" * 64,
        )

    async_to_sync(run)()


def _redis_reachable() -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", 6379), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _redis_reachable(), reason="no Redis on 127.0.0.1:6379")
def test_an_idle_socket_on_the_redis_layer_stays_open_past_the_layers_blocking_pop(
    settings,
) -> None:
    """P23a, through a real Redis: a socket left idle for longer than channels-redis's blocking pop
    is still open, answers a ping and is pushed the next message (the layer's read timeout used to
    equal the pop's, and the consumer could die of it)."""
    from channels.layers import channel_layers as layer_registry
    from channels_redis.core import RedisChannelLayer
    from portal.core.chat.layers import channel_layers

    settings.CHANNEL_LAYERS = channel_layers(
        "redis://127.0.0.1:6379/0", prefix=f"sclu-portal-test-{uuid.uuid4().hex[:8]}"
    )
    layer_registry.backends = {}
    maker = _person("Idle Maker", "staff")
    member = _person("Idle Member", "staff")
    room = chat_channels.create_channel(maker, name=f"idle-{uuid.uuid4().hex[:6]}", private=True)
    chat_channels.add_members(maker, room.pk, [member.pk])
    cookie = _cookie(member)

    async def run() -> None:
        import asyncio

        socket, ok = await _open(cookie)
        assert ok
        await asyncio.sleep(RedisChannelLayer.brpop_timeout + 2)
        await socket.send_json_to({"type": "ping"})
        assert await socket.receive_json_from(timeout=3) == {"type": "pong"}
        message = await database_sync_to_async(chat_messages.post)(maker, room.pk, body="Later")
        got = await socket.receive_json_from(timeout=5)
        assert got["message"]["id"] == str(message.pk)
        await socket.disconnect()

    try:
        async_to_sync(run)()
    finally:
        layer_registry.backends = {}
