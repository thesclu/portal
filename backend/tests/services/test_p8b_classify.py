"""'Record as...': one table drives the preview and the write, so these tests drive both from it.

The central claim is that what the dialog shows (`preview_classification`) is what `classify` then
does, for every row of the table. The rest pins the rows to the spec, the human-only door for
"Production -- complete", the refusals that must leave the ledger untouched, and the transaction.

Nothing here voids an event, so nothing here brushes against the known deferred bug in which
voiding the event that satisfied a deadline leaves the deadline satisfied (F6 in the P8a review).
"""

from datetime import UTC, date, datetime

import pytest
from portal.records.models import (
    Attachment,
    AuditLog,
    Deadline,
    Event,
    Fee,
    Request,
    StatusChange,
    Task,
)
from portal.records.services import classify as classify_service
from portal.records.services import edits as edits_service
from portal.records.services import events as events_service
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services import status as status_service
from portal.records.services import tasks as tasks_service
from portal.records.services.classify import KINDS, FeeInput
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError

from tests.p8b_helpers import add_file, attach, make_source

from .conftest import FILED_AT

pytestmark = pytest.mark.usefixtures("as_each_role")

# The spec's table, written out independently of the code under test:
# (menu item, key, event type, status appended, task opened, goes through override_status)
SPEC_TABLE = [
    ("Acknowledgment", "acknowledgment", "acknowledgment", "acknowledged", None, False),
    ("Determination", "determination", "determination", "determination_issued", None, False),
    ("Extension of time", "extension", "extension_notice", None, None, False),
    ("Fee estimate", "fee_estimate", "fee_notice", "fee_estimate_received", "decide_fee", False),
    (
        "Production — partial",
        "production_partial",
        "production",
        "partial_production",
        "review_production",
        False,
    ),
    (
        "Production — complete",
        "production_complete",
        "production",
        "production_received",
        None,
        True,
    ),
    ("No records", "no_records", "determination", "no_records_claimed", None, False),
    ("Denial", "denial", "determination", "denied", None, False),
    (
        "Asks to clarify or narrow",
        "clarification",
        "clarification",
        "clarification_requested",
        "reply_to_clarification",
        False,
    ),
]


@pytest.fixture
def filed(reserved, staff_actor, rules):
    """A filed request: status `filed`, a determination clock and a production clock running."""
    filing_service.file_request(staff_actor, reserved.pk, filed_at=FILED_AT, channel="email")
    return Request.objects.get(pk=reserved.pk)


@pytest.fixture
def inbound(filed, staff_actor):
    """An inbound message on the request with two files, unclassified (`correspondence`)."""
    source = make_source(staff_actor)
    cover = add_file(staff_actor, source, "cover-letter.pdf")
    records = add_file(staff_actor, source, "records.pdf")
    event = attach(staff_actor, filed, source)
    return event, source, cover, records


@pytest.fixture
def second_filed(filed, staff_actor, staff_user, agency, rules):
    """A second filed request at the same agency, for a letter that answers both."""
    other = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Widgets, February 2026",
        request_text="All widget inspection records for February 2026.",
        items=[requests_service.NewItem("1", "Widget inspection reports")],
    )
    filing_service.file_request(staff_actor, other.pk, filed_at=FILED_AT, channel="email")
    return Request.objects.get(pk=other.pk)


def _deadlines(request):
    return sorted(
        (
            d.pk,
            d.deadline_type,
            d.due_at,
            d.status,
            d.flagged_for_review,
            d.flag_note,
            d.request_item_id,
        )
        for d in Deadline.objects.filter(request_id=request.pk)
    )


def _counts(request):
    return {
        "status_rows": StatusChange.objects.filter(request_id=request.pk).count(),
        "tasks": Task.objects.filter(request_id=request.pk).count(),
        "fees": Fee.objects.filter(request_id=request.pk).count(),
        "audit": AuditLog.objects.count(),
        "events": Event.objects.filter(request_id=request.pk).count(),
    }


# ---- the table ---------------------------------------------------------------------------------


def test_the_table_is_the_specs_nine_rows() -> None:
    assert [
        (
            k.label,
            k.key,
            k.event_type,
            k.status,
            k.task_type,
            k.via_override,
        )
        for k in KINDS
    ] == SPEC_TABLE


