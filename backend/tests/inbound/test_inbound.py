"""Inbound mail: the webhook keeps every delivery, and ingest places or flags it.

Runs as the owner and again as portal_app, the role the webhook and the worker really have.
"""

import hashlib
import io
import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import Client
from portal.records.actors import Actor
from portal.records.inbound import ingest, match
from portal.records.inbound.eml import parse_eml
from portal.records.inbound.pages import count_pdf_pages
from portal.records.inbound.parse import parse_postmark, strip_html
from portal.records.models import Attachment, Event, Source, StatusChange
from portal.records.services import edits as edits_service
from portal.records.services import sources as sources_service

from tests.inbound.conftest import FIXTURE, attachment, deliver, payload_for, pdf_bytes

pytestmark = pytest.mark.usefixtures("as_each_role")

API = "/api/records"


@contextmanager
def freeze(moment):
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


def _ingest(response):
    assert response.status_code == 200, response.content
    return ingest.ingest_source(uuid.UUID(response.json()["source_id"]))


def _jobs() -> list[tuple[str, dict]]:
    with connection.cursor() as cursor:
        cursor.execute("select task_name, args from procrastinate_jobs order by id")
        return [
            (name, args if isinstance(args, dict) else json.loads(args)) for name, args in cursor
        ]


# ---- the webhook ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic not-base64!"},
        {"Authorization": "Bearer postmark:a-test-secret"},
    ],
)
def test_the_webhook_refuses_a_caller_without_the_secret(db, request_one, headers) -> None:
    body = json.dumps(payload_for(request_one.inbound_alias_email))
    response = Client().post(
        "/webhooks/postmark/inbound/", data=body, content_type="application/json", headers=headers
    )
    assert response.status_code == 401
    assert (
        deliver(payload_for(request_one.inbound_alias_email), secret="postmark:wrong").status_code
        == 401
    )
    assert not Source.objects.exists()


def test_an_unset_secret_refuses_everyone_including_an_empty_one(db, settings, request_one) -> None:
    settings.PORTAL = {**settings.PORTAL, "POSTMARK_INBOUND_SECRET": ""}
    assert deliver(payload_for(request_one.inbound_alias_email), secret="").status_code == 401
    assert deliver(payload_for(request_one.inbound_alias_email)).status_code == 401
    assert not Source.objects.exists()


def test_only_post_is_accepted(db) -> None:
    assert Client().get("/webhooks/postmark/inbound/").status_code == 405


def test_a_delivery_is_stored_untouched_and_its_job_deferred_in_the_same_transaction(
    db, request_one, media
) -> None:
    payload = payload_for(request_one.inbound_alias_email)
    body = json.dumps(payload).encode()
    response = deliver(body)
    assert response.status_code == 200 and response.json()["status"] == "stored"

    source = Source.objects.get()
    assert str(source.pk) == response.json()["source_id"]
    assert (source.source_type, source.external_ref) == ("postmark_email", payload["MessageID"])
    assert source.message_id == "<reply-1@agency.example.org>"
    assert source.raw_metadata["from"] == "records@agency.example.org"
    assert source.raw_metadata["subject"] == "RE: Records request"
    assert (media / source.raw_storage_url).read_bytes() == body
    assert source.raw_storage_url.endswith(f"{source.pk}.json")
    # Placed on arrival, before the delivery is acknowledged; its files are the job's to store.
    assert Event.objects.get().request_id == request_one.pk and not source.flagged_for_review
    assert not Attachment.objects.exists() and source.ingested_at is None
    assert _jobs() == [("records.ingest_postmark_source", {"source_id": str(source.pk)})]


def test_a_second_delivery_of_the_same_message_is_a_duplicate(db, request_one) -> None:
    payload = payload_for(request_one.inbound_alias_email)
    first = deliver(payload)
    second = deliver(payload)
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "source_id": first.json()["source_id"]}
    assert Source.objects.count() == 1 and len(_jobs()) == 1


def test_a_delivery_with_no_postmark_id_is_identified_by_its_own_bytes(db, request_one) -> None:
    payload = payload_for(request_one.inbound_alias_email, MessageID="")
    first = deliver(payload)
    assert deliver(payload).json()["status"] == "duplicate"
    source = Source.objects.get()
    assert source.external_ref.startswith("sha256:") and first.json()["status"] == "stored"
    changed = deliver({**payload, "Subject": "A different message"})
    assert changed.json()["status"] == "stored" and Source.objects.count() == 2


