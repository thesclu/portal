"""Changing a role (six since P20h) and deactivating a person. (Changing your own password is
Keycloak's since P25a.)"""

import io

import pytest
from django.core.management import call_command
from portal.core.models import AppRole, User
from portal.core.services import identity
from portal.records.actors import Actor
from portal.records.models import AuditLog, RecordsUser, Task
from portal.records.services import tasks as tasks_service
from portal.records.services.common import ServiceError

pytestmark = pytest.mark.usefixtures("as_each_role")


def _role_row(user):
    return AppRole.objects.get(user=user, app="records")


# ---- the role (P20h: six of them; the Director mark is retired) --------------------------------


@pytest.mark.parametrize(
    "role", ["admin", "staff", "staff_attorney", "limited_attorney", "intern", "requester"]
)
def test_every_one_of_the_six_roles_can_be_given_and_is_recorded_in_both_places(
    staff_user, admin_user, role
) -> None:
    identity.set_role(admin_user, staff_user, role)
    assert identity.records_role_of(staff_user) == role
    assert RecordsUser.objects.get(account=staff_user).role == role
    assert _role_row(staff_user).granted_by_id == admin_user.pk
    staff_user.refresh_from_db()
    assert staff_user.is_staff is (role == "admin")


def test_the_retired_marks_are_read_by_nothing(staff_user, admin_user) -> None:
    """The columns stay (DECISIONS 43) but no door turns on them: a member of staff carrying the
    old Director mark is refused an administrator's door like anyone."""
    AppRole.objects.filter(user=staff_user, app="records").update(
        is_director=True, can_file_without_review=True
    )
    assert not hasattr(identity, "is_director_of")
    assert not hasattr(identity, "can_file_without_review")
    assert identity.records_role_of(staff_user) == "staff"


def test_only_an_administrator_can_change_a_role_and_only_to_a_real_one(
    staff_user, colleague_user, admin_user
) -> None:
    with pytest.raises(identity.IdentityNotAllowed):
        identity.set_role(staff_user, colleague_user, "admin")
    with pytest.raises(identity.IdentityInvalid, match="Unknown role"):
        identity.set_role(admin_user, staff_user, "director")
    assert identity.records_role_of(colleague_user) == "staff"


def test_a_deactivated_administrator_cannot_change_roles(
    staff_user, admin_user, colleague_user
) -> None:
    other = identity.create_user(
        email="admin2@example.org", name="Admin Two", records_role="admin", is_staff=True
    )
    identity.deactivate_user(admin_user, other)
    with pytest.raises(identity.IdentityNotAllowed):
        identity.set_role(other, staff_user, "staff")


def test_a_role_that_sees_less_drops_the_work_it_can_no_longer_open(
    staff_user, admin_user, agency
) -> None:
    """Staff made an intern: whatever they were working that interns may not see is unassigned,
    with an audit line; their grants stay, since a grant is how an intern sees a request."""
    from portal.records.services import requests as requests_service

    actor = Actor.human(admin_user)
    request = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=admin_user.pk,
        subject="Not an intern's",
        request_text="Body.",
    )
    requests_service.assign_request(actor, request.pk, staff_user.pk)
    task = tasks_service.create_task(
        actor, request.pk, "manual", "theirs", assigned_to_user_id=staff_user.pk
    )
    identity.set_role(admin_user, staff_user, "intern")
    request.refresh_from_db()
    task.refresh_from_db()
    assert request.assigned_to_id is None and task.assigned_to_id is None
    lines = AuditLog.objects.filter(entity_id=task.pk, field_name="tasks.assigned_to_user_id")
    assert lines.exists()


# ---- the role is what a person can administer (F5) ---------------------------------------------


@pytest.fixture
def second_admin(db):
    return identity.create_user(
        email="admin2@example.org", name="Admin Two", records_role="admin", is_staff=True
    )


@pytest.mark.parametrize("lower", ["staff", "requester"])
def test_demoting_an_administrator_takes_their_administrator_power_with_it(
    admin_user, second_admin, lower
) -> None:
    """F5: `set_role` changed the records role only, so a demoted administrator kept `is_staff` and
    with it every administrator power -- including promoting themselves straight back."""
    identity.set_role(admin_user, second_admin, lower)

    second_admin.refresh_from_db()
    assert identity.records_role_of(second_admin) == lower
    assert second_admin.is_staff is False
    with pytest.raises(identity.IdentityNotAllowed):
        identity.set_role(second_admin, second_admin, "admin")
    assert identity.records_role_of(second_admin) == lower


