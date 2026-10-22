"""P23a: security and infrastructure fixes found building P20i, P21b and P22a (DECISIONS 47).

1. The new-user setup is held on the server: a person whose setup is unfinished, or who has an
   agreement version in force they have not accepted, is refused every API route but the ones the
   setup screens call, with a 403 the client turns into a visit to /setup.
2. Renaming yourself renames you everywhere: `core.users.name` and `records.users.name` together.
3. (Retired in P25a: two-step is Keycloak's, whose OTP policy keeps a code from working twice,
   `otpPolicyCodeReusable: false` in keycloak/realm/sclu.json.)
5. (Here) nothing; the socket's refusal is in test_p22_chat_socket.py. (The provider's sweep and
   the token endpoint's limit retired with the provider in P25a.)
6. A channel's name is unique among public channels only, so a name never reveals a private one.

Every person is synthetic."""

import inspect
import json
import re
import uuid
from datetime import date

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone
from portal.api import api
from portal.core.chat import channels as chat_channels
from portal.core.models import ChatChannel, User
from portal.core.rls import (
    SETUP_REQUIRED_CODE,
    SETUP_REQUIRED_DETAIL,
    own_setup_session_auth,
    setup_session_auth,
    signout_session_auth,
)
from portal.core.services import agreements, identity, setup
from portal.records.models import RecordsUser
from portal.records.services.common import InvalidTransition

from tests.core.test_p20f_signature import png

ROLES = ("admin", "staff", "staff_attorney", "limited_attorney", "intern", "requester")
REFUSED = {"detail": SETUP_REQUIRED_DETAIL, "code": SETUP_REQUIRED_CODE}

# The routes the setup screens call (frontend/src/screens/setup/*, app/router.tsx, app/session.tsx):
# who the session is and the name and title step (PATCH), signing out, the setup's steps and
# Finish, agreeing to a version. Nothing else is open to a person the setup holds.
OPEN_DURING_SETUP = {
    ("GET", "/core/auth/me"),
    ("PATCH", "/core/auth/me"),
    ("POST", "/core/auth/logout"),
    ("GET", "/core/setup"),
    ("POST", "/core/setup/finish"),
    ("POST", "/core/agreements/{agreement_id}/accept"),
}
# The signature step: the person's own signature only (read it, its image, draw, upload).
OWN_DURING_SETUP = {
    ("GET", "/core/users/{user_id}/signature"),
    ("GET", "/core/users/{user_id}/signature/image"),
    ("POST", "/core/users/{user_id}/signature/draw"),
    ("POST", "/core/users/{user_id}/signature/upload"),
}


def _make(role: str, **extra) -> User:
    return identity.create_user(
        email=f"p23a-{role}-{uuid.uuid4().hex[:8]}@example.org",
        name=f"Synthetic {role.replace('_', ' ').title()}",
        records_role=role,
        is_staff=role == "admin",
        **extra,
    )


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _routes():
    """Every (method, path, operation) in ninja's registry."""
    for prefix, router in api._routers:
        for path, view in router.path_operations.items():
            for operation in view.operations:
                for method in operation.methods:
                    yield method, f"{prefix}{path}", operation


def _url(path: str, operation, *, user_id=None) -> str:
    """The path with a value of the right type in each parameter (the converters are typed)."""
    signature = inspect.signature(operation.view_func)

    def value(match: re.Match) -> str:
        name = match.group(1)
        if name == "user_id" and user_id is not None:
            return str(user_id)
        annotation = signature.parameters[name].annotation
        if annotation is date:
            return "2026-01-05"
        if annotation is str:
            return "synthetic"
        return str(uuid.uuid4())

    return "/api" + re.sub(r"{(\w+)}", value, path)


def _call(client: Client, method: str, url: str):
    return client.generic(method, url, data=b"{}", content_type="application/json")


def _is_setup_refusal(response) -> bool:
    if response.status_code != 403 or not response.get("Content-Type", "").startswith(
        "application/json"
    ):
        return False
    return response.json() == REFUSED


# ---- 1. the setup is held on the server ---------------------------------------------------------


def test_the_routes_open_during_the_setup_are_exactly_the_ones_its_screens_call() -> None:
    """A route added later is closed to a person the setup holds unless someone decides it is the
    setup's; and nothing here opens without this list moving."""
    open_auth = (setup_session_auth, signout_session_auth)
    opened = {(m, p) for m, p, op in _routes() if op.auth_param in open_auth}
    own = {(m, p) for m, p, op in _routes() if op.auth_param is own_setup_session_auth}
    assert opened == OPEN_DURING_SETUP
    assert own == OWN_DURING_SETUP