@pytest.mark.parametrize("body", [b"not json at all", b"[1, 2, 3]", b'"a string"'])
def test_a_body_that_is_not_a_delivery_is_kept_and_flagged_not_refused(db, media, body) -> None:
    """A 4xx would make Postmark retry and then drop it. It is kept, and a person sees it."""
    response = deliver(body)
    assert response.status_code == 200 and response.json()["status"] == "stored"
    source = Source.objects.get()
    assert (media / source.raw_storage_url).read_bytes() == body
    assert source.flagged_for_review and source.flag_reason == "other"
    assert _jobs() == []
    # The job, if something ran it anyway, finds nothing to extract and changes nothing.
    ingest.ingest_source(source.pk)
    source.refresh_from_db()
    assert source.flag_reason == "other" and not Event.objects.exists()


def test_a_delivery_past_the_size_limit_is_kept_cut_short_and_flagged_not_refused(
    db, request_one, media
) -> None:
    """P20d: a 413 made Postmark give up and the message was lost; now what arrived whole before
    the limit is kept and flagged (tests/inbound/test_p20d_inbound.py has the rest)."""
    from portal.core.models import Setting

    Setting.objects.update_or_create(
        key="records.inbound_max_payload_bytes", defaults={"value": 2000}
    )
    big = payload_for(request_one.inbound_alias_email, TextBody="x" * 5000)
    response = deliver(big)
    assert response.status_code == 200
    source = Source.objects.get(pk=response.json()["source_id"])
    assert source.flag_reason == "attachment_too_large"
    # Only what was kept is stored, and it is no larger than the limit allowed to be read.
    stored = [p for p in media.rglob("*") if p.is_file()]
    assert len(stored) == 1 and stored[0].stat().st_size <= 2000
    assert deliver(payload_for(request_one.inbound_alias_email, Attachments=[])).status_code == 200


def test_a_body_larger_than_djangos_own_cap_is_still_read(db, settings, request_one) -> None:
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 1000
    big = payload_for(request_one.inbound_alias_email, TextBody="x" * 20000)
    assert deliver(big).status_code == 200


# ---- placing ----------------------------------------------------------------------------------


def test_a_message_sent_to_one_alias_lands_on_that_request_as_correspondence(
    db, request_one, request_two
) -> None:
    sent = "Mon, 2 Mar 2026 09:30:00 -0800"
    with freeze(datetime(2026, 3, 2, 18, 0, tzinfo=UTC)):
        result = _ingest(deliver(payload_for(request_one.inbound_alias_email, Date=sent)))
    event = result.event
    assert event is not None and event.request_id == request_one.pk
    assert (event.event_type_id, event.channel, event.direction) == (
        "correspondence",
        "email",
        "inbound",
    )
    assert event.recorded_by_system == "postmark_webhook" and event.recorded_by_user_id is None
    assert event.source_id == result.source.pk
    assert event.display_body.startswith("We acknowledge receipt")
    # 09:30 at -08:00 in the header; the same instant, stored as an instant.
    stored = Event.objects.get(pk=event.pk).occurred_at
    assert stored.astimezone(UTC).isoformat() == "2026-03-02T17:30:00+00:00"
    assert not result.source.flagged_for_review
    # Nothing automated says what the message MEANS: no status moved.
    request_one.refresh_from_db()
    assert request_one.current_status_id == "pending_filing"
    assert StatusChange.objects.filter(request_id=request_one.pk).count() == 1


def test_the_alias_is_found_in_cc_bcc_and_the_original_recipient_too(db, request_one) -> None:
    alias = request_one.inbound_alias_email
    for index, changes in enumerate(
        (
            {"CcFull": [{"Email": alias.upper(), "Name": ""}]},
            {"BccFull": [{"Email": alias, "Name": ""}]},
            {"OriginalRecipient": alias},
            {"To": f"Someone <{alias}>", "ToFull": []},
        )
    ):
        payload = payload_for("someone@example.org", MessageID=f"where-{index}", **changes)
        assert _ingest(deliver(payload)).event.request_id == request_one.pk, changes


