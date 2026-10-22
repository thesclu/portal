"""The P8a additions to the records API, one door at a time, each from the caller's side."""

import uuid

import pytest
from django.test import Client
from portal.records.actors import Actor
from portal.records.services import requests as requests_service

from tests.api.conftest import patch_json, post_json

pytestmark = pytest.mark.usefixtures("as_each_role")

DEADLINE = {"deadline_type": "production", "basis": "agency_commitment"}


@pytest.fixture
def with_items(db, staff_user, agency):
    """The api conftest's `reserved` has no items; narrowing and adding need some."""
    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Widgets",
        request_text="Widget records.",
        items=[
            requests_service.NewItem("1", "Widget inspection reports"),
            requests_service.NewItem("2", "Widget camera footage"),
        ],
    )


def _open(client, request_id, due_at, **extra):
    return post_json(
        client,
        f"/api/records/requests/{request_id}/deadlines",
        {**DEADLINE, "due_at": due_at, **extra},
    )


# ---- assignment --------------------------------------------------------------------------------


def test_assign_a_request_then_unassign_it(staff_client, reserved, colleague_user):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/assign",
        {"user_id": str(colleague_user.pk)},
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["assigned_to_user_id"] == str(colleague_user.pk)
    detail = staff_client.get(f"/api/records/requests/{reserved.pk}").json()
    assert detail["assigned_to_user_id"] == str(colleague_user.pk)

    resp = post_json(staff_client, f"/api/records/requests/{reserved.pk}/assign", {"user_id": None})
    assert resp.json()["assigned_to_user_id"] is None


