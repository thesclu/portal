"""P17: the policies in Postgres, exercised as portal_app with a web context, the way an API
request runs. A service or a query that forgets P9's rule still cannot read a privileged
request's rows for a person who is not on its list."""

from datetime import UTC, datetime

import pytest
from django.db import connection
from django.test import Client
from portal.core import rls
from portal.core.services import identity
from portal.records.actors import Actor
from portal.records.models import Deadline, Event, Request, RequestItem, Source, Task
from portal.records.services import access
from portal.records.services import events as events_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services import tasks as tasks_service

from tests.p20h_helpers import give, take

pytestmark = pytest.mark.app_role

WHEN = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def _web(user) -> None:
    rls.set_web_context(user)


def _system() -> None:
    rls.set_system_context()


@pytest.fixture
def world(staff_user, colleague_user, agency):
    """Two requests by the staff user: one public, one privileged, each with an item, an event
    on a source, a deadline and a task. Built as the portal's own work."""
    actor = Actor.human(staff_user)
    rows = {}
    for sensitivity in ("public", "privileged"):
        request = requests_service.reserve_request(
            actor,
            agency_id=agency.pk,
            requester_user_id=staff_user.pk,
            subject=f"{sensitivity} widgets",
            request_text="Body.",
            sensitivity=sensitivity,
            items=[requests_service.NewItem(item_number="1", description="Item one")],
        )
        source = sources_service.register_source(
            actor, source_type="postmark_email", raw_storage_url=f"test://{sensitivity}.eml"
        )
        events_service.record_event(
            actor,
            request.pk,
            event_type="correspondence",
            channel="email",
            direction="inbound",
            occurred_at=WHEN,
            display_body=f"{sensitivity} letter",
            source_id=source.pk,
        )
        tasks_service.create_task(actor, request.pk, "manual", f"{sensitivity} task")
        rows[sensitivity] = (request, source)
    return rows


def _counts() -> dict[str, int]:
    return {
        "requests": Request.objects.count(),
        "items": RequestItem.objects.count(),
        "events": Event.objects.count(),
        "sources": Source.objects.count(),
        "tasks": Task.objects.count(),
        "deadlines": Deadline.objects.count(),
    }


def test_a_person_off_the_list_cannot_read_a_privileged_request_or_anything_on_it(
    world, staff_user, colleague_user
) -> None:
    public, sealed = world["public"][0], world["privileged"][0]
    _system()
    everything = _counts()
    assert everything["requests"] == 2 and everything["events"] == 2
    # The requester of record sees both; a colleague who is not on the list sees one of each.
    _web(staff_user)
    assert _counts() == everything
    _web(colleague_user)
    seen = _counts()
    assert seen == {k: v // 2 for k, v in everything.items()}
    assert Request.objects.filter(pk=sealed.pk).exists() is False
    assert Request.objects.filter(pk=public.pk).exists() is True
    with pytest.raises(Request.DoesNotExist):
        Request.objects.get(pk=sealed.pk)
    # The ORM path a bug would take: every child by request id, and the source by its event.
    assert not Event.objects.filter(request_id=sealed.pk).exists()
    assert not Task.objects.filter(request_id=sealed.pk).exists()
    assert not Source.objects.filter(pk=world["privileged"][1].pk).exists()
    assert Source.objects.filter(pk=world["public"][1].pk).exists()
    # Unattached mail is staff's; a requester sees nothing of the Inbox.
    _system()
    loose = sources_service.register_source(
        Actor.human(staff_user), source_type="postmark_email", raw_storage_url="test://loose.eml"
    )
    _web(colleague_user)
    assert Source.objects.filter(pk=loose.pk).exists()
    # Nor can they write to it: an update on an invisible row touches nothing.
    assert Request.objects.filter(pk=sealed.pk).update(subject="x") == 0
    _system()
    assert Request.objects.get(pk=sealed.pk).subject == "privileged widgets"


def test_a_grant_opens_it_and_a_revoke_closes_it(world, staff_user, colleague_user) -> None:
    sealed = world["privileged"][0]
    _system()
    row = give(sealed.pk, colleague_user.pk, "Please.")
    _web(colleague_user)
    assert Request.objects.filter(pk=sealed.pk).exists()
    assert Event.objects.filter(request_id=sealed.pk).count() == 1
    _system()
    take(row.pk)
    _web(colleague_user)
    assert not Request.objects.filter(pk=sealed.pk).exists()


def test_a_requester_sees_only_their_own_and_no_one_elses_mail(
    world, staff_user, colleague_user, agency
) -> None:
    _system()
    requester = identity.create_user(
        email="req@example.org", name="Req Person", records_role="requester"
    )
    theirs = requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=requester.pk,
        subject="Theirs",
        request_text="B.",
    )
    _web(requester)
    assert [r.pk for r in Request.objects.all()] == [theirs.pk]
    assert Source.objects.count() == 0