def test_a_message_to_no_alias_is_flagged_and_placed_nowhere(db, request_one) -> None:
    for index, to in enumerate(
        (
            "info@example.org",
            # The right shape at the right domain, but nobody's.
            "zzzz-zzzz-zzzz@requests.thesclu.org",
            # A real alias at the wrong domain is not an alias.
            request_one.inbound_alias_email.replace("requests.thesclu.org", "evil.example.org"),
        )
    ):
        result = _ingest(deliver(payload_for(to, MessageID=f"nowhere-{index}")))
        assert result.event is None, to
        assert (result.source.flagged_for_review, result.source.flag_reason) == (
            True,
            "no_matching_request",
        )
    assert not Event.objects.exists()


def test_a_message_sent_to_two_requests_is_placed_on_neither(db, request_one, request_two) -> None:
    result = _ingest(
        deliver(payload_for(request_one.inbound_alias_email, request_two.inbound_alias_email))
    )
    assert result.event is None and not Event.objects.exists()
    assert result.source.flag_reason == "may_cover_others"
    assert "2 requests" in result.source.flag_note


def test_a_message_that_also_mentions_another_request_is_placed_and_flagged(
    db, request_one, request_two, owner
) -> None:
    edits_service.edit_with_audit(
        owner, "requests", request_two.pk, "agency_reference_number", "PRR-2026-0042", "From them."
    )
    by_alias = payload_for(
        request_one.inbound_alias_email,
        MessageID="mentions-alias",
        TextBody=f"See also our reply to {request_two.inbound_alias_email}.",
    )
    by_reference = payload_for(
        request_one.inbound_alias_email,
        MessageID="mentions-reference",
        TextBody="This also responds to prr-2026-0042, sent last week.",
    )
    for payload in (by_alias, by_reference):
        result = _ingest(deliver(payload))
        assert result.event.request_id == request_one.pk
        assert result.source.flag_reason == "may_cover_others"
        # The note names no request: it is read by everyone who can see the message.
        assert request_two.tracking_number not in result.source.flag_note
        assert [c.pk for c in ingest.candidates(result.source)] == [request_two.pk]
    # A number that merely contains the reference, or a short one, is not a mention.
    quiet = payload_for(
        request_one.inbound_alias_email,
        MessageID="no-mention",
        TextBody="Invoice XPRR-2026-00421 attached.",
    )
    assert _ingest(deliver(quiet)).source.flagged_for_review is False


def test_ingest_is_idempotent(db, request_one) -> None:
    response = deliver(
        payload_for(request_one.inbound_alias_email, Attachments=[attachment("a.txt", b"one")])
    )
    first = _ingest(response)
    again = _ingest(response)
    assert first.ingested is True and again.ingested is False
    assert Event.objects.count() == 1 and Attachment.objects.count() == 1


def test_an_html_only_message_is_stored_as_readable_text(db, request_one) -> None:
    html = (
        "<html><head><style>p{color:red}</style></head><body><p>Dear requester,</p>"
        "<p>Your request is <b>granted</b> &amp; attached.<br>Regards</p>"
        "<script>alert(1)</script></body></html>"
    )
    result = _ingest(
        deliver(payload_for(request_one.inbound_alias_email, TextBody="", HtmlBody=html))
    )
    assert result.event.display_body == (
        "Dear requester,\nYour request is granted & attached.\nRegards"
    )
    assert strip_html("") == ""


def test_a_message_with_no_date_or_a_date_with_no_zone_is_dated_when_it_was_captured(
    db, request_one
) -> None:
    for index, value in enumerate(("", "Mon, 2 Mar 2026 09:30:00", "not a date")):
        payload = payload_for(
            request_one.inbound_alias_email, MessageID=f"date-{index}", Date=value
        )
        result = _ingest(deliver(payload))
        assert result.event.occurred_at == result.source.captured_at, value


def test_a_message_with_no_message_id_header_is_still_placed(db, request_one) -> None:
    result = _ingest(deliver(payload_for(request_one.inbound_alias_email, Headers=[])))
    assert result.event is not None and result.source.message_id is None


# ---- attachments ------------------------------------------------------------------------------


