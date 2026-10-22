"""Real concurrency on the filing seam: two threads, two connections, one recipient.

Same shape as `backend/tests/services/test_concurrency.py`, and for the same reason -- `db`
(transaction=False) would give each thread its own rolled-back sandbox, so the two would never
actually contend at the database. Everything the test needs is built through the same services a
caller uses, because `django_db(transaction=True)` and the package's `db`-based fixtures wrap a
test in incompatible transaction handling.

WHAT THIS PROTECTS. Filing a recipient reads `status = 'pending'`, does irreversible work, and
writes `status = 'filed'`. Without `select_for_update` on the recipient row, a double-click or a
retried POST puts two callers past the check: both call `reserve_request`, which mints a
sequential tracking number and a unique inbound alias EACH TIME, and both call `file_request`,
which opens statutory deadlines. One UPDATE wins; the other request is orphaned with a burned
tracking number, a live alias that will misroute every reply that quotes it, and open deadlines
that will accrue overdue statuses and fire follow-ups for a letter nobody sent -- and nothing
deletes in `records`, so cleaning it up is a permanent withdrawal in the ledger.

No `app_role` marker, for the reason the records concurrency tests give: that fixture needs `db`
(transaction=False), and this needs transaction=True for the threads to hold separate connections.
"""

import threading
import time
from datetime import UTC, datetime

import pytest
from django.db import connection
from portal.core.services.identity import create_user
from portal.drafting.models import LetterRecipient, LetterTemplateRevision
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import filing as drafting_filing
from portal.drafting.services import library as library_service
from portal.records.actors import Actor
from portal.records.models import Event, Request
from portal.records.services import filing as records_filing
from portal.records.services.common import InvalidTransition

from tests.drafting.conftest import sendable_agency
from tests.p20h_helpers import give

CONCURRENCY_APPS = [
    "portal.core",
    "portal.records",
    "portal.records_db",
    "portal.drafting",
    "portal.drafting_db",
]


def _run_on_threads(fns):
    """Each callable on its own thread with its own connection, barrier-started so both reach
    the critical section together. Returns (results, errors) in call order."""
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
        t.join(timeout=30)
    return results, errors


def _setup(label: str):
    user = create_user(email=f"{label}@example.org", name="Racer")
    actor = Actor.human(user)
    agency = sendable_agency(actor, f"Test Agency {label}")
    draft = drafts_service.create_draft(subject="Race", created_by_user_id=user.pk)
    drafts_service.add_draft_item(draft.pk, item_number="1", body="The only ask")
    recipient = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")
    return actor, agency, draft, recipient


@pytest.mark.django_db(transaction=True, available_apps=CONCURRENCY_APPS)
def test_two_threads_filing_one_recipient_mint_exactly_one_request() -> None:
    actor, agency, draft, recipient = _setup("race-recipient")

    def file_it():
        return drafting_filing.file_draft(actor, draft.pk)

    results, errors = _run_on_threads([file_it, file_it])

    assert errors == [None, None], errors
    filed = [o for result in results for o in result.outcomes if o.filed]
    assert len(filed) == 1, [o.__dict__ for result in results for o in result.outcomes]
    assert Request.objects.filter(agency_id=agency.pk).count() == 1, (
        "two requests exist for one recipient: both threads got past the status check, so each "
        "minted its own tracking number and inbound alias and opened its own statutory deadlines "
        "-- select_for_update is not serialising the check-then-act on letter_recipients.status."
    )
    row = LetterRecipient.objects.get(pk=recipient.pk)
    assert row.status == "filed"
    assert row.request_id == filed[0].request_id


