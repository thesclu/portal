"""Privileged requests through HTTP: the things the census (test_p9_census.py) does not say.

The census proves every route refuses or omits. This file pins the particular behaviours: what a
refusal looks like to whom, the administrator's one exception, that hidden rows move no count,
the message rule's edges, that work cannot be handed to someone who cannot open it, and the two
edits that change who may see a request.
"""

import uuid
from datetime import date

import pytest
from django.test import Client
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.api.permissions import PRIVILEGED_DETAIL
from portal.records.models import AuditLog, RequestAccess, Task
from portal.records.services import access as access_service
from portal.records.services import deadlines as deadlines_service
from portal.records.services import events as events_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services import tasks as tasks_service
from portal.records.services.access import Viewer

from tests.api.conftest import patch_json, post_json
from tests.p8b_helpers import attach, make_source
from tests.p20h_helpers import give, take

API = "/api/records"


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def owner(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def sealed(db, owner, agency, requester_user):
    """Privileged, reserved by staff_user (who is therefore on its list), for requester_user."""
    return requests_service.reserve_request(
        owner,
        agency_id=agency.pk,
        requester_user_id=requester_user.pk,
        subject="SEALED-SUBJECT",
        request_text="SEALED-TEXT",
        items=[requests_service.NewItem("1", "SEALED-ITEM")],
        sensitivity="privileged",
    )


@pytest.fixture
def outsider(colleague_user) -> Client:
    return _client(colleague_user)


@pytest.fixture
def insider(staff_user) -> Client:
    return _client(staff_user)


# ---- what a refusal says, and to whom ---------------------------------------------------------


def test_a_direct_link_tells_staff_the_tracking_number_and_nothing_else(sealed, outsider) -> None:
    resp = outsider.get(f"{API}/requests/{sealed.pk}")
    assert resp.status_code == 403
    assert resp.json() == {
        "detail": PRIVILEGED_DETAIL,
        "code": "privileged",
        "tracking_number": sealed.tracking_number,
    }
    assert "SEALED" not in resp.content.decode()


def test_staff_still_get_404_for_an_id_that_names_nothing(outsider, db) -> None:
    assert outsider.get(f"{API}/requests/{uuid.uuid4()}").status_code == 404


def test_a_requester_cannot_tell_a_privileged_request_from_a_random_id(
    sealed, other_requester_user
) -> None:
    """P3's F3, kept: the privileged shape would confirm to a requester that the id is real."""
    client = _client(other_requester_user)
    random_resp = client.get(f"{API}/requests/{uuid.uuid4()}")
    real_resp = client.get(f"{API}/requests/{sealed.pk}")
    assert random_resp.status_code == real_resp.status_code == 403
    assert random_resp.json() == real_resp.json()
    assert "code" not in real_resp.json()


def test_the_requester_of_record_reads_their_privileged_request(sealed, requester_user) -> None:
    client = _client(requester_user)
    assert client.get(f"{API}/requests/{sealed.pk}").json()["subject"] == "SEALED-SUBJECT"
    ids = {row["id"] for row in client.get(f"{API}/requests").json()["items"]}
    assert ids == {str(sealed.pk)}


def test_an_administrator_sees_a_privileged_request_and_a_staff_attorney_does_not(
    sealed, admin_client
) -> None:
    """P20h (37a): privileged requests stay grant-only except for admins. A staff attorney sees
    every ordinary request and, like staff, a privileged one only when given it."""
    assert admin_client.get(f"{API}/requests/{sealed.pk}").status_code == 200
    assert admin_client.get(f"{API}/requests").json()["total"] == 1
    attorney = create_user(
        email="attorney@example.org",
        name="Attorney Person",
        records_role="staff_attorney",
    )
    refused = _client(attorney).get(f"{API}/requests/{sealed.pk}")
    assert refused.status_code == 403 and refused.json()["code"] == "privileged"


# ---- the access doors -------------------------------------------------------------------------


def test_reserving_a_privileged_request_through_the_api_lists_the_person_who_did(
    insider, staff_user, agency, requester_user
) -> None:
    made = post_json(
        insider,
        f"{API}/requests",
        {
            "agency_id": agency.pk,
            "requester_user_id": requester_user.pk,
            "subject": "Sealed from the start",
            "request_text": "Body.",
            "sensitivity": "privileged",
        },
    )
    assert made.status_code == 201, made.content
    listed = insider.get(f"{API}/requests/{made.json()['id']}/access").json()
    assert [(row["user_id"], row["revoked_at"]) for row in listed] == [(str(staff_user.pk), None)]
    assert listed[0]["user_name"] == staff_user.name


def test_sharing_and_removing_through_the_api(
    sealed, admin_client, insider, outsider, colleague_user
) -> None:
    """P20h: an administrator gives and takes access; someone on the list cannot."""
    refused = post_json(
        insider, f"{API}/requests/{sealed.pk}/access", {"user_id": colleague_user.pk}
    )
    assert refused.status_code == 403 and refused.json()["detail"] == "Administrators only."
    shared = post_json(
        admin_client,
        f"{API}/requests/{sealed.pk}/access",
        {"user_id": colleague_user.pk, "note": "Second chair."},
    )
    assert shared.status_code == 201, shared.content
    assert shared.json()["note"] == "Second chair."
    assert outsider.get(f"{API}/requests/{sealed.pk}").status_code == 200
    # Everyone who may see the request reads its list (the request page shows it, 231).
    assert len(insider.get(f"{API}/requests/{sealed.pk}/access").json()) == 2

    again = post_json(
        admin_client, f"{API}/requests/{sealed.pk}/access", {"user_id": colleague_user.pk}
    )
    assert again.status_code == 409

    assert insider.post(f"{API}/access/{shared.json()['id']}/revoke").status_code == 403
    removed = admin_client.post(f"{API}/access/{shared.json()['id']}/revoke")
    assert removed.status_code == 200 and removed.json()["revoked_at"] is not None
    assert outsider.get(f"{API}/requests/{sealed.pk}").status_code == 403


def test_the_last_person_cannot_be_removed_through_the_api(sealed, insider, admin_client) -> None:
    (row,) = insider.get(f"{API}/requests/{sealed.pk}/access").json()
    resp = admin_client.post(f"{API}/access/{row['id']}/revoke")
    assert resp.status_code == 409 and "last person" in resp.json()["detail"]


def test_an_administrator_may_add_themselves_and_it_leaves_a_row(
    sealed, admin_client, admin_user
) -> None:
    mine = post_json(
        admin_client,
        f"{API}/requests/{sealed.pk}/access",
        {"user_id": admin_user.pk, "note": "Covering."},
    )
    assert mine.status_code == 201, mine.content
    row = RequestAccess.objects.get(pk=mine.json()["id"])
    assert (row.user_id, row.granted_by_id, row.note) == (admin_user.pk, admin_user.pk, "Covering.")


def test_staff_who_are_not_administrators_cannot_add_themselves(
    sealed, outsider, colleague_user
) -> None:
    resp = post_json(outsider, f"{API}/requests/{sealed.pk}/access", {"user_id": colleague_user.pk})
    assert resp.status_code == 403
    assert not RequestAccess.objects.filter(user_id=colleague_user.pk).exists()


def test_an_ordinary_request_is_given_to_a_limited_attorney_through_the_api(
    reserved, admin_client, insider
) -> None:
    """P20h: a grant on an ordinary request is how a limited-scope attorney sees it (230)."""
    counsel = create_user(
        email="counsel@example.org",
        name="Counsel Person",
        records_role="limited_attorney",
    )
    as_counsel = _client(counsel)
    assert as_counsel.get(f"{API}/requests/{reserved.pk}").status_code == 403
    given = post_json(admin_client, f"{API}/requests/{reserved.pk}/access", {"user_id": counsel.pk})
    assert given.status_code == 201, given.content
    assert as_counsel.get(f"{API}/requests/{reserved.pk}").status_code == 200
    listed = insider.get(f"{API}/requests/{reserved.pk}/access").json()
    assert [row["user_id"] for row in listed] == [str(counsel.pk)]


# ---- the two edits that change who may see a request -------------------------------------------


def _edit(client, request, entity_type, entity_id, field, value):
    return post_json(
        client,
        f"{API}/requests/{request.pk}/edits",
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "field": field,
            "new_value": value,
            "note": "Because.",
        },
    )