def test_an_administrator_off_the_list_sees_it_and_the_index_is_empty(
    world, staff_user, admin_user
) -> None:
    """P20h (37a: privileged requests stay grant-only except for admins): an administrator reads a
    privileged request and everything on it with no grant, so the index of the ones they are not
    on, kept for its route, lists nothing."""
    sealed = world["privileged"][0]
    _web(admin_user)
    assert Request.objects.filter(pk=sealed.pk).exists()
    assert Event.objects.filter(request_id=sealed.pk).count() == 1
    assert Source.objects.filter(pk=world["privileged"][1].pk).exists()
    assert access.unlisted_privileged(access.Viewer(user_id=admin_user.pk, role="admin")) == []
    _web(staff_user)
    assert access.unlisted_privileged(access.Viewer(user_id=staff_user.pk, role="staff")) == []


def test_no_context_means_no_rows_and_the_api_sets_one(world, colleague_user) -> None:
    with connection.cursor() as cur:
        cur.execute("select set_config('app.context', '', true)")
    assert Request.objects.count() == 0 and Source.objects.count() == 0
    assert rls.current_context() == ""
    # The session auth sets the web context inside the request; the docket lists what it may.
    client = Client()
    client.force_login(colleague_user)
    body = client.get("/api/records/requests").json()
    assert [r["subject"] for r in body["items"]] == ["public widgets"]
    assert client.get(f"/api/records/requests/{world['privileged'][0].pk}").status_code == 403


def test_staff_off_the_list_can_still_ask_for_access_and_are_refused_by_name(
    world, staff_user, colleague_user
) -> None:
    sealed = world["privileged"][0]
    _web(colleague_user)
    assert access.sealed_tracking_number(sealed.pk) == sealed.tracking_number
    assert access.sealed_tracking_number(world["public"][0].pk) is None
    ask = access.ask_access(Actor.human(colleague_user), sealed.pk, "I am on the matter.")
    assert ask.request_id == sealed.pk
    client = Client()
    client.force_login(colleague_user)
    refused = client.get(f"/api/records/requests/{sealed.pk}")
    assert refused.status_code == 403 and sealed.tracking_number in refused.content.decode()
    assert (
        client.get("/api/records/requests/00000000-0000-0000-0000-000000000001").status_code == 404
    )


def test_the_ask_route_reaches_a_request_the_asker_cannot_read(world, colleague_user) -> None:
    """P20f: the route checked that the request exists with a read the policies refuse, so every
    ask from someone off the list came back 404 (the service-level test above never went
    through the route)."""
    sealed = world["privileged"][0]
    client = Client()
    client.force_login(colleague_user)
    asked = client.post(
        f"/api/records/requests/{sealed.pk}/access-requests",
        {"reason": "I am on the matter."},
        content_type="application/json",
    )
    assert asked.status_code == 201, asked.content
    assert asked.json()["tracking_number"] == sealed.tracking_number
    missing = client.post(
        "/api/records/requests/00000000-0000-0000-0000-000000000001/access-requests",
        {"reason": "Nothing there."},
        content_type="application/json",
    )
    assert missing.status_code == 404
    # P20g (records_db.0019): the asker reads their own ask back through "mine".
    mine = client.get("/api/records/access-requests/mine").json()
    assert [(a["request_id"], a["tracking_number"]) for a in mine] == [
        (str(sealed.pk), sealed.tracking_number)
    ]


def test_the_asker_reads_their_own_ask_and_nobody_else_off_the_list_does(
    world, staff_user, colleague_user
) -> None:
    """P20g, records_db.0019: one SELECT policy more on `access_requests`, the asker's own rows
    under the web context. Another person off the list still reads nothing of it."""
    from portal.core.services.identity import create_user
    from portal.records.models import AccessRequest

    sealed = world["privileged"][0]
    _system()
    third = create_user(email="third@example.org", name="Third Person", records_role="staff")
    _web(colleague_user)
    ask = access.ask_access(Actor.human(colleague_user), sealed.pk, "I am on the matter.")
    # The asker: their own ask, by the service and by the table.
    assert [a.pk for a in access.my_asks(colleague_user.pk)] == [ask.pk]
    assert AccessRequest.objects.filter(pk=ask.pk).values_list("reason", flat=True).get() == (
        "I am on the matter."
    )
    # Another member of staff who is not on the list: nothing, by any path.
    _web(third)
    assert not AccessRequest.objects.filter(pk=ask.pk).exists()
    assert access.my_asks(colleague_user.pk) == []
    assert access.my_asks(third.pk) == []
    # Someone on the list (the requester of record) still reads it, as before.
    _web(staff_user)
    assert AccessRequest.objects.filter(pk=ask.pk).exists()
    # No context, no rows: the new policy is the web context's only.
    with connection.cursor() as cur:
        cur.execute("select set_config('app.context', '', true)")
        cur.execute("select set_config('app.user_id', %s, true)", [str(colleague_user.pk)])
    assert not AccessRequest.objects.filter(pk=ask.pk).exists()