def test_promoting_a_member_of_staff_makes_them_a_portal_administrator(
    admin_user, staff_user, colleague_user
) -> None:
    """F5, the other direction: a promoted member of staff was still refused every admin door."""
    assert staff_user.is_staff is False
    identity.set_role(admin_user, staff_user, "admin")

    staff_user.refresh_from_db()
    assert staff_user.is_staff is True and identity.records_role_of(staff_user) == "admin"
    identity.set_role(staff_user, colleague_user, "intern")  # now allowed, was IdentityNotAllowed
    assert identity.records_role_of(colleague_user) == "intern"


def test_a_change_that_is_not_to_or_from_admin_leaves_is_staff_alone(
    admin_user, staff_user
) -> None:
    identity.set_role(admin_user, staff_user, "requester")
    staff_user.refresh_from_db()
    assert staff_user.is_staff is False
    identity.set_role(admin_user, admin_user, "admin")  # admin -> admin
    admin_user.refresh_from_db()
    assert admin_user.is_staff is True


def test_the_last_active_administrator_cannot_be_demoted(admin_user, second_admin) -> None:
    """Demotion is now the way administrator power is taken away, so it keeps the rule that
    `deactivate_user` already keeps: somebody must be left who can restore anyone."""
    identity.set_role(admin_user, second_admin, "staff")  # two admins: one may step down
    with pytest.raises(identity.IdentityInvalid, match="last active administrator"):
        identity.set_role(admin_user, admin_user, "staff_attorney")
    admin_user.refresh_from_db()
    assert admin_user.is_staff is True and identity.records_role_of(admin_user) == "admin"


def test_demoting_one_of_two_administrators_is_allowed_even_when_it_is_yourself(
    admin_user, second_admin
) -> None:
    identity.set_role(admin_user, admin_user, "staff")
    admin_user.refresh_from_db()
    assert admin_user.is_staff is False
    assert identity.records_role_of(admin_user) == "staff"


def test_demoting_a_deactivated_administrator_does_not_count_against_the_last_one(
    admin_user, second_admin
) -> None:
    identity.deactivate_user(admin_user, second_admin)
    identity.set_role(admin_user, second_admin, "staff")
    second_admin.refresh_from_db()
    assert second_admin.is_staff is False
    assert User.objects.get(pk=admin_user.pk).is_staff is True


# ---- bootstrap_admin ---------------------------------------------------------------------------


def test_bootstrap_admin_makes_the_first_administrator(db, monkeypatch) -> None:
    """P25a: no password. The person is made, invited, and their Keycloak account made and
    emailed at once (faked here: Keycloak is test_p25a_keycloak.py's)."""
    from portal.core.keycloak import admin as kc_admin

    asked = []
    monkeypatch.setattr(kc_admin, "sync_person", lambda user: asked.append(("sync", user.email)))
    monkeypatch.setattr(kc_admin, "send_invite", lambda user: asked.append(("invite", user.email)))
    out = io.StringIO()
    call_command(
        "bootstrap_admin", "--email", "first@example.org", "--name", "First Admin", stdout=out
    )
    first = User.objects.get(email="first@example.org")
    assert first.is_staff and identity.records_role_of(first) == "admin"
    assert not first.has_usable_password() and not first.is_active
    assert asked == [("sync", "first@example.org"), ("invite", "first@example.org")]


# ---- deactivating ------------------------------------------------------------------------------


@pytest.fixture
def busy(reserved_for_tasks, staff_user, colleague_user):
    """`colleague_user` holds two open tasks and one finished one."""
    actor = Actor.human(staff_user)
    open_one = tasks_service.create_task(
        actor, reserved_for_tasks.pk, "manual", "One", assigned_to_user_id=colleague_user.pk
    )
    open_two = tasks_service.create_task(
        actor, reserved_for_tasks.pk, "decide_fee", "Two", assigned_to_user_id=colleague_user.pk
    )
    done = tasks_service.create_task(
        actor, reserved_for_tasks.pk, "mail_letter", "Done", assigned_to_user_id=colleague_user.pk
    )
    tasks_service.complete_task(Actor.human(colleague_user), done.pk)
    return open_one, open_two, done