def test_making_a_request_privileged_through_the_api_keeps_the_editor_in(
    reserved, insider, outsider, admin_client, colleague_user
) -> None:
    resp = _edit(outsider, reserved, "requests", reserved.pk, "sensitivity", "privileged")
    assert resp.status_code == 200, resp.content
    assert outsider.get(f"{API}/requests/{reserved.pk}").status_code == 200
    # P20h: an administrator still sees it, with no grant.
    assert admin_client.get(f"{API}/requests/{reserved.pk}").status_code == 200
    # `reserved` was filed on behalf of staff_user, who therefore still sees it without a grant.
    assert insider.get(f"{API}/requests/{reserved.pk}").status_code == 200
    listed = outsider.get(f"{API}/requests/{reserved.pk}/access").json()
    assert [row["user_id"] for row in listed] == [str(colleague_user.pk)]


def test_unsealing_through_the_api_opens_it_and_empties_the_list(sealed, insider, outsider) -> None:
    assert (
        _edit(insider, sealed, "requests", sealed.pk, "sensitivity", "internal").status_code == 200
    )
    assert outsider.get(f"{API}/requests/{sealed.pk}").status_code == 200
    assert outsider.get(f"{API}/requests/{sealed.pk}/access").json() == []


def test_an_edit_must_name_a_row_of_the_request_in_the_path(
    sealed, reserved, insider, outsider, owner
) -> None:
    """The path was a decoration before: any visible request opened every other request's rows."""
    hidden_event = events_service.record_event(
        owner,
        sealed.pk,
        event_type="note",
        channel="system",
        direction="internal",
        occurred_at=sealed.created_at,
        display_body="SEALED-EVENT",
    )
    through_another = _edit(outsider, reserved, "events", hidden_event.pk, "display_body", "mine")
    assert through_another.status_code == 422
    assert "does not belong" in through_another.json()["detail"]
    hidden_event.refresh_from_db()
    assert hidden_event.display_body == "SEALED-EVENT"
    # The same mismatch is refused for someone who can see both: it is a rule about the path.
    assert (
        _edit(insider, reserved, "events", hidden_event.pk, "display_body", "x").status_code == 422
    )
    assert (
        _edit(insider, sealed, "events", hidden_event.pk, "display_body", "ok").status_code == 200
    )
    assert _edit(insider, sealed, "events", uuid.uuid4(), "display_body", "x").status_code == 404
    assert _edit(insider, sealed, "pigeons", uuid.uuid4(), "display_body", "x").status_code == 422


