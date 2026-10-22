"""P23b, from the caller's side: what the P20 screens drew and the API could not feed.

The Inbox: one row read by its id with the buckets it is in (140, 107), a date range on the inbox
and mail reads (154), who moved a message to Flagged and when (140), who recorded what it means
and when (143). Tasks: notes, voidable and never deleted (177), and the message a task is about
("Retry send" opens it). Agencies: the file an agency was imported from (172). The request page:
the record as a PDF (069), a reason on "Move it" (075) and on "File again" (097), the letter's
sender (102), the refusal that names who filed it and when (094), and the deadlines filing would
open, computed without writing anything (083). Visibility on every one of these routes is the
census's (`test_p9_census.py`); a few privileged cases are pinned here too.
"""

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from portal.core.models import Activity
from portal.core.services.identity import create_user
from portal.mail.models import Message
from portal.records.actors import Actor
from portal.records.models import Agency, Deadline, Event, Request, StatusChange, Task, TaskNote
from portal.records.services import common, directory
from portal.records.services import requests as requests_service
from portal.records.services import tasks as tasks_service

from tests.api.conftest import post_json
from tests.p8b_helpers import attach, make_source

BASE = "/api/records"
T0 = datetime(2026, 3, 2, 17, 0, tzinfo=UTC)  # 09:00 on Mar 2 in Los Angeles
RULES = Path(__file__).resolve().parent.parent / "services" / "fixtures"


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def actor(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def rules():
    with common.using_rule_data(
        rules_path=RULES / "rules_test.toml", calendars_path=RULES / "calendars_test.toml"
    ) as data:
        yield data


def _reserve(actor, agency, who, subject="Synthetic request", sensitivity="internal", items=()):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=who.pk,
        subject=subject,
        request_text="Synthetic.",
        sensitivity=sensitivity,
        items=[requests_service.NewItem(str(n), text) for n, text in items],
    )


# ---- the Inbox ---------------------------------------------------------------------------------


def test_one_row_by_id_with_the_buckets_it_is_in(staff_user, actor, agency) -> None:
    request = _reserve(actor, agency, staff_user)
    source = make_source(actor)
    attach(actor, request, source)
    client = _client(staff_user)
    body = client.get(f"{BASE}/inbox/{source.pk}").json()
    assert body["source"]["id"] == str(source.pk)
    assert body["buckets"] == ["new", "all"]
    assert body["events"][0]["tracking_number"] == request.tracking_number
    # Flagged while it was open: it left New, and the row says where it is now.
    post_json(client, f"{BASE}/sources/{source.pk}/flag", {"note": "Look", "reason": "other"})
    assert client.get(f"{BASE}/inbox/{source.pk}").json()["buckets"] == ["flagged", "all"]


def test_one_row_by_id_reads_a_message_past_the_loaded_page(staff_user, actor, agency) -> None:
    request = _reserve(actor, agency, staff_user)
    oldest = make_source(actor, captured_at=T0)
    attach(actor, request, oldest)
    for hours in range(1, 4):
        attach(actor, request, make_source(actor, captured_at=T0 + timedelta(hours=hours)))
    client = _client(staff_user)
    page = client.get(f"{BASE}/inbox", {"bucket": "new", "limit": 2}).json()
    assert str(oldest.pk) not in [row["source"]["id"] for row in page["items"]]
    assert client.get(f"{BASE}/inbox/{oldest.pk}").json()["buckets"] == ["new", "all"]


def test_one_row_by_id_of_a_privileged_message_is_refused(
    staff_user, colleague_user, actor, agency
) -> None:
    sealed = _reserve(actor, agency, staff_user, sensitivity="privileged")
    source = make_source(actor)
    attach(actor, sealed, source)
    response = _client(colleague_user).get(f"{BASE}/inbox/{source.pk}")
    assert response.status_code == 403
    assert response.json()["code"] == "privileged"
    assert sealed.tracking_number not in response.content.decode()


