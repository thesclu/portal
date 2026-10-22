"""A message cannot be attached to a request and handled without attaching at the same time, and two
requests that share a message cannot unmark each other's records.

`resolve_without_attaching` locks the source row and reads its events under that lock;
`record_event` takes the same lock for a source it is given (after the request's, the ledger's lock
order) and refuses a source that was resolved. So whichever of the two gets the lock first decides,
and the other is refused: the ledger never holds a message that is both attached and "handled
without attaching".

Same shape as the P8a forced-interleave test (`test_v2_concurrency.py`): two real threads on two
real connections, run as the owner role because SET LOCAL ROLE cannot be combined with
`transaction=True`, and the order is FORCED. The first thread takes the lock and holds its
transaction open; the second starts only then, and the first does not commit until `pg_locks` shows
the second blocked behind it. A barrier alone would land in the window only some of the time.

Each was broken on purpose and watched to fail (see the P8b worker log): take the lock out of
`record_event` and the first test attaches to a resolved message; read the source with a plain
`_get` in `resolve_without_attaching` and the second never blocks, so it resolves a message that is
being attached.

The third test is the P8b fix batch's (review F1). `classify` ADDS to the files marked as records,
and it must do so against the marks as they are once it holds the source's lock: two requests that
share one message are classified at the same time, and the one that waits for the lock must see
what the other committed. Reading the marks before the lock, then replacing, loses the other's.
"""

import threading
from datetime import UTC, datetime

import pytest
from django.db import connection, transaction
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.models import Attachment, Event, Source
from portal.records.services import classify as classify_service
from portal.records.services import directory
from portal.records.services import events as events_service
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services.common import InvalidTransition

from .test_v2_concurrency import _wait_until_blocked

_THREADED = {
    "transaction": True,
    "available_apps": ["portal.core", "portal.records", "portal.records_db"],
}


def _world(tag):
    user = create_user(email=f"{tag}@example.org", name=f"Person {tag}")
    person = Actor.human(user)
    agency = directory.create_agency(person, name=f"Agency {tag}", jurisdiction="Testland")
    request = requests_service.reserve_request(
        person,
        agency_id=agency.pk,
        requester_user_id=user.pk,
        subject="Race",
        request_text="Race body.",
    )
    source = sources_service.register_source(
        person, source_type="postmark_email", raw_storage_url=f"test://{tag}.json"
    )
    return person, request, source


def _attach(person, request, source):
    return events_service.record_event(
        person,
        request.pk,
        event_type="correspondence",
        channel="email",
        direction="inbound",
        occurred_at=datetime(2026, 3, 2, 17, 30, tzinfo=UTC),
        display_body="Mail on the request.",
        source_id=source.pk,
    )


def _two_threads(first, second):
    """`first` runs inside an open transaction and is told the second thread's backend pid once it
    is running; `second` runs on its own connection. Returns (outcomes, threads)."""
    holding, second_started = threading.Event(), threading.Event()
    second_pid: list[int] = []
    outcome: dict = {}

    def run_first():
        try:
            with transaction.atomic():
                first["act"]()
                holding.set()
                assert second_started.wait(5), "the second thread never started"
                assert _wait_until_blocked(second_pid[0]), "the second thread never blocked"
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the main thread below
            outcome["first_error"] = exc
        finally:
            holding.set()
            connection.close()

    def run_second():
        try:
            assert holding.wait(5), "the first thread never took its locks"
            with connection.cursor() as cur:
                cur.execute("select pg_backend_pid()")
                second_pid.append(cur.fetchone()[0])
            second_started.set()
            second["act"]()
            outcome["second"] = "succeeded"
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the main thread below
            outcome["second_error"] = exc
        finally:
            second_started.set()
            connection.close()

    threads = [threading.Thread(target=run_first), threading.Thread(target=run_second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not any(t.is_alive() for t in threads), outcome
    return outcome


@pytest.mark.django_db(**_THREADED)
def test_attaching_a_message_that_is_being_resolved_is_refused() -> None:
    person, request, source = _world("resolve-first")
    outcome = _two_threads(
        {
            "act": lambda: sources_service.resolve_without_attaching(
                person, source.pk, "duplicate", "same letter as before"
            )
        },
        {"act": lambda: _attach(person, request, source)},
    )
    assert "first_error" not in outcome, outcome
    assert isinstance(outcome.get("second_error"), InvalidTransition), outcome
    assert "handled without attaching" in str(outcome["second_error"])
    assert not Event.objects.filter(source_id=source.pk).exists()
    assert Source.objects.get(pk=source.pk).resolution == "duplicate"


@pytest.mark.django_db(**_THREADED)
def test_resolving_a_message_that_is_being_attached_is_refused() -> None:
    person, request, source = _world("attach-first")
    outcome = _two_threads(
        {"act": lambda: _attach(person, request, source)},
        {
            "act": lambda: sources_service.resolve_without_attaching(
                person, source.pk, "duplicate", "same letter as before"
            )
        },
    )
    assert "first_error" not in outcome, outcome
    assert isinstance(outcome.get("second_error"), InvalidTransition), outcome
    assert "attached to a request" in str(outcome["second_error"])
    assert Event.objects.filter(source_id=source.pk).count() == 1
    assert Source.objects.get(pk=source.pk).resolution is None


@pytest.mark.django_db(**_THREADED)
def test_two_requests_classified_together_for_one_message_each_keep_the_others_records() -> None:
    """The first thread has recorded request B's production (file b is a record, uncommitted) and
    holds the message's lock; the second records request A's production (file a) and blocks on that
    lock. When the first commits, the second must see b as a record and leave it so."""
    user = create_user(email="shared@example.org", name="Person shared")
    person = Actor.human(user)
    agency = directory.create_agency(
        person, name="Agency shared", jurisdiction="Testland", records_law="TESTLAW"
    )
    requests = []
    for subject in ("Request A", "Request B"):
        reserved = requests_service.reserve_request(
            person,
            agency_id=agency.pk,
            requester_user_id=user.pk,
            subject=subject,
            request_text="Body.",
        )
        filing_service.file_request(
            person, reserved.pk, filed_at=datetime(2026, 2, 10, 18, 0, tzinfo=UTC), channel="email"
        )
        requests.append(reserved)
    source = sources_service.register_source(
        person, source_type="postmark_email", raw_storage_url="test://shared.json"
    )
    files = [
        sources_service.add_attachment(
            person,
            source.pk,
            filename=f"for-{name}.pdf",
            storage_url=f"test://files/for-{name}.pdf",
            mime_type="application/pdf",
            size_bytes=100,
        )
        for name in ("a", "b")
    ]
    event_a, event_b = (_attach(person, request, source) for request in requests)

    outcome = _two_threads(
        {
            "act": lambda: classify_service.classify(
                person, event_b.pk, "production_partial", record_attachment_ids=[files[1].pk]
            )
        },
        {
            "act": lambda: classify_service.classify(
                person, event_a.pk, "production_partial", record_attachment_ids=[files[0].pk]
            )
        },
    )
    assert "first_error" not in outcome, outcome
    assert outcome.get("second") == "succeeded", outcome
    roles = {a.filename: a.role for a in Attachment.objects.filter(source_id=source.pk)}
    assert roles == {"for-a.pdf": "produced_record", "for-b.pdf": "produced_record"}