def test_attachments_are_stored_with_their_hash_size_type_and_page_count(
    db, request_one, media
) -> None:
    three_pages = pdf_bytes(3)
    files = [
        attachment("Response Letter.pdf", three_pages, "application/pdf"),
        attachment("notes.txt", b"plain notes", "text/plain"),
        attachment("broken.pdf", b"%PDF-1.7 this is not really a pdf", "application/pdf"),
        attachment("lies.pdf", b"just text with a pdf name", "application/pdf"),
    ]
    result = _ingest(deliver(payload_for(request_one.inbound_alias_email, Attachments=files)))
    rows = {a.filename: a for a in Attachment.objects.filter(source_id=result.source.pk)}
    assert set(rows) == {"Response Letter.pdf", "notes.txt", "broken.pdf", "lies.pdf"}
    letter = rows["Response Letter.pdf"]
    assert letter.checksum_sha256 == hashlib.sha256(three_pages).hexdigest()
    assert (letter.size_bytes, letter.mime_type, letter.page_count) == (
        len(three_pages),
        "application/pdf",
        3,
    )
    assert (media / letter.storage_url).read_bytes() == three_pages
    assert letter.storage_url.startswith(
        f"attachments/{result.source.pk}/{letter.checksum_sha256[:16]}-"
    )
    assert rows["notes.txt"].page_count is None
    # A file that cannot be counted gets null, and the message is still ingested.
    assert rows["broken.pdf"].page_count is None and rows["lies.pdf"].page_count is None
    assert result.event is not None


def test_counting_pages_never_raises() -> None:
    assert count_pdf_pages(pdf_bytes(1)) == 1
    for junk in (b"", b"%PDF-", b"%PDF-1.4\n%%EOF", b"\x00" * 50, io.BytesIO(b"x").getvalue()):
        assert count_pdf_pages(junk) is None


@pytest.mark.parametrize(
    "name",
    ["../../../etc/passwd", "..\\..\\evil.exe", "/abs/olute.pdf", ". .htaccess", "", "a\x00b‮.pdf"],
)
def test_an_attachments_name_cannot_choose_where_it_is_stored(db, request_one, media, name) -> None:
    result = _ingest(
        deliver(payload_for(request_one.inbound_alias_email, Attachments=[attachment(name, b"x")]))
    )
    (row,) = result.attachments
    stored = (media / row.storage_url).resolve()
    assert stored.is_file() and stored.parent == (media / "attachments" / str(result.source.pk))
    assert "/" not in row.filename and "\\" not in row.filename and not row.filename.startswith(".")
    assert row.filename and "\x00" not in row.filename


def test_bad_base64_in_an_attachment_does_not_lose_the_message(db, request_one) -> None:
    broken = {"Name": "x.bin", "Content": "!!!not base64!!!", "ContentType": "x/y"}
    result = _ingest(deliver(payload_for(request_one.inbound_alias_email, Attachments=[broken, 7])))
    assert result.event is not None and len(result.attachments) == 1


# ---- the import boundary ----------------------------------------------------------------------

EML = b"""From: Records Unit <Records@Agency.Example.org>
To: SCLU <%(alias)s>
Subject: RE: Records request
Message-ID: <reply-1@agency.example.org>
In-Reply-To: <original-1@requests.example.test>
References: <original-0@requests.example.test> <original-1@requests.example.test>
Date: Mon, 2 Mar 2026 09:30:00 -0800
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="b1"

--b1
Content-Type: multipart/alternative; boundary="b2"

--b2
Content-Type: text/plain; charset="utf-8"

We acknowledge receipt of your request.

Records Unit
--b2
Content-Type: text/html; charset="utf-8"

<html><body><p>We acknowledge receipt of your request.</p><p>Records Unit</p></body></html>
--b2--
--b1
Content-Type: text/plain
Content-Disposition: attachment; filename="acknowledgment.txt"
Content-Transfer-Encoding: base64

UmVjZWl2ZWQuIFRoYW5rIHlvdS4K
--b1--
"""


def test_an_eml_file_parses_to_the_same_message_as_the_postmark_delivery(db, request_one) -> None:
    alias = request_one.inbound_alias_email
    from_json = parse_postmark(json.loads(FIXTURE.read_text().replace("ALIAS_PLACEHOLDER", alias)))
    from_eml = parse_eml(EML % {b"alias": alias.encode()})
    for name in (
        "message_id",
        "from_addr",
        "to",
        "cc",
        "bcc",
        "subject",
        "date",
        "in_reply_to",
        "references",
    ):
        assert getattr(from_eml, name) == getattr(from_json, name), name
    assert from_eml.text_body.strip() == from_json.text_body.strip()
    assert [(a.name, a.content) for a in from_eml.attachments] == [
        (a.name, a.content) for a in from_json.attachments
    ]
    assert from_eml.external_ref is None
    assert match.find_aliases(from_eml) == match.find_aliases(from_json) == {alias}