def test_who_moved_it_to_flagged_and_when(staff_user, actor, agency) -> None:
    request = _reserve(actor, agency, staff_user)
    source = make_source(actor)
    attach(actor, request, source)
    client = _client(staff_user)
    post_json(client, f"{BASE}/sources/{source.pk}/flag", {"note": "Covers two", "reason": "other"})
    row = client.get(f"{BASE}/inbox/{source.pk}").json()
    assert row["flagged_by"] == staff_user.name
    assert row["flagged_at"] is not None
    line = Activity.objects.get(action="message_flagged", subject_id=source.pk)
    assert line.actor_id == staff_user.pk and line.request_id == request.pk
    # The line names the reason, never the message's words or the note.
    assert "Covers two" not in line.summary
    # In the list too.
    flagged = client.get(f"{BASE}/inbox", {"bucket": "flagged"}).json()["items"]
    assert next(r for r in flagged if r["source"]["id"] == str(source.pk))["flagged_by"]


def test_a_process_that_flags_is_named_as_the_process(staff_user) -> None:
    inbound = Actor.for_system("postmark_webhook")
    source = make_source(inbound, flag=("no_matching_request", "Sent to no request."))
    row = _client(staff_user).get(f"{BASE}/inbox/{source.pk}").json()
    assert row["flagged_by"] == "Incoming mail"


def test_who_recorded_what_it_means_and_when(staff_user, actor, agency) -> None:
    request = _reserve(actor, agency, staff_user)
    source = make_source(actor)
    event = attach(actor, request, source)
    client = _client(staff_user)
    assert client.get(f"{BASE}/inbox/{source.pk}").json()["recorded_by"] is None
    response = post_json(client, f"{BASE}/events/{event.pk}/classify", {"kind": "acknowledgment"})
    assert response.status_code == 200, response.content
    row = client.get(f"{BASE}/inbox/{source.pk}").json()
    assert row["recorded_by"] == staff_user.name and row["recorded_at"] is not None


def test_the_inbox_and_the_mail_read_take_a_date_range(staff_user, actor, agency) -> None:
    """The person's own dates, both inclusive, in their own zone (Los Angeles here)."""
    staff_user.timezone = "America/Los_Angeles"
    staff_user.save(update_fields=["timezone"])
    request = _reserve(actor, agency, staff_user)
    early = make_source(actor, captured_at=T0)  # Mar 2, 09:00 local
    late = make_source(actor, captured_at=datetime(2026, 3, 4, 7, 30, tzinfo=UTC))  # Mar 3 23:30
    attach(actor, request, early)
    attach(actor, request, late)
    client = _client(staff_user)

    def ids(**span):
        rows = client.get(f"{BASE}/inbox", {"bucket": "all", **span}).json()["items"]
        return {row["source"]["id"] for row in rows}

    assert ids(since="2026-03-03") == {str(late.pk)}
    assert ids(until="2026-03-02") == {str(early.pk)}
    assert ids(since="2026-03-02", until="2026-03-03") == {str(early.pk), str(late.pk)}
    refused = client.get(
        f"{BASE}/inbox", {"bucket": "all", "since": "2026-03-04", "until": "2026-03-01"}
    )
    assert refused.status_code == 422
    # The mail read: a draft counts from when it last changed.
    draft = Message.objects.create(
        request_id=request.pk,
        kind="reply",
        from_address="records@example.org",
        subject="Synthetic draft",
        updated_at=T0,
        created_by_id=staff_user.pk,
    )
    mail = client.get("/api/mail/messages", {"bucket": "drafts", "since": "2026-03-03"}).json()
    assert str(draft.pk) not in [m["id"] for m in mail["items"]]
    mail = client.get("/api/mail/messages", {"bucket": "drafts", "until": "2026-03-02"}).json()
    assert str(draft.pk) in [m["id"] for m in mail["items"]]


# ---- tasks -------------------------------------------------------------------------------------