@pytest.mark.unfinished_setup
@pytest.mark.parametrize("role", ROLES)
def test_an_unfinished_person_is_refused_every_route_but_the_setups(role, db, media) -> None:
    """Every route in the registry, as a person of each role who has not finished the setup. A
    refused route is refused before it looks at anything (the same body, whatever was asked for);
    the setup's own routes answer as they would anyway (a 404 for a made-up id, a 422 for an empty
    body), never with the setup's refusal."""
    person = _make(role)
    someone_else = _make("staff")
    assert setup.required(person)
    client = _client(person)
    refused, open_ = [], []
    for method, path, operation in _routes():
        if operation.auth_param is None or (method, path) == ("POST", "/core/auth/logout"):
            continue  # signed-out routes (sign-in, reset, CSRF); signing out is tried last
        if operation.auth_param is own_setup_session_auth:
            mine = _call(client, method, _url(path, operation, user_id=person.pk))
            assert not _is_setup_refusal(mine), (method, path, mine.content[:200])
            theirs = _call(client, method, _url(path, operation, user_id=someone_else.pk))
            assert _is_setup_refusal(theirs), (method, path, theirs.status_code)
            open_.append((method, path))
            continue
        response = _call(client, method, _url(path, operation))
        if operation.auth_param is setup_session_auth:
            assert not _is_setup_refusal(response), (method, path, response.content[:200])
            open_.append((method, path))
        else:
            assert _is_setup_refusal(response), (method, path, response.status_code)
            refused.append((method, path))
    signed_in_routes = [r for r in _routes() if r[2].auth_param is not None]
    assert len(open_) == len(OPEN_DURING_SETUP | OWN_DURING_SETUP) - 1  # all but signing out
    assert len(refused) == len(signed_in_routes) - len(OPEN_DURING_SETUP | OWN_DURING_SETUP)
    assert len(refused) > 200, "the registry walk found too little to be the whole API"
    # Signing out works, and then the person is simply signed out.
    assert _call(client, "POST", "/api/core/auth/logout").status_code == 200
    assert client.get("/api/core/auth/me").status_code == 401


@pytest.mark.unfinished_setup
def test_the_setup_can_be_walked_and_then_everything_opens(db, media) -> None:
    """Through the routes alone, as the screens walk it: who am I, the steps, name and title, the
    signature, each agreement, Finish. Refused before, open after."""
    person = _make("intern")
    client = _client(person)
    assert _is_setup_refusal(client.get("/api/core/chat"))
    assert client.get("/api/core/auth/me").json()["setup_required"] is True
    steps = client.get("/api/core/setup").json()
    named = client.patch(
        "/api/core/auth/me", {"name": "Synthetic Newcomer"}, content_type="application/json"
    )
    assert named.status_code == 200
    drawn = client.post(
        f"/api/core/users/{person.pk}/signature/draw",
        {"file": _upload()},
    )
    assert drawn.status_code == 200, drawn.content
    assert client.get(f"/api/core/users/{person.pk}/signature/image").status_code == 200
    for agreement in steps["agreements"]:
        accepted = client.post(f"/api/core/agreements/{agreement['id']}/accept")
        assert accepted.status_code == 200, accepted.content
    assert client.post("/api/core/setup/finish").status_code == 200
    assert client.get("/api/core/chat").status_code == 200
    assert client.get("/api/core/auth/me").json()["setup_required"] is False


def _upload():
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("signature.png", png(60, 20), "image/png")