def test_importing_an_eml_places_it_like_delivered_mail_and_importing_it_twice_is_one_source(
    db, request_one, staff_user, media
) -> None:
    data = EML % {b"alias": request_one.inbound_alias_email.encode()}
    result = ingest.import_eml(Actor.human(staff_user), data)
    assert result.source.source_type == "imported_eml"
    assert (media / result.source.raw_storage_url).read_bytes() == data
    assert result.event.request_id == request_one.pk
    assert result.event.recorded_by_user_id == staff_user.pk
    assert [a.filename for a in result.attachments] == ["acknowledgment.txt"]
    again = ingest.import_eml(Actor.human(staff_user), data)
    assert again.ingested is False and again.source.pk == result.source.pk
    assert Source.objects.count() == 1 and Event.objects.count() == 1


def test_a_file_that_is_not_mail_at_all_is_kept_and_flagged(db, staff_user) -> None:
    result = ingest.import_eml(Actor.human(staff_user), b"\x00\x01 nothing like an email")
    assert result.event is None and result.source.flag_reason == "no_matching_request"


# ---- privileged requests ------------------------------------------------------------------------


def test_mail_for_a_privileged_request_lands_and_is_hidden_from_people_off_its_list(
    db, sealed_request, request_one, staff_user, colleague_user
) -> None:
    """Ingest is a system actor and needs no place on the list. The message borrows the request's
    visibility from the moment it lands."""
    insider, outsider = Client(), Client()
    insider.force_login(staff_user)
    outsider.force_login(colleague_user)
    delivered = deliver(
        payload_for(sealed_request.inbound_alias_email, Subject="SEALED subject line")
    )
    source_id = delivered.json()["source_id"]
    # Review F3: BEFORE the job has run. The message is on its request from the moment it
    # exists, so it was never unattached mail that all staff may open.
    assert Event.objects.get().request_id == sealed_request.pk
    for path in (f"{API}/sources/{source_id}", f"{API}/sources/{source_id}/raw"):
        assert outsider.get(path).status_code == 403, path
    before = outsider.get(f"{API}/inbox?bucket=all&q=SEALED").content.decode()
    assert outsider.get(f"{API}/inbox?bucket=all").json()["total"] == 0 and "SEALED" not in before
    assert insider.get(f"{API}/inbox?bucket=new").json()["total"] == 1

    result = _ingest(delivered)
    assert result.event.request_id == sealed_request.pk
    assert insider.get(f"{API}/sources/{result.source.pk}").status_code == 200
    assert outsider.get(f"{API}/sources/{result.source.pk}").status_code == 403
    assert outsider.get(f"{API}/inbox?bucket=all").json()["total"] == 0
    assert insider.get(f"{API}/inbox?bucket=new").json()["total"] == 1


def test_candidates_are_worked_out_for_the_person_asking_and_never_stored(
    db, sealed_request, request_one, request_two, staff_user, colleague_user
) -> None:
    payload = payload_for(
        request_one.inbound_alias_email,
        TextBody=(
            f"Also see {sealed_request.inbound_alias_email} and {request_two.inbound_alias_email}."
        ),
    )
    result = _ingest(deliver(payload))
    source = Source.objects.get(pk=result.source.pk)
    stored = json.dumps([source.flag_note, source.raw_metadata])
    assert sealed_request.tracking_number not in stored and str(sealed_request.pk) not in stored

    insider, outsider = Client(), Client()
    insider.force_login(staff_user)
    outsider.force_login(colleague_user)
    path = f"{API}/sources/{source.pk}/candidates"
    seen_by_insider = {c["tracking_number"] for c in insider.get(path).json()}
    seen_by_outsider = outsider.get(path)
    assert seen_by_insider == {sealed_request.tracking_number, request_two.tracking_number}
    assert seen_by_outsider.status_code == 200
    assert [c["tracking_number"] for c in seen_by_outsider.json()] == [request_two.tracking_number]
    assert "SEALED" not in seen_by_outsider.content.decode()
    # A source that is not mail has no candidates.
    plain = sources_service.register_source(
        Actor.human(staff_user), source_type="other", raw_storage_url="test://nothing"
    )
    assert insider.get(f"{API}/sources/{plain.pk}/candidates").json() == []


# ---- the replay command -------------------------------------------------------------------


