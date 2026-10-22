"""The access list of a privileged request: the predicate, sharing, removing, and the rules that
keep a privileged request from being left with nobody on its list (BUILD_PLAN P9, rule 4).

P20h (DECISIONS 37a, 43): an administrator sees every privileged request with no grant, and only
an administrator gives or takes a grant; a grant on an ordinary request is how a limited-scope
attorney or an intern sees it. The six-role rule itself is pinned in tests/test_rls.py.

Runs as the owner and again as portal_app, so every write here is proved under the grants
records_db.0010 wrote. The race on the last grant is in test_p9_concurrency.py.
"""

import uuid

import pytest
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.models import Request, RequestAccess
from portal.records.services import access
from portal.records.services import edits as edits_service
from portal.records.services import requests as requests_service
from portal.records.services.access import Viewer
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError

from tests.p20h_helpers import give, giver, take

pytestmark = pytest.mark.usefixtures("as_each_role")


def _reserve(actor, agency, requester, *, sensitivity="privileged", subject="Sealed"):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=requester.pk,
        subject=subject,
        request_text="Body.",
        sensitivity=sensitivity,
    )


@pytest.fixture
def requester_person(db):
    return create_user(
        email="requester@example.org",
        name="Requester Person",
        records_role="requester",
    )


@pytest.fixture
def privileged(staff_actor, staff_user, agency, requester_person):
    """Reserved by staff_user (so they are on the list), for requester_person."""
    return _reserve(staff_actor, agency, requester_person)


def _viewer(user, role):
    return Viewer(user_id=user.pk, role=role)


def _active(request):
    return {row.user_id for row in access.active_access(request.pk)}


# ---- the predicate ----------------------------------------------------------------------------


def test_a_privileged_request_is_seen_by_its_list_and_its_requester_and_nobody_else(
    privileged, staff_user, colleague_user, admin_user, requester_person
) -> None:
    assert access.can_view(_viewer(staff_user, "staff"), privileged)
    assert access.can_view(_viewer(requester_person, "requester"), privileged)
    assert not access.can_view(_viewer(colleague_user, "staff"), privileged)
    assert not access.can_view(_viewer(colleague_user, "staff_attorney"), privileged)
    assert not access.can_view(_viewer(colleague_user, "limited_attorney"), privileged)
    assert not access.can_view(_viewer(colleague_user, "intern"), privileged)
    # P20h: "privileged requests stay grant-only except for admins" (37a).
    assert access.can_view(_viewer(admin_user, "admin"), privileged)
    assert not access.can_view(_viewer(colleague_user, None), privileged)


def test_other_requests_are_seen_as_before(
    staff_actor, agency, staff_user, colleague_user, admin_user, requester_person
) -> None:
    other_requester = create_user(
        email="other-requester@example.org",
        name="Other Requester",
        records_role="requester",
    )
    for sensitivity in ("public", "internal"):
        request = _reserve(staff_actor, agency, requester_person, sensitivity=sensitivity)
        assert access.can_view(_viewer(colleague_user, "staff"), request)
        assert access.can_view(_viewer(admin_user, "admin"), request)
        assert access.can_view(_viewer(requester_person, "requester"), request)
        assert not access.can_view(_viewer(other_requester, "requester"), request)
        assert not access.can_view(_viewer(colleague_user, None), request)
        assert _active(request) == set()


def test_a_staff_member_who_is_the_requester_of_record_sees_their_privileged_request(
    staff_actor, agency, colleague_user
) -> None:
    request = _reserve(staff_actor, agency, colleague_user)
    assert access.can_view(_viewer(colleague_user, "staff"), request)


def test_a_requester_sees_only_their_own_even_with_a_grant_left_from_a_staff_role(
    privileged, staff_actor, colleague_user
) -> None:
    give(privileged.pk, colleague_user.pk)
    assert not access.can_view(_viewer(colleague_user, "requester"), privileged)