def test_the_only_row_through_override_status_is_complete_production() -> None:
    assert [k.key for k in KINDS if k.via_override] == ["production_complete"]
    assert {k.status for k in KINDS if k.via_override} == {status_service.FULL_PRODUCTION}


@pytest.mark.parametrize("kind", [k.key for k in KINDS])
def test_the_preview_is_what_the_write_then_does(kind, filed, inbound, staff_actor) -> None:
    event, _source, cover, records = inbound
    row = classify_service.get_kind(kind)
    deadlines_before = _deadlines(filed)
    counts_before = _counts(filed)
    open_before = {t.pk for t in tasks_service.open_tasks(filed.pk)}

    preview = classify_service.preview_classification(event.pk, kind)

    # Reading the preview writes nothing at all.
    assert _counts(filed) == counts_before
    assert Request.objects.get(pk=filed.pk).current_status_id == "filed"
    assert preview.kind == row and preview.event_id == event.pk
    assert preview.status_from == "filed" and preview.event_type_from == "correspondence"
    assert preview.event_type_to == row.event_type and preview.status_to == row.status
    assert [d.pk for d in preview.deadlines] == [
        d.pk
        for d in Deadline.objects.filter(request_id=filed.pk, status="active").order_by("due_at")
    ]

    result = classify_service.classify(
        staff_actor,
        event.pk,
        kind,
        note="Because the agency said so.",
        fee=FeeInput(amount_cents=41250, description="Duplication", due_at=date(2026, 3, 20))
        if row.takes_fee
        else None,
        record_attachment_ids=[records.pk] if row.takes_records else (),
    )

    # The event is what the preview said.
    assert Event.objects.get(pk=event.pk).event_type_id == preview.event_type_to
    assert result.event.event_type_id == preview.event_type_to

    # The status is what the preview said, written the way it said.
    after = Request.objects.get(pk=filed.pk)
    new_rows = list(
        StatusChange.objects.filter(request_id=filed.pk).order_by("-effective_at")[
            : _counts(filed)["status_rows"] - counts_before["status_rows"]
        ]
    )
    if preview.status_to is None:
        assert after.current_status_id == preview.status_from and new_rows == []
        assert result.status_change is None
    else:
        assert after.current_status_id == preview.status_to
        (change,) = new_rows
        assert result.status_change is not None and result.status_change.pk == change.pk
        assert (change.status_id, change.reason) == (preview.status_to, preview.status_reason)
        if preview.status_reason == "event":
            assert change.triggering_event_id == event.pk
        else:
            assert change.triggering_event_id is None

    # The task is what the preview said: one of that type, undated, made by the person.
    opened = [t for t in tasks_service.open_tasks(filed.pk) if t.pk not in open_before]
    if preview.task is None:
        assert opened == [] and result.task is None
    else:
        (task,) = opened
        assert task.task_type == preview.task.task_type
        assert task.description == preview.task.description
        assert task.due_at is None and preview.task.due_at is None
        assert task.created_by_id == staff_actor.user_id
        assert result.task is not None and result.task.pk == task.pk

    # Classifying never touches a deadline, which is why the preview can show them as they stand.
    assert _deadlines(filed) == deadlines_before
    assert [d.pk for d in preview.deadlines] == [
        d[0] for d in sorted(deadlines_before, key=lambda d: d[2]) if d[3] == "active"
    ]
    # And one event, not a second one.
    assert Event.objects.filter(request_id=filed.pk).count() == counts_before["events"]
    assert cover.pk  # the files exist; the records are checked separately below


def test_the_preview_of_a_no_records_claim_says_the_chokepoint_opens_the_task(
    filed, inbound, staff_actor
) -> None:
    event = inbound[0]
    preview = classify_service.preview_classification(event.pk, "no_records")
    assert preview.task is not None
    assert preview.task.task_type == "review_no_records_claim"
    assert preview.task.opened_by == "status_chokepoint"
    result = classify_service.classify(staff_actor, event.pk, "no_records")
    assert result.task is not None and result.task.task_type == "review_no_records_claim"
    assert result.task.created_by_id == staff_actor.user_id
    assert (
        Task.objects.filter(request_id=filed.pk, task_type="review_no_records_claim").count() == 1
    )