@pytest.fixture
def reserved_for_tasks(db, staff_user, agency):
    from portal.records.services import requests as requests_service

    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Tasks",
        request_text="Body.",
    )


def test_deactivating_hands_the_open_tasks_on_first_and_audits_each(
    busy, admin_user, staff_user, colleague_user
) -> None:
    open_one, open_two, done = busy

    result = identity.deactivate_user(admin_user, colleague_user, reassign_tasks_to=staff_user.pk)

    assert result.is_active is False
    assert not User.objects.get(pk=colleague_user.pk).is_active
    assert Task.objects.get(pk=open_one.pk).assigned_to_id == staff_user.pk
    assert Task.objects.get(pk=open_two.pk).assigned_to_id == staff_user.pk
    # A finished task stays with the person who had it.
    assert Task.objects.get(pk=done.pk).assigned_to_id == colleague_user.pk
    lines = AuditLog.objects.filter(
        entity_type="tasks",
        field_name="tasks.assigned_to_user_id",
        entity_id__in=[open_one.pk, open_two.pk],
    )
    assert lines.count() == 2 and {line.edited_by_id for line in lines} == {admin_user.pk}


def test_deactivating_with_nobody_named_leaves_the_tasks_unassigned(
    busy, admin_user, colleague_user
) -> None:
    open_one, open_two, _ = busy
    identity.deactivate_user(admin_user, colleague_user)
    assert Task.objects.get(pk=open_one.pk).assigned_to_id is None
    assert Task.objects.get(pk=open_two.pk).assigned_to_id is None


def test_a_refused_handover_stops_the_deactivation(busy, admin_user, colleague_user) -> None:
    requester = identity.create_user(
        email="req2@example.org", name="Not Staff", records_role="requester"
    )
    open_one, _, _ = busy
    with pytest.raises(ServiceError, match="requester"):
        identity.deactivate_user(admin_user, colleague_user, reassign_tasks_to=requester.pk)
    assert User.objects.get(pk=colleague_user.pk).is_active
    assert Task.objects.get(pk=open_one.pk).assigned_to_id == colleague_user.pk


def test_deactivating_is_for_administrators_and_refuses_the_last_one_and_a_handback(
    staff_user, admin_user, colleague_user
) -> None:
    with pytest.raises(identity.IdentityNotAllowed):
        identity.deactivate_user(staff_user, colleague_user)
    with pytest.raises(identity.IdentityInvalid, match="last active administrator"):
        identity.deactivate_user(admin_user, admin_user)
    with pytest.raises(identity.IdentityInvalid, match="handed back"):
        identity.deactivate_user(admin_user, colleague_user, reassign_tasks_to=colleague_user.pk)
    assert User.objects.get(pk=admin_user.pk).is_active
    assert User.objects.get(pk=colleague_user.pk).is_active


def test_a_second_administrator_makes_the_first_deactivatable_and_reactivation_works(
    admin_user, colleague_user
) -> None:
    second = identity.create_user(
        email="admin3@example.org", name="Admin Three", records_role="admin", is_staff=True
    )
    identity.deactivate_user(second, admin_user)
    assert not User.objects.get(pk=admin_user.pk).is_active
    identity.reactivate_user(second, admin_user)
    assert User.objects.get(pk=admin_user.pk).is_active
    # Deactivating someone already off is a no-op, not an error.
    identity.deactivate_user(second, colleague_user)
    identity.deactivate_user(second, colleague_user)


def test_a_deactivated_person_cannot_be_assigned_work_afterwards(
    reserved_for_tasks, admin_user, colleague_user, staff_user
) -> None:
    identity.deactivate_user(admin_user, colleague_user)
    with pytest.raises(ServiceError, match="deactivated"):
        tasks_service.create_task(
            Actor.human(staff_user),
            reserved_for_tasks.pk,
            "manual",
            "Too late",
            assigned_to_user_id=colleague_user.pk,
        )