def test_the_three_forms_of_the_rule_agree(
    staff_actor, agency, staff_user, colleague_user, admin_user, requester_person
) -> None:
    """`can_view`, `scope` and `visible_q` are one rule in three shapes. Every viewer, every kind
    of request: the three must give the same answer, and a new clause added to one and not the
    others fails here."""
    colleague_actor = Actor.human(colleague_user)
    requests = [
        _reserve(staff_actor, agency, requester_person, sensitivity="public"),
        _reserve(staff_actor, agency, requester_person, sensitivity="internal"),
        _reserve(staff_actor, agency, requester_person),
        _reserve(colleague_actor, agency, colleague_user),
        _reserve(colleague_actor, agency, staff_user),
        _reserve(staff_actor, agency, admin_user, sensitivity="internal"),
    ]
    viewers = [
        _viewer(staff_user, "staff"),
        _viewer(colleague_user, "staff"),
        _viewer(colleague_user, "staff_attorney"),
        _viewer(colleague_user, "limited_attorney"),
        _viewer(colleague_user, "intern"),
        _viewer(staff_user, "intern"),
        _viewer(admin_user, "admin"),
        _viewer(requester_person, "requester"),
        _viewer(admin_user, "requester"),
        _viewer(staff_user, None),
    ]
    everything = list(Request.objects.filter(pk__in=[r.pk for r in requests]))
    seen_something = set()
    for viewer in viewers:
        one_by_one = {r.pk for r in everything if access.can_view(viewer, r)}
        scoped = {r.pk for r in access.scope(viewer, everything)}
        queried = set(
            Request.objects.filter(
                access.visible_q(viewer), pk__in=one_by_one | scoped
            ).values_list("pk", flat=True)
        ) | set(
            Request.objects.filter(access.visible_q(viewer))
            .filter(pk__in=[r.pk for r in everything])
            .values_list("pk", flat=True)
        )
        by_ids = access.visible_ids(viewer, [r.pk for r in everything] + [uuid.uuid4()])
        assert one_by_one == scoped == queried == by_ids, viewer
        seen_something |= one_by_one
    # Not vacuous: the viewers differ, and nobody without a role sees anything.
    assert seen_something == {r.pk for r in everything}
    assert access.visible_ids(viewers[-1], [r.pk for r in everything]) == set()
    assert access.visible_ids(viewers[0], [r.pk for r in everything]) != access.visible_ids(
        viewers[1], [r.pk for r in everything]
    )


def test_visible_q_takes_a_path_to_the_request(privileged, staff_user, colleague_user) -> None:
    from portal.records.models import StatusChange

    rows = StatusChange.objects.filter(request_id=privileged.pk)
    assert rows.filter(access.visible_q(_viewer(staff_user, "staff"), "request__")).exists()
    assert not rows.filter(access.visible_q(_viewer(colleague_user, "staff"), "request__")).exists()
    assert not rows.filter(access.visible_q(_viewer(colleague_user, None), "request__")).exists()


# ---- rule 4: nobody is locked out by accident -------------------------------------------------


def test_reserving_a_privileged_request_puts_the_person_reserving_it_on_the_list(
    privileged, staff_user
) -> None:
    (row,) = access.active_access(privileged.pk)
    assert (row.user_id, row.granted_by_id) == (staff_user.pk, staff_user.pk)
    assert row.note == "Reserved the request."


def test_a_system_actor_reserving_a_privileged_request_is_granted_nothing(
    system_actor, agency, requester_person
) -> None:
    request = _reserve(system_actor, agency, requester_person)
    assert _active(request) == set()
    assert access.can_view(_viewer(requester_person, "requester"), request)


def test_making_a_request_privileged_grants_the_editor_in_the_same_edit(
    staff_actor, agency, staff_user, colleague_user, requester_person
) -> None:
    request = _reserve(staff_actor, agency, requester_person, sensitivity="internal")
    colleague = Actor.human(colleague_user)
    edits_service.edit_with_audit(
        colleague, "requests", request.pk, "sensitivity", "privileged", "Counsel asked."
    )
    assert _active(request) == {colleague_user.pk}
    request.refresh_from_db()
    assert access.can_view(_viewer(colleague_user, "staff"), request)
    assert not access.can_view(_viewer(staff_user, "staff"), request)