# ---- a hidden request moves no number ---------------------------------------------------------


def _numbers(client, agency) -> dict:
    """Every count and total an outsider is shown, anywhere."""
    docket = client.get(f"{API}/requests").json()
    inbox = client.get(f"{API}/inbox?bucket=all").json()
    home = client.get(f"{API}/home").json()
    calendar = client.get(f"{API}/calendar?start=2019-12-01&end=2020-03-01").json()
    page = client.get(f"{API}/agencies/{agency.pk}").json()
    return {
        "docket_total": docket["total"],
        "docket_rows": len(docket["items"]),
        "privileged_total": client.get(f"{API}/requests?sensitivity=privileged").json()["total"],
        "inbox_counts": inbox["counts"],
        "inbox_total": inbox["total"],
        "sources_total": client.get(f"{API}/sources").json()["total"],
        "flagged_total": client.get(f"{API}/sources?flagged=true").json()["total"],
        "tasks_open": len(client.get(f"{API}/tasks").json()),
        "tasks_all": len(client.get(f"{API}/tasks?state=all").json()),
        "home_overdue": len(home["overdue"]),
        "home_due_soon": len(home["due_soon"]),
        "home_tasks": len(home["assigned_tasks"]),
        "home_flagged": home["flagged"]["counts"],
        "calendar": len(calendar["deadlines"]),
        "rules_missing": len(client.get(f"{API}/rules").json()["missing"]),
        "agency_requests": len(page["requests"]),
        "agency_record": page["response_record"],
    }


def _fill(owner, request, assignee) -> None:
    """One of everything that is counted somewhere, on `request`."""
    attach(owner, request, make_source(owner, flag=("other", "look at this")))
    attach(owner, request, make_source(owner))
    deadlines_service.open_deadline(
        owner,
        request.pk,
        deadline_type="production",
        due_at=date(2020, 1, 6),
        basis="agency_commitment",
    )
    tasks_service.create_task(owner, request.pk, "manual", "do it", assigned_to_user_id=assignee.pk)
    tasks_service.create_task(owner, request.pk, "enter_deadline_rules", "a rule is missing")
    # Counted by the agency page's response record ("extensions taken").
    events_service.record_event(
        owner,
        request.pk,
        event_type="extension_notice",
        channel="email",
        direction="inbound",
        occurred_at=request.created_at,
        display_body="We need more time.",
    )


def test_no_count_moves_when_a_privileged_request_the_viewer_cannot_see_is_added(
    owner, agency, staff_user, colleague_user, requester_user, outsider, insider, reserved
) -> None:
    _fill(owner, reserved, colleague_user)
    before = _numbers(outsider, agency)
    seen_before = _numbers(insider, agency)
    assert before["docket_total"] == 1 and before["inbox_total"] == 2 and before["tasks_open"] == 2

    hidden = requests_service.reserve_request(
        owner,
        agency_id=agency.pk,
        requester_user_id=requester_user.pk,
        subject="SEALED",
        request_text="SEALED",
        sensitivity="privileged",
    )
    _fill(owner, hidden, staff_user)

    assert _numbers(outsider, agency) == before
    # And it is not that nothing was added: the person on the list sees every number move.
    seen_after = _numbers(insider, agency)
    for key in ("docket_total", "inbox_total", "sources_total", "tasks_open", "calendar"):
        assert seen_after[key] > seen_before[key], key
    assert seen_after["privileged_total"] == 1 and seen_after["rules_missing"] == 2
    assert seen_after["inbox_counts"] != seen_before["inbox_counts"]
    assert before["agency_record"]["extensions_taken"] == 1
    assert seen_after["agency_record"]["extensions_taken"] == 2


# ---- messages borrow their visibility from where they landed -----------------------------------


def _sees(user, source) -> bool:
    return access_service.can_view_source(Viewer(user.pk, "staff"), source.pk)