def test_a_no_records_claim_does_not_open_a_second_review_task_and_the_preview_says_so(
    filed, inbound, staff_actor
) -> None:
    event, source, *_ = inbound
    classify_service.classify(staff_actor, event.pk, "no_records")
    again = attach(staff_actor, filed, source)
    preview = classify_service.preview_classification(again.pk, "no_records")
    assert preview.task is None
    classify_service.classify(staff_actor, again.pk, "no_records")
    assert (
        Task.objects.filter(request_id=filed.pk, task_type="review_no_records_claim").count() == 1
    )


# ---- "Production -- complete": human only, as a manual override ---------------------------------


def test_complete_production_by_a_human_lands_as_a_manual_override_with_the_note(
    filed, inbound, staff_actor, staff_user
) -> None:
    event = inbound[0]
    result = classify_service.classify(
        staff_actor, event.pk, "production_complete", note="Checked every page against the index."
    )
    change = result.status_change
    assert change is not None
    assert (change.status_id, change.reason) == ("production_received", "manual_override")
    assert change.triggering_event_id is None
    assert "Checked every page against the index." in change.note
    assert str(event.pk) in change.note
    assert Request.objects.get(pk=filed.pk).current_status_id == "production_received"
    assert Event.objects.get(pk=event.pk).event_type_id == "production"


def test_complete_production_needs_a_note_and_without_one_nothing_is_written(
    filed, inbound, staff_actor
) -> None:
    event = inbound[0]
    before = _counts(filed)
    for note in (None, "", "   "):
        with pytest.raises(ServiceError, match="needs a note"):
            classify_service.classify(staff_actor, event.pk, "production_complete", note=note)
    assert _counts(filed) == before
    assert Event.objects.get(pk=event.pk).event_type_id == "correspondence"


@pytest.mark.parametrize("kind", [k.key for k in KINDS])
def test_a_system_actor_is_refused_for_every_row_and_nothing_is_written(
    kind, filed, inbound, system_actor
) -> None:
    event = inbound[0]
    before = _counts(filed)
    with pytest.raises(NotAllowed):
        classify_service.classify(system_actor, event.pk, kind, note="a process cannot say this")
    assert _counts(filed) == before
    assert Request.objects.get(pk=filed.pk).current_status_id == "filed"
    assert Event.objects.get(pk=event.pk).event_type_id == "correspondence"


def test_the_status_chokepoint_alone_stops_a_system_actor_reaching_complete_production(
    filed, inbound, system_actor, monkeypatch
) -> None:
    """Take away classify's own human check, the audited edit's and override_status's, and the one
    guard left -- `_append_status_change` -- still refuses. It is the real lock; the others are
    belt and braces."""
    event = inbound[0]
    monkeypatch.setattr(classify_service, "require_human", lambda actor, what: actor.user_id)
    monkeypatch.setattr(status_service, "require_human", lambda actor, what: actor.user_id)
    monkeypatch.setattr(edits_service, "edit_with_audit", lambda *args, **kwargs: None)
    with pytest.raises(NotAllowed, match="written only by a person"):
        classify_service.classify(system_actor, event.pk, "production_complete", note="n")
    assert not StatusChange.objects.filter(
        request_id=filed.pk, status_id="production_received"
    ).exists()


# ---- what each input is for, and what is refused ----------------------------------------------


def test_a_fee_estimate_records_the_fee_and_the_task_can_be_dated(
    filed, inbound, staff_actor
) -> None:
    event = inbound[0]
    due = datetime(2026, 3, 27, 17, 0, tzinfo=UTC)
    result = classify_service.classify(
        staff_actor,
        event.pk,
        "fee_estimate",
        fee=FeeInput(
            amount_cents=41250, description="Duplication, 1650 pages", due_at=date(2026, 3, 20)
        ),
        task_due_at=due,
    )
    fee = result.fee
    assert fee is not None
    assert (fee.status, fee.amount_cents, fee.source_event_id) == ("assessed", 41250, event.pk)
    assert fee.description == "Duplication, 1650 pages" and fee.due_at == date(2026, 3, 20)
    assert result.task is not None and result.task.task_type == "decide_fee"
    assert result.task.due_at == due
    assert Request.objects.get(pk=filed.pk).current_status_id == "fee_estimate_received"


