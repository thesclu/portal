"""P20d: the two flag reasons the inbound pipeline now tells apart, and what "Show original" reads.

`reply_to_ignored`: mail sent to no request's address that answers mail the portal sent (its
In-Reply-To or References carry the provider's id of a sent message). `attachment_too_large`: a
delivery past the size the webhook reads, kept without its files instead of being refused.
Synthetic addresses and text only.
"""

import json
import uuid

import pytest
from django.test import Client
from portal.core.models import Setting
from portal.records.actors import Actor
from portal.records.inbound import ingest, match
from portal.records.inbound.eml import parse_eml
from portal.records.inbound.parse import parse_postmark
from portal.records.inbound.postmark import salvage
from portal.records.models import Attachment, Event, Source
from portal.records.services import events as events_service
from portal.records.services import sources as sources_service

from tests.inbound.conftest import attachment, deliver, payload_for

pytestmark = pytest.mark.usefixtures("as_each_role")

API = "/api/records"
PROVIDER_ID = "b8e563c2-054c-4ae9-b1fd-a1ad67b9096b"


def _sent_on(request, owner, provider_id: str = PROVIDER_ID) -> Source:
    """A message the portal sent on `request`, as `mail.send.deliver` records it."""
    source = sources_service.register_source(
        owner,
        source_type="sent_email",
        raw_storage_url="test://sent",
        external_ref=f"sent:{provider_id}",
        raw_metadata={"subject": "Synthetic filing"},
    )
    events_service.record_event(
        owner,
        request.pk,
        event_type="correspondence",
        channel="email",
        direction="outbound",
        occurred_at=source.captured_at,
        display_body="Synthetic filing.",
        source_id=source.pk,
    )
    return source


def _reply_headers(provider_id: str = PROVIDER_ID) -> list[dict]:
    return [
        {"Name": "Message-ID", "Value": "<reply-9@agency.example.org>"},
        {"Name": "In-Reply-To", "Value": f"<{provider_id}@mtasv.net>"},
        {"Name": "References", "Value": f"<{provider_id}@mtasv.net>"},
    ]


# ---- reply_to_ignored -----------------------------------------------------------------------


def test_a_reply_to_our_mail_sent_to_another_address_is_flagged_reply_to_ignored(
    db, owner, request_one, request_two, staff_user
) -> None:
    _sent_on(request_one, owner)
    payload = payload_for("someone@another.example.org", Headers=_reply_headers())
    response = deliver(payload)
    assert response.status_code == 200, response.content
    source = Source.objects.get(pk=response.json()["source_id"])
    assert (source.flagged_for_review, source.flag_reason) == (True, "reply_to_ignored")
    assert source.flag_note == match.REPLY_TO_NOTE
    # Not placed: a header is the sender's word, not proof (match's docstring).
    assert not Event.objects.filter(source_id=source.pk).exists()
    # The note names no request; the candidates do, for a person who may see it.
    assert request_one.tracking_number not in (source.flag_note or "")
    client = Client()
    client.force_login(staff_user)
    found = client.get(f"{API}/sources/{source.pk}/candidates").json()
    assert [c["tracking_number"] for c in found] == [request_one.tracking_number]


def test_mail_to_no_address_that_answers_nothing_we_sent_is_still_no_matching_request(
    db, owner, request_one
) -> None:
    _sent_on(request_one, owner)
    stranger = payload_for(
        "someone@another.example.org",
        Headers=_reply_headers("11111111-2222-4333-8444-555555555555"),
    )
    source = Source.objects.get(pk=deliver(stranger).json()["source_id"])
    assert source.flag_reason == "no_matching_request"


def test_a_reply_that_reaches_the_alias_is_placed_whatever_its_headers_say(
    db, owner, request_one
) -> None:
    _sent_on(request_one, owner)
    response = deliver(payload_for(request_one.inbound_alias_email, Headers=_reply_headers()))
    source = Source.objects.get(pk=response.json()["source_id"])
    assert not source.flagged_for_review
    assert Event.objects.filter(source_id=source.pk, request_id=request_one.pk).exists()


def test_the_stored_flag_never_depends_on_a_privileged_request(
    db, owner, sealed_request, staff_user, colleague_user
) -> None:
    _sent_on(sealed_request, owner)
    response = deliver(payload_for("someone@another.example.org", Headers=_reply_headers()))
    source = Source.objects.get(pk=response.json()["source_id"])
    # Whether it is flagged reply_to_ignored must not tell anyone the request is privileged.
    assert source.flag_reason == "no_matching_request"
    insider, outsider = Client(), Client()
    insider.force_login(staff_user)
    outsider.force_login(colleague_user)
    path = f"{API}/sources/{source.pk}/candidates"
    assert [c["tracking_number"] for c in insider.get(path).json()] == [
        sealed_request.tracking_number
    ]
    assert outsider.get(path).json() == []