def test_assigning_to_a_requester_or_a_stranger_is_refused(staff_client, reserved, requester_user):
    assert (
        post_json(
            staff_client,
            f"/api/records/requests/{reserved.pk}/assign",
            {"user_id": str(requester_user.pk)},
        ).status_code
        == 422
    )
    assert (
        post_json(
            staff_client,
            f"/api/records/requests/{reserved.pk}/assign",
            {"user_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )


def test_a_requester_cannot_assign_anything(requester_client, own_request, colleague_user):
    resp = post_json(
        requester_client,
        f"/api/records/requests/{own_request.pk}/assign",
        {"user_id": str(colleague_user.pk)},
    )
    assert resp.status_code == 403


def test_the_docket_filters_by_assignee_me_or_uuid_and_by_sensitivity(
    staff_client, staff_user, colleague_user, reserved, agency
):
    from portal.records.actors import Actor
    from portal.records.services import requests as requests_service

    mine = requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Mine",
        request_text="Body.",
        sensitivity="privileged",
    )
    requests_service.assign_request(Actor.human(staff_user), mine.pk, staff_user.pk)

    def ids(**params):
        resp = staff_client.get("/api/records/requests", params)
        assert resp.status_code == 200, resp.content
        return {r["id"] for r in resp.json()["items"]}

    assert ids(assignee="me") == {str(mine.pk)}
    assert ids(assignee=str(staff_user.pk)) == {str(mine.pk)}
    assert ids(assignee=str(colleague_user.pk)) == set()
    assert ids(sensitivity="privileged") == {str(mine.pk)}
    assert ids(sensitivity="internal") == {str(reserved.pk)}
    assert staff_client.get("/api/records/requests", {"assignee": "everyone"}).status_code == 422


def test_a_docket_row_carries_its_day_count_and_assignee(staff_client, reserved):
    _open(staff_client, reserved.pk, "2026-04-01")
    row = next(
        r
        for r in staff_client.get("/api/records/requests").json()["items"]
        if r["id"] == str(reserved.pk)
    )
    assert row["next_deadline_at"] == "2026-04-01"
    # The agency fixture's law has no calendar in the shipped data, so the count is null rather
    # than a guess; the signed value itself is pinned in the service tests.
    assert "days_to_next_deadline" in row and row["assigned_to_user_id"] is None


# ---- narrowing and adding items ----------------------------------------------------------------


def test_narrow_a_request_returns_the_request_with_the_new_wording(staff_client, with_items):
    item = with_items.items.get(item_number="1")
    resp = post_json(
        staff_client,
        f"/api/records/requests/{with_items.pk}/narrow",
        {"edits": {str(item.pk): "Inspection reports for outside agencies"}, "note": "agreed"},
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert {i["id"]: i["description"] for i in body["items"]}[str(item.pk)] == (
        "Inspection reports for outside agencies"
    )
    assert [e["event_type"] for e in body["events"]] == ["narrowing"]
    assert body["events"][0]["display_body"] == "agreed"


def test_narrow_refuses_a_stranger_item_and_an_empty_edit(staff_client, reserved):
    assert (
        post_json(
            staff_client,
            f"/api/records/requests/{reserved.pk}/narrow",
            {"edits": {str(uuid.uuid4()): "x"}, "note": "n"},
        ).status_code
        == 404
    )
    assert (
        post_json(
            staff_client, f"/api/records/requests/{reserved.pk}/narrow", {"edits": {}, "note": "n"}
        ).status_code
        == 422
    )


def test_add_a_request_item(staff_client, with_items):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{with_items.pk}/items",
        {
            "item_number": "3",
            "description": "Purchase orders",
            "provision": "widgets",
            "note": "named in the agency's reply",
        },
    )
    assert resp.status_code == 201, resp.content
    assert (resp.json()["item_number"], resp.json()["provision"]) == ("3", "widgets")
    detail = staff_client.get(f"/api/records/requests/{with_items.pk}").json()
    assert [i["item_number"] for i in detail["items"]] == ["1", "2", "3"]
    duplicate = post_json(
        staff_client,
        f"/api/records/requests/{with_items.pk}/items",
        {"item_number": "3", "description": "Again", "note": "n"},
    )
    assert duplicate.status_code == 422


def test_a_new_request_can_carry_item_provisions(staff_client, staff_user, agency):
    resp = post_json(
        staff_client,
        "/api/records/requests",
        {
            "agency_id": str(agency.pk),
            "requester_user_id": str(staff_user.pk),
            "subject": "With provisions",
            "request_text": "Body.",
            "items": [{"item_number": "1", "description": "Footage", "provision": "widgets"}],
        },
    )
    assert resp.status_code == 201, resp.content
    detail = staff_client.get(f"/api/records/requests/{resp.json()['id']}").json()
    assert detail["items"][0]["provision"] == "widgets"


# ---- tasks -------------------------------------------------------------------------------------


def _task(client, request_id, task_type="manual", **extra):
    resp = post_json(
        client,
        "/api/records/tasks",
        {"request_id": str(request_id), "task_type": task_type, "description": "Do it", **extra},
    )
    assert resp.status_code == 201, resp.content
    return resp.json()


def test_a_task_reports_who_made_it_who_has_it_and_its_category(
    staff_client, reserved, staff_user, colleague_user
):
    task = _task(
        staff_client, reserved.pk, "decide_fee", assigned_to_user_id=str(colleague_user.pk)
    )
    assert task["created_by_user_id"] == str(staff_user.pk) and task["created_by_system"] is None
    assert task["assigned_to_user_id"] == str(colleague_user.pk)
    assert task["category"] == "follow_ups"
    # It names its request, so a list of tasks reads without opening each one.
    assert task["tracking_number"] == reserved.tracking_number
    assert task["agency_name"] == reserved.agency.name
    # The same task read through the request page is the same shape: one serializer, two doors.
    detail = staff_client.get(f"/api/records/requests/{reserved.pk}").json()
    assert detail["tasks"] == [task]


def test_the_tasks_list_filters_by_assignee_state_and_category(
    staff_client, reserved, staff_user, colleague_user
):
    mine = _task(staff_client, reserved.pk, "mail_letter", assigned_to_user_id=str(staff_user.pk))
    theirs = _task(staff_client, reserved.pk, "manual", assigned_to_user_id=str(colleague_user.pk))
    done = _task(staff_client, reserved.pk, "confirm_filed")
    post_json(staff_client, f"/api/records/tasks/{done['id']}/complete", {})

    def ids(**params):
        resp = staff_client.get("/api/records/tasks", params)
        assert resp.status_code == 200, resp.content
        return {t["id"] for t in resp.json()}

    assert ids(assignee="me") == {mine["id"]}
    assert ids(assignee=str(colleague_user.pk)) == {theirs["id"]}
    assert ids(state="completed") == {done["id"]}
    assert ids(state="all") == {mine["id"], theirs["id"], done["id"]}
    assert ids(category="person") == {theirs["id"]}
    assert ids(category="delivery_and_filing") == {mine["id"]}
    assert ids(open="false") == {mine["id"], theirs["id"], done["id"]}  # the original switch
    assert staff_client.get("/api/records/tasks", {"state": "finished"}).status_code == 422
    assert staff_client.get("/api/records/tasks", {"category": "other"}).status_code == 422


def test_patch_a_task_leaves_omitted_fields_alone_and_null_clears_the_date(staff_client, reserved):
    task = _task(staff_client, reserved.pk, due_at="2026-03-01T17:00:00Z")
    resp = patch_json(
        staff_client, f"/api/records/tasks/{task['id']}", {"note": "reworded", "description": "New"}
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["description"] == "New" and resp.json()["due_at"] is not None
    resp = patch_json(
        staff_client, f"/api/records/tasks/{task['id']}", {"note": "no date now", "due_at": None}
    )
    assert resp.json()["due_at"] is None and resp.json()["description"] == "New"
    assert (
        patch_json(staff_client, f"/api/records/tasks/{task['id']}", {"note": "x"}).status_code
        == 422
    )


def test_assign_and_reopen_a_task(staff_client, reserved, colleague_user):
    task = _task(staff_client, reserved.pk)
    resp = post_json(
        staff_client,
        f"/api/records/tasks/{task['id']}/assign",
        {"user_id": str(colleague_user.pk)},
    )
    assert resp.status_code == 200 and resp.json()["assigned_to_user_id"] == str(colleague_user.pk)

    assert (
        post_json(
            staff_client, f"/api/records/tasks/{task['id']}/reopen", {"note": "n"}
        ).status_code
        == 409
    )
    post_json(staff_client, f"/api/records/tasks/{task['id']}/complete", {})
    resp = post_json(
        staff_client, f"/api/records/tasks/{task['id']}/reopen", {"note": "not actually done"}
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["completed_at"] is None and resp.json()["completed_by_id"] is None


def test_the_new_task_doors_are_closed_to_a_requester(
    requester_user, own_request, staff_client, staff_user
):
    task = _task(staff_client, own_request.pk)
    # `staff_client` and `requester_client` share one Client object (api/conftest.py), so the
    # second login would silently replace the first. A requester of their own, here.
    requester_client = Client()
    requester_client.force_login(requester_user)
    for method, path, body in (
        (patch_json, f"/api/records/tasks/{task['id']}", {"note": "n", "description": "x"}),
        (post_json, f"/api/records/tasks/{task['id']}/assign", {}),
        (post_json, f"/api/records/tasks/{task['id']}/reopen", {"note": "n"}),
    ):
        assert method(requester_client, path, body).status_code == 403, path


# ---- deadlines ---------------------------------------------------------------------------------


def test_a_deadline_of_an_unknown_kind_is_422_not_500(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/deadlines",
        {"deadline_type": "not_a_kind", "due_at": "2026-04-01", "basis": "agency_commitment"},
    )
    assert resp.status_code == 422, resp.content


def test_the_new_kinds_open_through_the_api(staff_client, reserved):
    for kind in ("fee_response", "other"):
        resp = post_json(
            staff_client,
            f"/api/records/requests/{reserved.pk}/deadlines",
            {"deadline_type": kind, "due_at": "2026-04-01", "basis": "agency_commitment"},
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["deadline_type"] == kind


def test_resolve_due_date_keeps_the_path_deadline_and_supersedes_the_other(staff_client, reserved):
    earlier = _open(staff_client, reserved.pk, "2026-03-01").json()
    later = _open(staff_client, reserved.pk, "2026-04-01").json()
    resp = post_json(
        staff_client,
        f"/api/records/deadlines/{later['id']}/resolve",
        {"drop_deadline_id": earlier["id"], "note": "the agency's letter governs"},
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["id"] == later["id"] and resp.json()["flagged_for_review"] is False
    detail = staff_client.get(f"/api/records/requests/{reserved.pk}").json()
    dropped = next(d for d in detail["deadlines"] if d["id"] == earlier["id"])
    assert dropped["status"] == "superseded"
    assert dropped["superseded_by_deadline_id"] == later["id"]
    assert detail["next_deadline_at"] == "2026-04-01"
    # Resolving the same pair again: the dropped one is no longer active.
    again = post_json(
        staff_client,
        f"/api/records/deadlines/{later['id']}/resolve",
        {"drop_deadline_id": earlier["id"], "note": "again"},
    )
    assert again.status_code == 409


# ---- directors ---------------------------------------------------------------------------------


def _edit_due_at(client, request_id, deadline_id, value="2026-05-01"):
    return post_json(
        client,
        f"/api/records/requests/{request_id}/edits",
        {
            "entity_type": "deadlines",
            "entity_id": deadline_id,
            "field": "due_at",
            "new_value": value,
            "note": "the agency moved it",
        },
    )


def test_a_staff_attorney_or_staff_member_with_the_old_mark_cannot_move_a_deadline(
    director_client, reserved
):
    """P20h: the doors that move an existing deadline's date are an administrator's (30l, 42d).
    Both doors BUILD_PLAN names, plus the third that does the same thing (opening a deadline that
    `supersedes` another): each refuses a staff attorney, and a member of staff who still carries
    the retired Director mark, with 403, and changes nothing."""
    from portal.core.models import AppRole
    from portal.core.services.identity import create_user

    deadline = _open(director_client, reserved.pk, "2026-04-01").json()
    attorney = create_user(
        email="attorney@example.org",
        name="Attorney",
        records_role="staff_attorney",
    )
    marked = create_user(email="marked@example.org", name="Marked", records_role="staff")
    AppRole.objects.filter(user=marked, app="records").update(is_director=True)
    for person in (attorney, marked):
        client = Client()
        client.force_login(person)
        assert _edit_due_at(client, reserved.pk, deadline["id"]).status_code == 403
        assert (
            post_json(
                client,
                f"/api/records/deadlines/{deadline['id']}/supersede",
                {**DEADLINE, "due_at": "2026-05-01"},
            ).status_code
            == 403
        )
        superseding = _open(client, reserved.pk, "2026-05-01", supersedes=deadline["id"])
        assert superseding.status_code == 403

    detail = director_client.get(f"/api/records/requests/{reserved.pk}").json()
    assert [(d["due_at"], d["status"]) for d in detail["deadlines"]] == [("2026-04-01", "active")]


def test_staff_without_the_marker_are_refused_too(staff_client, director_client, reserved):
    deadline = _open(director_client, reserved.pk, "2026-04-01").json()
    assert _edit_due_at(staff_client, reserved.pk, deadline["id"]).status_code == 403
    assert (
        post_json(
            staff_client,
            f"/api/records/deadlines/{deadline['id']}/supersede",
            {**DEADLINE, "due_at": "2026-05-01"},
        ).status_code
        == 403
    )


def test_a_director_passes_all_three_doors(director_client, reserved):
    first = _open(director_client, reserved.pk, "2026-04-01").json()
    assert _edit_due_at(director_client, reserved.pk, first["id"]).status_code == 200
    second = post_json(
        director_client,
        f"/api/records/deadlines/{first['id']}/supersede",
        {**DEADLINE, "due_at": "2026-06-01"},
    )
    assert second.status_code == 201, second.content
    third = _open(director_client, reserved.pk, "2026-07-01", supersedes=second.json()["id"])
    assert third.status_code == 201, third.content


def test_supersedes_cannot_reach_another_requests_deadline(director_client, reserved, own_request):
    """F1, from the API: a write on request A must not end request B's clock."""
    theirs = _open(director_client, own_request.pk, "2026-09-18").json()

    resp = _open(director_client, reserved.pk, "2026-10-01", supersedes=theirs["id"])

    assert resp.status_code == 422, resp.content
    assert "different request" in resp.json()["detail"]
    theirs_now = director_client.get(f"/api/records/requests/{own_request.pk}").json()
    assert theirs_now["next_deadline_at"] == "2026-09-18"
    assert [(d["status"], d["superseded_by_deadline_id"]) for d in theirs_now["deadlines"]] == [
        ("active", None)
    ]
    assert director_client.get(f"/api/records/requests/{reserved.pk}").json()["deadlines"] == []


def test_a_deadline_that_is_not_active_cannot_be_superseded_by_either_door(
    director_client, reserved
):
    """F1: a waived row was flipped to `superseded` and lost what it was."""
    waived = _open(director_client, reserved.pk, "2026-04-01").json()
    post_json(
        director_client,
        f"/api/records/deadlines/{waived['id']}/waive",
        {"note": "agency withdrew the promise"},
    )

    via_open = _open(director_client, reserved.pk, "2026-05-01", supersedes=waived["id"])
    via_supersede = post_json(
        director_client,
        f"/api/records/deadlines/{waived['id']}/supersede",
        {**DEADLINE, "due_at": "2026-05-01"},
    )

    assert (via_open.status_code, via_supersede.status_code) == (409, 409)
    assert "not active" in via_open.json()["detail"]
    rows = director_client.get(f"/api/records/requests/{reserved.pk}").json()["deadlines"]
    assert [(d["status"], d["superseded_by_deadline_id"]) for d in rows] == [("waived", None)]


def test_superseding_a_deadline_that_does_not_exist_is_404(director_client, reserved):
    resp = _open(director_client, reserved.pk, "2026-05-01", supersedes=str(uuid.uuid4()))
    assert resp.status_code == 404
    assert director_client.get(f"/api/records/requests/{reserved.pk}").json()["deadlines"] == []


def test_the_guard_is_only_on_the_date_not_on_every_edit(staff_client, director_client, reserved):
    """A non-director may still edit the OTHER deadline fields and every other entity, and may
    open, waive and satisfy deadlines: only moving an existing date takes a director."""
    deadline = _open(staff_client, reserved.pk, "2026-04-01").json()
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/edits",
        {
            "entity_type": "deadlines",
            "entity_id": deadline["id"],
            "field": "legal_basis",
            "new_value": "as quoted in the letter",
            "note": "added the basis",
        },
    )
    assert resp.status_code == 200, resp.content
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/edits",
        {
            "entity_type": "requests",
            "entity_id": str(reserved.pk),
            "field": "subject",
            "new_value": "Renamed",
            "note": "typo",
        },
    )
    assert resp.status_code == 200, resp.content


def test_the_role_not_a_mark_is_what_the_client_reads_on_me(director_client, staff_client):
    assert director_client.get("/api/core/auth/me").json()["records_role"] == "admin"
    assert "is_director" not in staff_client.get("/api/core/auth/me").json()


# ---- agencies ----------------------------------------------------------------------------------


def test_an_agency_round_trips_its_address_and_filing_fields(staff_client):
    created = post_json(
        staff_client,
        "/api/records/agencies",
        {
            "name": "Addressed Agency",
            "jurisdiction": "Testland",
            "records_law": "TESTLAW",
            "short_name": "ADDR",
            "recipient_office": "Records Unit",
            "mailing_street": "1 Example Way",
            "mailing_city_state_zip": "Testville, TL 00000",
            "filing_method": "portal",
            "portal_kind": "NextRequest",
        },
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert (body["short_name"], body["filing_method"], body["portal_kind"]) == (
        "ADDR",
        "portal",
        "NextRequest",
    )
    assert body["request_email_verified_at"] is None
    resp = patch_json(
        staff_client,
        f"/api/records/agencies/{body['id']}",
        {"note": "moved to email", "filing_method": "email", "portal_kind": None},
    )
    assert resp.status_code == 200, resp.content
    assert (resp.json()["filing_method"], resp.json()["portal_kind"]) == ("email", None)


def test_a_bad_filing_method_is_422(staff_client, agency):
    resp = patch_json(
        staff_client,
        f"/api/records/agencies/{agency.pk}",
        {"note": "typo", "filing_method": "fax"},
    )
    assert resp.status_code == 422
    resp = post_json(
        staff_client,
        "/api/records/agencies",
        {
            "name": "Bad",
            "jurisdiction": "Testland",
            "filing_method": "email",
            "portal_kind": "GovQA",
        },
    )
    assert resp.status_code == 422


def test_a_contact_can_be_created_as_the_primary_one(staff_client, agency):
    first = post_json(
        staff_client,
        f"/api/records/agencies/{agency.pk}/contacts",
        {"name": "First", "is_primary": True},
    )
    second = post_json(
        staff_client,
        f"/api/records/agencies/{agency.pk}/contacts",
        {"name": "Second", "is_primary": True},
    )
    assert first.status_code == second.status_code == 201
    rows = staff_client.get(f"/api/records/agencies/{agency.pk}/contacts").json()
    assert {c["name"]: c["is_primary"] for c in rows} == {"First": False, "Second": True}


def test_the_agency_page_returns_requests_and_a_computed_response_record(
    staff_client, reserved, agency
):
    resp = staff_client.get(f"/api/records/agencies/{agency.pk}")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["id"] == str(agency.pk) and body["name"] == agency.name
    assert [r["id"] for r in body["requests"]] == [str(reserved.pk)]
    assert body["requests"][0]["agency_name"] == agency.name
    record = body["response_record"]
    assert set(record) == {
        "median_days_to_determination",
        "determinations_on_time",
        "determinations_total",
        "extensions_taken",
    }
    assert record["extensions_taken"] == 0
    assert staff_client.get(f"/api/records/agencies/{uuid.uuid4()}").status_code == 404


def test_the_agency_page_is_staff_only(requester_client, agency):
    assert requester_client.get(f"/api/records/agencies/{agency.pk}").status_code == 403


# ---- fees --------------------------------------------------------------------------------------


def test_fees_carry_a_description_and_a_payment_method(staff_client, reserved):
    created = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/fees",
        {"status": "assessed", "amount_cents": 4250, "description": "Duplication, 425 pages"},
    )
    assert created.status_code == 201, created.content
    fee_id = created.json()["id"]
    assert created.json()["description"] == "Duplication, 425 pages"
    resp = patch_json(
        staff_client,
        f"/api/records/fees/{fee_id}",
        {"status": "paid", "note": "check mailed", "payment_method": "Check #1041"},
    )
    assert resp.status_code == 200, resp.content
    assert (resp.json()["description"], resp.json()["payment_method"]) == (
        "Duplication, 425 pages",
        "Check #1041",
    )
    resp = patch_json(
        staff_client,
        f"/api/records/fees/{fee_id}",
        {"status": "paid", "note": "cleared", "payment_method": None},
    )
    assert resp.json()["payment_method"] is None and resp.json()["description"] is not None


def test_a_fee_patch_writes_only_the_fields_it_sent(staff_client, reserved):
    """F3: a method-only PATCH of a paid fee erased `paid_at`, and a description-only PATCH of a
    waived one erased `waiver_reason`: half the schema had PATCH semantics and half had PUT."""
    paid = post_json(
        staff_client, f"/api/records/requests/{reserved.pk}/fees", {"status": "assessed"}
    ).json()
    resp = patch_json(
        staff_client,
        f"/api/records/fees/{paid['id']}",
        {"status": "paid", "paid_at": "2026-09-20T17:00:00Z", "note": "cheque cleared"},
    )
    assert resp.status_code == 200 and resp.json()["paid_at"] is not None
    paid_at = resp.json()["paid_at"]

    resp = patch_json(
        staff_client,
        f"/api/records/fees/{paid['id']}",
        {"status": "paid", "payment_method": "Check #1041", "note": "method"},
    )
    assert resp.status_code == 200, resp.content
    assert (resp.json()["paid_at"], resp.json()["payment_method"]) == (paid_at, "Check #1041")

    waived = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/fees",
        {"status": "waived", "waiver_reason": "public interest"},
    ).json()
    resp = patch_json(
        staff_client,
        f"/api/records/fees/{waived['id']}",
        {"status": "waived", "description": "Duplication", "note": "described"},
    )
    assert resp.status_code == 200, resp.content
    assert (resp.json()["waiver_reason"], resp.json()["description"]) == (
        "public interest",
        "Duplication",
    )


def test_a_fee_patch_with_an_explicit_null_clears_the_field(staff_client, reserved):
    fee = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/fees",
        {"status": "waived", "waiver_reason": "public interest"},
    ).json()
    patch_json(
        staff_client,
        f"/api/records/fees/{fee['id']}",
        {"status": "paid", "paid_at": "2026-09-20T17:00:00Z", "note": "paid after all"},
    )

    resp = patch_json(
        staff_client,
        f"/api/records/fees/{fee['id']}",
        {"status": "assessed", "paid_at": None, "waiver_reason": None, "note": "reversed"},
    )

    assert resp.status_code == 200, resp.content
    assert (resp.json()["paid_at"], resp.json()["waiver_reason"]) == (None, None)


# ---- definitions -------------------------------------------------------------------------------


def test_definitions_serve_the_new_vocabularies(staff_client):
    body = staff_client.get("/api/records/definitions").json()
    kinds = {k["code"]: k for k in body["deadline_kinds"]}
    assert list(kinds) == [
        "statutory_determination",
        "production",
        "fee_response",
        "challenge_response",  # P14
        "other",
    ]
    assert kinds["production"]["overdue_status"] == "production_overdue"
    assert kinds["fee_response"]["overdue_status"] is None
    types = {t["code"]: t["category"] for t in body["task_types"]}
    assert types["manual"] == "person" and types["decide_fee"] == "follow_ups"
    assert "mail_letter" in types and "print_and_mail" not in types
    assert body["task_categories"] == ["delivery_and_filing", "deadlines", "follow_ups", "person"]
    assert body["flag_reasons"] == [
        "delivery_failed",
        "no_matching_request",
        "may_cover_others",
        "reply_to_ignored",  # P20d
        "attachment_too_large",  # P20d
        "assistant",
        "other",
    ]
    assert body["resolutions"] == ["duplicate", "automated_reply", "suspicious", "other"]
    assert {s["code"] for s in body["statuses"]} >= {
        "clarification_requested",
        "fee_estimate_received",
    }
    assert {e["code"] for e in body["event_types"]} >= {"fee_payment", "narrowing"}


def test_an_event_can_be_logged_on_the_in_person_channel(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/events",
        {
            "event_type": "correspondence",
            "channel": "in_person",
            "direction": "outbound",
            "occurred_at": "2026-03-05T12:00:00Z",
            "display_body": "Met the records officer at the counter.",
        },
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["channel"] == "in_person"