@pytest.mark.parametrize("role", ROLES)
def test_a_new_agreement_version_holds_a_finished_person_until_they_agree(role, db, media) -> None:
    """A finished person of each role, then an administrator publishes the next version of the
    confidentiality agreement (everyone signs it): every route but the setup's is refused until
    they agree; then everything opens again, Finish or not."""
    person = _make(role)
    client = _client(person)
    assert client.get("/api/core/chat").status_code == 200
    assert client.get("/api/core/notifications").status_code == 200
    publisher = _make("admin")
    current = agreements.current("confidentiality")
    assert current is not None
    new = agreements.publish(publisher, "confidentiality", current.body + "\n\nOne more clause.")

    for path in ("/api/core/chat", "/api/core/notifications", "/api/records/requests"):
        assert _is_setup_refusal(client.get(path)), path
    assert _is_setup_refusal(_client(publisher).get("/api/core/agreements")), "the admin too"
    me = client.get("/api/core/auth/me").json()
    assert me["setup_required"] is True
    run = client.get("/api/core/setup").json()
    assert [a["id"] for a in run["agreements"]] == [str(new.pk)]

    # Agreeing needs a signature; the person stores theirs through the setup's own route.
    stored = client.post(f"/api/core/users/{person.pk}/signature/upload", {"file": _upload()})
    assert stored.status_code == 200, stored.content
    assert client.post(f"/api/core/agreements/{new.pk}/accept").status_code == 200
    assert client.get("/api/core/chat").status_code == 200
    assert client.get("/api/core/auth/me").json()["setup_required"] is False


def test_an_unfinished_administrator_may_not_touch_anyone_elses_signature(db, media) -> None:
    """Their own signature is the setup's third step; anyone else's is administration, refused
    until they have finished."""
    admin = _make("admin")
    other = _make("staff")
    User.objects.filter(pk=admin.pk).update(setup_completed_at=None)
    client = _client(admin)
    assert client.get(f"/api/core/users/{admin.pk}/signature").status_code == 200
    assert _is_setup_refusal(client.get(f"/api/core/users/{other.pk}/signature"))
    refused = client.post(f"/api/core/users/{other.pk}/signature/upload", {"file": _upload()})
    assert _is_setup_refusal(refused)
    # Finished (the tests' people are; this one was set back), it is theirs to do again.
    User.objects.filter(pk=admin.pk).update(setup_completed_at=timezone.now())
    assert client.get(f"/api/core/users/{other.pk}/signature").status_code == 200


@pytest.mark.unfinished_setup
@pytest.mark.parametrize("role", ROLES)
def test_the_one_query_answer_agrees_with_the_long_one(role, db, media) -> None:
    """The hold asks `agreements.has_pending` (one query) on every request; it must say exactly
    what `pending` (the setup screen's list) says, through a person's whole story."""

    def same() -> bool:
        person.refresh_from_db()
        long = bool(agreements.pending(person))
        assert agreements.has_pending(person, identity.records_role_of(person)) is long
        assert setup.required(person) is (person.setup_completed_at is None or long)
        return long

    person = _make(role)
    assert same() is True  # nothing accepted
    from portal.core.services import signatures

    signatures.save(person, person.pk, png(60, 20), source="drawn")
    first = agreements.pending(person)[0]
    agreements.accept(person, first.pk, ip_address="192.0.2.9", user_agent="test")
    same()
    for agreement in agreements.pending(person):
        agreements.accept(person, agreement.pk, ip_address="192.0.2.9", user_agent="test")
    assert same() is False
    setup.finish(person)
    assert same() is False and setup.required(person) is False
    publisher = _make("admin")
    current = agreements.current("intern")
    agreements.publish(publisher, "intern", current.body + "\n\nAnother clause.")
    held = same()
    assert held is (role in ("staff", "intern", "requester"))  # the intern agreement's roles


def test_a_signed_out_caller_is_still_a_401_not_the_setups_refusal(db) -> None:
    assert Client().get("/api/core/chat").status_code == 401
    assert Client().get("/api/core/setup").status_code == 401


# ---- 2. renaming yourself renames you everywhere ------------------------------------------------


@pytest.mark.app_role
def test_renaming_yourself_renames_you_in_the_records_too(db) -> None:
    """As portal_app: the records profile's name (what requests, tasks and the audit show) moves
    with the account's, in the same request."""
    person = _make("staff")
    client = _client(person)
    answer = client.patch(
        "/api/core/auth/me",
        {"name": "  Synthetic Renamed  ", "title": "Staff"},
        content_type="application/json",
    )
    assert answer.status_code == 200 and answer.json()["name"] == "Synthetic Renamed"
    person.refresh_from_db()
    profile = RecordsUser.objects.get(pk=person.pk)
    assert person.name == profile.name == "Synthetic Renamed"
    # A blank name changes neither; a time zone alone leaves the name where it is.
    client.patch("/api/core/auth/me", {"name": "   "}, content_type="application/json")
    client.patch(
        "/api/core/auth/me", {"timezone": "America/Chicago"}, content_type="application/json"
    )
    person.refresh_from_db()
    assert person.name == RecordsUser.objects.get(pk=person.pk).name == "Synthetic Renamed"
    assert person.timezone == "America/Chicago"