# ---- attachment_too_large ----------------------------------------------------------------------


def _limit(value: int) -> None:
    Setting.objects.update_or_create(
        key="records.inbound_max_payload_bytes", defaults={"value": value}
    )


def test_a_delivery_past_the_limit_is_kept_without_its_files_and_flagged(
    db, request_one, media
) -> None:
    payload = payload_for(
        request_one.inbound_alias_email,
        Attachments=[attachment("records.bin", b"x" * 6000)],
    )
    _limit(len(json.dumps(payload)) // 2)
    response = deliver(payload)
    assert response.status_code == 200, response.content
    source = Source.objects.get(pk=response.json()["source_id"])
    assert (source.flagged_for_review, source.flag_reason) == (True, "attachment_too_large")
    assert source.flag_note == ingest.TOO_LARGE_NOTE
    # Placed by its address like any other message.
    assert Event.objects.filter(source_id=source.pk, request_id=request_one.pk).exists()
    # Readable: sender, subject and text survive; the files do not.
    message = ingest.load_message(source)
    assert message is not None
    assert message.subject == payload["Subject"] and message.text_body == payload["TextBody"]
    assert message.attachments == []
    ingest.ingest_source(source.pk)
    assert not Attachment.objects.filter(source_id=source.pk).exists()
    source.refresh_from_db()
    assert source.flag_reason == "attachment_too_large"


def test_a_delivery_past_the_limit_that_cannot_be_read_is_still_kept_and_flagged(
    db, request_one, media
) -> None:
    _limit(64)
    response = deliver(b"not json at all, and much longer than sixty-four bytes " * 4)
    assert response.status_code == 200, response.content
    source = Source.objects.get(pk=response.json()["source_id"])
    assert source.flag_reason == "attachment_too_large"
    assert source.flag_note == ingest.TOO_LARGE_UNREADABLE_NOTE
    assert not Event.objects.filter(source_id=source.pk).exists()


def test_salvage_keeps_every_whole_field_but_the_files() -> None:
    whole = {"From": "a@example.org", "Subject": "S", "TextBody": "T", "Attachments": [1, 2]}
    text = json.dumps(whole).encode()
    assert salvage(text) == {"From": "a@example.org", "Subject": "S", "TextBody": "T"}
    # Cut inside a value: the fields before it are kept, the cut one is not.
    cut = json.dumps({"From": "a@example.org", "TextBody": "y" * 100}).encode()[:40]
    assert salvage(cut) == {"From": "a@example.org"}
    # A multi-byte character cut in half at the end is not an error.
    assert salvage(json.dumps({"Subject": "é" * 10}, ensure_ascii=False).encode()[:-3]) == {}
    assert salvage(b"[1, 2]") is None and salvage(b"") is None


# ---- the sender's name and the original's headers ----------------------------------------------


def test_the_sender_name_and_every_header_are_read(db, request_one, staff_user) -> None:
    payload = payload_for(request_one.inbound_alias_email, Headers=_reply_headers())
    message = parse_postmark(payload)
    assert message.from_name == "Records Unit"
    assert message.header_list[0] == ("Message-ID", "<reply-9@agency.example.org>")
    eml = parse_eml(
        b'From: "Records Desk" <desk@agency.example.org>\r\nTo: x@example.org\r\n'
        b"Subject: Hi\r\nX-Custom: one\r\n\r\nBody\r\n"
    )
    assert eml.from_name == "Records Desk"
    assert [name for name, _ in eml.header_list] == ["From", "To", "Subject", "X-Custom"]

    source = Source.objects.get(pk=deliver(payload).json()["source_id"])
    assert source.raw_metadata["from_name"] == "Records Unit"
    client = Client()
    client.force_login(staff_user)
    body = client.get(f"{API}/sources/{source.pk}/message").json()
    assert body["from_name"] == "Records Unit"
    names = [h["name"] for h in body["headers"]]
    # Postmark's own headers first, in order, then the fields it hands over separately.
    assert names[:3] == ["Message-ID", "In-Reply-To", "References"]
    assert {"From", "To", "Subject", "Date"} <= set(names[3:])
    from_header = next(h for h in body["headers"] if h["name"] == "From")
    assert from_header["value"] == "Records Unit <records@agency.example.org>"


def test_flag_reasons_carry_the_new_words_and_each_can_be_set(db, owner, request_one) -> None:
    for reason in ("reply_to_ignored", "attachment_too_large"):
        assert reason in sources_service.FLAG_REASONS
        source = sources_service.register_source(
            Actor.for_system("test"), source_type="other", raw_storage_url=f"test://{uuid.uuid4()}"
        )
        flagged = sources_service.flag_source(owner, source.pk, "Synthetic.", reason)
        assert flagged.flag_reason == reason