def test_a_fee_estimate_with_no_fee_given_records_no_fee_row(filed, inbound, staff_actor) -> None:
    result = classify_service.classify(staff_actor, inbound[0].pk, "fee_estimate")
    assert result.fee is None  # no fee row without a fee given
    assert result.status_change is not None and result.task is not None
    assert not Fee.objects.filter(request_id=filed.pk).exists()


def test_production_marks_exactly_the_named_files_as_records(filed, inbound, staff_actor) -> None:
    event, source, cover, records = inbound
    result = classify_service.classify(
        staff_actor, event.pk, "production_partial", record_attachment_ids=[records.pk]
    )
    roles = {a.filename: a.role for a in result.attachments}
    assert roles == {"cover-letter.pdf": "attachment", "records.pdf": "produced_record"}
    assert Attachment.objects.get(pk=cover.pk).role == "attachment"
    assert AuditLog.objects.filter(field_name="attachments.role", entity_id=records.pk).count() == 1


def test_leaving_the_files_empty_touches_no_file(filed, inbound, staff_actor) -> None:
    event, source, cover, records = inbound
    sources_service.set_attachment_roles(staff_actor, source.pk, [records.pk])
    classify_service.classify(staff_actor, event.pk, "production_partial")
    assert Attachment.objects.get(pk=records.pk).role == "produced_record"


def _roles(attachments) -> dict[str, str]:
    return {a.filename: a.role for a in attachments}


def test_classifying_a_shared_message_for_a_second_request_keeps_the_first_requests_records(
    filed, second_filed, staff_actor
) -> None:
    """One letter answering two requests is one source and two events, and a file's role belongs to
    the source. Recording request 2's production must ADD to the files marked as records for
    request 1, never replace them (P8b review F1)."""
    source = make_source(staff_actor)
    for_first = add_file(staff_actor, source, "for-first.pdf")
    for_second = add_file(staff_actor, source, "for-second.pdf")
    first_event = attach(staff_actor, filed, source)
    second_event = attach(staff_actor, second_filed, source)

    one = classify_service.classify(
        staff_actor, first_event.pk, "production_partial", record_attachment_ids=[for_first.pk]
    )
    assert _roles(one.attachments) == {
        "for-first.pdf": "produced_record",
        "for-second.pdf": "attachment",
    }

    two = classify_service.classify(
        staff_actor, second_event.pk, "production_partial", record_attachment_ids=[for_second.pk]
    )
    both = {"for-first.pdf": "produced_record", "for-second.pdf": "produced_record"}
    assert _roles(two.attachments) == both
    assert _roles(Attachment.objects.filter(source_id=source.pk)) == both

    # Nothing was ever demoted, so the first file has exactly one role change on record.
    (change,) = AuditLog.objects.filter(field_name="attachments.role", entity_id=for_first.pk)
    assert (change.old_value, change.new_value) == ("attachment", "produced_record")


def test_naming_a_file_that_is_already_a_record_changes_nothing_and_writes_no_audit_line(
    filed, second_filed, staff_actor
) -> None:
    source = make_source(staff_actor)
    marked = add_file(staff_actor, source, "marked.pdf")
    other = add_file(staff_actor, source, "other.pdf")
    first_event = attach(staff_actor, filed, source)
    second_event = attach(staff_actor, second_filed, source)
    classify_service.classify(
        staff_actor, first_event.pk, "production_partial", record_attachment_ids=[marked.pk]
    )
    audit_before = AuditLog.objects.filter(field_name="attachments.role").count()

    again = classify_service.classify(
        staff_actor, second_event.pk, "production_partial", record_attachment_ids=[marked.pk]
    )
    assert _roles(again.attachments) == {"marked.pdf": "produced_record", "other.pdf": "attachment"}
    assert AuditLog.objects.filter(field_name="attachments.role").count() == audit_before
    assert Attachment.objects.get(pk=other.pk).role == "attachment"