def test_an_edit_that_leaves_it_privileged_changes_nothing_on_the_list(
    privileged, staff_actor, staff_user, colleague_user
) -> None:
    give(privileged.pk, colleague_user.pk)
    before = RequestAccess.objects.filter(request_id=privileged.pk).count()
    edits_service.edit_with_audit(
        staff_actor, "requests", privileged.pk, "sensitivity", "privileged", "No change."
    )
    assert _active(privileged) == {staff_user.pk, colleague_user.pk}
    assert RequestAccess.objects.filter(request_id=privileged.pk).count() == before


def test_a_system_actor_cannot_change_who_may_see_a_request(
    system_actor, staff_actor, agency, requester_person
) -> None:
    request = _reserve(staff_actor, agency, requester_person, sensitivity="internal")
    with pytest.raises(NotAllowed):
        edits_service.edit_with_audit(
            system_actor, "requests", request.pk, "sensitivity", "privileged", "A job did it."
        )
    request.refresh_from_db()
    assert request.sensitivity == "internal"


def test_making_it_not_privileged_revokes_the_list_so_it_cannot_come_back_unseen(
    privileged, staff_actor, staff_user, colleague_user
) -> None:
    give(privileged.pk, colleague_user.pk)
    edits_service.edit_with_audit(
        staff_actor, "requests", privileged.pk, "sensitivity", "internal", "No longer sealed."
    )
    assert _active(privileged) == set()
    rows = RequestAccess.objects.filter(request_id=privileged.pk)
    assert rows.count() == 2 and all(r.revoked_by_id == staff_user.pk for r in rows.all())

    # Privileged again, by the colleague: the old list does not revive. Only the editor is on it.
    edits_service.edit_with_audit(
        Actor.human(colleague_user),
        "requests",
        privileged.pk,
        "sensitivity",
        "privileged",
        "Sealed again.",
    )
    assert _active(privileged) == {colleague_user.pk}
    assert rows.all().count() == 3


def test_unsealing_keeps_the_grants_of_those_who_see_an_ordinary_request_only_by_one(
    privileged, staff_actor, staff_user
) -> None:
    """P20h: a limited-scope attorney or an intern on the list keeps exactly what they had; staff
    come off (they see an ordinary request anyway, and a list must not revive unseen)."""
    counsel = create_user(
        email="counsel@example.org",
        name="Counsel Person",
        records_role="limited_attorney",
    )
    give(privileged.pk, counsel.pk)
    edits_service.edit_with_audit(
        staff_actor, "requests", privileged.pk, "sensitivity", "internal", "No longer sealed."
    )
    assert _active(privileged) == {counsel.pk}
    privileged.refresh_from_db()
    assert access.can_view(_viewer(counsel, "limited_attorney"), privileged)


def test_a_limited_attorney_or_intern_cannot_change_whether_a_request_is_privileged(
    staff_actor, agency, requester_person
) -> None:
    request = _reserve(staff_actor, agency, requester_person, sensitivity="internal")
    for role in ("limited_attorney", "intern"):
        person = create_user(email=f"{role}@example.org", name=role, records_role=role)
        give(request.pk, person.pk)
        with pytest.raises(NotAllowed, match="Only staff"):
            edits_service.edit_with_audit(
                Actor.human(person), "requests", request.pk, "sensitivity", "privileged", "x"
            )
    request.refresh_from_db()
    assert request.sensitivity == "internal"


def test_the_last_grant_cannot_be_revoked(privileged, staff_actor, staff_user) -> None:
    (row,) = access.active_access(privileged.pk)
    with pytest.raises(InvalidTransition, match="last person"):
        take(row.pk)
    assert _active(privileged) == {staff_user.pk}


def test_a_grant_held_by_someone_who_cannot_sign_in_does_not_count_as_another_person(
    privileged, staff_actor, staff_user, colleague_user
) -> None:
    """Two rows, one usable person. Removing the usable one would leave the request to nobody."""
    give(privileged.pk, colleague_user.pk)
    type(colleague_user).objects.filter(pk=colleague_user.pk).update(is_active=False)
    mine = next(r for r in access.active_access(privileged.pk) if r.user_id == staff_user.pk)
    with pytest.raises(InvalidTransition, match="last person"):
        take(mine.pk)
    # The unusable one can go.
    theirs = next(r for r in access.active_access(privileged.pk) if r.user_id == colleague_user.pk)
    take(theirs.pk)
    assert _active(privileged) == {staff_user.pk}


