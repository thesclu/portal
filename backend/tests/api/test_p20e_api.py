"""P20e: the agency importer (168, DECISIONS 37c), editing a contact (171), the Agencies list's
figures (163) and a task's history (177).

The importer is read twice -- a preview that writes nothing, and the import itself in one
transaction -- so each rule is pinned on the route a person uses: how a row is classified (new,
matching by name and jurisdiction or by the name of an agency merged into one, or a problem),
that a chosen match is filled in only where it is blank, that nothing half-imports, the one
activity line, and that only staff may do any of it.
"""

import datetime
import io
import json
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from openpyxl import Workbook
from portal.core.models import Activity
from portal.records.actors import Actor
from portal.records.models import Agency, AgencyContact, AuditLog
from portal.records.services import agency_import, directory
from portal.records.services import requests as requests_service
from portal.records.services import status as status_service
from portal.records.services import tasks as tasks_service

BASE = "/api/records"

HEADER = "Agency name,Email,Portal,State,Files by,Street\n"


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _upload(text: str, name: str = "agencies.txt") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/plain")


def _preview(client: Client, text: str, mapping: dict | None = None):
    data: dict = {"file": _upload(text)}
    if mapping is not None:
        data["mapping"] = json.dumps(mapping)
    return client.post(f"{BASE}/agencies/import/preview", data)


def _import(client: Client, text: str, decisions: dict | None = None, name="agencies.txt"):
    data: dict = {"file": _upload(text, name)}
    if decisions is not None:
        data["decisions"] = json.dumps(decisions)
    return client.post(f"{BASE}/agencies/import", data)


def _row(body: dict, number: int) -> dict:
    return next(row for row in body["rows"] if row["number"] == number)


@pytest.fixture
def existing(staff_user):
    """One agency already in the directory with some fields filled, and one merged into it."""
    actor = Actor.human(staff_user)
    kept = directory.create_agency(
        actor,
        name="Harbor City Police Department",
        jurisdiction="Testland",
        default_request_email="records@harbor.example.org",
        filing_method="email",
    )
    alias = directory.create_agency(actor, name="Harbor City PD", jurisdiction="Testland")
    directory.merge_agencies(actor, keep_id=kept.pk, merge_id=alias.pk, note="imported twice")
    return kept


# ---- the preview ------------------------------------------------------------------------------


def test_the_preview_classifies_every_row_and_writes_nothing(staff_user, existing):
    text = (
        HEADER
        + "Lakeside Records Office,pra@lakeside.example.org,,Testland,,\n"
        + "  harbor city police department. ,,,TESTLAND,,\n"  # same name, folded
        + "Harbor City PD,,,Testland,,\n"  # the merged agency's name: the kept one
        + ",nobody@example.org,,Testland,,\n"  # no name
        + "Ridge County Sheriff,not-an-address,,Testland,,\n"
        + "Mesa Transit Authority,,https://mesa.example.org/form,Testland,carrier pigeon,\n"
        + "Valley Water District,,,,,\n"  # no jurisdiction
        + "Lakeside Records Office,,,Testland,,\n"  # the same as row 2
    )
    before = Agency.objects.count()
    response = _preview(_client(staff_user), text)
    assert response.status_code == 200, response.content
    body = response.json()
    assert Agency.objects.count() == before
    assert body["filename"] == "agencies.txt"
    assert body["mapping"] == {
        "Agency name": "name",
        "Email": "default_request_email",
        "Portal": "portal_url",
        "State": "jurisdiction",
        "Files by": "filing_method",
        "Street": "mailing_street",
    }
    new = _row(body, 2)
    assert new["status"] == "new"
    # Nothing said how it files, and it has an email: it files by email.
    assert new["values"]["filing_method"] == "email"
    for number in (3, 4):
        match = _row(body, number)
        assert match["status"] == "match", match
        assert match["match_agency_id"] == str(existing.pk)
        assert match["match_agency_name"] == existing.name
    problems = {row["number"]: row["problems"] for row in body["rows"] if row["problems"]}
    assert problems[5] == ["No name"]
    assert problems[6] == ["“not-an-address” is not an email address"]
    assert "carrier pigeon" in problems[7][0]
    assert problems[8] == ["No jurisdiction"]
    assert problems[9] == ["The same agency as row 2"]
    assert (body["new"], body["matches"], body["problems"]) == (1, 2, 5)


def test_a_column_can_be_read_as_another_field_or_left_out(staff_user, db):
    text = "Agency name,Email,Portal,State\nPine Grove Clerk,pine@example.org,,Testland\n"
    client = _client(staff_user)
    mapping = {"Agency name": "name", "Email": None, "Portal": None, "State": "jurisdiction"}
    body = _preview(client, text, mapping).json()
    assert body["mapping"] == mapping
    assert "default_request_email" not in _row(body, 2)["values"]
    twice = _preview(client, text, {"Agency name": "name", "Email": "name"})
    assert twice.status_code == 422
    assert "Two columns" in twice.json()["detail"]