def test_notes_on_a_task_are_written_listed_and_voided_never_deleted(
    staff_user, colleague_user, actor, agency
) -> None:
    request = _reserve(actor, agency, staff_user)
    task = tasks_service.create_task(actor, request.pk, "manual", "Call the records officer")
    client = _client(staff_user)
    made = post_json(client, f"{BASE}/tasks/{task.pk}/notes", {"body": "  Called, no answer.  "})
    assert made.status_code == 201, made.content
    note = made.json()
    assert note["body"] == "Called, no answer." and note["author_name"] == staff_user.name
    assert post_json(client, f"{BASE}/tasks/{task.pk}/notes", {"body": "  "}).status_code == 422
    # A colleague voids it with a reason; it stays, struck through.
    other = _client(colleague_user)
    path = f"{BASE}/tasks/{task.pk}/notes/{note['id']}/void"
    assert post_json(other, path, {"reason": ""}).status_code == 422
    voided = post_json(other, path, {"reason": "Wrong task."})
    assert voided.status_code == 200
    assert voided.json()["voided_by_name"] == colleague_user.name
    assert post_json(other, path, {"reason": "Again."}).status_code == 409
    listed = client.get(f"{BASE}/tasks/{task.pk}/notes").json()
    assert [n["void_reason"] for n in listed] == ["Wrong task."]
    assert TaskNote.objects.filter(task_id=task.pk).count() == 1
    # A note is reached only under its own task.
    second = tasks_service.create_task(actor, request.pk, "manual", "Another")
    wrong = post_json(client, f"{BASE}/tasks/{second.pk}/notes/{note['id']}/void", {"reason": "x"})
    assert wrong.status_code == 404


def test_a_task_names_the_message_it_is_about(staff_user, actor, agency) -> None:
    request = _reserve(actor, agency, staff_user)
    other = _reserve(actor, agency, staff_user, subject="Another")
    source = make_source(actor)
    attach(actor, request, source)
    client = _client(staff_user)
    made = post_json(
        client,
        f"{BASE}/tasks",
        {
            "request_id": str(request.pk),
            "task_type": "manual",
            "description": "Answer this",
            "source_id": str(source.pk),
        },
    )
    assert made.status_code == 201, made.content
    assert made.json()["source_id"] == str(source.pk)
    # Mail of another request is refused.
    refused = post_json(
        client,
        f"{BASE}/tasks",
        {
            "request_id": str(other.pk),
            "task_type": "manual",
            "description": "Answer this",
            "source_id": str(source.pk),
        },
    )
    assert refused.status_code == 422


def test_a_task_a_classification_opens_names_its_message(staff_user, actor, agency) -> None:
    request = _reserve(actor, agency, staff_user)
    source = make_source(actor)
    event = attach(actor, request, source)
    client = _client(staff_user)
    response = post_json(client, f"{BASE}/events/{event.pk}/classify", {"kind": "clarification"})
    assert response.status_code == 200, response.content
    task = Task.objects.get(request_id=request.pk, task_type="reply_to_clarification")
    assert task.source_id == source.pk


def test_a_bounce_opens_a_retry_task_that_names_the_bounced_message(
    staff_user, actor, agency
) -> None:
    from portal.mail.services import delivery

    request = _reserve(actor, agency, staff_user)
    source = make_source(actor, source_type="postmark_email")
    attach(actor, request, source, direction="outbound")
    message = Message.objects.create(
        request_id=request.pk,
        kind="reply",
        from_address="records@example.org",
        subject="Synthetic",
        status="sent",
        sent_at=T0,
        source_id=source.pk,
        created_by_id=staff_user.pk,
    )
    delivery._on_bounce(message, "nobody@agency.example.org", {"Description": "mailbox full"})
    task = Task.objects.get(request_id=request.pk, task_type="retry_delivery")
    assert task.source_id == source.pk
    assert _client(staff_user).get(f"{BASE}/tasks/{task.pk}/history").status_code == 200


# ---- agencies ----------------------------------------------------------------------------------


def test_an_imported_agency_records_the_file_it_came_from(staff_user, actor) -> None:
    client = _client(staff_user)
    text = "Agency name,Email,State\nLakeside Records Office,pra@lakeside.example.org,Testland\n"
    response = client.post(
        f"{BASE}/agencies/import",
        {"file": SimpleUploadedFile("lakeside.csv", text.encode(), content_type="text/csv")},
    )
    assert response.status_code == 200, response.content
    made = Agency.objects.get(name="Lakeside Records Office")
    assert made.imported_from == "lakeside.csv"
    line = Activity.objects.get(action="agencies_imported")
    assert line.data["agency_ids"] == [str(made.pk)]
    listed = {a["name"]: a for a in client.get(f"{BASE}/agencies").json()}
    assert listed["Lakeside Records Office"]["imported_from"] == "lakeside.csv"
    by_hand = directory.create_agency(actor, name="By Hand Agency", jurisdiction="Testland")
    assert client.get(f"{BASE}/agencies/{by_hand.pk}").json()["imported_from"] is None