# ---- sharing and removing (P20h: an administrator's) ------------------------------------------


def test_an_administrator_shares_and_the_row_says_who_and_why(
    privileged, admin_user, colleague_user
) -> None:
    admin = Actor.human(admin_user)
    row = access.grant_access(admin, privileged.pk, colleague_user.pk, "  Second chair.  ")
    assert (row.user_id, row.granted_by_id, row.note) == (
        colleague_user.pk,
        admin_user.pk,
        "Second chair.",
    )
    assert row.revoked_at is None
    assert access.can_view(_viewer(colleague_user, "staff"), privileged)


def test_nobody_but_an_administrator_shares_not_even_someone_on_the_list(
    privileged, staff_actor, staff_user, colleague_user, admin_user
) -> None:
    for actor in (staff_actor, Actor.human(colleague_user)):
        with pytest.raises(NotAllowed, match="Only an administrator"):
            access.grant_access(actor, privileged.pk, colleague_user.pk)
    assert _active(privileged) == {staff_user.pk}


def test_an_ordinary_request_is_given_to_a_limited_attorney_or_an_intern_the_same_way(
    staff_actor, agency, requester_person, admin_user
) -> None:
    request = _reserve(staff_actor, agency, requester_person, sensitivity="internal")
    for role in ("limited_attorney", "intern"):
        person = create_user(email=f"given-{role}@example.org", name=role, records_role=role)
        assert not access.can_view(_viewer(person, role), request)
        row = access.grant_access(Actor.human(admin_user), request.pk, person.pk, "Yours.")
        assert access.can_view(_viewer(person, role), request)
        access.revoke_access(Actor.human(admin_user), row.pk)
        assert not access.can_view(_viewer(person, role), request)


def test_sharing_is_refused_for_the_wrong_request_person_or_actor(
    privileged, system_actor, colleague_user, requester_person
) -> None:
    admin = Actor.human(giver())
    with pytest.raises(NotFound):
        access.grant_access(admin, uuid.uuid4(), colleague_user.pk)
    with pytest.raises(NotFound):
        access.grant_access(admin, privileged.pk, uuid.uuid4())
    with pytest.raises(ServiceError, match="requester"):
        access.grant_access(admin, privileged.pk, requester_person.pk)
    with pytest.raises(NotAllowed):
        access.grant_access(system_actor, privileged.pk, colleague_user.pk)
    type(colleague_user).objects.filter(pk=colleague_user.pk).update(is_active=False)
    with pytest.raises(ServiceError, match="deactivated"):
        access.grant_access(admin, privileged.pk, colleague_user.pk)
    assert RequestAccess.objects.filter(request_id=privileged.pk).count() == 1


def test_sharing_twice_is_refused_and_sharing_again_after_a_revoke_is_a_new_row(
    privileged, colleague_user
) -> None:
    first = give(privileged.pk, colleague_user.pk)
    with pytest.raises(InvalidTransition, match="already has access"):
        give(privileged.pk, colleague_user.pk)
    take(first.pk)
    second = give(privileged.pk, colleague_user.pk)
    assert second.pk != first.pk
    history = RequestAccess.objects.filter(request_id=privileged.pk, user_id=colleague_user.pk)
    assert history.count() == 2 and history.filter(revoked_at__isnull=True).count() == 1


def test_revoking_ends_access_and_records_who_did_it(
    privileged, admin_user, colleague_user
) -> None:
    row = give(privileged.pk, colleague_user.pk)
    revoked = access.revoke_access(Actor.human(admin_user), row.pk)
    assert revoked.revoked_at is not None and revoked.revoked_by_id == admin_user.pk
    assert not access.can_view(_viewer(colleague_user, "staff"), privileged)
    with pytest.raises(InvalidTransition, match="already been removed"):
        access.revoke_access(Actor.human(admin_user), row.pk)


