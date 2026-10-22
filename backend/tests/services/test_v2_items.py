"""Narrowing a request's items, and adding one."""

import uuid

import pytest
from portal.records.models import AuditLog, Event, RequestItem, StatusChange
from portal.records.services import items as items_service
from portal.records.services import requests as requests_service
from portal.records.services.common import NotAllowed, NotFound, ServiceError

pytestmark = pytest.mark.usefixtures("as_each_role")


def _item(request, number):
    return request.items.get(item_number=number)


def _audit(item_id):
    return list(AuditLog.objects.filter(entity_id=item_id, field_name="request_items.description"))


# ---- narrowing ---------------------------------------------------------------------------------


def test_narrowing_audits_each_item_and_records_one_event_carrying_the_note(
    reserved, staff_actor, staff_user
) -> None:
    one, two = _item(reserved, "1"), _item(reserved, "2")
    statuses_before = StatusChange.objects.filter(request_id=reserved.pk).count()

    event = items_service.narrow_items(
        staff_actor,
        reserved.pk,
        {one.pk: "Widget inspection reports for outside agencies", two.pk: two.description},
        "narrowed at the agency's request",
    )

    assert RequestItem.objects.get(pk=one.pk).description.endswith("outside agencies")
    # Item 2 was named with its own wording back: no change, no line.
    assert RequestItem.objects.get(pk=two.pk).description == two.description
    (line,) = _audit(one.pk)
    assert line.old_value == "Widget inspection reports"
    assert line.new_value == "Widget inspection reports for outside agencies"
    assert line.note == "narrowed at the agency's request" and line.edited_by_id == staff_user.pk
    assert _audit(two.pk) == []

    assert Event.objects.filter(request_id=reserved.pk, event_type_id="narrowing").count() == 1
    assert event.event_type_id == "narrowing"
    assert event.display_body == "narrowed at the agency's request"
    assert (event.channel, event.direction) == ("system", "internal")
    assert event.recorded_by_user_id == staff_user.pk
    assert StatusChange.objects.filter(request_id=reserved.pk).count() == statuses_before


def test_narrowing_is_one_transaction(reserved, staff_actor) -> None:
    one, two = _item(reserved, "1"), _item(reserved, "2")
    with pytest.raises(ServiceError, match="nothing"):
        items_service.narrow_items(
            staff_actor, reserved.pk, {one.pk: "Fine new wording", two.pk: "  "}, "why"
        )
    assert RequestItem.objects.get(pk=one.pk).description == one.description
    assert _audit(one.pk) == []
    assert not Event.objects.filter(request_id=reserved.pk, event_type_id="narrowing").exists()


def test_narrowing_refuses_an_item_of_another_request(
    reserved, staff_actor, staff_user, agency
) -> None:
    elsewhere = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Elsewhere",
        request_text="Body.",
        items=[requests_service.NewItem("1", "Somebody else's item")],
    )
    foreign = _item(elsewhere, "1")
    with pytest.raises(NotFound):
        items_service.narrow_items(staff_actor, reserved.pk, {foreign.pk: "Hijacked"}, "why")
    with pytest.raises(NotFound):
        items_service.narrow_items(staff_actor, reserved.pk, {uuid.uuid4(): "Nothing"}, "why")
    assert RequestItem.objects.get(pk=foreign.pk).description == "Somebody else's item"


def test_narrowing_is_refused_for_a_process_a_blank_note_and_nothing_to_change(
    reserved, staff_actor, system_actor
) -> None:
    one = _item(reserved, "1")
    with pytest.raises(NotAllowed):
        items_service.narrow_items(system_actor, reserved.pk, {one.pk: "New"}, "why")
    with pytest.raises(ServiceError, match="note"):
        items_service.narrow_items(staff_actor, reserved.pk, {one.pk: "New"}, " ")
    with pytest.raises(ServiceError, match="Nothing to narrow"):
        items_service.narrow_items(staff_actor, reserved.pk, {}, "why")
    with pytest.raises(ServiceError, match="wording changed"):
        items_service.narrow_items(staff_actor, reserved.pk, {one.pk: one.description}, "why")


# ---- adding ------------------------------------------------------------------------------------


def test_an_added_item_goes_last_with_its_provision_and_leaves_an_audit_line(
    reserved, staff_actor
) -> None:
    added = items_service.add_item(
        staff_actor,
        reserved.pk,
        requests_service.NewItem("3", "Widget purchase orders", provision="widgets"),
        "the agency's letter mentions a third category",
    )
    assert (added.item_number, added.provision) == ("3", "widgets")
    assert added.sort_order > max(i.sort_order for i in reserved.items.exclude(pk=added.pk))
    (line,) = _audit(added.pk)
    assert (line.old_value, line.new_value) == (None, "Widget purchase orders")
    assert [i.item_number for i in reserved.items.order_by("sort_order", "item_number")] == [
        "1",
        "2",
        "3",
    ]


def test_an_added_item_can_name_a_parent_and_an_explicit_place(reserved, staff_actor) -> None:
    child = items_service.add_item(
        staff_actor,
        reserved.pk,
        requests_service.NewItem(
            "2a", "Footage from camera 2", parent_item_number="2", sort_order=9
        ),
        "split item 2",
    )
    assert child.parent_item_id == _item(reserved, "2").pk and child.sort_order == 9


def test_adding_is_refused_for_a_duplicate_number_a_missing_parent_and_the_usual_reasons(
    reserved, staff_actor, system_actor
) -> None:
    with pytest.raises(ServiceError, match="already exists"):
        items_service.add_item(
            staff_actor, reserved.pk, requests_service.NewItem("1", "Again"), "why"
        )
    with pytest.raises(ServiceError, match="parent"):
        items_service.add_item(
            staff_actor,
            reserved.pk,
            requests_service.NewItem("4", "Orphan", parent_item_number="99"),
            "why",
        )
    with pytest.raises(NotAllowed):
        items_service.add_item(system_actor, reserved.pk, requests_service.NewItem("4", "X"), "y")
    with pytest.raises(ServiceError, match="note"):
        items_service.add_item(staff_actor, reserved.pk, requests_service.NewItem("4", "X"), "")
    with pytest.raises(ServiceError, match="number and a description"):
        items_service.add_item(staff_actor, reserved.pk, requests_service.NewItem(" ", "X"), "why")
    assert reserved.items.count() == 2


def test_an_item_provision_is_reachable_through_the_audited_edit_door(
    reserved, staff_actor
) -> None:
    from portal.records.services import edits as edits_service

    two = _item(reserved, "2")
    edits_service.edit_with_audit(
        staff_actor,
        "request_items",
        two.pk,
        "provision",
        "widgets",
        "carve-out named in the letter",
    )
    assert RequestItem.objects.get(pk=two.pk).provision == "widgets"