def test_classify_leaves_a_supporting_document_alone_while_it_adds_records(
    filed, inbound, staff_actor
) -> None:
    event, source, cover, records = inbound
    supporting = add_file(staff_actor, source, "exhibit.pdf", role="supporting_document")
    classify_service.classify(
        staff_actor, event.pk, "production_partial", record_attachment_ids=[records.pk]
    )
    assert Attachment.objects.get(pk=supporting.pk).role == "supporting_document"
    assert Attachment.objects.get(pk=cover.pk).role == "attachment"


def test_the_standalone_door_is_still_the_one_that_replaces_the_set(
    filed, second_filed, staff_actor
) -> None:
    """`classify` adds; `POST /sources/{id}/records` (`set_attachment_roles`) states the whole set,
    so naming one file after two were records unmarks the other. That asymmetry is the design."""
    source = make_source(staff_actor)
    for_first = add_file(staff_actor, source, "for-first.pdf")
    for_second = add_file(staff_actor, source, "for-second.pdf")
    first_event = attach(staff_actor, filed, source)
    second_event = attach(staff_actor, second_filed, source)
    classify_service.classify(
        staff_actor, first_event.pk, "production_partial", record_attachment_ids=[for_first.pk]
    )
    classify_service.classify(
        staff_actor, second_event.pk, "production_partial", record_attachment_ids=[for_second.pk]
    )

    rows = sources_service.set_attachment_roles(staff_actor, source.pk, [for_second.pk])
    assert _roles(rows) == {"for-first.pdf": "attachment", "for-second.pdf": "produced_record"}
    assert _roles(sources_service.set_attachment_roles(staff_actor, source.pk, [])) == {
        "for-first.pdf": "attachment",
        "for-second.pdf": "attachment",
    }


def test_the_agency_reference_number_is_saved_through_the_audited_edit(
    filed, inbound, staff_actor, staff_user
) -> None:
    event = inbound[0]
    classify_service.classify(
        staff_actor, event.pk, "acknowledgment", agency_reference_number="  PRA 26-0412 "
    )
    assert Request.objects.get(pk=filed.pk).agency_reference_number == "PRA 26-0412"
    (line,) = AuditLog.objects.filter(field_name="requests.agency_reference_number")
    assert line.new_value == "PRA 26-0412" and line.edited_by_id == staff_user.pk

    # The same number again, or none, writes no second line.
    classify_service.classify(
        staff_actor, event.pk, "acknowledgment", agency_reference_number="PRA 26-0412"
    )
    classify_service.classify(staff_actor, event.pk, "acknowledgment", agency_reference_number=" ")
    assert AuditLog.objects.filter(field_name="requests.agency_reference_number").count() == 1


def test_the_event_type_edit_is_audited_with_the_note_and_skipped_when_it_already_matches(
    filed, inbound, staff_actor
) -> None:
    event = inbound[0]
    classify_service.classify(staff_actor, event.pk, "determination", note="Per the letter.")
    (line,) = AuditLog.objects.filter(field_name="events.event_type")
    assert (line.old_value, line.new_value, line.note) == (
        "correspondence",
        "determination",
        "Per the letter.",
    )
    # Recording it as another determination-typed row leaves the type alone: no second line.
    classify_service.classify(staff_actor, event.pk, "denial")
    assert AuditLog.objects.filter(field_name="events.event_type").count() == 1
    assert Request.objects.get(pk=filed.pk).current_status_id == "denied"


def test_an_extension_notice_changes_no_status_and_computes_no_date(
    filed, inbound, staff_actor
) -> None:
    before = _deadlines(filed)
    result = classify_service.classify(
        staff_actor, inbound[0].pk, "extension", note="Asked for more time."
    )
    assert result.status_change is None and result.task is None
    assert Request.objects.get(pk=filed.pk).current_status_id == "filed"
    assert _deadlines(filed) == before
    assert Event.objects.get(pk=inbound[0].pk).event_type_id == "extension_notice"