def test_revoking_is_refused_for_anyone_but_an_administrator(
    privileged, staff_actor, staff_user, system_actor, colleague_user
) -> None:
    row = give(privileged.pk, colleague_user.pk)
    mine = next(r for r in access.active_access(privileged.pk) if r.user_id == staff_user.pk)
    for actor in (staff_actor, Actor.human(colleague_user)):
        with pytest.raises(NotAllowed, match="Only an administrator"):
            access.revoke_access(actor, row.pk)
        with pytest.raises(NotAllowed, match="Only an administrator"):
            access.revoke_access(actor, mine.pk)
    with pytest.raises(NotAllowed):
        access.revoke_access(system_actor, row.pk)
    with pytest.raises(NotFound):
        take(uuid.uuid4())
    assert _active(privileged) == {staff_user.pk, colleague_user.pk}


# ---- found by the second-pass review ------------------------------------------------------------


def test_a_requester_of_record_with_the_requester_role_cannot_share_or_remove(
    privileged, staff_actor, requester_person, colleague_user
) -> None:
    """F10. They see their own request; the list is staff's. Only the API door refused this."""
    as_requester = Actor.human(requester_person)
    with pytest.raises(NotAllowed):
        access.grant_access(as_requester, privileged.pk, colleague_user.pk)
    row = give(privileged.pk, colleague_user.pk)
    with pytest.raises(NotAllowed):
        access.revoke_access(as_requester, row.pk)
    assert colleague_user.pk in _active(privileged)


def test_an_edit_of_sensitivity_by_someone_no_longer_on_the_list_is_refused(
    privileged, colleague_user
) -> None:
    """F9, without the race: the door let them through, someone sealed it, the edit runs."""
    colleague = Actor.human(colleague_user)
    for value in ("privileged", "internal"):
        with pytest.raises(NotAllowed, match="no longer have access"):
            edits_service.edit_with_audit(
                colleague, "requests", privileged.pk, "sensitivity", value, "I was here first."
            )
    privileged.refresh_from_db()
    assert privileged.sensitivity == "privileged"
    assert colleague_user.pk not in _active(privileged)


def test_work_on_a_privileged_request_cannot_be_assigned_past_its_list_by_any_actor(
    privileged, staff_actor, system_actor, colleague_user
) -> None:
    """F3. The rule is the services', so a job and the Users screen keep it too."""
    from portal.records.services import tasks as tasks_service

    for actor in (staff_actor, system_actor):
        with pytest.raises(ServiceError, match="gives it to them first"):
            tasks_service.create_task(
                actor, privileged.pk, "manual", "x", assigned_to_user_id=colleague_user.pk
            )
    with pytest.raises(ServiceError, match="gives it to them first"):
        requests_service.assign_request(staff_actor, privileged.pk, colleague_user.pk)
    # Unassigned work, and work for someone on the list, is as it was.
    tasks_service.create_task(system_actor, privileged.pk, "manual", "nobody's yet")
    give(privileged.pk, colleague_user.pk)
    requests_service.assign_request(staff_actor, privileged.pk, colleague_user.pk)


def test_a_person_who_is_disabled_comes_off_every_list_and_is_replaced_only_where_last(
    staff_actor, staff_user, colleague_user, admin_user, agency, requester_person
) -> None:
    """F4. Two requests: on one the colleague is alone, on the other someone else remains."""
    colleague = Actor.human(colleague_user)
    alone = _reserve(colleague, agency, requester_person, subject="alone")
    shared = _reserve(colleague, agency, requester_person, subject="shared")
    give(shared.pk, staff_user.pk)

    access.on_person_disabled(Actor.human(admin_user), colleague_user.pk)

    assert _active(alone) == {admin_user.pk}
    assert _active(shared) == {staff_user.pk}
    revoked = RequestAccess.objects.filter(user_id=colleague_user.pk)
    assert revoked.count() == 2
    assert all(r.revoked_by_id == admin_user.pk for r in revoked.all())
    # Idempotent: nothing left to take.
    access.on_person_disabled(Actor.human(admin_user), colleague_user.pk)
    assert RequestAccess.objects.filter(user_id=admin_user.pk).count() == 1


def test_nobody_can_take_over_from_themselves(privileged, staff_actor, staff_user) -> None:
    with pytest.raises(ServiceError, match="only person with access"):
        access.on_person_disabled(staff_actor, staff_user.pk)
    assert _active(privileged) == {staff_user.pk}
