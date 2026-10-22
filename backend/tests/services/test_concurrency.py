"""Real concurrency, on real threads and real connections -- transaction=True so the two threads
actually contend at the database rather than each getting its own rolled-back sandbox.

Both fixtures below build their own actor/agency/request instead of using the package's `db`-based
fixtures: `django_db(transaction=True)` and the default `db` fixture wrap a test in incompatible
transaction handling, so these tests create everything they need through the same service calls
the fixtures use.
"""

import threading
from datetime import date

import pytest
from django.db import connection
from django.utils import timezone as django_timezone
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.models import Deadline, Request, StatusChange
from portal.records.services import common, directory
from portal.records.services import deadlines as deadlines_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service


class _FrozenClock:
    """Same device test_chokepoint.py uses for next_effective_at's own must-not-regress test: a
    wall clock frozen at one instant, so two concurrent callers computing `now()` collide instead
    of missing each other by however many microseconds real time advances between them."""

    def __init__(self, instant):
        self._instant = instant

    def now(self):
        return self._instant


def _run_on_threads(fns):
    """Run each callable on its own thread with its own DB connection, barrier-started so both
    reach their critical section together. Returns (results, errors) in call order."""
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


@pytest.mark.django_db(
    transaction=True,
    available_apps=["portal.core", "portal.records", "portal.records_db"],
)
def test_register_source_is_idempotent_under_a_concurrent_insert() -> None:
    """R1: two real threads racing register_source on the same external_ref must not raise -- the
    loser must find and return the winner's row, not a raw IntegrityError."""
    user = create_user(email="race-source@example.org", name="Racer")
    actor = Actor.human(user)

    def make():
        return sources_service.register_source(
            actor,
            source_type="postmark_email",
            raw_storage_url="s3://bucket/race.eml",
            external_ref="race-ref-1",
        )

    results, errors = _run_on_threads([make, make])

    assert errors == [None, None], errors
    assert results[0] is not None and results[1] is not None
    assert results[0].pk == results[1].pk

    from portal.records.models import Source

    assert Source.objects.filter(external_ref="race-ref-1").count() == 1


@pytest.mark.django_db(
    transaction=True,
    available_apps=["portal.core", "portal.records", "portal.records_db"],
)
def test_lock_request_serialises_concurrent_status_appends(monkeypatch) -> None:
    """R2: two real threads appending a status change to the SAME request through the real
    override_status -> lock_request -> select_for_update path must not race: every effective_at
    is distinct, and current_status ends up as whichever row has the latest effective_at.

    The clock is frozen (same device as test_chokepoint.py's next_effective_at regression test)
    so both threads' `next_effective_at` computes the identical `now()` -- the exact condition
    that needs `select_for_update` to serialise, since without it both threads can read the same
    "latest existing row" and both decide that frozen instant is free.
    """
    from portal.records.services import status as status_service

    frozen = django_timezone.now()
    monkeypatch.setattr(common, "timezone", _FrozenClock(frozen))

    user = create_user(email="race-lock@example.org", name="Locker")
    actor = Actor.human(user)
    agency = directory.create_agency(actor, name="Lock Agency", jurisdiction="Testland")
    request = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=user.pk,
        subject="Race",
        request_text="Race body.",
    )

    def override(note):
        return lambda: status_service.override_status(actor, request.pk, "acknowledged", note)

    results, errors = _run_on_threads([override("thread-a"), override("thread-b")])

    assert errors == [None, None], errors

    rows = list(StatusChange.objects.filter(request_id=request.pk).order_by("effective_at"))
    effective_ats = [r.effective_at for r in rows]
    assert len(effective_ats) == len(set(effective_ats)), (
        f"effective_at values were not all distinct: {effective_ats}"
    )

    latest_row = max(rows, key=lambda r: r.effective_at)
    current = Request.objects.get(pk=request.pk)
    assert current.current_status_id == latest_row.status_id


@pytest.mark.django_db(
    transaction=True,
    available_apps=["portal.core", "portal.records", "portal.records_db"],
)
def test_lock_request_serialises_concurrent_deadline_satisfaction() -> None:
    """D1: `satisfy_deadline` did not call `lock_request`, so `requests.next_deadline_at` could
    race the same way R2's `current_status` race did, through the schema's own
    `trg_sync_next_deadline` trigger instead of the app-level recompute R2 protects. Two active
    deadlines, satisfied concurrently on separate threads/connections: each thread's trigger-fired
    `select min(due_at) from deadlines where status = 'active'` runs on its own read-committed
    snapshot before the other thread's `UPDATE deadlines` has committed, so both compute a stale
    non-null `next_deadline_at` and whichever thread's trigger-driven `UPDATE requests` commits
    last wins with the wrong value -- even though zero deadlines are left active. No `app_role`
    marker, for the same reason R2 has none: that fixture needs `db` (transaction=False), and this
    needs `transaction=True` for the two threads to hold genuinely separate connections."""
    user = create_user(email="race-deadline@example.org", name="Deadline Racer")
    actor = Actor.human(user)
    agency = directory.create_agency(actor, name="Deadline Lock Agency", jurisdiction="Testland")
    request = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=user.pk,
        subject="Race",
        request_text="Race body.",
    )

    first = deadlines_service.open_deadline(
        actor,
        request.pk,
        deadline_type="production",
        due_at=date(2026, 10, 1),
        basis="statutory",
    )
    second = deadlines_service.open_deadline(
        actor,
        request.pk,
        deadline_type="production",
        due_at=date(2026, 11, 1),
        basis="statutory",
    )
    request.refresh_from_db()
    assert request.next_deadline_at == date(2026, 10, 1), (
        "setup sanity: the earlier of the two active deadlines should already be the cached one"
    )

    def satisfy(deadline_id):
        # satisfied_by_event_id is a nullable FK; the race lives at the request-row cache, not the
        # event link, so None keeps this test's setup to what the race actually needs.
        return lambda: deadlines_service.satisfy_deadline(actor, deadline_id, None)

    results, errors = _run_on_threads([satisfy(first.pk), satisfy(second.pk)])

    assert errors == [None, None], errors
    assert results[0] is not None and results[1] is not None
    assert Deadline.objects.filter(request_id=request.pk, status="active").count() == 0

    request.refresh_from_db()
    assert request.next_deadline_at is None, (
        f"both deadlines are satisfied, so next_deadline_at should be None; got "
        f"{request.next_deadline_at!r} -- lock_request is not serialising satisfy_deadline "
        "against the schema's own next-deadline trigger."
    )