def test_unattached_mail_is_triage_and_every_staff_member_sees_it(
    owner, outsider, colleague_user
) -> None:
    loose = make_source(owner, flag=("no_matching_request", "whose is this?"))
    assert _sees(colleague_user, loose)
    assert outsider.get(f"{API}/sources/{loose.pk}").status_code == 200
    assert outsider.get(f"{API}/inbox?bucket=flagged").json()["total"] == 1


def test_a_message_on_a_sealed_request_is_hidden_and_filing_it_there_hides_it(
    sealed, owner, insider, outsider, colleague_user
) -> None:
    loose = make_source(owner)
    assert outsider.get(f"{API}/sources/{loose.pk}").status_code == 200
    attach(owner, sealed, loose, body="SEALED-BODY")
    refused = outsider.get(f"{API}/sources/{loose.pk}")
    assert refused.status_code == 403
    assert refused.json() == {
        "detail": PRIVILEGED_DETAIL,
        "code": "privileged",
        "tracking_number": None,
    }
    assert insider.get(f"{API}/sources/{loose.pk}").status_code == 200
    assert not _sees(colleague_user, loose)


def test_a_message_on_two_requests_shows_only_the_one_the_viewer_may_see(
    sealed, reserved, owner, insider, outsider
) -> None:
    shared = make_source(owner)
    attach(owner, sealed, shared, body="SEALED-BODY")
    attach(owner, reserved, shared, body="ordinary body")

    def row(client, query=""):
        page = client.get(f"{API}/inbox?bucket=all{query}").json()
        return page["items"][0] if page["items"] else None

    theirs = row(outsider)
    assert [e["request_id"] for e in theirs["events"]] == [str(reserved.pk)]
    assert sealed.tracking_number not in str(theirs) and "SEALED" not in str(theirs)
    assert {e["request_id"] for e in row(insider)["events"]} == {str(sealed.pk), str(reserved.pk)}

    # Neither a filter nor a search reaches through the hidden request to find the message.
    assert row(outsider, f"&request_id={sealed.pk}") is None
    assert row(outsider, f"&q={sealed.tracking_number}") is None
    assert row(outsider, "&q=SEALED-BODY") is None
    assert row(outsider, f"&q={reserved.tracking_number}") is not None
    assert row(insider, f"&request_id={sealed.pk}") is not None
    assert row(insider, "&q=SEALED-BODY") is not None


def test_voiding_the_event_does_not_hand_a_sealed_message_to_everyone(
    sealed, reserved, owner, insider, outsider, colleague_user
) -> None:
    message = make_source(owner)
    event = attach(owner, sealed, message, body="SEALED-BODY")
    events_service.void_event(owner, event.pk, "wrong request")
    assert not _sees(colleague_user, message)
    assert outsider.get(f"{API}/sources/{message.pk}").status_code == 403
    assert outsider.get(f"{API}/inbox?bucket=all").json()["total"] == 0
    assert insider.get(f"{API}/sources/{message.pk}").status_code == 200
    # Placed on a request everyone sees, it is everyone's again.
    attach(owner, reserved, message)
    assert _sees(colleague_user, message)


def test_a_message_voided_off_an_ordinary_request_and_live_on_a_sealed_one_is_hidden(
    sealed, reserved, owner, colleague_user
) -> None:
    """The voided event must not vouch for it: only where it IS decides, while it is anywhere."""
    message = make_source(owner)
    wrong = attach(owner, reserved, message)
    events_service.void_event(owner, wrong.pk, "belongs to the sealed one")
    attach(owner, sealed, message, body="SEALED-BODY")
    assert not _sees(colleague_user, message)


def test_files_and_notes_of_a_hidden_message_are_refused(sealed, owner, outsider, media) -> None:
    added = sources_service.add_document(
        owner,
        sealed.pk,
        source_type="scanned_mail",
        received_at=sealed.created_at,
        files=[sources_service.IncomingFile("sealed.pdf", [b"SEALED-BYTES"])],
    )
    note = sources_service.add_source_note(owner, added.source.pk, "SEALED-NOTE")
    for resp in (
        outsider.get(f"{API}/attachments/{added.attachments[0].pk}/download"),
        outsider.get(f"{API}/sources/{added.source.pk}/notes"),
        outsider.get(f"{API}/sources/{added.source.pk}/raw"),
        post_json(outsider, f"{API}/source-notes/{note.pk}/void", {"reason": "x"}),
    ):
        assert resp.status_code == 403 and resp.json()["code"] == "privileged"
        assert b"SEALED" not in resp.content


# ---- assigning is not sharing -------------------------------------------------------------------