# ---- the import -------------------------------------------------------------------------------


def test_new_rows_are_created_and_a_match_is_filled_in_only_where_blank(staff_user, existing):
    text = (
        HEADER
        + "Lakeside Records Office,pra@lakeside.example.org,,Testland,,\n"
        + "Harbor City Police Department,other@harbor.example.org,,Testland,Web form,"
        + "9 Pier Road\n"
    )
    response = _import(_client(staff_user), text, decisions={"3": "update"})
    assert response.status_code == 200, response.content
    assert response.json()["created"] == 1
    assert response.json()["updated"] == 1
    created = Agency.objects.get(name="Lakeside Records Office")
    assert (created.jurisdiction, created.filing_method) == ("Testland", "email")
    existing.refresh_from_db()
    # The address and filing method it already had are kept; only the blank street is filled.
    assert existing.default_request_email == "records@harbor.example.org"
    assert existing.filing_method == "email"
    assert existing.portal_kind is None
    assert existing.mailing_street == "9 Pier Road"
    lines = AuditLog.objects.filter(entity_type="agencies", entity_id=existing.pk)
    assert [line.field_name for line in lines] == ["agencies.mailing_street"]
    assert "agencies.txt" in lines[0].note


def test_a_match_with_no_choice_or_skip_is_left_alone(staff_user, existing):
    text = HEADER + "Harbor City Police Department,,,Testland,,9 Pier Road\n"
    text += "Lakeside Records Office,,,Testland,,\n"
    client = _client(staff_user)
    assert _import(client, text, decisions={"2": "skip"}).json()["skipped"] == 1
    existing.refresh_from_db()
    assert existing.mailing_street is None


