"""PATCH /api/core/users/{id}. (POST /api/core/auth/password retired in P25a: a password is
changed on Keycloak's own page, test_p25a_keycloak.py.)"""

import json

import pytest
from django.test import Client
from portal.core.models import User
from portal.core.services import identity
from portal.records.actors import Actor
from portal.records.models import Task
from portal.records.services import directory
from portal.records.services import requests as requests_service
from portal.records.services import tasks as tasks_service

pytestmark = pytest.mark.usefixtures("as_each_role")


def _patch(client, user_id, payload):
    return client.patch(
        f"/api/core/users/{user_id}", data=json.dumps(payload), content_type="application/json"
    )


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def _as(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def admin_client(admin_user):
    return _as(admin_user)


# ---- PATCH /users/{id} -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role", ["admin", "staff", "staff_attorney", "limited_attorney", "intern", "requester"]
)
def test_an_admin_gives_any_of_the_six_roles(admin_client, staff_user, role):
    resp = _patch(admin_client, staff_user.pk, {"role": role})
    assert resp.status_code == 200, resp.content
    assert (resp.json()["records_role"], resp.json()["is_staff"]) == (role, role == "admin")


def test_the_retired_marks_are_not_part_of_a_user_any_more(admin_client, staff_user):
    """P20h (42d): the Director and "files without review" marks are gone from the API; a body
    that still sends one changes nothing and the response carries neither."""
    resp = _patch(admin_client, staff_user.pk, {"is_director": True})
    assert resp.status_code == 200, resp.content
    assert "is_director" not in resp.json() and "can_file_without_review" not in resp.json()
    assert resp.json()["records_role"] == "staff"


def test_only_a_portal_administrator_may_patch_a_user(staff_user, colleague_user):
    resp = _patch(_as(staff_user), colleague_user.pk, {"role": "admin"})
    assert resp.status_code == 403
    assert identity.records_role_of(colleague_user) == "staff"
    assert Client().patch("/api/core/users/x", content_type="application/json").status_code == 401


def test_an_unknown_role_or_user_is_refused(admin_client, staff_user):
    assert _patch(admin_client, staff_user.pk, {"role": "director"}).status_code == 422
    import uuid

    assert _patch(admin_client, uuid.uuid4(), {"role": "staff"}).status_code == 404


def test_deactivating_hands_the_open_tasks_to_the_named_person(
    admin_client, admin_user, staff_user, colleague_user, agency
):
    request = requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="S",
        request_text="B.",
    )
    task = tasks_service.create_task(
        Actor.human(staff_user), request.pk, "manual", "T", assigned_to_user_id=colleague_user.pk
    )
    resp = _patch(
        admin_client,
        colleague_user.pk,
        {"is_active": False, "reassign_tasks_to": str(staff_user.pk)},
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["is_active"] is False
    assert Task.objects.get(pk=task.pk).assigned_to_id == staff_user.pk

    resp = _patch(admin_client, colleague_user.pk, {"is_active": True})
    assert resp.status_code == 200 and resp.json()["is_active"] is True


def test_deactivating_without_naming_anyone_hands_the_tasks_to_the_administrator_doing_it(
    admin_client, admin_user, staff_user, colleague_user, agency
):
    """P20h (42f): "Reassign their tasks to" defaults to the administrator doing it; naming
    nobody (null) leaves them open and unassigned, as before."""
    request = requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="S",
        request_text="B.",
    )
    first = tasks_service.create_task(
        Actor.human(staff_user), request.pk, "manual", "T1", assigned_to_user_id=colleague_user.pk
    )
    resp = _patch(admin_client, colleague_user.pk, {"is_active": False})
    assert resp.status_code == 200, resp.content
    assert Task.objects.get(pk=first.pk).assigned_to_id == admin_user.pk

    _patch(admin_client, colleague_user.pk, {"is_active": True})
    second = tasks_service.create_task(
        Actor.human(staff_user), request.pk, "manual", "T2", assigned_to_user_id=colleague_user.pk
    )
    resp = _patch(admin_client, colleague_user.pk, {"is_active": False, "reassign_tasks_to": None})
    assert resp.status_code == 200, resp.content
    assert Task.objects.get(pk=second.pk).assigned_to_id is None


def test_a_deactivated_person_is_signed_out_of_a_session_they_already_had(
    admin_client, colleague_user
):
    colleague = _as(colleague_user)
    assert colleague.get("/api/core/auth/me").status_code == 200
    _patch(admin_client, colleague_user.pk, {"is_active": False})
    assert colleague.get("/api/core/auth/me").status_code == 401


def test_reassign_tasks_to_without_deactivating_is_refused_not_ignored(
    admin_client, colleague_user, staff_user
):
    resp = _patch(admin_client, colleague_user.pk, {"reassign_tasks_to": str(staff_user.pk)})
    assert resp.status_code == 422


def test_the_last_administrator_cannot_be_deactivated(admin_client, admin_user):
    resp = _patch(admin_client, admin_user.pk, {"is_active": False})
    assert resp.status_code == 422 and "last active administrator" in resp.json()["detail"]
    assert User.objects.get(pk=admin_user.pk).is_active