def test_work_on_a_sealed_request_goes_only_to_someone_on_its_list(
    sealed, owner, insider, colleague_user, staff_user
) -> None:
    task = tasks_service.create_task(owner, sealed.pk, "manual", "SEALED-TASK")
    message = make_source(owner)
    attach(owner, sealed, message)
    target = {"user_id": colleague_user.pk}
    attempts = [
        post_json(insider, f"{API}/requests/{sealed.pk}/assign", target),
        post_json(insider, f"{API}/tasks/{task.pk}/assign", target),
        post_json(insider, f"{API}/sources/{message.pk}/assignee", target),
        post_json(
            insider,
            f"{API}/tasks",
            {
                "request_id": sealed.pk,
                "task_type": "manual",
                "description": "another",
                "assigned_to_user_id": colleague_user.pk,
            },
        ),
    ]
    for resp in attempts:
        assert resp.status_code == 422, resp.content
        detail = resp.json()["detail"]
        assert "gives it to them first" in detail and colleague_user.name in detail
    sealed.refresh_from_db()
    task.refresh_from_db()
    assert sealed.assigned_to_id is None and task.assigned_to_id is None
    assert not RequestAccess.objects.filter(user_id=colleague_user.pk).exists()

    # Given first, every one of them goes through.
    give(sealed.pk, colleague_user.pk)
    assert post_json(insider, f"{API}/requests/{sealed.pk}/assign", target).status_code == 200
    assert post_json(insider, f"{API}/tasks/{task.pk}/assign", target).status_code == 200
    assert post_json(insider, f"{API}/sources/{message.pk}/assignee", target).status_code == 200
    # Unassigning and assigning to oneself never needed it.
    assert post_json(insider, f"{API}/tasks/{task.pk}/assign", {"user_id": None}).status_code == 200
    mine = post_json(insider, f"{API}/tasks/{task.pk}/assign", {"user_id": staff_user.pk})
    assert mine.status_code == 200


def test_assigning_on_an_ordinary_request_is_as_it_was(
    reserved, owner, insider, colleague_user, requester_user
) -> None:
    ok = post_json(insider, f"{API}/requests/{reserved.pk}/assign", {"user_id": colleague_user.pk})
    assert ok.status_code == 200
    # A requester is still refused by the service, in the service's own words.
    no = post_json(insider, f"{API}/requests/{reserved.pk}/assign", {"user_id": requester_user.pk})
    assert no.status_code == 422 and "requester" in no.json()["detail"]


def test_a_task_assigned_before_access_was_removed_drops_off_their_list(
    sealed, owner, colleague_user, outsider
) -> None:
    row = give(sealed.pk, colleague_user.pk)
    tasks_service.create_task(
        owner, sealed.pk, "manual", "SEALED-TASK", assigned_to_user_id=colleague_user.pk
    )
    assert len(outsider.get(f"{API}/tasks?assignee=me").json()) == 1
    assert len(outsider.get(f"{API}/home").json()["assigned_tasks"]) == 1
    take(row.pk)
    assert outsider.get(f"{API}/tasks?assignee=me").json() == []
    assert outsider.get(f"{API}/home").json()["assigned_tasks"] == []


def test_a_body_that_names_a_request_nobody_has_is_refused_as_it_always_was(insider, db) -> None:
    """HANDOFF convention 9: a dangling request id in a body is the database's to refuse (422),
    and the visibility check must not turn it into something else."""
    resp = post_json(
        insider,
        f"{API}/tasks",
        {"request_id": uuid.uuid4(), "task_type": "manual", "description": "x"},
    )
    assert resp.status_code == 422


def test_a_deactivated_person_and_a_requester_cannot_be_given_access(
    sealed, admin_client, requester_user
) -> None:
    gone = create_user(email="gone@example.org", name="Gone")
    type(gone).objects.filter(pk=gone.pk).update(is_active=False)
    for user in (gone, requester_user):
        resp = post_json(admin_client, f"{API}/requests/{sealed.pk}/access", {"user_id": user.pk})
        assert resp.status_code == 422, resp.content


# ---- found by the second-pass review (notes/portal/2026-10-05_p9_review_reviewer.md) ------------


def test_a_message_that_passed_through_an_ordinary_request_stays_sealed_when_its_event_is_voided(
    sealed, reserved, owner, insider, outsider, colleague_user, media
) -> None:
    """F2. Landed on an ordinary request by mistake, moved to the sealed one, noted there, then the
    sealed event voided. The old voided event on the ordinary request must not vouch for it."""
    added = sources_service.add_document(
        owner,
        reserved.pk,
        source_type="scanned_mail",
        received_at=reserved.created_at,
        files=[sources_service.IncomingFile("SEALED-letter.pdf", [b"SEALED-BYTES"])],
    )
    message, wrong = added.source, added.event
    moved = post_json(
        insider,
        f"{API}/sources/{message.pk}/assign",
        {"request_id": sealed.pk, "mode": "belongs_here_instead", "from_event_id": wrong.pk},
    )
    assert moved.status_code == 201, moved.content
    sources_service.add_source_note(owner, message.pk, "SEALED-NOTE written while sealed")
    assert outsider.get(f"{API}/sources/{message.pk}").status_code == 403

    events_service.void_event(owner, moved.json()["id"], "not this one either")
    assert not _sees(colleague_user, message)
    for path in (
        f"{API}/sources/{message.pk}",
        f"{API}/sources/{message.pk}/notes",
        f"{API}/attachments/{added.attachments[0].pk}/download",
    ):
        resp = outsider.get(path)
        assert resp.status_code == 403 and b"SEALED" not in resp.content, path
    assert "SEALED" not in outsider.get(f"{API}/inbox?bucket=all").content.decode()
    assert insider.get(f"{API}/sources/{message.pk}").status_code == 200
    # Someone on the list places it where everyone may see it, and it is everyone's again.
    attach(owner, reserved, message)
    assert outsider.get(f"{API}/sources/{message.pk}").status_code == 200