def test_webhook_replay_goes_through_the_real_view_and_runs_the_job(
    db, request_one, tmp_path, capsys
) -> None:
    saved = tmp_path / "delivery.json"
    saved.write_text(json.dumps(payload_for(request_one.inbound_alias_email)))
    out = io.StringIO()
    call_command("webhook_replay", str(saved), stdout=out)
    printed = out.getvalue()
    assert "webhook: 200" in printed and "flagged: False" in printed
    assert f"on {request_one.tracking_number}" in printed and "files:   1" in printed
    out = io.StringIO()
    call_command("webhook_replay", str(saved), stdout=out)
    assert "duplicate" in out.getvalue() and "already ingested" in out.getvalue()
    assert f"on {request_one.tracking_number}" in out.getvalue()
    assert Event.objects.count() == 1


# ---- found by the second-pass review (notes/portal/2026-10-05_p5_review_reviewer.md) ------------


@pytest.mark.parametrize(
    "changes",
    [
        {"Subject": "nul \x00 in the subject"},
        {"Subject": "a lone surrogate \ud800 here"},
        {"MessageID": "id-with-\x00-nul"},
        {"Headers": [{"Name": "Message-ID", "Value": "<a\x00b@example.org>"}]},
        {"FromFull": {"Email": "x\x00y@example.org", "Name": ""}},
        {"TextBody": "body with \x00 and \udfff"},
        {"TextBody": "", "HtmlBody": "<p>html \x00 only \ud800</p>"},
    ],
)
def test_hostile_characters_never_turn_a_delivery_into_an_error(db, request_one, changes) -> None:
    """F1. A 500 makes Postmark retry and then drop the message. These are characters Postgres
    will not store; the sender chooses the subject, the addresses and the ids."""
    response = deliver(payload_for(request_one.inbound_alias_email, **changes))
    assert response.status_code == 200, response.content
    result = _ingest(response)
    assert result.event is not None and result.event.request_id == request_one.pk
    assert "\x00" not in json.dumps(result.source.raw_metadata) + result.event.display_body


def test_json_nested_past_the_parsers_depth_is_kept_and_flagged(db) -> None:
    for body in (b"[" * 200000, b'{"a":' * 100000):
        response = deliver(body)
        assert response.status_code == 200, response.content
    assert Source.objects.filter(flag_reason="other").count() == 2


def test_a_delivery_with_no_content_length_or_no_body_is_a_transport_error_not_a_message(
    db, request_one
) -> None:
    """F9. Django reads nothing without a length; the empty body's hash would become "the"
    delivery and every later one a duplicate of it."""
    from tests.inbound.conftest import URL, basic

    body = json.dumps(payload_for(request_one.inbound_alias_email)).encode()
    headers = basic()
    no_length = Client().generic(
        "POST", URL, body, "application/json", headers=headers, CONTENT_LENGTH=""
    )
    empty = Client().post(URL, data=b"", content_type="application/json", headers=headers)
    assert (no_length.status_code, empty.status_code) == (411, 411)
    assert not Source.objects.exists()


def test_the_job_failing_for_the_last_time_flags_the_message_and_it_stays_on_its_request(
    db, request_one, monkeypatch
) -> None:
    """F2. No retry was configured, and a failed job left the message in no bucket at all."""
    from portal.records import jobs

    source_id = deliver(
        payload_for(request_one.inbound_alias_email, Attachments=[attachment("a.txt", b"x")])
    ).json()["source_id"]

    def explode(_source_id):
        raise RuntimeError("storage is down")

    monkeypatch.setattr(jobs.ingest, "ingest_source", explode)
    early = SimpleNamespace(job=SimpleNamespace(attempts=0))
    with pytest.raises(RuntimeError):
        jobs.ingest_postmark_source(early, source_id)
    assert not Source.objects.get(pk=source_id).flagged_for_review, "not yet: it will be retried"

    last = SimpleNamespace(job=SimpleNamespace(attempts=jobs.INGEST_ATTEMPTS - 1))
    jobs.ingest_postmark_source(last, source_id)  # ends quietly
    source = Source.objects.get(pk=source_id)
    assert source.flag_reason == "other" and "could not be extracted" in source.flag_note
    assert source.ingested_at is None, "still owed, so a later run does the work"
    assert Event.objects.get().request_id == request_one.pk

    monkeypatch.undo()
    assert len(ingest.ingest_source(uuid.UUID(source_id)).attachments) == 1