def test_the_two_names_change_in_one_transaction(db, monkeypatch) -> None:
    """If the records profile cannot be renamed, the account is not renamed either."""
    person = _make("staff")
    original = person.name

    class Refused(Exception):
        pass

    def refuse(self, **kwargs):
        raise Refused()

    monkeypatch.setattr("django.db.models.query.QuerySet.update", refuse)
    with pytest.raises(Refused):
        identity.update_profile(person, name="Synthetic Half Renamed")
    monkeypatch.undo()
    assert User.objects.get(pk=person.pk).name == original
    assert RecordsUser.objects.get(pk=person.pk).name == original


# ---- 6. channel names never reveal a private channel -------------------------------------------


def test_a_name_never_says_that_a_private_channel_exists(db) -> None:
    """A private channel's name can be taken by a public channel and by another private one: the
    answer to "make #x" is the same whether or not someone's private #x exists."""
    insider = _make("staff")
    outsider = _make("intern")
    secret = chat_channels.create_channel(insider, name="hush", private=True)
    also_private = _client(outsider).post(
        "/api/core/chat/channels",
        data=json.dumps({"name": "Hush", "private": True}),
        content_type="application/json",
    )
    assert also_private.status_code == 201, also_private.content
    public = _client(outsider).post(
        "/api/core/chat/channels",
        data=json.dumps({"name": "hush", "private": False}),
        content_type="application/json",
    )
    assert public.status_code == 201, public.content
    also_private = ChatChannel.objects.get(pk=also_private.json()["id"])
    assert {secret.name, also_private.name, public.json()["name"]} == {"hush"}
    assert len({secret.pk, also_private.pk, public.json()["id"]}) == 3


def test_a_public_name_is_still_one_channel_and_private_ones_cannot_take_it(db) -> None:
    """Public names are unique (everyone can see them, so refusing says nothing new), and a
    private channel may not take a public one's name, which would put two #x in one list."""
    maker = _make("staff")
    other = _make("staff_attorney")
    chat_channels.create_channel(maker, name="open-room", private=False)
    client = _client(other)
    for private in (False, True):
        answer = client.post(
            "/api/core/chat/channels",
            data=json.dumps({"name": "OPEN-ROOM", "private": private}),
            content_type="application/json",
        )
        assert answer.status_code == 409, (private, answer.content)
        assert answer.json()["detail"] == "There is already a channel called #open-room."
    with pytest.raises(InvalidTransition):
        chat_channels.create_channel(other, name="open-room", private=True)


def test_the_database_holds_public_names_unique_and_lets_private_ones_repeat(db) -> None:
    maker = _make("staff")

    def row(name: str, private: bool) -> ChatChannel:
        return ChatChannel.objects.create(
            kind=ChatChannel.CHANNEL, name=name, is_private=private, created_by=maker
        )

    row("same", True)
    row("same", True)
    row("same", False)
    with pytest.raises(IntegrityError), transaction.atomic():
        row("SAME", False)


# ---- 7. production serves the socket ----------------------------------------------------------


def test_the_redis_layer_waits_longer_than_its_own_blocking_pop() -> None:
    """With redis-py 8's 5-second read timeout and channels-redis's 5-second blocking pop, an idle
    socket's consumer could give up reading first and die ("Timeout reading from …"); between
    containers it did so within seconds (P23a's Docker run). The layer's connections now read
    for longer than the pop blocks."""
    from channels_redis.core import RedisChannelLayer
    from portal.core.chat.layers import channel_layers

    assert channel_layers("")["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"
    config = channel_layers("redis://redis.invalid:6379/0")["default"]
    assert config["BACKEND"] == "channels_redis.core.RedisChannelLayer"
    (host,) = config["CONFIG"]["hosts"]
    assert host["address"] == "redis://redis.invalid:6379/0"
    assert host["socket_timeout"] >= RedisChannelLayer.brpop_timeout + 10
    # And that is what channels-redis hands redis-py: the pool's connections carry it.
    from channels_redis.utils import create_pool, decode_hosts

    pool = create_pool(decode_hosts(config["CONFIG"]["hosts"])[0])
    assert pool.connection_kwargs["socket_timeout"] == host["socket_timeout"]