def test_a_refused_part_undoes_the_whole_patch(admin_client, colleague_user, admin_user):
    """A role change sent together with a deactivation that is then refused must not stick: ninja
    turns the refusal into a 4xx without the exception reaching Django, so the request
    transaction would otherwise commit the first half."""
    requester = identity.create_user(
        email="req3@example.org", name="Asker Three", records_role="requester"
    )
    agency = directory.create_agency(
        Actor.human(admin_user), name="Atomic Agency", jurisdiction="Testland"
    )
    request = requests_service.reserve_request(
        Actor.human(admin_user),
        agency_id=agency.pk,
        requester_user_id=admin_user.pk,
        subject="S",
        request_text="B.",
    )
    tasks_service.create_task(
        Actor.human(admin_user), request.pk, "manual", "T", assigned_to_user_id=colleague_user.pk
    )
    resp = _patch(
        admin_client,
        colleague_user.pk,
        {"role": "admin", "is_active": False, "reassign_tasks_to": str(requester.pk)},
    )
    assert resp.status_code == 422, resp.content
    assert identity.records_role_of(colleague_user) == "staff"
    assert User.objects.get(pk=colleague_user.pk).is_active


def test_the_user_list_reports_each_role(admin_client, staff_user, admin_user):
    identity.set_role(admin_user, staff_user, "limited_attorney")
    rows = {u["email"]: u for u in admin_client.get("/api/core/users").json()}
    assert rows[staff_user.email]["records_role"] == "limited_attorney"
    assert rows[admin_user.email]["records_role"] == "admin"


def test_demoting_an_administrator_over_the_api_takes_their_power_and_they_cannot_undo_it(
    admin_client, admin_user
):
    """F5, the reviewer's own sequence: the demoted person PATCHed THEMSELVES back to admin."""
    second = identity.create_user(
        email="admin2@example.org",
        name="Admin Two",
        records_role="admin",
        is_staff=True,
    )
    resp = _patch(admin_client, second.pk, {"role": "requester"})
    assert resp.status_code == 200, resp.content
    assert (resp.json()["records_role"], resp.json()["is_staff"]) == ("requester", False)

    again = _patch(_as(second), second.pk, {"role": "admin"})

    assert again.status_code == 403
    second.refresh_from_db()
    assert second.is_staff is False and identity.records_role_of(second) == "requester"


def test_promoting_staff_over_the_api_lets_them_use_the_admin_doors(
    admin_client, staff_user, colleague_user
):
    """F5, the other direction: PATCH {"role": "admin"} left the person at 403 on this very door."""
    resp = _patch(admin_client, staff_user.pk, {"role": "admin"})
    assert resp.status_code == 200, resp.content
    assert (resp.json()["records_role"], resp.json()["is_staff"]) == ("admin", True)

    resp = _patch(_as(staff_user), colleague_user.pk, {"role": "staff_attorney"})

    assert resp.status_code == 200, resp.content
    assert identity.records_role_of(colleague_user) == "staff_attorney"


def test_the_only_administrator_cannot_demote_themselves_over_the_api(admin_client, admin_user):
    resp = _patch(admin_client, admin_user.pk, {"role": "staff"})
    assert resp.status_code == 422 and "last active administrator" in resp.json()["detail"]
    admin_user.refresh_from_db()
    assert admin_user.is_staff is True and identity.records_role_of(admin_user) == "admin"


def test_a_refused_deactivation_also_undoes_the_administrator_flag(admin_client, colleague_user):
    """The role and `is_staff` now move together, and the whole PATCH is still one transaction."""
    resp = _patch(
        admin_client,
        colleague_user.pk,
        {"role": "admin", "is_active": False, "reassign_tasks_to": str(colleague_user.pk)},
    )
    assert resp.status_code == 422, resp.content
    colleague_user.refresh_from_db()
    assert (colleague_user.is_staff, colleague_user.is_active) == (False, True)
    assert identity.records_role_of(colleague_user) == "staff"


# ---- GET /users --------------------------------------------------------------------------------


def test_a_requester_cannot_list_the_users(staff_user):
    """F4: the list of every person's name, email and role was open to any signed-in account, and
    requesters are outside people. The refusal is the records API's own 403, word for word."""
    requester = identity.create_user(
        email="asker@example.org", name="Asker", records_role="requester"
    )
    client = _as(requester)

    resp = client.get("/api/core/users")

    assert resp.status_code == 403
    assert resp.json() == client.get("/api/records/agencies").json() == {"detail": "Staff only."}
    assert staff_user.email not in resp.content.decode()


def test_someone_with_no_records_role_cannot_list_the_users(staff_user):
    nobody = User.objects.create_user(email="nobody@example.org", name="Nobody")
    assert identity.records_role_of(nobody) is None
    assert _as(nobody).get("/api/core/users").status_code == 403


def test_staff_who_are_not_administrators_can_list_the_users(staff_user, colleague_user):
    """Staff need the list to assign work, so the gate is the records role, not `is_staff`."""
    assert staff_user.is_staff is False
    resp = _as(staff_user).get("/api/core/users")
    assert resp.status_code == 200
    assert {row["email"] for row in resp.json()} >= {staff_user.email, colleague_user.email}


def test_an_administrator_can_list_the_users(admin_client, staff_user, admin_user):
    resp = admin_client.get("/api/core/users")
    assert resp.status_code == 200
    assert {row["email"] for row in resp.json()} >= {staff_user.email, admin_user.email}


def test_the_user_list_needs_a_session(db):
    assert Client().get("/api/core/users").status_code == 401


def test_the_portal_has_no_password_door_any_more(staff_user):
    """P25a: changing a password is Keycloak's page (an application-initiated action)."""
    resp = _post(_as(staff_user), "/api/core/auth/password", {"current_password": "x"})
    assert resp.status_code in (404, 405)