@pytest.mark.parametrize(
    ("kind", "kwargs", "message"),
    [
        ("acknowledgment", {"fee": FeeInput(amount_cents=100)}, "does not take a fee"),
        ("fee_estimate", {"fee": FeeInput(amount_cents=-1)}, "negative"),
        ("denial", {"record_attachment_ids": ["x"]}, "does not mark any files"),
        ("acknowledgment", {"task_due_at": datetime(2026, 4, 1, tzinfo=UTC)}, "opens no task"),
        (
            "no_records",
            {"task_due_at": datetime(2026, 4, 1, tzinfo=UTC)},
            "takes no due date",
        ),
        ("not_a_kind", {}, "Unknown classification"),
    ],
)
def test_input_that_does_not_apply_is_refused_and_nothing_is_written(
    kind, kwargs, message, filed, inbound, staff_actor
) -> None:
    before = _counts(filed)
    if "record_attachment_ids" in kwargs:
        kwargs = {"record_attachment_ids": [inbound[3].pk]}
    with pytest.raises(ServiceError, match=message):
        classify_service.classify(staff_actor, inbound[0].pk, kind, **kwargs)
    assert _counts(filed) == before
    assert Event.objects.get(pk=inbound[0].pk).event_type_id == "correspondence"


def test_files_of_another_message_or_no_message_cannot_be_marked_as_records(
    filed, inbound, staff_actor
) -> None:
    event, _source, _cover, records = inbound
    stranger = add_file(staff_actor, make_source(staff_actor), "other.pdf")
    before = _counts(filed)
    with pytest.raises(ServiceError, match="Not files of this message"):
        classify_service.classify(
            staff_actor,
            event.pk,
            "production_partial",
            record_attachment_ids=[records.pk, stranger.pk],
        )
    sourceless = events_service.record_event(
        staff_actor,
        filed.pk,
        event_type="correspondence",
        channel="call",
        direction="inbound",
        occurred_at=FILED_AT,
        display_body="They phoned.",
    )
    with pytest.raises(ServiceError, match="no message behind it"):
        classify_service.classify(
            staff_actor, sourceless.pk, "production_partial", record_attachment_ids=[records.pk]
        )
    assert _counts(filed)["status_rows"] == before["status_rows"]
    assert Attachment.objects.get(pk=records.pk).role == "attachment"


def test_an_event_that_did_not_come_in_cannot_be_recorded_as_a_response(filed, staff_actor) -> None:
    sent = events_service.record_event(
        staff_actor,
        filed.pk,
        event_type="follow_up",
        channel="email",
        direction="outbound",
        occurred_at=FILED_AT,
        display_body="We wrote to them.",
    )
    with pytest.raises(ServiceError, match="did not come in"):
        classify_service.classify(staff_actor, sent.pk, "acknowledgment")
    with pytest.raises(ServiceError, match="did not come in"):
        classify_service.preview_classification(sent.pk, "acknowledgment")


def test_a_voided_event_and_an_unknown_event_cannot_be_classified(
    filed, inbound, staff_actor
) -> None:
    import uuid

    event = inbound[0]
    events_service.void_event(staff_actor, event.pk, "wrong request")
    with pytest.raises(InvalidTransition, match="voided"):
        classify_service.classify(staff_actor, event.pk, "acknowledgment")
    with pytest.raises(InvalidTransition, match="voided"):
        classify_service.preview_classification(event.pk, "acknowledgment")
    with pytest.raises(NotFound):
        classify_service.classify(staff_actor, uuid.uuid4(), "acknowledgment")


def test_a_failure_part_way_leaves_nothing_behind(filed, inbound, staff_actor, monkeypatch) -> None:
    """The event type is edited and the status appended before the task is opened. If the task
    cannot be, the first two are undone: it is one transaction."""
    event = inbound[0]

    def refuse(*args, **kwargs):
        raise ServiceError("the task could not be opened")

    monkeypatch.setattr(tasks_service, "create_task", refuse)
    before = _counts(filed)
    with pytest.raises(ServiceError, match="could not be opened"):
        classify_service.classify(staff_actor, event.pk, "clarification", note="n")
    assert _counts(filed) == before
    assert Request.objects.get(pk=filed.pk).current_status_id == "filed"
    assert Event.objects.get(pk=event.pk).event_type_id == "correspondence"


def test_classifying_does_not_mark_the_message_reviewed(filed, inbound, staff_actor) -> None:
    """Review is its own act: one letter can answer several requests."""
    event, source, *_ = inbound
    classify_service.classify(staff_actor, event.pk, "acknowledgment")
    from portal.records.models import Source

    assert Source.objects.get(pk=source.pk).reviewed_at is None