@pytest.mark.django_db(transaction=True, available_apps=CONCURRENCY_APPS)
def test_two_threads_reserving_one_recipient_produce_one_request() -> None:
    """P10: reserving is the check-then-act now. The loser sees the winner's request under the
    recipient's lock and is refused; nothing is orphaned."""
    actor, agency, draft, recipient = _setup("race-reserve")

    results, errors = _run_on_threads(
        [lambda: drafting_filing.reserve_recipient(actor, recipient.pk)] * 2
    )

    refused = [e for e in errors if e is not None]
    assert len(refused) == 1 and isinstance(refused[0], InvalidTransition), errors
    assert Request.objects.filter(agency_id=agency.pk).count() == 1
    row = LetterRecipient.objects.get(pk=recipient.pk)
    assert row.status == "pending" and row.request_id is not None
    assert Request.objects.get(pk=row.request_id).current_status_id == "pending_filing"


@pytest.mark.django_db(transaction=True, available_apps=CONCURRENCY_APPS)
@pytest.mark.parametrize("attempt", range(5))
def test_two_threads_filing_one_reserved_recipient_one_through_each_door(attempt) -> None:
    """One lock order across the seam: the request row, then the recipient. A reserved letter is
    filed from the drafting side and from its request's own page at the same moment. Exactly one
    filing happens, the other caller is refused, and nobody deadlocks (P3b's F1 was the opposite
    order meeting itself). Run several times, because a lock-order inversion only bites on some
    interleavings."""
    actor, agency, draft, recipient = _setup(f"race-doors-{attempt}")
    reserved = drafting_filing.reserve_recipient(actor, recipient.pk)
    when = datetime(2026, 2, 10, 18, 0, tzinfo=UTC)

    results, errors = _run_on_threads(
        [
            lambda: drafting_filing.file_recipient(actor, recipient.pk, filed_at=when),
            lambda: records_filing.file_request(
                actor, reserved.request_id, filed_at=when, channel="email"
            ),
        ]
    )

    refused = [e for e in errors if e is not None]
    assert len(refused) == 1 and isinstance(refused[0], InvalidTransition), errors
    request = Request.objects.get(pk=reserved.request_id)
    assert request.current_status_id == "filed"
    assert Event.objects.filter(request_id=request.pk, event_type_id="submission").count() == 1
    row = LetterRecipient.objects.get(pk=recipient.pk)
    assert row.status == "filed" and row.filed_at == when


@pytest.mark.django_db(transaction=True, available_apps=CONCURRENCY_APPS)
def test_a_skip_and_a_filing_of_one_reserved_letter_cannot_both_succeed() -> None:
    """Both take the request's lock first and read its status under it. Whichever is second is
    refused, so a letter is never both sent and withdrawn."""
    actor, agency, draft, recipient = _setup("race-skip")
    reserved = drafting_filing.reserve_recipient(actor, recipient.pk)

    results, errors = _run_on_threads(
        [
            lambda: drafting_filing.file_recipient(actor, recipient.pk),
            lambda: drafting_filing.skip_recipient(actor, recipient.pk, "Changed our minds."),
        ]
    )

    assert sum(e is None for e in errors) == 1, errors
    assert isinstance(next(e for e in errors if e is not None), InvalidTransition)
    request = Request.objects.get(pk=reserved.request_id)
    row = LetterRecipient.objects.get(pk=recipient.pk)
    assert (request.current_status_id, row.status) in {("filed", "filed"), ("withdrawn", "skipped")}


@pytest.mark.django_db(transaction=True, available_apps=CONCURRENCY_APPS)
def test_two_threads_starting_a_letter_from_one_template_mint_exactly_one_revision() -> None:
    """The same instrument on the template row, moved with the minting: `revision = max + 1` is a
    check-then-act, and since P10 it happens when a letter is started (and on Save), not when one
    is filed."""
    user = create_user(email="race-rev@example.org", name="Racer")
    template = library_service.create_template(name="Race template", legal_basis="TESTLAW")
    library_service.add_template_item(template.pk, item_number="1", body="The only ask")

    def start():
        return drafts_service.create_draft(from_template_id=template.pk, created_by_user_id=user.pk)

    results, errors = _run_on_threads([start, start])

    assert errors == [None, None], errors
    revisions = LetterTemplateRevision.objects.filter(template_id=template.pk)
    assert revisions.count() == 1, (
        "two revisions of one unedited template state: select_for_update is not serialising "
        "mint_or_reuse_revision's max(revision) + 1."
    )
    assert {d.source_revision_id for d in results} == {revisions.get().pk}