def test_moving_a_message_to_a_sealed_request_does_not_name_it_on_the_request_it_left(
    sealed, reserved, owner, insider, outsider
) -> None:
    """F7. The void reason is read by everyone who sees the ordinary request."""
    message = make_source(owner)
    wrong = attach(owner, reserved, message)
    moved = post_json(
        insider,
        f"{API}/sources/{message.pk}/assign",
        {"request_id": sealed.pk, "mode": "belongs_here_instead", "from_event_id": wrong.pk},
    )
    assert moved.status_code == 201, moved.content
    page = outsider.get(f"{API}/requests/{reserved.pk}").content.decode()
    assert "reassigned to another request" in page
    assert sealed.tracking_number not in page
    # Between two ordinary requests the reason still says where it went.
    other = requests_service.reserve_request(
        owner,
        agency_id=reserved.agency_id,
        requester_user_id=reserved.requester_id,
        subject="Another",
        request_text="Another.",
    )
    second = make_source(owner)
    first_event = attach(owner, reserved, second)
    post_json(
        insider,
        f"{API}/sources/{second.pk}/assign",
        {"request_id": other.pk, "mode": "belongs_here_instead", "from_event_id": first_event.pk},
    )
    assert other.tracking_number in outsider.get(f"{API}/requests/{reserved.pk}").content.decode()


def test_an_id_in_a_body_must_be_a_row_of_the_request_being_written_to(
    sealed, reserved, owner, outsider
) -> None:
    """F5. A deadline, a fee or an event on an ordinary request citing a sealed request's rows
    would carry that request's void reason and dates out to people off its list."""
    sealed_event = events_service.record_event(
        owner,
        sealed.pk,
        event_type="correspondence",
        channel="email",
        direction="inbound",
        occurred_at=sealed.created_at,
        display_body="SEALED-BODY",
    )
    sealed_deadline = deadlines_service.open_deadline(
        owner,
        sealed.pk,
        deadline_type="production",
        due_at=date(2031, 1, 6),
        basis="agency_commitment",
    )
    sealed_item = sealed.items.first()
    own_deadline = deadlines_service.open_deadline(
        owner,
        reserved.pk,
        deadline_type="production",
        due_at=date(2031, 1, 6),
        basis="agency_commitment",
    )
    new_deadline = {"deadline_type": "production", "due_at": "2031-02-03", "basis": "statutory"}
    event = {
        "event_type": "note",
        "channel": "system",
        "direction": "internal",
        "occurred_at": "2026-03-02T17:30:00Z",
        "display_body": "x",
    }
    base = f"{API}/requests/{reserved.pk}"
    attempts = {
        "a deadline citing a sealed event": post_json(
            outsider, f"{base}/deadlines", {**new_deadline, "source_event_id": sealed_event.pk}
        ),
        "a deadline on a sealed item": post_json(
            outsider, f"{base}/deadlines", {**new_deadline, "request_item_id": sealed_item.pk}
        ),
        "a fee citing a sealed event": post_json(
            outsider, f"{base}/fees", {"status": "assessed", "source_event_id": sealed_event.pk}
        ),
        "an event citing a sealed deadline": post_json(
            outsider, f"{base}/events", {**event, "related_deadline_id": sealed_deadline.pk}
        ),
        "satisfying with a sealed event": post_json(
            outsider,
            f"{API}/deadlines/{own_deadline.pk}/satisfy",
            {"satisfied_by_event_id": sealed_event.pk},
        ),
    }
    for what, resp in attempts.items():
        assert resp.status_code == 422, (what, resp.status_code, resp.content)
        assert "different request" in resp.json()["detail"], what
    own_deadline.refresh_from_db()
    assert own_deadline.status == "active"
    # A row of the same request is accepted, and an id that names nothing is still the database's.
    own_event = attach(owner, reserved, make_source(owner))
    ok = post_json(outsider, f"{base}/deadlines", {**new_deadline, "source_event_id": own_event.pk})
    assert ok.status_code == 201, ok.content
    nothing = post_json(
        outsider, f"{base}/deadlines", {**new_deadline, "source_event_id": uuid.uuid4()}
    )
    assert nothing.status_code == 422 and "different request" not in nothing.json()["detail"]


