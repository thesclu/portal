"""Two real threads on two real connections, for the P8a services that lock before they write.

Same shape as test_concurrency.py, and for the same reason: `transaction=True` so the threads
actually contend at the database, each test building its own people and rows instead of using the
package's `db` fixtures. These run as the owner role (SET LOCAL ROLE cannot be combined with
transaction=True); the same writes run as portal_app in the single-threaded tests beside them.

Each was broken on purpose, watched to fail, and restored (see the P8a worker log): removing
`lock_request` from `resolve_conflict` deadlocks the two threads inside the schema's own
next-deadline trigger; removing the agency lock from `_clear_other_primaries` lets the second
thread die on the unique index with a raw IntegrityError.

The sweep test at the end is different: its interleaving is FORCED, not hoped for (see
`_wait_until_blocked`), because the defect it pins is a check-then-act window and a barrier alone
only lands in it some of the time.
"""

import threading
import time
from datetime import UTC, date, datetime

import pytest
from django.db import connection, transaction
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.models import AgencyContact, Deadline, StatusChange
from portal.records.services import deadlines as deadlines_service
from portal.records.services import directory
from portal.records.services import events as events_service
from portal.records.services import requests as requests_service
from portal.records.services.common import InvalidTransition, lock_request

EARLY = date(2026, 3, 1)
LATE = date(2026, 4, 1)

_THREADED = {
    "transaction": True,
    "available_apps": ["portal.core", "portal.records", "portal.records_db"],
}