@pytest.mark.django_db(transaction=True, available_apps=CONCURRENCY_APPS)
def test_a_wave_and_disabling_a_person_take_request_locks_in_one_order() -> None:
    """Review F4. A wave is one transaction and used to lock each reserved request as it reached
    it, in recipient order; `access.on_person_disabled` locks several requests in id order. With
    the two orders opposed they deadlocked. The interleaving is FORCED: the wave is paused after
    its first filing until the other transaction is visibly waiting on a lock."""
    from portal.records.services import access as access_service

    owner = create_user(email="wave-owner@example.org", name="Owner")
    second = create_user(email="wave-second@example.org", name="Second")
    admin = create_user(
        email="wave-admin@example.org",
        name="Admin",
        records_role="admin",
        is_staff=True,
    )
    actor = Actor.human(owner)
    # A wave goes through its recipients in the order they were added. The deadlock needed that
    # to be the OPPOSITE of the requests' id order, which is true of half of all two-recipient
    # waves. Ids are random, so build drafts until one comes out that way.
    for attempt in range(40):
        draft = drafts_service.create_draft(
            subject="Sealed wave", sensitivity="privileged", created_by_user_id=owner.pk
        )
        drafts_service.add_draft_item(draft.pk, item_number="1", body="The only ask")
        recipients = [
            drafts_service.add_recipient(
                draft.pk, agency_id=sendable_agency(actor, f"Wave Agency {attempt} {n}").pk
            )
            for n in (1, 2)
        ]
        requests = [drafting_filing.reserve_recipient(actor, r.pk).request_id for r in recipients]
        if requests[0] > requests[1]:
            break
    else:
        pytest.fail("no draft came out with its requests in the unlucky order")
    for request_id in requests:
        give(request_id, second.pk)

    errors: list = [None, None]
    disabler = None
    real_file = drafting_filing.file_recipient
    calls = []

    def disable() -> None:
        try:
            access_service.on_person_disabled(Actor.human(admin), second.pk)
        except BaseException as exc:  # noqa: BLE001
            errors[1] = exc
        finally:
            connection.close()

    def file_and_pause(*args, **kwargs):
        nonlocal disabler
        result = real_file(*args, **kwargs)
        calls.append(args[1])
        if len(calls) == 1:
            disabler = threading.Thread(target=disable)
            disabler.start()
            time.sleep(0.5)  # long enough for it to take its first lock, if it can
        return result

    drafting_filing.file_recipient = file_and_pause
    try:
        try:
            wave = drafting_filing.file_draft(actor, draft.pk)
        except BaseException as exc:  # noqa: BLE001
            errors[0] = exc
            wave = None
    finally:
        drafting_filing.file_recipient = real_file
    assert disabler is not None
    disabler.join(timeout=15)

    assert errors == [None, None], errors
    assert wave is not None and wave.filed_count == 2
    assert not access_service.granted_request_ids(second.pk)


@pytest.mark.django_db(transaction=True, available_apps=CONCURRENCY_APPS)
def test_an_edit_and_a_reservation_of_one_recipient_take_turns() -> None:
    """Review F11. `update_recipient` read the row and wrote it with no lock, so "skip" could land
    while a reservation was in flight: a skipped recipient with a live reserved request."""
    actor, agency, draft, recipient = _setup("race-edit")

    results, errors = _run_on_threads(
        [
            lambda: drafting_filing.reserve_recipient(actor, recipient.pk),
            lambda: drafts_service.update_recipient(recipient.pk, status="skipped"),
        ]
    )

    row = LetterRecipient.objects.get(pk=recipient.pk)
    # Either the skip went first and the reservation was refused, or the reservation went first
    # and the skip was told to use the skip action. Never both.
    assert sum(e is None for e in errors) == 1, errors
    assert (row.status, row.request_id is None) in {("skipped", True), ("pending", False)}
