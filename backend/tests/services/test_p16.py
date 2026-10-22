"""P16: closure days (one more holiday on an agency's calendar) and merging two agencies."""

import json
from datetime import date

import pytest
from django.test import Client
from portal.records.actors import Actor
from portal.records.models import Agency, Deadline
from portal.records.services import closures, directory
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import InvalidTransition, NotFound, ServiceError

from .conftest import FILED_AT

# The fictional TESTLAW determination clock: seven calendar days from a filing on Feb 10, 2026
# (a Tuesday where the agency is) lands on Feb 17; a closure on the 17th and 18th moves it.
DUE = date(2026, 2, 17)
AFTER_CLOSURE = date(2026, 2, 19)


def _filed(actor, user, agency, subject="Closed days"):
    request = requests_service.reserve_request(
        actor, agency_id=agency.pk, requester_user_id=user.pk, subject=subject, request_text="B."
    )
    filing_service.file_request(actor, request.pk, filed_at=FILED_AT, channel="email")
    return request


def _determination_due(request) -> date:
    return Deadline.objects.get(
        request_id=request.pk, deadline_type="statutory_determination"
    ).due_at


def _other_agency(actor, name="Other Agency"):
    return directory.create_agency(actor, name=name, jurisdiction="Testland", records_law="TESTLAW")


def test_a_closure_moves_where_a_clock_lands_and_flags_what_already_ran(
    staff_actor, staff_user, agency
) -> None:
    before = _filed(staff_actor, staff_user, agency, "Before the closure")
    assert _determination_due(before) == DUE
    added = closures.add_closures(
        staff_actor,
        agency_ids=[agency.pk],
        starts_on=DUE,
        ends_on=date(2026, 2, 18),
        reason="Winter closure",
        citation="A citation the person typed",
    )
    assert len(added.closures) == 1 and added.closures[0].agency_id == agency.pk
    # Both active deadlines of the earlier request fall on or after the first closed day.
    assert added.flagged_deadlines == 2 and added.tasks_raised == 1
    flagged = Deadline.objects.filter(request_id=before.pk, flagged_for_review=True)
    assert flagged.count() == 2 and "Closure days were added" in flagged.first().flag_note
    assert any("Closure days" in t.description for t in tasks_service.open_tasks(before.pk))
    assert closures.closure_dates(agency.pk) == frozenset({DUE, date(2026, 2, 18)})
    # Adding another closure raises no second task while the first is open.
    again = closures.add_closures(
        staff_actor,
        agency_ids=[agency.pk],
        starts_on=date(2026, 3, 2),
        ends_on=date(2026, 3, 2),
        reason="Furlough",
        citation="Typed too",
    )
    assert again.tasks_raised == 0 and again.flagged_deadlines == 1

    after = _filed(staff_actor, staff_user, agency, "After the closure")
    assert _determination_due(after) == AFTER_CLOSURE

    # Voided, the days are open again; the void is kept, not deleted.
    voided = closures.void_closure(
        staff_actor, added.closures[0].pk, "Entered for the wrong office"
    )
    assert voided.voided_at is not None and voided.void_note.startswith("Entered")
    assert closures.closure_dates(agency.pk) == frozenset({date(2026, 3, 2)})
    assert _determination_due(_filed(staff_actor, staff_user, agency, "Third")) == DUE
    with pytest.raises(InvalidTransition):
        closures.void_closure(staff_actor, added.closures[0].pk, "twice")
    assert [c.pk for c in closures.closures_of(agency.pk, include_past=True)] == [
        again.closures[0].pk
    ]
    assert len(closures.closures_of(agency.pk, include_past=True, include_voided=True)) == 2


def test_a_closure_needs_its_words_and_real_agencies(staff_actor, agency) -> None:
    good = {"starts_on": DUE, "ends_on": DUE, "reason": "r", "citation": "c"}
    with pytest.raises(ServiceError, match="at least one agency"):
        closures.add_closures(staff_actor, agency_ids=[], **good)
    with pytest.raises(ServiceError, match="cannot end before"):
        closures.add_closures(
            staff_actor, agency_ids=[agency.pk], **{**good, "ends_on": date(2026, 2, 16)}
        )
    with pytest.raises(ServiceError, match="reason"):
        closures.add_closures(staff_actor, agency_ids=[agency.pk], **{**good, "reason": "  "})
    with pytest.raises(ServiceError, match="citation"):
        closures.add_closures(staff_actor, agency_ids=[agency.pk], **{**good, "citation": ""})
    with pytest.raises(NotFound):
        closures.add_closures(
            staff_actor, agency_ids=[agency.pk, "00000000-0000-0000-0000-000000000001"], **good
        )
    with pytest.raises(ServiceError, match="human"):
        closures.add_closures(Actor.for_system("sweep"), agency_ids=[agency.pk], **good)


