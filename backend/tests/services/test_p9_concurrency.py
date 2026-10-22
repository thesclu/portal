"""Two real threads on two real connections, for the access list's one check-then-act rule.

Same shape as test_v2_concurrency.py and run as the owner for the same reason. "The last person
cannot be removed" is a count followed by a write; `revoke_access` takes `lock_request` first so
the count is still true when the write lands. Without the lock both threads count the other's
grant as remaining, both revoke, and a privileged request is left with nobody on its list.

Broken on purpose, watched to fail and restored: see the P9 entry in notes.
"""

import pytest
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.services import access, directory
from portal.records.services import edits as edits_service
from portal.records.services import requests as requests_service
from portal.records.services.common import InvalidTransition

from tests.services.test_v2_concurrency import _THREADED, _run_on_threads


def _two_people_on_one_privileged_request():
    """Two members of staff on a privileged request's list, and two administrators: since P20h only
    an administrator takes a grant away (DECISIONS 37a, 43), so the race is two administrators."""
    one = create_user(email="list-a@example.org", name="List A")
    two = create_user(email="list-b@example.org", name="List B")
    admins = [
        create_user(
            email=f"list-admin-{n}@example.org",
            name=f"List Admin {n}",
            records_role="admin",
            is_staff=True,
        )
        for n in (1, 2)
    ]
    actor_one = Actor.human(one)
    admin_one, admin_two = (Actor.human(a) for a in admins)
    agency = directory.create_agency(actor_one, name="Sealed Agency", jurisdiction="Testland")
    request = requests_service.reserve_request(
        actor_one,
        agency_id=agency.pk,
        requester_user_id=create_user(
            email="list-requester@example.org",
            name="List Requester",
            records_role="requester",
        ).pk,
        subject="Sealed race",
        request_text="Body.",
        sensitivity="privileged",
    )
    access.grant_access(admin_one, request.pk, two.pk)
    rows = {row.user_id: row for row in access.active_access(request.pk)}
    return request, actor_one, (admin_one, rows[one.pk]), (admin_two, rows[two.pk])


@pytest.mark.django_db(**_THREADED)
def test_two_administrators_removing_both_grants_at_once_cannot_empty_the_list() -> None:
    request, _, (admin_one, row_one), (admin_two, row_two) = _two_people_on_one_privileged_request()

    results, errors = _run_on_threads(
        [
            lambda: access.revoke_access(admin_one, row_two.pk),
            lambda: access.revoke_access(admin_two, row_one.pk),
        ]
    )

    refused = [e for e in errors if e is not None]
    assert len(refused) == 1 and isinstance(refused[0], InvalidTransition), errors
    assert sum(r is not None for r in results) == 1
    assert len(access.active_access(request.pk)) == 1


@pytest.mark.django_db(**_THREADED)
def test_two_administrators_removing_one_grant_at_once_remove_it_once() -> None:
    request, _, (admin_one, _), (admin_two, row_two) = _two_people_on_one_privileged_request()

    results, errors = _run_on_threads(
        [
            lambda: access.revoke_access(admin_one, row_two.pk),
            lambda: access.revoke_access(admin_two, row_two.pk),
        ]
    )

    refused = [e for e in errors if e is not None]
    assert len(refused) == 1 and isinstance(refused[0], InvalidTransition), errors
    assert sum(r is not None for r in results) == 1
    assert len(access.active_access(request.pk)) == 1


@pytest.mark.django_db(**_THREADED)
def test_a_share_racing_the_edit_that_unseals_the_request_leaves_only_what_it_should() -> None:
    """Making a request not privileged revokes its list, except the grants of a limited-scope
    attorney or an intern, who see an ordinary request only through one (P20h). An administrator
    giving it to a limited-scope attorney at the same moment must end with exactly that grant
    live, whichever lands first; the staff grants never survive the unsealing."""
    request, actor_one, (admin_one, _), _ = _two_people_on_one_privileged_request()
    third = create_user(
        email="list-c@example.org",
        name="List C",
        records_role="limited_attorney",
    )

    _, errors = _run_on_threads(
        [
            lambda: edits_service.edit_with_audit(
                actor_one, "requests", request.pk, "sensitivity", "internal", "Unsealed."
            ),
            lambda: access.grant_access(admin_one, request.pk, third.pk),
        ]
    )

    assert errors == [None, None], errors
    assert [row.user_id for row in access.active_access(request.pk)] == [third.pk]