def test_whatever_a_person_does_first_the_job_still_extracts_the_files(
    db, request_one, staff_user
) -> None:
    """F7. "Already ingested" was inferred from flagged, reviewed or attached, all of which a
    person can do in the moment before the job runs."""
    actor = Actor.human(staff_user)
    for index, act in enumerate(
        (
            lambda pk: sources_service.flag_source(actor, pk, "odd", "other"),
            lambda pk: sources_service.mark_source_reviewed(actor, pk),
            lambda pk: sources_service.add_source_note(actor, pk, "a note"),
        )
    ):
        payload = payload_for(
            request_one.inbound_alias_email,
            MessageID=f"human-first-{index}",
            Attachments=[attachment("a.txt", b"x")],
        )
        source_id = uuid.UUID(deliver(payload).json()["source_id"])
        act(source_id)
        result = ingest.ingest_source(source_id)
        assert result.ingested is True and len(result.attachments) == 1, index
        assert ingest.ingest_source(source_id).ingested is False


@pytest.mark.parametrize(
    "date",
    [
        "Fri, 31 Dec 9999 23:59:59 -1400",
        "Mon, 1 Jan 2101 00:00:00 +0000",
        "Thu, 1 Jan 1970 00:00:00 +0000",
        format_datetime(datetime.now(UTC) - timedelta(days=60)),
    ],
)
def test_a_date_that_cannot_be_right_is_not_written_to_the_ledger(
    db, request_one, staff_user, date
) -> None:
    """F4. The sender writes the Date header. Year 9999 at -14:00 stored as year 10000, could not
    be read back, and every page listing the event returned 500, on an append-only table."""
    result = _ingest(deliver(payload_for(request_one.inbound_alias_email, Date=date)))
    assert result.event.occurred_at == result.source.captured_at
    assert result.source.flag_reason == "other" and "not believable" in result.source.flag_note
    client = Client()
    client.force_login(staff_user)
    assert client.get(f"{API}/requests/{request_one.pk}").status_code == 200
    assert client.get(f"{API}/inbox?bucket=all").status_code == 200


def test_an_imported_file_may_be_old_but_not_from_the_future(db, request_one, staff_user) -> None:
    data = EML % {b"alias": request_one.inbound_alias_email.encode()}
    old = ingest.import_eml(Actor.human(staff_user), data.replace(b"2 Mar 2026", b"2 Mar 2019"))
    assert old.event.occurred_at.year == 2019 and not old.source.flagged_for_review
    future = data.replace(b"2 Mar 2026", b"2 Mar 2099").replace(b"reply-1@", b"reply-2@")
    later = ingest.import_eml(Actor.human(staff_user), future)
    assert later.event.occurred_at == later.source.captured_at and later.source.flagged_for_review


@pytest.mark.parametrize(
    "html",
    [
        "<br" + " " * 200_000,
        "<script " * 30_000,
        "<" * 200_000,
        "<p>" * 100_000,
        "<style>" + "x" * 300_000,
        "&amp;" * 100_000,
    ],
)
def test_stripping_html_takes_time_in_proportion_to_its_length(html) -> None:
    """F5. Two of the first version's patterns were quadratic; a megabyte took minutes, inside
    the worker's transaction. Linear means these finish at once."""
    import time

    started = time.perf_counter()
    strip_html(html)
    assert time.perf_counter() - started < 2.0


def test_stripping_html_still_reads_properly() -> None:
    assert strip_html("<p>One</p><p>Two<br/>Three</p><div>Four</div>") == "One\nTwo\nThree\nFour"
    assert strip_html("a<script>x()</script>b<STYLE>p{}</STYLE>c") == "a b c"
    assert strip_html("keep <b>this</b> &lt;tag&gt;") == "keep this <tag>"
    assert strip_html("text then <unclosed") == "text then"
    assert strip_html("before<script>never closed") == "before"


def test_a_guess_at_a_privileged_requests_reference_number_changes_nothing_anyone_can_see(
    db, sealed_request, request_one, owner, staff_user, colleague_user
) -> None:
    """F10. Anyone can send mail here. The stored flag and note must not differ between a right
    guess and a wrong one; people on the list still get the candidate."""
    edits_service.edit_with_audit(
        owner, "requests", sealed_request.pk, "agency_reference_number", "SEAL-77881", "From them."
    )
    seen = []
    for index, guess in enumerate(("SEAL-77881", "SEAL-00000")):
        payload = payload_for(
            request_one.inbound_alias_email, MessageID=f"guess-{index}", TextBody=f"Re {guess}."
        )
        source = _ingest(deliver(payload)).source
        seen.append((source.flagged_for_review, source.flag_reason, source.flag_note))
    assert seen[0] == seen[1] == (False, None, None)
    right = Source.objects.get(external_ref="guess-0")
    insider = Client()
    insider.force_login(staff_user)
    listed = insider.get(f"{API}/sources/{right.pk}/candidates").json()
    assert [c["tracking_number"] for c in listed] == [sealed_request.tracking_number]