def test_merging_marks_the_duplicate_and_reads_fold_it_in(staff_actor, staff_user, agency) -> None:
    twin = _other_agency(staff_actor, "Test Agency (imported twice)")
    theirs = _filed(staff_actor, staff_user, twin, "Filed with the twin")
    with pytest.raises(ServiceError, match="different"):
        directory.merge_agencies(staff_actor, keep_id=agency.pk, merge_id=agency.pk, note="x")
    with pytest.raises(ServiceError, match="why"):
        directory.merge_agencies(staff_actor, keep_id=agency.pk, merge_id=twin.pk, note=" ")
    merged = directory.merge_agencies(
        staff_actor, keep_id=agency.pk, merge_id=twin.pk, note="Same department, twice."
    )
    assert merged.merged_into_id == agency.pk and merged.merged_at is not None
    assert merged.merged_by_id == staff_user.pk and merged.merge_note == "Same department, twice."
    # Nothing moved: the request still says the twin. Reads fold the twin into the kept one.
    assert Agency.objects.get(pk=twin.pk).name == "Test Agency (imported twice)"
    assert theirs.pk in {r.pk for r in requests_service.docket(agency_id=agency.pk)}
    assert directory.same_agency_ids(agency.pk) == [agency.pk, twin.pk]
    # The duplicate takes no new requests and no closures.
    with pytest.raises(InvalidTransition, match="merged into"):
        requests_service.reserve_request(
            staff_actor,
            agency_id=twin.pk,
            requester_user_id=staff_user.pk,
            subject="x",
            request_text="y",
        )
    with pytest.raises(InvalidTransition, match="merged into"):
        closures.add_closures(
            staff_actor, agency_ids=[twin.pk], starts_on=DUE, ends_on=DUE, reason="r", citation="c"
        )
    # A merged agency cannot be the one kept, and nothing merges into a duplicate.
    third = _other_agency(staff_actor, "Third")
    with pytest.raises(InvalidTransition, match="itself merged"):
        directory.merge_agencies(staff_actor, keep_id=twin.pk, merge_id=third.pk, note="n")
    with pytest.raises(InvalidTransition, match="already merged"):
        directory.merge_agencies(staff_actor, keep_id=third.pk, merge_id=twin.pk, note="n")
    directory.merge_agencies(staff_actor, keep_id=agency.pk, merge_id=third.pk, note="n")
    with pytest.raises(InvalidTransition, match="merged into"):
        directory.merge_agencies(staff_actor, keep_id=third.pk, merge_id=twin.pk, note="n")
    # Undone: its own agency again.
    back = directory.unmerge_agency(staff_actor, twin.pk, "Not the same after all.")
    assert back.merged_into_id is None and back.merged_at is None and back.merge_note is None
    assert directory.same_agency_ids(agency.pk) == [agency.pk, third.pk]
    with pytest.raises(InvalidTransition, match="not merged"):
        directory.unmerge_agency(staff_actor, twin.pk, "again")
    with pytest.raises(InvalidTransition, match="merged into"):
        directory.merge_agencies(staff_actor, keep_id=twin.pk, merge_id=agency.pk, note="n")


def test_the_routes_are_administrators_to_write_and_staff_to_read(
    staff_actor, staff_user, admin_user, agency
) -> None:
    twin = _other_agency(staff_actor, "Twin")
    filed = _filed(staff_actor, staff_user, agency)
    staff, admin = Client(), Client()
    staff.force_login(staff_user)
    admin.force_login(admin_user)
    body = {
        "agency_ids": [str(agency.pk)],
        "starts_on": DUE.isoformat(),
        "ends_on": DUE.isoformat(),
        "reason": "Holiday closure",
        "citation": "Typed by a person",
    }
    post = lambda c, path, payload: c.post(  # noqa: E731
        path, data=json.dumps(payload), content_type="application/json"
    )
    assert post(staff, "/api/records/agencies/closures", body).status_code == 403
    made = post(admin, "/api/records/agencies/closures", body)
    assert made.status_code == 201, made.content
    assert made.json()["flagged_deadlines"] == 2 and made.json()["tasks_raised"] == 1
    closure_id = made.json()["closures"][0]["id"]
    listed = staff.get(
        f"/api/records/agencies/closures?agency_id={agency.pk}&include_past=true"
    ).json()
    assert [c["id"] for c in listed] == [closure_id] and listed[0]["agency_name"] == "Test Agency"
    # Coming closures only, unless asked.
    assert staff.get("/api/records/agencies/closures").json() == []
    assert len(staff.get("/api/records/agencies/closures?include_past=true").json()) == 1
    detail = staff.get(f"/api/records/agencies/{agency.pk}").json()
    assert [c["id"] for c in detail["closures"]] == [closure_id] and detail["merged_from"] == []
    assert filed.tracking_number in json.dumps(detail)

    merge = {"keep_id": str(agency.pk), "merge_id": str(twin.pk), "note": "Same."}
    assert post(staff, "/api/records/agencies/merge", merge).status_code == 403
    merged = post(admin, "/api/records/agencies/merge", merge)
    assert merged.status_code == 200 and merged.json()["merged_into_agency_id"] == str(agency.pk)
    names = [a["name"] for a in staff.get("/api/records/agencies").json()]
    assert "Twin" not in names
    names = [a["name"] for a in staff.get("/api/records/agencies?include_merged=true").json()]
    assert "Twin" in names
    detail = staff.get(f"/api/records/agencies/{agency.pk}").json()
    assert [a["name"] for a in detail["merged_from"]] == ["Twin"]
    assert post(staff, f"/api/records/agencies/{twin.pk}/unmerge", {"note": "n"}).status_code == 403
    assert (
        post(admin, f"/api/records/agencies/{twin.pk}/unmerge", {"note": "Own."}).status_code == 200
    )
    assert (
        post(staff, f"/api/records/agencies/closures/{closure_id}/void", {"note": "n"}).status_code
        == 403
    )
    gone = post(
        admin, f"/api/records/agencies/closures/{closure_id}/void", {"note": "Wrong office"}
    )
    assert gone.status_code == 200 and gone.json()["voided_at"] is not None
    assert (
        post(admin, "/api/records/agencies/closures", {**body, "citation": ""}).status_code == 422
    )