def test_reading_ones_own_ask_opens_nothing_of_the_request(world, colleague_user) -> None:
    """The ask holds the request's id, the reason and the decision. Reading it must not make the
    request, or anything hanging off it, readable to the asker."""
    from portal.records.models import AccessRequest

    sealed = world["privileged"][0]
    _web(colleague_user)
    access.ask_access(Actor.human(colleague_user), sealed.pk, "I am on the matter.")
    assert AccessRequest.objects.filter(user_id=colleague_user.pk).count() == 1
    # A join through the ask still meets the request's own policy.
    assert not AccessRequest.objects.filter(
        user_id=colleague_user.pk, request__subject__isnull=False
    ).exists()
    assert not Request.objects.filter(pk=sealed.pk).exists()
    assert not Event.objects.filter(request_id=sealed.pk).exists()
    assert not Task.objects.filter(request_id=sealed.pk).exists()
    assert not RequestItem.objects.filter(request_id=sealed.pk).exists()
    # Through the API: the ask names only what the refusal already named, and the request stays
    # the privileged 403.
    client = Client()
    client.force_login(colleague_user)
    mine = client.get("/api/records/access-requests/mine").json()
    assert len(mine) == 1
    assert "privileged widgets" not in str(mine)
    assert set(mine[0]) == {
        "id",
        "request_id",
        "tracking_number",
        "user_id",
        "user_name",
        "reason",
        "created_at",
        "decided_at",
        "decided_by_user_id",
        "decision",
        "decision_note",
    }
    assert client.get(f"/api/records/requests/{sealed.pk}").status_code == 403


# ---- P20h: six roles, the same rule in Postgres and in Python -----------------------------------


def _person(email: str, role: str, **extra):
    return identity.create_user(email=email, name=email.split("@")[0], records_role=role, **extra)


@pytest.fixture
def roles_world(agency):
    """Six requests and a person of every role, built as the portal's own work.

      ordinary   requester of record a requester; given to nobody
      to_counsel ordinary, given to the limited-scope attorney
      to_intern  ordinary, given to the intern
      by_intern  ordinary, made by the second intern (they are its requester of record)
      sealed     privileged, reserved by the maker (who is on its list)
      sealed_int privileged, made by the second intern (its requester of record, and on the list
                 as the person who reserved it)
    and one message placed on nothing.
    """
    _system()
    people = {
        "maker": _person("roles-maker@example.org", "staff"),
        "admin": _person("roles-admin@example.org", "admin", is_staff=True),
        "staff": _person("roles-staff@example.org", "staff"),
        "attorney": _person("roles-attorney@example.org", "staff_attorney"),
        "counsel": _person("roles-counsel@example.org", "limited_attorney"),
        "counsel_none": _person("roles-counsel-none@example.org", "limited_attorney"),
        "intern": _person("roles-intern@example.org", "intern"),
        "intern_maker": _person("roles-intern-maker@example.org", "intern"),
        "requester": _person("roles-requester@example.org", "requester"),
    }
    maker = Actor.human(people["maker"])

    def reserve(name, *, by=maker, requester="requester", sensitivity="internal"):
        request = requests_service.reserve_request(
            by,
            agency_id=agency.pk,
            requester_user_id=people[requester].pk,
            subject=f"{name} widgets",
            request_text="Body.",
            sensitivity=sensitivity,
        )
        tasks_service.create_task(maker, request.pk, "manual", f"{name} task")
        return request

    requests = {
        "ordinary": reserve("ordinary"),
        "to_counsel": reserve("to_counsel"),
        "to_intern": reserve("to_intern"),
        "by_intern": reserve(
            "by_intern", by=Actor.human(people["intern_maker"]), requester="intern_maker"
        ),
        "sealed": reserve("sealed", sensitivity="privileged"),
        "sealed_int": reserve(
            "sealed_int",
            by=Actor.human(people["intern_maker"]),
            requester="intern_maker",
            sensitivity="privileged",
        ),
    }
    give(requests["to_counsel"].pk, people["counsel"].pk)
    give(requests["to_intern"].pk, people["intern"].pk)
    loose = sources_service.register_source(
        maker, source_type="postmark_email", raw_storage_url="test://roles-loose.eml"
    )
    return people, requests, loose