def test_a_damaged_attachment_is_kept_as_far_as_it_goes_and_the_message_is_flagged(
    db, request_one
) -> None:
    """F11."""
    files = [
        {"Name": "unpadded.txt", "Content": "aGVsbG8", "ContentType": "text/plain"},
        {**attachment("short.txt", b"hello"), "ContentLength": 500},
    ]
    result = _ingest(deliver(payload_for(request_one.inbound_alias_email, Attachments=files)))
    assert (
        result.source.flag_reason == "other" and "did not arrive whole" in result.source.flag_note
    )
    assert {a.filename: a.size_bytes for a in result.attachments} == {
        "unpadded.txt": 5,
        "short.txt": 5,
    }
    assert result.event is not None


def test_mail_for_a_request_that_is_closed_lands_on_it_and_is_flagged(
    db, request_one, owner
) -> None:
    """F13. It is that request's mail, so it goes there; but nobody is watching that request."""
    from portal.records.services import status as status_service

    status_service.override_status(owner, request_one.pk, "withdrawn", "Not needed.")
    result = _ingest(deliver(payload_for(request_one.inbound_alias_email)))
    assert result.event.request_id == request_one.pk
    assert result.source.flag_reason == "other" and "closed or withdrawn" in result.source.flag_note
    request_one.refresh_from_db()
    assert request_one.current_status_id == "withdrawn", "and still nothing automated moved it"


def test_a_message_that_is_only_files_says_which(db, request_one) -> None:
    """F14."""
    payload = payload_for(
        request_one.inbound_alias_email,
        TextBody="",
        HtmlBody="",
        Attachments=[attachment("one.pdf", b"x"), attachment("two.pdf", b"y")],
    )
    assert _ingest(deliver(payload)).event.display_body == "Attachments: one.pdf, two.pdf"


def test_a_forwarded_message_inside_an_eml_is_kept_and_a_delivered_one_is_not_imported_twice(
    db, request_one, staff_user
) -> None:
    """F12."""
    inner = b"From: a@example.org\nSubject: inner\n\nInner body.\n"
    outer = (
        b"From: b@example.org\nTo: nobody@example.org\nSubject: fwd\n"
        b"Message-ID: <fwd-1@example.org>\nMIME-Version: 1.0\n"
        b'Content-Type: multipart/mixed; boundary="b"\n\n'
        b"--b\nContent-Type: text/plain\n\nSee attached.\n"
        b"--b\nContent-Type: message/rfc822\n"
        b'Content-Disposition: attachment; filename="fwd.eml"\n\n' + inner + b"--b--\n"
    )
    (forwarded,) = parse_eml(outer).attachments
    assert b"Inner body." in forwarded.content and forwarded.size > 0

    delivered = _ingest(deliver(payload_for(request_one.inbound_alias_email)))
    same = EML % {b"alias": request_one.inbound_alias_email.encode()}
    again = ingest.import_eml(Actor.human(staff_user), same)
    assert again.ingested is False and again.source.pk == delivered.source.pk
    assert Event.objects.count() == 1


def test_a_message_can_be_read_before_it_is_attached_to_anything(
    db, staff_user, colleague_user
) -> None:
    """P4c. An unattached message has no event to carry its text, so the Inbox reads it from the
    stored original. Plain text only."""
    payload = payload_for(
        "nobody@example.org", TextBody="", HtmlBody="<p>Hello <b>there</b></p><script>x()</script>"
    )
    source_id = deliver(payload).json()["source_id"]
    client = Client()
    client.force_login(staff_user)
    body = client.get(f"{API}/sources/{source_id}/message").json()
    assert body["readable"] is True and body["body"] == "Hello there"
    assert (body["from_addr"], body["subject"]) == (
        "records@agency.example.org",
        "RE: Records request",
    )
    plain = sources_service.register_source(
        Actor.human(staff_user), source_type="other", raw_storage_url="test://nothing"
    )
    assert client.get(f"{API}/sources/{plain.pk}/message").json()["readable"] is False