# ---- the request page --------------------------------------------------------------------------


def test_the_record_as_a_pdf(staff_user, actor, agency) -> None:
    from pypdf import PdfReader

    request = _reserve(actor, agency, staff_user, items=[(1, "Widget inspection reports")])
    attach(actor, request, make_source(actor), body="The agency acknowledged it.")
    response = _client(staff_user).get(f"{BASE}/requests/{request.pk}/record.pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert request.tracking_number in response["Content-Disposition"]
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    for expected in (request.tracking_number, "Widget inspection reports", "acknowledged it"):
        assert expected in text, expected
    line = Activity.objects.get(action="record_exported")
    assert line.request_id == request.pk and line.actor_id == staff_user.pk


def test_moving_a_message_writes_the_reason_where_it_leaves(staff_user, actor, agency) -> None:
    wrong = _reserve(actor, agency, staff_user, subject="Wrong one")
    right = _reserve(actor, agency, staff_user, subject="Right one")
    source = make_source(actor)
    event = attach(actor, wrong, source)
    response = post_json(
        _client(staff_user),
        f"{BASE}/sources/{source.pk}/assign",
        {
            "request_id": str(right.pk),
            "mode": "belongs_here_instead",
            "from_event_id": str(event.pk),
            "reason": "Answers the other request.",
        },
    )
    assert response.status_code == 201, response.content
    event.refresh_from_db()
    assert event.void_reason == f"reassigned to {right.tracking_number}: Answers the other request."


def test_filing_again_writes_why_on_the_first_request(staff_user, actor, agency) -> None:
    request = _reserve(actor, agency, staff_user)
    client = _client(staff_user)
    response = post_json(
        client,
        "/api/drafting/drafts",
        {"from_request_id": str(request.pk), "refile_reason": "Their system lost the first one"},
    )
    assert response.status_code == 201, response.content
    note = Event.objects.get(request_id=request.pk, event_type_id="note")
    assert note.display_body == "Filing again: Their system lost the first one"
    assert note.actor_user_id == staff_user.pk
    alone = post_json(client, "/api/drafting/drafts", {"subject": "x", "refile_reason": "Why"})
    assert alone.status_code == 422


def test_a_letter_received_names_its_sender(staff_user, actor, agency, media) -> None:
    request = _reserve(actor, agency, staff_user)
    contact = directory.create_contact(actor, agency.pk, name="L. Alvarez", email="la@example.org")
    elsewhere = directory.create_agency(actor, name="Elsewhere", jurisdiction="Testland")
    stranger = directory.create_contact(actor, elsewhere.pk, name="Someone Else")
    client = _client(staff_user)
    form = {
        "source_type": "scanned_mail",
        "received_at": "2026-03-02T17:00:00Z",
        "display_body": "Determination letter.",
    }
    made = client.post(
        f"{BASE}/requests/{request.pk}/documents", {**form, "contact_id": contact.pk}
    )
    assert made.status_code == 201, made.content
    assert made.json()["event"]["actor_agency_contact_id"] == str(contact.pk)
    refused = client.post(
        f"{BASE}/requests/{request.pk}/documents", {**form, "contact_id": stranger.pk}
    )
    assert refused.status_code == 422


def test_a_refused_filing_names_who_filed_it_and_when(
    staff_user, colleague_user, actor, agency, rules
) -> None:
    request = _reserve(actor, agency, staff_user)
    body = {"filed_at": "2026-03-02T17:00:00Z", "channel": "email"}
    first = post_json(_client(colleague_user), f"{BASE}/requests/{request.pk}/file", body)
    assert first.status_code == 200, first.content
    again = post_json(_client(staff_user), f"{BASE}/requests/{request.pk}/file", body)
    assert again.status_code == 409
    found = again.json()
    assert found["code"] == "changed" and found["changed_by"] == colleague_user.name
    assert found["detail"].startswith(f"{colleague_user.name} marked this filed at ")
    assert found["detail"].endswith("Refresh to see it.")
    mine = post_json(_client(colleague_user), f"{BASE}/requests/{request.pk}/file", body)
    assert mine.json()["detail"].startswith("You marked this filed at ")


def test_the_deadlines_filing_would_open_are_computed_and_nothing_is_written(
    staff_user, actor, agency, rules
) -> None:
    request = _reserve(actor, agency, staff_user, items=[(1, "Widgets")])
    when = "2026-02-10T18:00:00Z"
    before = (Deadline.objects.count(), Event.objects.count(), StatusChange.objects.count())
    response = post_json(
        _client(staff_user), f"{BASE}/requests/{request.pk}/file/preview", {"filed_at": when}
    )
    assert response.status_code == 200, response.content
    preview = response.json()
    assert (Deadline.objects.count(), Event.objects.count(), StatusChange.objects.count()) == before
    assert Request.objects.get(pk=request.pk).current_status_id == "pending_filing"
    # Filing then opens exactly those.
    post_json(
        _client(staff_user),
        f"{BASE}/requests/{request.pk}/file",
        {"filed_at": when, "channel": "email"},
    )
    opened = sorted(
        (d.deadline_type, d.due_at.isoformat(), d.legal_basis)
        for d in Deadline.objects.filter(request_id=request.pk)
    )
    planned = sorted(
        (d["deadline_type"], d["due_at"], d["legal_basis"]) for d in preview["deadlines"]
    )
    assert planned == opened and planned
    assert preview["problem"] is None
    # The rule's own citation travels with it (the fixture's invented one).
    assert all("TESTLAW §" in d["citation"] for d in preview["deadlines"])


def test_the_preview_says_when_no_rule_covers_the_law(staff_user, actor, rules) -> None:
    norules = directory.create_agency(
        actor, name="No Rules Agency", jurisdiction="Testland", records_law="TESTLAW_NORULES"
    )
    request = _reserve(actor, norules, staff_user)
    response = post_json(
        _client(staff_user),
        f"{BASE}/requests/{request.pk}/file/preview",
        {"filed_at": "2026-02-10T18:00:00Z"},
    )
    body = response.json()
    assert body["deadlines"] == [] and "TESTLAW_NORULES" in body["problem"]


def test_an_intern_may_write_notes_on_a_task_they_see(admin_user, actor, agency) -> None:
    intern = create_user(email="p23b-intern@example.org", name="Intern", records_role="intern")
    request = _reserve(actor, agency, intern)  # an intern's request: interns see it
    task = tasks_service.create_task(actor, request.pk, "manual", "Synthetic")
    made = post_json(_client(intern), f"{BASE}/tasks/{task.pk}/notes", {"body": "Done half."})
    assert made.status_code == 201, made.content
    assert json.loads(made.content)["author_name"] == "Intern"


def test_a_deadline_names_the_message_its_date_came_from(staff_user, actor, agency) -> None:
    """085 "Where the date comes from · letter of Sep 5": the dialog sends the message's event;
    an event of another request is refused, so a deadline never points at someone else's mail."""
    request = _reserve(actor, agency, staff_user)
    other = _reserve(actor, agency, staff_user, subject="Another")
    event = attach(actor, request, make_source(actor), channel="mail")
    foreign = attach(actor, other, make_source(actor))
    body = {"deadline_type": "production", "due_at": "2031-01-06", "basis": "agency_commitment"}
    client = _client(staff_user)
    made = post_json(
        client,
        f"{BASE}/requests/{request.pk}/deadlines",
        {**body, "source_event_id": str(event.pk)},
    )
    assert made.status_code == 201, made.content
    assert made.json()["source_event_id"] == str(event.pk)
    refused = post_json(
        client,
        f"{BASE}/requests/{request.pk}/deadlines",
        {**body, "source_event_id": str(foreign.pk)},
    )
    assert refused.status_code in (404, 422)