def _run_on_threads(fns):
    """Each callable on its own thread and connection, barrier-started so both reach their
    critical section together. Returns (results, errors) in call order."""
    barrier = threading.Barrier(len(fns))
    results: list = [None] * len(fns)
    errors: list = [None] * len(fns)

    def worker(i, fn):
        try:
            barrier.wait(timeout=5)
            results[i] = fn()
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the main thread below
            errors[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return results, errors


def _open(actor, request, kind, due):
    return deadlines_service.open_deadline(
        actor, request.pk, deadline_type=kind, due_at=due, basis="agency_commitment"
    )


def _primaries(agency):
    return set(
        AgencyContact.objects.filter(agency_id=agency.pk, is_primary=True).values_list(
            "name", flat=True
        )
    )


@pytest.mark.django_db(**_THREADED)
def test_two_people_resolving_the_same_conflict_opposite_ways_cannot_both_win() -> None:
    """lock_request first, in the shape of test_concurrency.py's D1. Two people open the same
    "Resolve due date" dialog; one keeps the earlier date, the other the later. Without the lock
    both read both rows as active, both write, and the result is each date superseded by the
    other: no active deadline, next_deadline_at null, and the cache and the ledger disagree about
    whether anything is due. With it, the second is refused because the row it meant to drop is
    no longer active."""
    user = create_user(email="resolver-a@example.org", name="Resolver A")
    other = create_user(email="resolver-b@example.org", name="Resolver B")
    actor_a, actor_b = Actor.human(user), Actor.human(other)
    agency = directory.create_agency(actor_a, name="Resolve Agency", jurisdiction="Testland")
    request = requests_service.reserve_request(
        actor_a,
        agency_id=agency.pk,
        requester_user_id=user.pk,
        subject="Race",
        request_text="Race body.",
    )
    earlier = _open(actor_a, request, "production", EARLY)
    later = _open(actor_a, request, "production", LATE)

    results, errors = _run_on_threads(
        [
            lambda: deadlines_service.resolve_conflict(actor_a, earlier.pk, later.pk, "keep early"),
            lambda: deadlines_service.resolve_conflict(actor_b, later.pk, earlier.pk, "keep late"),
        ]
    )

    refused = [e for e in errors if e is not None]
    assert len(refused) == 1 and isinstance(refused[0], InvalidTransition), errors
    assert sum(r is not None for r in results) == 1

    rows = {d.pk: d for d in Deadline.objects.filter(request_id=request.pk)}
    active = [d for d in rows.values() if d.status == "active"]
    superseded = [d for d in rows.values() if d.status == "superseded"]
    assert len(active) == 1 and len(superseded) == 1, {d.pk: d.status for d in rows.values()}
    assert superseded[0].superseded_by_deadline_id == active[0].pk
    request.refresh_from_db()
    assert request.next_deadline_at == active[0].due_at


@pytest.mark.django_db(**_THREADED)
def test_two_people_making_different_contacts_primary_at_once_do_not_collide() -> None:
    """The unique index allows one primary per agency, and it is checked statement by statement.
    Two concurrent "make primary" calls on two contacts must take turns on the agency's row, not
    both insert and let one die on the index as a raw IntegrityError."""
    user = create_user(email="primary-a@example.org", name="Primary A")
    actor = Actor.human(user)
    agency = directory.create_agency(actor, name="Primary Agency", jurisdiction="Testland")
    one = directory.create_contact(actor, agency.pk, name="One")
    two = directory.create_contact(actor, agency.pk, name="Two")

    barrier = threading.Barrier(2)
    errors: list = [None, None]

    def make(i, contact):
        try:
            barrier.wait(timeout=5)
            directory.update_contact(actor, contact.pk, f"thread {i}", is_primary=True)
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the main thread below
            errors[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=make, args=(i, c)) for i, c in enumerate((one, two))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == [None, None], errors
    assert len(_primaries(agency)) == 1


def _wait_until_blocked(pid: int, timeout: float = 5.0) -> bool:
    """True once backend `pid` is visibly waiting for a lock it has not been granted.

    `pg_locks` is read live. `pg_stat_activity` is not: Postgres snapshots it once per transaction,
    and the caller is inside one, so it would never show the other backend arrive.
    """
    give_up = time.monotonic() + timeout
    while time.monotonic() < give_up:
        with connection.cursor() as cur:
            cur.execute("select count(*) from pg_locks where pid = %s and not granted", [pid])
            if cur.fetchone()[0]:
                return True
        time.sleep(0.01)
    return False


@pytest.mark.django_db(**_THREADED)
def test_a_sweep_that_loses_the_race_to_a_satisfy_does_not_overwrite_it() -> None:
    """F2. The overdue sweep reads the deadline as active, then waits for the request's lock while
    a person holds it and records the determination that satisfies that very deadline. When the
    lock comes free the sweep must look again: before the fix it wrote `missed` over `satisfied`
    and appended `determination_overdue` AFTER a determination had been recorded (4 of 4 runs).

    The order is forced. Thread 1 takes the lock first; thread 2 starts only then, reads the row,
    and blocks; thread 1 does not satisfy until `pg_locks` shows thread 2 blocked.
    """
    user = create_user(email="sweep-a@example.org", name="Sweep A")
    person, sweep = Actor.human(user), Actor.for_system("test_sweep")
    agency = directory.create_agency(person, name="Sweep Agency", jurisdiction="Testland")
    request = requests_service.reserve_request(
        person,
        agency_id=agency.pk,
        requester_user_id=user.pk,
        subject="Sweep",
        request_text="Sweep body.",
    )
    deadline = _open(person, request, "statutory_determination", EARLY)

    holding, sweeper_pid_known = threading.Event(), threading.Event()
    sweeper_pid: list[int] = []
    outcome: dict = {}

    def satisfier():
        try:
            with transaction.atomic():
                lock_request(request.pk)
                holding.set()
                assert sweeper_pid_known.wait(5), "the sweep thread never started"
                assert _wait_until_blocked(sweeper_pid[0]), "the sweep never reached the lock"
                answer = events_service.record_event(
                    person,
                    request.pk,
                    event_type="determination",
                    channel="email",
                    direction="inbound",
                    occurred_at=datetime(2026, 2, 20, 18, 0, tzinfo=UTC),
                    display_body="Determination: records will be produced.",
                    status="determination_issued",
                )
                deadlines_service.satisfy_deadline(person, deadline.pk, answer.pk)
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the main thread below
            outcome["satisfy_error"] = exc
        finally:
            holding.set()  # never leave the sweep thread waiting on a thread that has died
            connection.close()

    def sweeper():
        try:
            assert holding.wait(5), "the satisfy thread never took the lock"
            with connection.cursor() as cur:
                cur.execute("select pg_backend_pid()")
                sweeper_pid.append(cur.fetchone()[0])
            sweeper_pid_known.set()
            outcome["swept"] = deadlines_service.mark_deadline_missed(sweep, deadline.pk)
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the main thread below
            outcome["sweep_error"] = exc
        finally:
            sweeper_pid_known.set()
            connection.close()

    threads = [threading.Thread(target=satisfier), threading.Thread(target=sweeper)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not any(t.is_alive() for t in threads), outcome
    assert "satisfy_error" not in outcome and "sweep_error" not in outcome, outcome
    assert outcome["swept"] is None
    row = Deadline.objects.get(pk=deadline.pk)
    assert row.status == "satisfied" and row.satisfied_by_event_id is not None
    assert not StatusChange.objects.filter(
        request_id=request.pk, reason="deadline_expired"
    ).exists()
    request.refresh_from_db()
    assert request.current_status_id == "determination_issued"
