"""Reservation: the number, the alias, and the two doors that stay shut."""

import re
from datetime import UTC

import pytest
from django.db import connection
from django.utils import timezone
from portal.records import aliases
from portal.records.models import (
    Request,
    RequestItem,
    StatusChange,
    UseServicesInstead,
    writable,
)
from portal.records.services import requests as requests_service

TRACKING_RE = re.compile(r"^REQ-\d{4}-\d{6}$")


def test_tracking_number_and_alias_shape(reserved) -> None:
    assert TRACKING_RE.match(reserved.tracking_number), reserved.tracking_number
    assert aliases.ALIAS_RE.match(reserved.inbound_alias_email), reserved.inbound_alias_email
    assert reserved.inbound_alias_email.endswith("@requests.thesclu.org")


def test_reservation_leaves_filed_at_null_and_status_pending(reserved) -> None:
    assert reserved.filed_at is None
    assert reserved.current_status_id == "pending_filing"
    history = StatusChange.objects.filter(request_id=reserved.pk)
    assert [row.status_id for row in history] == ["pending_filing"]
    assert history.get().note == "reserved"


def test_items_resolve_their_parent_within_the_batch(db, staff_actor, staff_user, agency) -> None:
    request = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Nested",
        request_text="One item split in two.",
        items=[
            requests_service.NewItem("4", "Camera footage"),
            requests_service.NewItem("4a", "Body-worn only", parent_item_number="4"),
        ],
    )
    child = request.items.get(item_number="4a")
    assert child.parent_item.item_number == "4"


def test_void_refuses_to_leave_a_request_with_no_status_history(reserved, staff_actor) -> None:
    """The refusal is not tidiness. Without it, `recompute_request_current_status` writes NULL
    into requests.current_status (NOT NULL) and the user sees a raw database error."""
    from portal.records.services import events as events_service
    from portal.records.services.common import InvalidTransition

    event = events_service.record_event(
        staff_actor,
        reserved.pk,
        event_type="note",
        channel="system",
        direction="internal",
        occurred_at=reserved.created_at,
        display_body="Reclassifying the reservation.",
        status="acknowledged",
    )
    # Void the reservation row itself, so this event now backs the ONLY unvoided status row.
    StatusChange.objects.filter(request_id=reserved.pk, reason="manual_override").update(
        voided_at=timezone.now()
    )
    assert StatusChange.objects.filter(request_id=reserved.pk, voided_at__isnull=True).count() == 1

    with pytest.raises(InvalidTransition, match="no status history"):
        events_service.void_event(staff_actor, event.pk, "would orphan the request")
    reserved.refresh_from_db()
    assert reserved.current_status_id == "acknowledged"


def test_the_database_alone_would_raise_a_raw_not_null_error(reserved, staff_actor) -> None:
    """The other half of the proof above: what happens when the service is bypassed."""
    from django.db import IntegrityError

    with pytest.raises(IntegrityError, match='null value in column "current_status"'):
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE records.status_changes SET voided_at = now() WHERE request_id = %s",
                [str(reserved.pk)],
            )


def test_model_writes_outside_the_services_are_refused(db, staff_user, agency) -> None:
    with pytest.raises(UseServicesInstead):
        Request.objects.create(
            tracking_number="REQ-2026-999999",
            origin="filed",
            requester_id=staff_user.pk,
            agency_id=agency.pk,
            subject="s",
            request_text="t",
            current_status_id="pending_filing",
        )


def test_requests_are_never_deleted(reserved) -> None:
    with pytest.raises(UseServicesInstead):
        reserved.delete()
    with writable():
        with pytest.raises(UseServicesInstead):
            reserved.delete()
    assert reserved.created_at.tzinfo is not None
    assert reserved.created_at.astimezone(UTC) is not None


def test_sort_order_survives_reservation_and_outranks_the_free_text_number(
    db, staff_actor, staff_user, agency
) -> None:
    """records_db.0005. `item_number` is free text, so ordering by it alone puts '10' ahead of
    '2' -- an agency answering the items in the order the letter printed them would be answering
    a different letter. Reservation carries the drafting order across, and everything that reads
    items back orders by it first.
    """
    request = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Ten items",
        request_text="Body.",
        items=[
            requests_service.NewItem("2", "Second ask", sort_order=1),
            requests_service.NewItem("10", "Tenth ask", sort_order=9),
        ],
    )
    items = RequestItem.objects.filter(request_id=request.pk)
    assert [i.item_number for i in items.order_by("item_number")] == ["10", "2"], (
        "control: the string sort is the bug sort_order exists to fix"
    )
    assert [i.item_number for i in items.order_by("sort_order", "item_number")] == ["2", "10"]
    assert [i.sort_order for i in items.order_by("sort_order")] == [1, 9]
