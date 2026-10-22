"""Edits, the audit trail they leave, and the directory writes that follow the same discipline."""

from datetime import timedelta

import pytest
from portal.records.models import AuditLog, Fee, RequestItem
from portal.records.services import directory
from portal.records.services import edits as edits_service
from portal.records.services import events as events_service
from portal.records.services import fees as fees_service
from portal.records.services.common import NotAllowed, ServiceError

from .conftest import FILED_AT


def test_only_allowlisted_fields_can_be_edited(reserved, staff_actor) -> None:
    with pytest.raises(ServiceError, match="not editable"):
        edits_service.edit_with_audit(
            staff_actor, "requests", reserved.pk, "tracking_number", "REQ-2026-000999", "why"
        )
    with pytest.raises(ServiceError, match="not editable"):
        edits_service.edit_with_audit(
            staff_actor, "requests", reserved.pk, "current_status", "closed", "why"
        )


def test_an_edit_needs_a_human_and_a_note(reserved, staff_actor, system_actor) -> None:
    with pytest.raises(NotAllowed):
        edits_service.edit_with_audit(
            system_actor, "requests", reserved.pk, "subject", "New", "why"
        )
    with pytest.raises(ServiceError, match="note"):
        edits_service.edit_with_audit(staff_actor, "requests", reserved.pk, "subject", "New", " ")


def test_an_edit_writes_the_old_and_the_new_value(reserved, staff_actor) -> None:
    audit = edits_service.edit_with_audit(
        staff_actor, "requests", reserved.pk, "subject", "Widgets, revised", "they renamed it"
    )
    assert audit.field_name == "requests.subject"
    assert audit.old_value == "Widgets, January 2026"
    assert audit.new_value == "Widgets, revised"
    assert audit.edited_by_id == staff_actor.user_id
    reserved.refresh_from_db()
    assert reserved.subject == "Widgets, revised"


def test_classifying_an_event_appends_the_status_it_means(reserved, staff_actor) -> None:
    event = events_service.record_event(
        staff_actor,
        reserved.pk,
        event_type="correspondence",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT,
        display_body="We have no responsive records.",
    )
    edits_service.edit_with_audit(
        staff_actor,
        "events",
        event.pk,
        "event_type",
        "determination",
        "this is their determination, not routine correspondence",
        status="determination_issued",
    )
    event.refresh_from_db()
    reserved.refresh_from_db()
    assert event.event_type_id == "determination"
    assert reserved.current_status_id == "determination_issued"
    assert AuditLog.objects.filter(entity_id=event.pk, field_name="events.event_type").exists()


def test_an_agency_edit_is_audited_field_by_field(agency, staff_actor) -> None:
    updated = directory.update_agency(
        staff_actor,
        agency.pk,
        "they moved and changed their address",
        default_request_email="records@example.org",
        portal_url="https://example.org/records",
    )
    assert updated.default_request_email == "records@example.org"
    rows = AuditLog.objects.filter(entity_type="agencies", entity_id=agency.pk)
    assert sorted(row.field_name for row in rows) == [
        "agencies.default_request_email",
        "agencies.portal_url",
    ]
    assert directory.update_agency(staff_actor, agency.pk, "no change", name=agency.name)
    assert AuditLog.objects.filter(entity_type="agencies").count() == 2


def test_an_unknown_agency_field_is_refused(agency, staff_actor) -> None:
    with pytest.raises(ServiceError, match="not editable"):
        directory.update_agency(staff_actor, agency.pk, "why", records_law_id="nope")


def test_a_contact_is_created_and_edited_through_the_service(agency, staff_actor) -> None:
    contact = directory.create_contact(
        staff_actor, agency.pk, name="A. Clerk", email="clerk@example.org", title="Records Officer"
    )
    directory.update_contact(staff_actor, contact.pk, "they were promoted", title="Records Chief")
    contact.refresh_from_db()
    assert contact.title == "Records Chief"
    assert (
        AuditLog.objects.filter(
            entity_type="agency_contacts", field_name="agency_contacts.title"
        ).count()
        == 1
    )


def test_a_request_items_sort_order_is_editable_through_the_audited_door(
    reserved, staff_actor
) -> None:
    """F4: records_db.0005 grants UPDATE (sort_order) on records.request_items for the drafting
    fan-out to write through, but nothing made it reachable from here -- an audited edit was the
    only door the app role has for changing a row after creation, and EDITABLE did not list it.
    An unreachable grant is a standing invitation; this is what makes portal_app's grant actually
    usable through the one path every edit is required to take."""
    item = RequestItem.objects.filter(request_id=reserved.pk, item_number="1").get()
    audit = edits_service.edit_with_audit(
        staff_actor, "request_items", item.pk, "sort_order", 7, "reordered in the letter"
    )
    assert audit.field_name == "request_items.sort_order"
    assert audit.old_value == "0"
    assert audit.new_value == "7"
    item.refresh_from_db()
    assert item.sort_order == 7


def test_a_fee_change_audits_every_field_that_moved(reserved, staff_actor) -> None:
    fee = fees_service.record_fee(
        staff_actor, reserved.pk, status="assessed", amount_cents=2500, due_at=FILED_AT.date()
    )
    paid_at = FILED_AT + timedelta(days=3)
    fees_service.update_fee(
        staff_actor, fee.pk, status="paid", paid_at=paid_at, note="cheque cleared"
    )
    assert Fee.objects.get(pk=fee.pk).status == "paid"
    rows = AuditLog.objects.filter(entity_type="fees", entity_id=fee.pk)
    assert sorted(row.field_name for row in rows) == ["fees.paid_at", "fees.status"]
    assert rows.get(field_name="fees.status").old_value == "assessed"


def test_a_system_actor_cannot_leave_a_null_author_audit_row_via_update_fee(
    reserved, staff_actor, system_actor
) -> None:
    """R3: `edit_with_audit` already refuses a system actor; `fees.update_fee` reached the same
    audit_log table through `record_audit` without that gate. The gate belongs at `record_audit`
    itself so every present and future caller inherits it."""
    fee = fees_service.record_fee(
        staff_actor, reserved.pk, status="assessed", amount_cents=1500, due_at=FILED_AT.date()
    )
    before = AuditLog.objects.filter(entity_type="fees", entity_id=fee.pk).count()
    with pytest.raises(NotAllowed, match="requires a human actor"):
        fees_service.update_fee(
            system_actor, fee.pk, status="waived", waiver_reason="auto-swept", note="sweep"
        )
    assert AuditLog.objects.filter(entity_type="fees", entity_id=fee.pk).count() == before
    assert not AuditLog.objects.filter(entity_type="fees", edited_by_id__isnull=True).exists()
