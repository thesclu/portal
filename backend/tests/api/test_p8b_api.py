"""The P8b doors, one at a time, from the caller's side: the Inbox and the messages in it, "Add a
document", "Record as...", the downloads, Home, the Calendar and the Rules screen."""

import hashlib
import json
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from portal.records import storage
from portal.records.actors import Actor
from portal.records.models import Attachment, AuditLog, Event, Request, Source, StatusChange
from portal.records.services import common
from portal.records.services import deadlines as deadlines_service
from portal.records.services import events as events_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services import tasks as tasks_service
from portal.records.services.classify import KINDS

from tests.api.conftest import post_json
from tests.p8b_helpers import add_file, attach, make_source

pytestmark = pytest.mark.usefixtures("as_each_role")

FIXTURES = Path(__file__).resolve().parent.parent / "services" / "fixtures"
BASE = "/api/records"


@pytest.fixture
def rules():
    """The fictional TESTLAW rules and calendar, never the shipped ones."""
    with common.using_rule_data(
        rules_path=FIXTURES / "rules_test.toml", calendars_path=FIXTURES / "calendars_test.toml"
    ) as data:
        yield data


@pytest.fixture
def actor(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def inbound(actor, reserved):
    """An unclassified inbound message on a request, with two files."""
    source = make_source(actor)
    cover = add_file(actor, source, "cover-letter.pdf")
    records = add_file(actor, source, "records.pdf")
    event = attach(actor, reserved, source)
    return event, source, cover, records


def _upload(name="scan.pdf", data=b"%PDF-1.4 fictional", content_type="application/pdf"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def _document(client, request_id, *files, **fields):
    data = {
        "source_type": "scanned_mail",
        "received_at": "2026-03-02T17:30:00Z",
        **fields,
    }
    if files:
        data["files"] = list(files)
    return client.post(f"{BASE}/requests/{request_id}/documents", data=data)


# ---- definitions -------------------------------------------------------------------------------


def test_definitions_serve_the_buckets_and_the_classification_table(staff_client):
    body = staff_client.get(f"{BASE}/definitions").json()
    assert body["inbox_buckets"] == ["flagged", "new", "all"]
    served = {c["kind"]: c for c in body["classifications"]}
    assert list(served) == [k.key for k in KINDS]
    complete = served["production_complete"]
    assert complete["through_override"] is True and complete["note_required"] is True
    assert complete["status"] == "production_received" and complete["takes_records"] is True
    assert served["fee_estimate"]["takes_fee"] is True
    assert served["fee_estimate"]["task_type"] == "decide_fee"
    assert served["extension"]["status"] is None and served["extension"]["task_type"] is None


# ---- who may call ------------------------------------------------------------------------------


DOORS = [
    ("get", "/inbox?bucket=all", None),
    ("post", "/sources/{source}/flag", {"note": "x"}),
    ("post", "/sources/{source}/assignee", {}),
    ("post", "/sources/{source}/unreview", {}),
    ("post", "/sources/{source}/resolve", {"resolution": "duplicate"}),
    ("get", "/sources/{source}/notes", None),
    ("post", "/sources/{source}/notes", {"body": "x"}),
    ("post", "/source-notes/{note}/void", {"reason": "x"}),
    ("post", "/sources/{source}/records", {"record_attachment_ids": []}),
    ("get", "/sources/{source}/raw", None),
    ("get", "/attachments/{attachment}/download", None),
    ("post", "/requests/{request}/documents", {"source_type": "other", "channel": "mail"}),
    ("get", "/events/{event}/classify?kind=acknowledgment", None),
    ("post", "/events/{event}/classify", {"kind": "acknowledgment"}),
    ("get", "/home", None),
    ("get", "/calendar?start=2026-01-01&end=2026-01-31", None),
    ("get", "/rules", None),
]


def _fill(path, inbound, reserved, note):
    event, source, cover, _ = inbound
    return path.format(
        source=source.pk, attachment=cover.pk, event=event.pk, request=reserved.pk, note=note.pk
    )


def _call(client, method, url, body):
    """A call with a VALID body, so a 403 can only be the permission check and not a 422 that
    arrived first: ninja validates the body before the handler's own check runs."""
    if method == "get":
        return client.get(url)
    if url.endswith("/documents"):
        return client.post(url, data={"received_at": "2026-03-02T17:30:00Z", **body})
    return post_json(client, url, body)


@pytest.mark.parametrize(("method", "path", "body"), DOORS)
def test_a_requester_gets_403_and_a_stranger_gets_401_on_every_door(
    method, path, body, inbound, reserved, actor, requester_client
):
    note = sources_service.add_source_note(actor, inbound[1].pk, "a note")
    url = BASE + _fill(path, inbound, reserved, note)
    assert _call(requester_client, method, url, body).status_code == 403
    assert _call(Client(), method, url, body).status_code == 401


# ---- the Inbox ---------------------------------------------------------------------------------


def test_the_inbox_lists_a_bucket_with_the_counts_of_all_three(
    staff_client, actor, reserved, staff_user
):
    flagged = make_source(actor, flag=("no_matching_request", "no alias"))
    landed = make_source(actor, metadata={"subject": "Re: widgets"})
    add_file(actor, landed, "audit.xlsx")
    event = attach(actor, reserved, landed, body="Here is the audit.")
    sources_service.add_source_note(actor, landed.pk, "ask Maya")

    page = staff_client.get(f"{BASE}/inbox", {"bucket": "new"}).json()
    assert page["bucket"] == "new" and page["total"] == 1
    assert page["counts"] == {"flagged": 1, "new": 1, "all": 2}
    (row,) = page["items"]
    assert row["source"]["id"] == str(landed.pk)
    assert row["source"]["raw_metadata"] == {"subject": "Re: widgets"}
    assert [a["filename"] for a in row["attachments"]] == ["audit.xlsx"]
    assert "storage_url" not in row["attachments"][0]
    (where,) = row["events"]
    assert where["id"] == str(event.pk) and where["tracking_number"] == reserved.tracking_number
    assert where["agency_name"] == "Test Agency" and where["display_body"] == "Here is the audit."
    assert row["note_count"] == 1 and row["assignee_name"] is None

    flagged_page = staff_client.get(f"{BASE}/inbox", {"bucket": "flagged"}).json()
    assert [r["source"]["id"] for r in flagged_page["items"]] == [str(flagged.pk)]
    assert flagged_page["items"][0]["source"]["flag_reason"] == "no_matching_request"


def test_the_inbox_filters_searches_and_pages(staff_client, actor, reserved, colleague_user):
    one, two, three = (make_source(actor) for _ in range(3))
    attach(actor, reserved, one, body="the vendor agreement")
    sources_service.assign_source(actor, two.pk, colleague_user.pk)
    add_file(actor, three, "siting-map.pdf")

    def ids(**params):
        resp = staff_client.get(f"{BASE}/inbox", {"bucket": "all", **params})
        assert resp.status_code == 200, resp.content
        return {r["source"]["id"] for r in resp.json()["items"]}

    assert ids(request_id=str(reserved.pk)) == {str(one.pk)}
    assert ids(agency_id=str(reserved.agency_id)) == {str(one.pk)}
    assert ids(assignee=str(colleague_user.pk)) == {str(two.pk)}
    assert ids(assignee="me") == set()
    assert ids(q="vendor agreement") == {str(one.pk)}
    assert ids(q="siting") == {str(three.pk)}
    page = staff_client.get(f"{BASE}/inbox", {"bucket": "all", "limit": 2, "offset": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["limit"] == 2


def test_the_inbox_needs_a_known_bucket(staff_client):
    assert staff_client.get(f"{BASE}/inbox").status_code == 422
    resp = staff_client.get(f"{BASE}/inbox", {"bucket": "sent"})
    assert resp.status_code == 422 and "Unknown inbox bucket" in resp.json()["detail"]
    assert (
        staff_client.get(f"{BASE}/inbox", {"bucket": "all", "assignee": "nobody"}).status_code
        == 422
    )


# ---- a message: flag, assign, unreview, resolve ------------------------------------------------


def test_flag_a_message_and_the_reason_is_checked(staff_client, actor):
    source = make_source(actor)
    resp = post_json(
        staff_client,
        f"{BASE}/sources/{source.pk}/flag",
        {"note": "Looks like it covers two requests.", "reason": "may_cover_others"},
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert (body["flagged_for_review"], body["flag_reason"]) == (True, "may_cover_others")
    # The reason defaults to other.
    assert (
        post_json(staff_client, f"{BASE}/sources/{source.pk}/flag", {"note": "x"}).json()[
            "flag_reason"
        ]
        == "other"
    )
    bad = post_json(
        staff_client, f"{BASE}/sources/{source.pk}/flag", {"note": "x", "reason": "spam"}
    )
    assert bad.status_code == 422 and "Unknown flag reason" in bad.json()["detail"]
    missing = post_json(staff_client, f"{BASE}/sources/{uuid.uuid4()}/flag", {"note": "x"})
    assert missing.status_code == 404


def test_the_assignee_door_names_a_person_and_the_assign_door_still_attaches_to_a_request(
    staff_client, actor, reserved, colleague_user, requester_user
):
    source = make_source(actor)
    resp = post_json(
        staff_client, f"{BASE}/sources/{source.pk}/assignee", {"user_id": str(colleague_user.pk)}
    )
    assert resp.status_code == 200 and resp.json()["assigned_to_user_id"] == str(colleague_user.pk)
    assert not Event.objects.filter(source_id=source.pk).exists()  # nothing was attached
    assert (
        post_json(staff_client, f"{BASE}/sources/{source.pk}/assignee", {}).json()[
            "assigned_to_user_id"
        ]
        is None
    )
    refused = post_json(
        staff_client, f"{BASE}/sources/{source.pk}/assignee", {"user_id": str(requester_user.pk)}
    )
    assert refused.status_code == 422

    # `/assign` is still "attach to a request": it wants a request, not a person.
    assert (
        post_json(
            staff_client, f"{BASE}/sources/{source.pk}/assign", {"user_id": str(colleague_user.pk)}
        ).status_code
        == 422
    )
    anchor = attach(actor, reserved, source)
    second = make_source(actor)
    attached = post_json(
        staff_client,
        f"{BASE}/sources/{second.pk}/assign",
        {
            "request_id": str(reserved.pk),
            "mode": "also_applies",
            "from_event_id": str(anchor.pk),
        },
    )
    assert attached.status_code == 201 and attached.json()["source_id"] == str(second.pk)


def test_unreview_then_the_message_is_back_in_new(staff_client, actor, reserved):
    source = make_source(actor)
    attach(actor, reserved, source)
    post_json(staff_client, f"{BASE}/sources/{source.pk}/reviewed", {})
    assert staff_client.get(f"{BASE}/inbox", {"bucket": "new"}).json()["total"] == 0
    resp = post_json(staff_client, f"{BASE}/sources/{source.pk}/unreview", {})
    assert resp.status_code == 200 and resp.json()["reviewed_at"] is None
    assert staff_client.get(f"{BASE}/inbox", {"bucket": "new"}).json()["total"] == 1
    assert AuditLog.objects.filter(field_name="sources.reviewed_at").count() == 1


def test_resolve_without_attaching_and_the_refusal_when_it_is_attached(
    staff_client, actor, reserved
):
    loose = make_source(actor, flag=("other", "odd"))
    resp = post_json(
        staff_client,
        f"{BASE}/sources/{loose.pk}/resolve",
        {"resolution": "duplicate", "note": "Same PDF as an earlier production."},
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert (body["resolution"], body["flagged_for_review"]) == ("duplicate", False)
    assert body["reviewed_at"] is not None

    attached = make_source(actor)
    attach(actor, reserved, attached)
    refused = post_json(
        staff_client,
        f"{BASE}/sources/{attached.pk}/resolve",
        {"resolution": "duplicate", "note": "n"},
    )
    assert refused.status_code == 409 and "attached to a request" in refused.json()["detail"]
    assert Source.objects.get(pk=attached.pk).resolution is None
    assert (
        post_json(
            staff_client, f"{BASE}/sources/{loose.pk}/resolve", {"resolution": "spam"}
        ).status_code
        == 422
    )


def test_notes_are_listed_added_and_voided(staff_client, actor, staff_user):
    source = make_source(actor)
    made = post_json(
        staff_client, f"{BASE}/sources/{source.pk}/notes", {"body": "Call them first."}
    )
    assert made.status_code == 201, made.content
    note = made.json()
    assert note["author_user_id"] == str(staff_user.pk) and note["voided_at"] is None
    assert (
        post_json(staff_client, f"{BASE}/sources/{source.pk}/notes", {"body": "  "}).status_code
        == 422
    )

    voided = post_json(
        staff_client, f"{BASE}/source-notes/{note['id']}/void", {"reason": "Wrong message."}
    )
    assert voided.status_code == 200 and voided.json()["void_reason"] == "Wrong message."
    again = post_json(staff_client, f"{BASE}/source-notes/{note['id']}/void", {"reason": "again"})
    assert again.status_code == 409

    both = staff_client.get(f"{BASE}/sources/{source.pk}/notes").json()
    assert [n["id"] for n in both] == [note["id"]] and both[0]["body"] == "Call them first."
    assert (
        staff_client.get(f"{BASE}/sources/{source.pk}/notes", {"include_voided": False}).json()
        == []
    )
    assert staff_client.get(f"{BASE}/sources/{uuid.uuid4()}/notes").status_code == 404


def test_mark_files_as_records(staff_client, inbound):
    _event, source, cover, records = inbound
    resp = post_json(
        staff_client,
        f"{BASE}/sources/{source.pk}/records",
        {"record_attachment_ids": [str(records.pk)]},
    )
    assert resp.status_code == 200, resp.content
    assert {a["filename"]: a["role"] for a in resp.json()} == {
        "cover-letter.pdf": "attachment",
        "records.pdf": "produced_record",
    }
    bad = post_json(
        staff_client,
        f"{BASE}/sources/{source.pk}/records",
        {"record_attachment_ids": [str(uuid.uuid4())]},
    )
    assert bad.status_code == 422 and Attachment.objects.get(pk=cover.pk).role == "attachment"


# ---- "Add a document" --------------------------------------------------------------------------


def test_add_a_document_by_multipart(staff_client, reserved, media):
    resp = _document(
        staff_client,
        reserved.pk,
        _upload("scan-1.pdf", b"first"),
        _upload("scan-2.pdf", b"second", "application/pdf"),
        display_body="Letter from the records unit.",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["source"]["source_type"] == "scanned_mail"
    assert body["source"]["reviewed_at"] is not None
    assert (body["event"]["event_type"], body["event"]["channel"], body["event"]["direction"]) == (
        "correspondence",
        "mail",
        "inbound",
    )
    assert body["event"]["request_id"] == str(reserved.pk)
    assert body["event"]["source_id"] == body["source"]["id"]
    assert [(a["filename"], a["size_bytes"]) for a in body["attachments"]] == [
        ("scan-1.pdf", 5),
        ("scan-2.pdf", 6),
    ]
    assert body["attachments"][0]["checksum_sha256"] == hashlib.sha256(b"first").hexdigest()
    assert body["attachments"][0]["page_count"] is None
    assert all("storage_url" not in a for a in body["attachments"])
    # It is now in the request's timeline and in the Inbox's All (reviewed, so not in New).
    assert (
        staff_client.get(f"{BASE}/requests/{reserved.pk}").json()["events"][-1]["id"]
        == body["event"]["id"]
    )
    assert staff_client.get(f"{BASE}/inbox", {"bucket": "all"}).json()["counts"]["new"] == 0


def test_a_hostile_file_name_is_cleaned_in_the_response_on_disk_and_in_the_download(
    staff_client, reserved, media
):
    resp = _document(staff_client, reserved.pk, _upload("../../../etc/cron.d/evil\x00.pdf", b"x"))
    assert resp.status_code == 201, resp.content
    (attachment,) = resp.json()["attachments"]
    assert attachment["filename"] == "evil.pdf"
    stored = Attachment.objects.get(pk=attachment["id"])
    assert stored.storage_url.endswith("-evil.pdf") and ".." not in stored.storage_url
    assert (media / stored.storage_url).is_file()
    assert not list(media.parent.glob("etc"))
    dl = staff_client.get(f"{BASE}/attachments/{attachment['id']}/download")
    assert dl.status_code == 200
    assert 'filename="evil.pdf"' in dl["Content-Disposition"]


@pytest.mark.parametrize(
    ("given", "kept"),
    [(". .htaccess", "htaccess"), ("報告" * 150 + ".pdf", None)],
    ids=["leading-dot", "cjk-over-the-byte-cap"],
)
def test_one_sanitised_name_is_the_row_the_stored_path_and_the_download_header(
    staff_client, reserved, media, given, kept
):
    """P8b review F6: the name is made safe once, at ingest, and the database row, the stored
    path and the download header all carry that one string. The reviewer's probe stored
    ". .htaccess" as `.htaccess` in the row but `htaccess` on disk and in the header, and a name of
    300 CJK characters became a 459-byte path segment, more than a Linux filesystem allows."""
    resp = _document(staff_client, reserved.pk, _upload(given, b"x"))
    assert resp.status_code == 201, resp.content
    (attachment,) = resp.json()["attachments"]
    name = attachment["filename"]
    assert kept is None or name == kept
    assert not name.startswith(".")
    assert len(name.encode()) <= storage.MAX_NAME_BYTES

    row = Attachment.objects.get(pk=attachment["id"])
    assert row.filename == name
    segment = row.storage_url.rsplit("/", 1)[-1]
    assert segment.endswith(f"-{name}")
    assert len(segment.encode()) <= 255  # one path segment on ext4 or xfs
    assert (media / row.storage_url).is_file()

    dl = staff_client.get(f"{BASE}/attachments/{attachment['id']}/download")
    assert dl.status_code == 200
    header = dl["Content-Disposition"]
    served = (
        header.split('filename="', 1)[1].split('"', 1)[0]
        if 'filename="' in header
        else unquote(header.split("filename*=utf-8''", 1)[1].split(";", 1)[0])
    )
    assert served == name


def test_a_document_of_type_other_needs_a_channel_and_text_alone_is_enough(
    staff_client, reserved, media
):
    assert _document(staff_client, reserved.pk, _upload(), source_type="other").status_code == 422
    ok = _document(
        staff_client,
        reserved.pk,
        source_type="other",
        channel="in_person",
        display_body="Handed over.",
    )
    assert ok.status_code == 201 and ok.json()["attachments"] == []
    assert ok.json()["event"]["channel"] == "in_person"


def test_add_a_document_refusals(staff_client, reserved, media):
    assert _document(staff_client, reserved.pk, _upload(), source_type="pigeon").status_code == 422
    assert _document(staff_client, reserved.pk).status_code == 422  # nothing to add
    assert _document(staff_client, uuid.uuid4(), _upload()).status_code == 404
    naive = _document(staff_client, reserved.pk, _upload(), received_at="2026-03-02T17:30:00")
    assert naive.status_code == 422 and "time zone" in naive.json()["detail"]
    assert not Source.objects.exists()
    assert [p for p in media.rglob("*") if p.is_file()] == []


def test_a_file_over_the_size_limit_is_422_and_leaves_nothing(staff_client, reserved, media):
    from portal.core.models import Setting

    Setting.objects.update_or_create(
        key="records.upload_max_file_bytes", defaults={"value": 8, "description": "test"}
    )
    resp = _document(staff_client, reserved.pk, _upload("big.pdf", b"123456789"))
    assert resp.status_code == 422 and "larger than the size limit" in resp.json()["detail"]
    assert not Source.objects.exists()
    assert [p for p in media.rglob("*") if p.is_file()] == []


def test_logging_a_phone_call_or_a_meeting_is_the_existing_event_door(staff_client, reserved):
    for channel in ("call", "in_person"):
        resp = post_json(
            staff_client,
            f"{BASE}/requests/{reserved.pk}/events",
            {
                "event_type": "correspondence",
                "channel": channel,
                "direction": "inbound",
                "occurred_at": "2026-03-05T12:00:00Z",
                "display_body": f"Logged by {channel}.",
            },
        )
        assert resp.status_code == 201 and resp.json()["channel"] == channel


# ---- the downloads -----------------------------------------------------------------------------


def test_a_download_is_an_attachment_with_nosniff_and_only_inert_types_are_believed(
    staff_client, reserved, media
):
    body = _document(
        staff_client,
        reserved.pk,
        _upload("letter.pdf", b"%PDF-bytes"),
        _upload("page.html", b"<script>alert(1)</script>", "text/html"),
    ).json()
    pdf, html = body["attachments"]

    served = staff_client.get(f"{BASE}/attachments/{pdf['id']}/download")
    assert served.status_code == 200
    assert b"".join(served.streaming_content) == b"%PDF-bytes"
    assert served["Content-Type"] == "application/pdf"
    assert served["Content-Disposition"].startswith("attachment;")
    assert served["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in served["Cache-Control"]

    hostile = staff_client.get(f"{BASE}/attachments/{html['id']}/download")
    assert hostile["Content-Type"] == "application/octet-stream"
    assert hostile["Content-Disposition"].startswith("attachment;")
    assert hostile["X-Content-Type-Options"] == "nosniff"


def test_a_download_whose_file_is_missing_or_outside_storage_is_404(staff_client, actor, media):
    source = make_source(actor)
    (media.parent / "outside.txt").write_text("secret")
    for name in ("attachments/not-there.pdf", "../outside.txt", "/etc/passwd"):
        attachment = sources_service.add_attachment(
            actor, source.pk, filename="x.pdf", storage_url=name, mime_type="application/pdf"
        )
        resp = staff_client.get(f"{BASE}/attachments/{attachment.pk}/download")
        assert resp.status_code == 404, name
        assert b"secret" not in resp.content
    assert staff_client.get(f"{BASE}/attachments/{uuid.uuid4()}/download").status_code == 404


def test_the_raw_original_of_a_hand_added_document_is_its_manifest(
    staff_client, actor, reserved, media
):
    body = _document(staff_client, reserved.pk, _upload("scan.pdf", b"abc")).json()
    resp = staff_client.get(f"{BASE}/sources/{body['source']['id']}/raw")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/json"
    assert resp["Content-Disposition"].startswith("attachment;")
    assert resp["X-Content-Type-Options"] == "nosniff"
    manifest = json.loads(b"".join(resp.streaming_content))
    assert manifest["files"][0]["sha256"] == hashlib.sha256(b"abc").hexdigest()
    # A source whose original is not in storage (here, a placeholder address) is a 404.
    elsewhere = make_source(actor)
    assert staff_client.get(f"{BASE}/sources/{elsewhere.pk}/raw").status_code == 404
    assert staff_client.get(f"{BASE}/sources/{uuid.uuid4()}/raw").status_code == 404


# ---- "Record as..." ----------------------------------------------------------------------------


def _classify_url(event):
    return f"{BASE}/events/{event.pk}/classify"


@pytest.mark.parametrize("kind", [k.key for k in KINDS])
def test_the_preview_door_and_the_write_door_agree_for_every_kind(kind, staff_client, inbound):
    event, _source, _cover, records = inbound
    row = next(k for k in KINDS if k.key == kind)
    preview = staff_client.get(_classify_url(event), {"kind": kind})
    assert preview.status_code == 200, preview.content
    shown = preview.json()
    assert (shown["kind"], shown["label"]) == (row.key, row.label)
    assert shown["event_type_to"] == row.event_type and shown["status_to"] == row.status
    assert shown["note_required"] is row.note_required
    # Previewing wrote nothing.
    assert Event.objects.get(pk=event.pk).event_type_id == "correspondence"

    written = post_json(
        staff_client,
        _classify_url(event),
        {
            "kind": kind,
            "note": "Because the letter says so.",
            "fee": {"amount_cents": 41250, "description": "Duplication"} if row.takes_fee else None,
            "record_attachment_ids": [str(records.pk)] if row.takes_records else [],
        },
    )
    assert written.status_code == 200, written.content
    done = written.json()
    assert done["event"]["event_type"] == shown["event_type_to"]
    expected_status = shown["status_to"] or shown["status_from"]
    assert done["request"]["current_status"] == expected_status
    if shown["status_to"] is None:
        assert done["status_change"] is None
    else:
        assert done["status_change"]["status"] == shown["status_to"]
        assert done["status_change"]["reason"] == shown["status_reason"]
    assert (done["task"] or {}).get("task_type") == (shown["task"] or {}).get("task_type")
    assert (done["fee"] is not None) is row.takes_fee
    assert [a["role"] for a in done["attachments"]].count("produced_record") == (
        1 if row.takes_records else 0
    )
    # The deadlines it showed are the ones that are still there.
    after = staff_client.get(f"{BASE}/requests/{shown['request_id']}").json()["deadlines"]
    assert sorted(d["id"] for d in after if d["status"] == "active") == sorted(
        d["id"] for d in shown["deadlines"]
    )


def test_recording_a_shared_message_for_a_second_request_keeps_the_first_requests_records(
    staff_client, actor, staff_user, agency, reserved
):
    """Over HTTP: "Record as..." adds to the files marked as records, and only
    `POST /sources/{id}/records` states the whole set (P8b review F1)."""
    other = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="A second request",
        request_text="Another body.",
    )
    source = make_source(actor)
    for_first = add_file(actor, source, "for-first.pdf")
    for_second = add_file(actor, source, "for-second.pdf")
    first, second = attach(actor, reserved, source), attach(actor, other, source)

    def classify(event, attachment):
        resp = post_json(
            staff_client,
            _classify_url(event),
            {"kind": "production_partial", "record_attachment_ids": [str(attachment.pk)]},
        )
        assert resp.status_code == 200, resp.content
        return {a["filename"]: a["role"] for a in resp.json()["attachments"]}

    assert classify(first, for_first) == {
        "for-first.pdf": "produced_record",
        "for-second.pdf": "attachment",
    }
    both = {"for-first.pdf": "produced_record", "for-second.pdf": "produced_record"}
    assert classify(second, for_second) == both

    replaced = post_json(
        staff_client,
        f"{BASE}/sources/{source.pk}/records",
        {"record_attachment_ids": [str(for_second.pk)]},
    )
    assert {a["filename"]: a["role"] for a in replaced.json()} == {
        "for-first.pdf": "attachment",
        "for-second.pdf": "produced_record",
    }


def test_complete_production_by_a_person_is_a_manual_override_and_needs_its_note(
    staff_client, inbound, reserved
):
    event = inbound[0]
    missing = post_json(staff_client, _classify_url(event), {"kind": "production_complete"})
    assert missing.status_code == 422 and "needs a note" in missing.json()["detail"]
    assert Request.objects.get(pk=reserved.pk).current_status_id != "production_received"
    assert Event.objects.get(pk=event.pk).event_type_id == "correspondence"

    ok = post_json(
        staff_client,
        _classify_url(event),
        {"kind": "production_complete", "note": "Checked every page against the index."},
    )
    assert ok.status_code == 200, ok.content
    change = ok.json()["status_change"]
    assert (change["status"], change["reason"]) == ("production_received", "manual_override")
    assert change["triggering_event_id"] is None
    stored = StatusChange.objects.get(pk=change["id"])
    assert "Checked every page" in stored.note


def test_the_events_door_still_refuses_production_received(staff_client, reserved):
    resp = post_json(
        staff_client,
        f"{BASE}/requests/{reserved.pk}/events",
        {
            "event_type": "production",
            "channel": "email",
            "direction": "inbound",
            "occurred_at": "2026-03-20T12:00:00Z",
            "display_body": "Everything responsive is attached.",
            "status": "production_received",
        },
    )
    assert resp.status_code == 403


def test_classify_refusals_map_to_4xx_and_leave_the_request_alone(
    staff_client, inbound, reserved, actor
):
    event, _source, _cover, records = inbound
    status_before = Request.objects.get(pk=reserved.pk).current_status_id

    assert staff_client.get(_classify_url(event), {"kind": "nonsense"}).status_code == 422
    assert staff_client.get(_classify_url(event)).status_code == 422  # kind is required
    stranger = add_file(actor, make_source(actor), "other.pdf")
    bad_file = post_json(
        staff_client,
        _classify_url(event),
        {"kind": "production_partial", "record_attachment_ids": [str(stranger.pk)]},
    )
    assert bad_file.status_code == 422 and "Not files of this message" in bad_file.json()["detail"]
    fee_on_ack = post_json(
        staff_client,
        _classify_url(event),
        {"kind": "acknowledgment", "fee": {"amount_cents": 5}},
    )
    assert fee_on_ack.status_code == 422
    assert post_json(staff_client, _classify_url(event), {"kind": "nope"}).status_code == 422
    unknown = f"{BASE}/events/{uuid.uuid4()}/classify"
    assert staff_client.get(unknown, {"kind": "denial"}).status_code == 404
    assert post_json(staff_client, unknown, {"kind": "denial"}).status_code == 404

    assert Request.objects.get(pk=reserved.pk).current_status_id == status_before
    assert Event.objects.get(pk=event.pk).event_type_id == "correspondence"
    assert Attachment.objects.get(pk=records.pk).role == "attachment"

    events_service.void_event(actor, event.pk, "wrong request")
    voided = post_json(staff_client, _classify_url(event), {"kind": "acknowledgment"})
    assert voided.status_code == 409


# ---- Home, the Calendar, the Rules ---------------------------------------------------------------


def test_home_lists_overdue_due_soon_assigned_and_flagged(
    staff_client, actor, staff_user, reserved, agency, rules
):
    today = timezone.now().date()
    long_ago = deadlines_service.open_deadline(
        actor,
        reserved.pk,
        deadline_type="statutory_determination",
        due_at=date(2020, 1, 6),
        basis="statutory",
    )
    soon = deadlines_service.open_deadline(
        actor,
        reserved.pk,
        deadline_type="production",
        due_at=today + timedelta(days=3),
        basis="agency_commitment",
    )
    deadlines_service.open_deadline(
        actor,
        reserved.pk,
        deadline_type="fee_response",
        due_at=date(2099, 1, 1),
        basis="agency_commitment",
    )
    tasks_service.create_task(
        actor, reserved.pk, "manual", "Call the agency", assigned_to_user_id=staff_user.pk
    )
    flagged = make_source(actor, flag=("delivery_failed", "bounced"))

    resp = staff_client.get(f"{BASE}/home")
    assert resp.status_code == 200, resp.content
    home = resp.json()
    (overdue,) = home["overdue"]
    assert overdue["deadline"]["id"] == str(long_ago.pk)
    assert overdue["tracking_number"] == reserved.tracking_number
    assert overdue["agency_name"] == "Test Agency" and overdue["kind_label"] == "Determination"
    assert overdue["days_from_today"] < 0
    (due,) = home["due_soon"]
    assert due["deadline"]["id"] == str(soon.pk) and 0 <= due["days_from_today"] <= 4
    assert home["due_soon_window_days"] == 7
    assert [t["task"]["description"] for t in home["assigned_tasks"]] == ["Call the agency"]
    assert home["assigned_tasks"][0]["tracking_number"] == reserved.tracking_number
    assert [r["source"]["id"] for r in home["flagged"]["items"]] == [str(flagged.pk)]
    assert home["flagged"]["counts"]["flagged"] == 1


def test_the_calendar_door_takes_a_range(staff_client, actor, reserved, rules):
    inside = deadlines_service.open_deadline(
        actor, reserved.pk, deadline_type="production", due_at=date(2026, 3, 12), basis="statutory"
    )
    deadlines_service.open_deadline(
        actor,
        reserved.pk,
        deadline_type="production",
        due_at=date(2026, 5, 1),
        basis="agency_commitment",
    )
    resp = staff_client.get(f"{BASE}/calendar", {"start": "2026-03-01", "end": "2026-03-31"})
    assert resp.status_code == 200, resp.content
    view = resp.json()
    assert (view["start"], view["end"]) == ("2026-03-01", "2026-03-31")
    assert [r["deadline"]["id"] for r in view["deadlines"]] == [str(inside.pk)]
    (testland,) = view["calendars"]
    assert testland["key"] == "testland" and testland["weekend"] == [5, 6]
    assert testland["holidays"] == []
    january = staff_client.get(
        f"{BASE}/calendar", {"start": "2026-01-01", "end": "2026-01-31"}
    ).json()
    assert january["calendars"][0]["holidays"] == [{"date": "2026-01-13", "name": None}]

    assert staff_client.get(f"{BASE}/calendar", {"start": "2026-03-01"}).status_code == 422
    backwards = staff_client.get(f"{BASE}/calendar", {"start": "2026-03-02", "end": "2026-03-01"})
    assert backwards.status_code == 422 and "before its start" in backwards.json()["detail"]


def test_get_rules_against_the_testlaw_fixtures_returns_their_rules_and_no_others(
    staff_client, rules
):
    resp = staff_client.get(f"{BASE}/rules")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert [r["id"] for r in body["rules"]] == [
        "testlaw.determination.general.2020-01-01",
        "testlaw.production.general.2020-01-01",
        "testlaw.production.widgets.2020-01-01",
    ]
    assert {r["law"] for r in body["rules"]} == {"TESTLAW"}
    widgets = body["rules"][2]
    assert (widgets["provision"], widgets["count"], widgets["unit"]) == (
        "widgets",
        12,
        "business_days",
    )
    assert widgets["citation"].startswith("TESTLAW")
    assert (widgets["effective_from"], widgets["effective_to"]) == ("2020-01-01", None)
    (calendar,) = body["calendars"]
    assert calendar["key"] == "testland" and calendar["timezone"] == "America/Los_Angeles"
    assert calendar["laws"] == ["TESTLAW", "TESTLAW_NORULES"]
    assert calendar["holiday_count"] == 2 and calendar["last_holiday"] == "2026-11-11"
    assert body["missing"] == []


def test_get_rules_is_the_rules_file_passed_through(staff_client):
    """Without the substitution: whatever the shipped file says, the screen says, number for
    number. This asserts the pass-through and says nothing about what any statute provides."""
    from deadline_rules import load_calendars, load_rules

    shipped = load_rules()
    calendars, _ = load_calendars()
    body = staff_client.get(f"{BASE}/rules").json()
    assert [(r["id"], r["count"], r["unit"], r["citation"]) for r in body["rules"]] == [
        (r.id, r.days, r.unit, r.citation) for r in shipped
    ]
    assert {c["key"]: c["holiday_count"] for c in body["calendars"]} == {
        key: len(cal.holidays) for key, cal in calendars.items()
    }


def test_get_rules_lists_the_provisions_with_no_rule_from_open_tasks(
    staff_client, actor, reserved, rules
):
    task = tasks_service.create_task(
        actor, reserved.pk, "enter_deadline_rules", "No production rule for provision 'widgets'."
    )
    (missing,) = staff_client.get(f"{BASE}/rules").json()["missing"]
    assert missing["task"]["id"] == str(task.pk)
    assert missing["tracking_number"] == reserved.tracking_number
    assert "widgets" in missing["task"]["description"]
    tasks_service.complete_task(actor, task.pk)
    assert staff_client.get(f"{BASE}/rules").json()["missing"] == []