def test_sealing_takes_the_work_away_from_people_who_can_no_longer_open_it(
    reserved, owner, insider, outsider, colleague_user, admin_user
) -> None:
    """F8. Assigned to a colleague, then sealed by someone else: the colleague must not be left
    holding a request and a task they cannot open and that appear on none of their lists."""
    assert (
        post_json(
            insider, f"{API}/requests/{reserved.pk}/assign", {"user_id": colleague_user.pk}
        ).status_code
        == 200
    )
    theirs = tasks_service.create_task(
        owner, reserved.pk, "manual", "theirs", assigned_to_user_id=colleague_user.pk
    )
    done = tasks_service.create_task(
        owner, reserved.pk, "manual", "done", assigned_to_user_id=colleague_user.pk
    )
    tasks_service.complete_task(Actor.human(colleague_user), done.pk)
    sealer = _client(admin_user)
    assert (
        _edit(sealer, reserved, "requests", reserved.pk, "sensitivity", "privileged").status_code
        == 200
    )

    reserved.refresh_from_db()
    theirs.refresh_from_db()
    done.refresh_from_db()
    assert reserved.assigned_to_id is None and theirs.assigned_to_id is None
    assert done.assigned_to_id == colleague_user.pk  # completed work is history
    lines = AuditLog.objects.filter(
        entity_id__in=[reserved.pk, theirs.pk], edited_by_id=admin_user.pk
    )
    assert lines.filter(field_name__endswith="assigned_to_user_id").count() == 2
    assert all(
        "no longer has access" in line.note for line in lines if "assigned" in line.field_name
    )
    # The requester of record (staff_user here) still sees it and would have kept their work.
    assert insider.get(f"{API}/requests/{reserved.pk}").status_code == 200


def test_removing_someone_from_the_list_takes_their_work_on_it_away(
    sealed, owner, colleague_user, staff_user
) -> None:
    row = give(sealed.pk, colleague_user.pk)
    requests_service.assign_request(owner, sealed.pk, colleague_user.pk)
    theirs = tasks_service.create_task(
        owner, sealed.pk, "manual", "theirs", assigned_to_user_id=colleague_user.pk
    )
    mine = tasks_service.create_task(
        owner, sealed.pk, "manual", "mine", assigned_to_user_id=staff_user.pk
    )
    take(row.pk)
    sealed.refresh_from_db()
    assert sealed.assigned_to_id is None
    assert Task.objects.get(pk=theirs.pk).assigned_to_id is None
    assert Task.objects.get(pk=mine.pk).assigned_to_id == staff_user.pk


# ---- the Users screen reaches privileged requests too -------------------------------------------


def _deactivate(client, user, **extra):
    return patch_json(client, f"/api/core/users/{user.pk}", {"is_active": False, **extra})


def test_deactivating_someone_never_hands_sealed_work_to_a_person_who_cannot_open_it(
    sealed, reserved, owner, staff_user, colleague_user, admin_client, admin_user
) -> None:
    """F3. The person the work is handed to is not on the list. Work on the ordinary request is
    handed on; work on the sealed one is not."""
    second = _person_on_list(owner, sealed)
    sealed_task = tasks_service.create_task(
        owner, sealed.pk, "manual", "SEALED-TASK", assigned_to_user_id=staff_user.pk
    )
    plain_task = tasks_service.create_task(
        owner, reserved.pk, "manual", "plain", assigned_to_user_id=staff_user.pk
    )
    resp = _deactivate(admin_client, staff_user, reassign_tasks_to=colleague_user.pk)
    assert resp.status_code == 200, resp.content
    assert b"SEALED" not in resp.content
    assert Task.objects.get(pk=plain_task.pk).assigned_to_id == colleague_user.pk
    assert Task.objects.get(pk=sealed_task.pk).assigned_to_id is None
    # Someone else was on the list, so the administrator was not put on it (they see it anyway,
    # P20h).
    assert {r.user_id for r in access_service.active_access(sealed.pk)} == {second.pk}


def test_deactivating_someone_hands_sealed_work_to_a_person_who_can_open_it(
    sealed, owner, staff_user, admin_client
) -> None:
    second = _person_on_list(owner, sealed)
    sealed_task = tasks_service.create_task(
        owner, sealed.pk, "manual", "SEALED-TASK", assigned_to_user_id=staff_user.pk
    )
    assert _deactivate(admin_client, staff_user, reassign_tasks_to=second.pk).status_code == 200
    assert Task.objects.get(pk=sealed_task.pk).assigned_to_id == second.pk


def _person_on_list(owner, request):
    person = create_user(email="second@example.org", name="Second Chair")
    give(request.pk, person.pk)
    return person