# Who sees which of `roles_world`'s requests (DECISIONS 37a, 43).
EXPECTED = {
    "admin": {"ordinary", "to_counsel", "to_intern", "by_intern", "sealed", "sealed_int"},
    "maker": {"ordinary", "to_counsel", "to_intern", "by_intern", "sealed"},
    "staff": {"ordinary", "to_counsel", "to_intern", "by_intern"},
    "attorney": {"ordinary", "to_counsel", "to_intern", "by_intern"},
    "counsel": {"to_counsel"},
    "counsel_none": set(),
    "intern": {"to_intern", "by_intern"},
    "intern_maker": {"by_intern", "sealed_int"},
    "requester": {"ordinary", "to_counsel", "to_intern", "sealed"},
}
SEES_UNPLACED_MAIL = {"admin", "maker", "staff", "attorney"}


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_each_role_sees_exactly_its_requests_in_postgres_and_in_python(roles_world, who) -> None:
    people, requests, loose = roles_world
    names = {r.pk: name for name, r in requests.items()}
    person = people[who]
    viewer = access.Viewer(user_id=person.pk, role=identity.records_role_of(person))
    expected = EXPECTED[who]

    _web(person)
    # Postgres: the request rows, and the rows hanging off them, follow the rule.
    assert {names[pk] for pk in Request.objects.values_list("pk", flat=True)} == expected
    tasks = {t.description.split(" ")[0] for t in Task.objects.all()}
    assert tasks == expected
    assert Source.objects.filter(pk=loose.pk).exists() is (who in SEES_UNPLACED_MAIL)

    _system()
    # Python: the queryset rule, the in-hand rule and the bulk rule say the same.
    everything = list(Request.objects.all())
    assert {names[r.pk] for r in Request.objects.filter(access.visible_q(viewer))} == expected
    assert {names[r.pk] for r in everything if access.can_view(viewer, r)} == expected
    assert {names[r.pk] for r in access.scope(viewer, everything)} == expected
    assert access.can_view_source(viewer, loose.pk) is (who in SEES_UNPLACED_MAIL)


@pytest.mark.parametrize("who", ["admin", "staff", "attorney", "counsel", "intern", "requester"])
def test_counts_and_totals_follow_what_each_role_may_see(roles_world, who) -> None:
    """A request hidden from someone moves no figure they are shown: the Docket's total and its
    stat strip count only what they may see."""
    people, _, _ = roles_world
    client = Client()
    client.force_login(people[who])
    docket = client.get("/api/records/requests")
    assert docket.status_code == 200
    assert docket.json()["total"] == len(EXPECTED[who])
    if who == "requester":
        # A requester's own list (My requests); the team's strip and tasks are not theirs.
        assert client.get("/api/records/requests/summary").status_code == 403
        return
    summary = client.get("/api/records/requests/summary").json()
    assert summary["all_open"] == len(EXPECTED[who])
    tasks = client.get("/api/records/tasks?scope=everyone").json()
    assert len(tasks) == len(EXPECTED[who])


def test_a_role_that_sees_less_is_refused_a_request_without_being_told_it_exists(
    roles_world,
) -> None:
    """A limited-scope attorney or an intern opening a request they were not given gets the plain
    refusal a random id gets, never the privileged one that names it."""
    people, requests, _ = roles_world
    for who in ("counsel", "intern"):
        client = Client()
        client.force_login(people[who])
        for name in ("ordinary", "sealed"):
            hidden = client.get(f"/api/records/requests/{requests[name].pk}")
            nothing = client.get("/api/records/requests/00000000-0000-0000-0000-000000000000")
            assert hidden.status_code == nothing.status_code == 403
            assert hidden.json() == nothing.json()
            assert requests[name].tracking_number not in hidden.content.decode()


def test_only_an_administrator_gives_or_takes_access(roles_world) -> None:
    people, requests, _ = roles_world
    target = requests["ordinary"]
    for who in ("maker", "staff", "attorney", "counsel", "intern"):
        client = Client()
        client.force_login(people[who])
        refused = client.post(
            f"/api/records/requests/{target.pk}/access",
            data={"user_id": str(people["counsel_none"].pk)},
            content_type="application/json",
        )
        assert refused.status_code == 403, who
    admin = Client()
    admin.force_login(people["admin"])
    given = admin.post(
        f"/api/records/requests/{target.pk}/access",
        data={"user_id": str(people["counsel_none"].pk)},
        content_type="application/json",
    )
    assert given.status_code == 201
    row = given.json()["id"]
    staff = Client()
    staff.force_login(people["staff"])
    assert staff.post(f"/api/records/access/{row}/revoke").status_code == 403
    assert admin.post(f"/api/records/access/{row}/revoke").status_code == 200
