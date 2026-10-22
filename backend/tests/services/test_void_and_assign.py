"""Voiding and reassignment: the corrections that keep "we thought X" on the record."""

from datetime import timedelta

import pytest
from portal.records.models import AuditLog, Deadline, Event, StatusChange
from portal.records.services import assignment as assignment_service
from portal.records.services import deadlines as deadlines_service
from portal.records.services import events as events_service
from portal.records.services import filing as filing_service
from portal.records.services import sources as sources_service
from portal.records.services.common import InvalidTransition, NotAllowed

from .conftest import FILED_AT


@pytest.fixture
def filed(reserved, staff_actor, rules):
    filing_service.file_request(staff_actor, reserved.pk, filed_at=FILED_AT, channel="email")
    reserved.refresh_from_db()
    return reserved


def test_voiding_an_event_recomputes_the_cache_and_flags_its_deadlines(filed, staff_actor) -> None:
    event = events_service.record_event(
        staff_actor,
        filed.pk,
        event_type="determination",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + timedelta(days=2),
        display_body="We will produce by April.",
        status="determination_issued",
    )
    promised = deadlines_service.open_deadline(
        staff_actor,
        filed.pk,
        deadline_type="production",
        due_at=(FILED_AT + timedelta(days=60)).date(),
        basis="agency_commitment",
        source_event_id=event.pk,
    )
    filed.refresh_from_db()
    assert filed.current_status_id == "determination_issued"

    events_service.void_event(staff_actor, event.pk, "belongs to another request")

    filed.refresh_from_db()
    assert filed.current_status_id == "filed"
    assert Event.objects.get(pk=event.pk).void_reason == "belongs to another request"
    assert StatusChange.objects.get(triggering_event_id=event.pk).voided_at is not None
    promised.refresh_from_db()
    assert promised.flagged_for_review
    assert "source event voided" in promised.flag_note
    audit = AuditLog.objects.get(entity_type="events", entity_id=event.pk)
    assert audit.field_name == "events.voided_at"
    assert audit.note == "belongs to another request"


def test_voiding_is_human_only_and_never_happens_twice(filed, staff_actor, system_actor) -> None:
    event = events_service.record_event(
        staff_actor,
        filed.pk,
        event_type="correspondence",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT,
        display_body="hello",
    )
    with pytest.raises(NotAllowed):
        events_service.void_event(system_actor, event.pk, "automated guess")
    events_service.void_event(staff_actor, event.pk, "wrong request")
    with pytest.raises(InvalidTransition, match="already voided"):
        events_service.void_event(staff_actor, event.pk, "again")


def test_also_applies_puts_a_second_event_on_the_same_source(
    filed, staff_actor, staff_user, agency, rules
) -> None:
    from portal.records.services import requests as requests_service

    other = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Second request",
        request_text="Also asked for.",
    )
    source = sources_service.register_source(
        staff_actor,
        source_type="postmark_email",
        raw_storage_url="s3://test/one-letter.eml",
        external_ref="pm-multi-1",
        flagged=True,
        flag_note="looks like it answers more than one request",
    )
    first = events_service.record_event(
        staff_actor,
        filed.pk,
        event_type="correspondence",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + timedelta(days=4),
        display_body="One letter, three requests.",
        source_id=source.pk,
    )
    second = assignment_service.assign_source_to_request(
        staff_actor, source.pk, other.pk, mode="also_applies", from_event_id=first.pk
    )
    assert second.request_id == other.pk
    assert second.source_id == source.pk
    assert second.occurred_at == first.occurred_at
    assert second.display_body == first.display_body
    assert Event.objects.get(pk=first.pk).voided_at is None
    source.refresh_from_db()
    assert source.reviewed_at is not None
    assert source.flagged_for_review is False


def test_belongs_here_instead_voids_the_original(
    filed, staff_actor, staff_user, agency, rules
) -> None:
    from portal.records.services import requests as requests_service

    other = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="The real one",
        request_text="Actually about this.",
    )
    source = sources_service.register_source(
        staff_actor, source_type="postmark_email", raw_storage_url="s3://test/misrouted.eml"
    )
    wrong = events_service.record_event(
        staff_actor,
        filed.pk,
        event_type="correspondence",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + timedelta(days=4),
        display_body="Auto-matched to the wrong alias.",
        source_id=source.pk,
        status="acknowledged",
    )
    moved = assignment_service.assign_source_to_request(
        staff_actor, source.pk, other.pk, mode="belongs_here_instead", from_event_id=wrong.pk
    )
    wrong.refresh_from_db()
    assert wrong.voided_at is not None
    assert wrong.void_reason == f"reassigned to {other.tracking_number}"
    assert moved.request_id == other.pk
    assert moved.source_id == source.pk
    filed.refresh_from_db()
    assert filed.current_status_id == "filed"


def test_registering_the_same_external_ref_twice_returns_the_first_row(db, staff_actor) -> None:
    """A re-delivered webhook must find the evidence it already stored, not duplicate it."""
    first = sources_service.register_source(
        staff_actor,
        source_type="postmark_email",
        raw_storage_url="s3://test/a.eml",
        external_ref="pm-dup-1",
    )
    again = sources_service.register_source(
        staff_actor,
        source_type="postmark_email",
        raw_storage_url="s3://test/b.eml",
        external_ref="pm-dup-1",
    )
    assert again.pk == first.pk
    assert again.raw_storage_url == "s3://test/a.eml"


def test_opening_a_superseding_deadline_closes_the_old_one(filed, staff_actor) -> None:
    original = deadlines_service.earliest_active(filed.pk, "production")
    replacement = deadlines_service.open_deadline(
        staff_actor,
        filed.pk,
        deadline_type="production",
        due_at=original.due_at + timedelta(days=14),
        basis="agency_commitment",
        supersedes=original.pk,
    )
    original.refresh_from_db()
    assert original.status == "superseded"
    assert original.superseded_by_deadline_id == replacement.pk
    assert Deadline.objects.get(pk=replacement.pk).flagged_for_review


def test_voiding_appends_to_an_existing_conflict_note_instead_of_overwriting_it(
    filed, staff_actor
) -> None:
    """R4: a deadline can already carry a conflict note from `open_deadline` before its source
    event is voided. The void must APPEND its own note, not replace the conflict text -- a
    reviewer reading the flag afterward needs both causes, not just the most recent one."""
    determination = deadlines_service.earliest_active(filed.pk, "statutory_determination")
    event = events_service.record_event(
        staff_actor,
        filed.pk,
        event_type="determination",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + timedelta(days=1),
        display_body="Extension letter: a different determination date.",
    )
    conflicting = deadlines_service.open_deadline(
        staff_actor,
        filed.pk,
        deadline_type="statutory_determination",
        due_at=determination.due_at + timedelta(days=10),
        basis="agency_commitment",
        source_event_id=event.pk,
    )
    assert conflicting.flagged_for_review
    conflict_text = conflicting.flag_note
    assert "conflicts with active deadline" in conflict_text

    events_service.void_event(staff_actor, event.pk, "extension letter was a forgery")

    conflicting.refresh_from_db()
    assert conflict_text in conflicting.flag_note
    assert "source event voided: extension letter was a forgery" in conflicting.flag_note
    assert conflicting.flagged_for_review