def test_deactivating_the_last_person_on_a_list_passes_it_to_the_administrator_doing_it(
    sealed, staff_user, admin_client, admin_user
) -> None:
    """F4. Otherwise no list anywhere shows who has it. (Since P20h an administrator sees it
    anyway, so it is never visible to nobody; the row still says who took it over.)"""
    assert admin_client.get(f"{API}/requests").json()["total"] == 1
    assert _deactivate(admin_client, staff_user).status_code == 200

    rows = {r.user_id: r for r in RequestAccess.objects.filter(request_id=sealed.pk)}
    assert rows[staff_user.pk].revoked_at is not None
    assert rows[staff_user.pk].revoked_by_id == admin_user.pk
    taken = rows[admin_user.pk]
    assert taken.revoked_at is None and taken.granted_by_id == admin_user.pk
    assert "Took over" in taken.note and staff_user.name in taken.note
    assert admin_client.get(f"{API}/requests").json()["total"] == 1
    # Reactivating the person does not bring their old access back unseen.
    assert (
        patch_json(
            admin_client, f"/api/core/users/{staff_user.pk}", {"is_active": True}
        ).status_code
        == 200
    )
    assert {r.user_id for r in access_service.active_access(sealed.pk)} == {admin_user.pk}
    assert _client(staff_user).get(f"{API}/requests/{sealed.pk}").status_code == 403


def test_turning_the_last_person_on_a_list_into_a_requester_passes_it_on_the_same_way(
    sealed, staff_user, admin_client, admin_user
) -> None:
    resp = patch_json(admin_client, f"/api/core/users/{staff_user.pk}", {"role": "requester"})
    assert resp.status_code == 200, resp.content
    assert {r.user_id for r in access_service.active_access(sealed.pk)} == {admin_user.pk}
    # A change of role that keeps them on staff touches nothing.
    second = _person_on_list(Actor.human(admin_user), sealed)
    assert (
        patch_json(admin_client, f"/api/core/users/{second.pk}", {"role": "admin"}).status_code
        == 200
    )
    assert {r.user_id for r in access_service.active_access(sealed.pk)} == {
        admin_user.pk,
        second.pk,
    }


def test_an_administrator_with_no_staff_role_in_records_cannot_take_a_request_over(
    sealed, staff_user
) -> None:
    """The list is for people who can open the request. Deactivation is refused, with the reason,
    rather than leaving the request to nobody."""
    odd = create_user(
        email="odd-admin@example.org",
        name="Odd Admin",
        records_role="requester",
        is_staff=True,
    )
    resp = _deactivate(_client(odd), staff_user)
    assert resp.status_code == 422 and "only person with access" in resp.json()["detail"]
    staff_user.refresh_from_db()
    assert staff_user.is_active
    assert {r.user_id for r in access_service.active_access(sealed.pk)} == {staff_user.pk}


# ---- the administrators' index (DECISIONS, Still open 11) ----------------------------------------


def test_the_administrators_index_is_empty_now_that_they_see_every_privileged_request(
    sealed, reserved, admin_client, outsider, insider
) -> None:
    """owner: "as long as there is one account with ultra level access (mine)". Built in P9 as an
    index an administrator could add themselves from; since P20h (37a) an administrator simply
    sees every privileged request, so the index, kept for its route, lists nothing."""
    listed = admin_client.get(f"{API}/access/unlisted")
    assert listed.status_code == 200 and listed.json() == []
    assert admin_client.get(f"{API}/requests/{sealed.pk}").status_code == 200
    # Staff who are not administrators are refused, on the list of the request or not.
    assert outsider.get(f"{API}/access/unlisted").status_code == 403
    assert insider.get(f"{API}/access/unlisted").status_code == 403


# ---- P4b: the request page's files and notes, and the docket's sort ------------------------------


def test_a_requests_files_and_message_notes_are_listed_for_its_own_page(
    reserved, owner, insider, media
) -> None:
    added = sources_service.add_document(
        owner,
        reserved.pk,
        source_type="scanned_mail",
        received_at=reserved.created_at,
        files=[sources_service.IncomingFile("letter.pdf", [b"x"])],
    )
    sources_service.add_source_note(owner, added.source.pk, "Call them first.")
    files = insider.get(f"{API}/requests/{reserved.pk}/attachments").json()
    notes = insider.get(f"{API}/requests/{reserved.pk}/source-notes").json()
    assert [(f["filename"], f["source_id"]) for f in files] == [
        ("letter.pdf", str(added.source.pk))
    ]
    assert [n["body"] for n in notes] == ["Call them first."]
    assert insider.get(f"{API}/requests/{uuid.uuid4()}/attachments").status_code == 404


def test_the_docket_can_be_ordered_and_refuses_an_unknown_order(
    reserved, owner, agency, staff_user, insider
) -> None:
    newer = requests_service.reserve_request(
        owner,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Newer",
        request_text="Body.",
    )
    deadlines_service.open_deadline(
        owner,
        reserved.pk,
        deadline_type="production",
        due_at=date(2031, 1, 6),
        basis="agency_commitment",
    )

    def order(sort):
        return [r["id"] for r in insider.get(f"{API}/requests?sort={sort}").json()["items"]]

    assert order("newest") == [str(newer.pk), str(reserved.pk)]
    # The one with a deadline comes first; the one with none goes last, not first.
    assert order("deadline") == [str(reserved.pk), str(newer.pk)]
    assert insider.get(f"{API}/requests?sort=sideways").status_code == 422