def test_a_bad_row_never_half_imports(staff_user, existing, monkeypatch):
    client = _client(staff_user)
    text = HEADER + "Lakeside Records Office,,,Testland,,\n" + "Ridge County Sheriff,nope,,,,\n"
    body = _import(client, text).json()
    assert body["created"] == 1 and body["problems"] == 1
    assert not Agency.objects.filter(name="Ridge County Sheriff").exists()

    # A failure part way through undoes every row written before it.
    real = directory.create_agency
    calls = {"n": 0}

    def fails_second_time(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise directory.ServiceError("refused for the test")
        return real(*args, **kwargs)

    monkeypatch.setattr(directory, "create_agency", fails_second_time)
    before = Agency.objects.count()
    activity = Activity.objects.count()
    text = HEADER + "Pine Grove Clerk,,,Testland,,\n" + "Cedar Bay Port,,,Testland,,\n"
    response = _import(client, text)
    assert response.status_code == 422
    assert Agency.objects.count() == before
    assert not Agency.objects.filter(name="Pine Grove Clerk").exists()
    assert Activity.objects.count() == activity


def test_the_import_writes_one_activity_line_naming_the_count(staff_user, existing):
    text = HEADER + "Lakeside Records Office,,,Testland,,\n" + "Pine Grove Clerk,,,Testland,,\n"
    text += "Harbor City Police Department,,,Testland,,9 Pier Road\n"
    _import(_client(staff_user), text, decisions={"4": "update"}, name="west.txt")
    lines = Activity.objects.filter(action="agencies_imported")
    assert lines.count() == 1
    line = lines.get()
    assert line.actor_id == staff_user.pk
    assert line.summary == "Imported agencies from west.txt: 2 agencies added, 1 filled in"
    assert line.data["created"] == 2 and line.data["updated"] == 1


def test_a_file_with_nothing_to_add_is_refused(staff_user, existing):
    response = _import(_client(staff_user), HEADER + ",,,Testland,,\n")
    assert response.status_code == 422
    assert not Activity.objects.filter(action="agencies_imported").exists()


def test_only_staff_may_preview_or_import(requester_user, admin_user, db):
    text = HEADER + "Lakeside Records Office,,,Testland,,\n"
    requester = _client(requester_user)
    assert _preview(requester, text).status_code == 403
    assert _import(requester, text).status_code == 403
    anonymous = Client()
    assert _preview(anonymous, text).status_code == 401
    assert _import(anonymous, text).status_code == 401
    assert not Agency.objects.filter(name="Lakeside Records Office").exists()
    assert _import(_client(admin_user), text).status_code == 200


# ---- editing a contact ------------------------------------------------------------------------


def test_a_contact_is_edited_with_a_note_and_a_requester_is_refused(
    staff_user, requester_user, agency
):
    contact = directory.create_contact(Actor.human(staff_user), agency.pk, name="J. Doe")
    path = f"{BASE}/agencies/contacts/{contact.pk}"
    body = {
        "note": "full name from a signature",
        "name": "Jordan Doe",
        "copy_on_every_request": True,
    }
    refused = _client(requester_user).patch(path, json.dumps(body), "application/json")
    assert refused.status_code == 403
    response = _client(staff_user).patch(path, json.dumps(body), "application/json")
    assert response.status_code == 200, response.content
    assert response.json()["name"] == "Jordan Doe"
    contact = AgencyContact.objects.get(pk=contact.pk)
    assert contact.copy_on_every_request is True
    fields = set(AuditLog.objects.filter(entity_id=contact.pk).values_list("field_name", flat=True))
    assert fields == {"agency_contacts.name", "agency_contacts.copy_on_every_request"}
    no_note = {"note": " ", "name": "Someone"}
    assert (
        _client(staff_user).patch(path, json.dumps(no_note), "application/json").status_code == 422
    )


# ---- the list's figures -----------------------------------------------------------------------


def test_the_list_counts_only_requests_the_caller_may_see(staff_user, colleague_user, agency):
    actor = Actor.human(staff_user)

    def reserve(subject, sensitivity="internal"):
        return requests_service.reserve_request(
            actor,
            agency_id=agency.pk,
            requester_user_id=staff_user.pk,
            subject=subject,
            request_text=subject,
            sensitivity=sensitivity,
        )

    reserve("open one")
    closed = reserve("closed one")
    status_service.override_status(actor, closed.pk, "closed", "set for the test")
    reserve("SEALED", sensitivity="privileged")
    alias = directory.create_agency(actor, name="Same Agency, Other Name", jurisdiction="Testland")
    requests_service.reserve_request(
        actor,
        agency_id=alias.pk,
        requester_user_id=staff_user.pk,
        subject="on the merged row",
        request_text="x",
    )
    directory.merge_agencies(actor, keep_id=agency.pk, merge_id=alias.pk, note="the same")

    def row(user):
        rows = _client(user).get(f"{BASE}/agencies").json()
        return next(r for r in rows if r["id"] == str(agency.pk))

    # The colleague is not on the sealed request's list: it moves neither figure for them.
    seen = row(colleague_user)
    assert (seen["open_requests"], seen["requests_total"]) == (2, 3)
    assert seen["median_days_to_determination"] is None
    # Its reserver is on the list.
    mine = row(staff_user)
    assert (mine["open_requests"], mine["requests_total"]) == (3, 4)


# ---- a task's history -------------------------------------------------------------------------


def test_a_tasks_history_is_its_audit_lines(staff_user, colleague_user, reserved):
    actor = Actor.human(staff_user)
    task = tasks_service.create_task(actor, reserved.pk, "manual", "Call the records unit")
    tasks_service.assign_task(actor, task.pk, colleague_user.pk)
    tasks_service.update_task(actor, task.pk, "they asked for more time", description="Call back")
    lines = _client(staff_user).get(f"{BASE}/tasks/{task.pk}/history").json()
    assert [line["field"] for line in lines] == ["assigned_to_user_id", "description"]
    assert lines[0]["actor_name"] == staff_user.name
    assert lines[1]["old_value"] == "Call the records unit"
    assert lines[1]["note"] == "they asked for more time"


def test_a_task_names_its_requests_subject(staff_user, reserved):
    tasks_service.create_task(Actor.human(staff_user), reserved.pk, "manual", "Call them")
    rows = _client(staff_user).get(f"{BASE}/tasks", {"request_id": str(reserved.pk)}).json()
    assert rows[0]["request_subject"] == reserved.subject


def test_records_can_be_read_oldest_first(staff_user, db):
    client = _client(staff_user)
    newest = client.get(f"{BASE}/produced").json()
    oldest = client.get(f"{BASE}/produced", {"order": "oldest"}).json()
    assert oldest["total"] == newest["total"]
    assert oldest["items"] == list(reversed(newest["items"]))
    assert client.get(f"{BASE}/produced", {"order": "sideways"}).status_code == 422


# ---- the importer reads an Excel workbook as it reads comma-separated text --------------------


def _workbook(*sheets: list[list]) -> bytes:
    """An .xlsx built in memory: one list of rows per worksheet."""
    book = Workbook()
    book.remove(book.active)
    for number, rows in enumerate(sheets, start=1):
        sheet = book.create_sheet(f"Sheet {number}")
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _post_bytes(client: Client, route: str, data: bytes, name: str = "agencies.xlsx"):
    upload = SimpleUploadedFile(name, data, content_type="application/octet-stream")
    return client.post(f"{BASE}/agencies/import{route}", {"file": upload})


ROWS = [
    ["Agency name", "Email", "Portal", "State", "Files by", "Street"],
    ["Lakeside Records Office", "records@lakeside.example.org", None, "Testland", None, None],
    ["Harbor City Police Department", None, None, "Testland", None, "9 Pier Road"],
    ["Test Agency No Place", None, None, None, None, None],
]


def test_a_workbook_previews_and_imports_like_the_same_rows_as_text(staff_user, existing):
    client = _client(staff_user)
    as_text = "\n".join(",".join(cell or "" for cell in row) for row in ROWS) + "\n"
    from_text = _preview(client, as_text).json()
    from_book = _post_bytes(client, "/preview", _workbook(ROWS)).json()
    assert {**from_book, "filename": ""} == {**from_text, "filename": ""}
    assert [r["status"] for r in from_book["rows"]] == ["new", "match", "problem"]

    response = _post_bytes(client, "", _workbook(ROWS))
    assert response.status_code == 200
    assert response.json()["created"] == 1
    assert Agency.objects.filter(name="Lakeside Records Office").exists()


def test_a_whole_number_loses_its_point_zero_and_a_date_is_iso_text():
    rows = [["Name", "State", "Notes"], ["Test Agency Zip", "Testland", 92093.0]]
    table = agency_import._table(_workbook(rows))
    assert table[1][1] == ["Test Agency Zip", "Testland", "92093"]
    assert agency_import._cell_text(2.5) == "2.5"
    assert agency_import._cell_text(datetime.datetime(2026, 3, 4, 5, 6)) == "2026-03-04"
    assert agency_import._cell_text(datetime.date(2026, 3, 4)) == "2026-03-04"
    assert agency_import._cell_text("  padded ") == "padded"


def test_empty_rows_and_empty_cells_are_dropped_and_rows_keep_their_place():
    rows = [["Name", "State"], [None, None], ["Test Agency One", None], ["", "  "], ["Two", "X"]]
    table = agency_import._table(_workbook(rows))
    assert table == [
        (1, ["Name", "State"]),
        (3, ["Test Agency One", ""]),
        (5, ["Two", "X"]),
    ]


def test_only_the_first_worksheet_is_read(db):
    first = [["Name", "State"], ["Test Agency First", "Testland"]]
    second = [["Name", "State"], ["Test Agency Second", "Testland"]]
    found = agency_import.preview(_workbook(first, second))
    assert [r.values["name"] for r in found.rows] == ["Test Agency First"]


def _zip_claiming(size: int) -> bytes:
    """A small ZIP whose member says it unpacks to `size` bytes (the header is what is read)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("xl/workbook.xml", "x")
    data = bytearray(out.getvalue())
    packed = (1).to_bytes(4, "little")
    huge = size.to_bytes(4, "little")
    for signature, offset in ((b"PK\x01\x02", 24), (b"PK\x03\x04", 22)):
        at = data.index(signature)
        assert data[at + offset : at + offset + 4] == packed
        data[at + offset : at + offset + 4] = huge
    return bytes(data)


def test_a_zip_bomb_is_refused_before_it_is_read(staff_user, db):
    bomb = _zip_claiming(agency_import.MAX_WORKBOOK_UNPACKED_BYTES + 1)
    with pytest.raises(agency_import.ServiceError, match="too big"):
        agency_import.preview(bomb)
    response = _post_bytes(_client(staff_user), "/preview", bomb)
    assert response.status_code == 422


def test_a_zip_that_is_not_a_workbook_or_is_garbage_is_refused_not_a_500(staff_user, db):
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("readme.txt", "Test Agency, Testland")
    garbage = b"PK\x03\x04" + b"\x00\xff random bytes" * 20
    client = _client(staff_user)
    for data in (plain.getvalue(), garbage):
        with pytest.raises(agency_import.ServiceError, match="could not be read"):
            agency_import.preview(data)
        for route in ("/preview", ""):
            response = _post_bytes(client, route, data)
            assert response.status_code == 422
            assert "could not be read" in response.json()["detail"]


def test_a_workbook_with_too_many_rows_is_refused(staff_user, db):
    rows = [["Name", "State"]] + [
        [f"Test Agency {n}", "Testland"] for n in range(agency_import.MAX_ROWS + 1)
    ]
    with pytest.raises(agency_import.ServiceError, match="more rows than an agency list"):
        agency_import.preview(_workbook(rows))
    response = _post_bytes(_client(staff_user), "/preview", _workbook(rows))
    assert response.status_code == 422


def test_bytes_that_are_neither_text_nor_a_workbook_say_so(db):
    with pytest.raises(agency_import.ServiceError, match="neither comma-separated text nor"):
        agency_import.preview(b"\x81\x8d\x8f\x90\x9d")
