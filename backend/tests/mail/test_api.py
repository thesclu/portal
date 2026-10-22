"""Over HTTP: drafts, send and cancel, the Sent listing, saved replies, the webhook, and
send-and-file from stage 4."""

import base64
import json

import pytest
from portal.drafting.models import LetterRecipient
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import letter as letter_service
from portal.mail.models import Message
from portal.mail.services import send
from portal.records.models import Attachment, Request

from tests.api.conftest import patch_json, post_json
from tests.drafting.conftest import sendable_agency

BASE = "/api/mail"


def test_draft_send_cancel_and_the_listing(staff_client, a_request, provider, media):
    made = post_json(
        staff_client,
        f"{BASE}/messages",
        {
            "request_id": str(a_request.pk),
            "subject": "Hello",
            "body_text": "Body.",
            "to": [{"address": "records@agency.example.org", "name": "Records"}],
        },
    )
    assert made.status_code == 201, made.content
    message = made.json()
    assert message["from_address"] == a_request.inbound_alias_email
    assert message["recipients"][0]["name"] == "Records"
    edited = patch_json(
        staff_client, f"{BASE}/messages/{message['id']}", {"subject": "Hello again"}
    )
    assert edited.status_code == 200 and edited.json()["subject"] == "Hello again"
    assert staff_client.get(f"{BASE}/counts").json()["drafts"] == 1
    queued = post_json(staff_client, f"{BASE}/messages/{message['id']}/send", {})
    assert queued.status_code == 200 and queued.json()["status"] == "queued"
    back = post_json(staff_client, f"{BASE}/messages/{message['id']}/cancel", {})
    assert back.status_code == 200 and back.json()["status"] == "draft"
    post_json(staff_client, f"{BASE}/messages/{message['id']}/send", {})
    Message.objects.filter(pk=message["id"]).update(send_after=None)
    assert send.deliver(message["id"]).outcome == "sent"
    sent = staff_client.get(f"{BASE}/messages?bucket=sent").json()
    assert sent["total"] == 1 and sent["items"][0]["status"] == "sent"
    assert sent["items"][0]["test_send"] is True
    assert staff_client.get(f"{BASE}/messages?bucket=drafts").json()["total"] == 0
    assert staff_client.get(f"{BASE}/sending").json()["enabled"] is False
    gone = staff_client.delete(f"{BASE}/messages/{message['id']}")
    assert gone.status_code == 409, gone.content  # sent; the guard


def test_a_draft_can_be_discarded(staff_client, a_request):
    made = post_json(staff_client, f"{BASE}/messages", {"request_id": str(a_request.pk)}).json()
    assert staff_client.delete(f"{BASE}/messages/{made['id']}").status_code == 204
    assert staff_client.get(f"{BASE}/messages/{made['id']}").status_code == 404


def test_saved_replies(staff_client):
    made = post_json(
        staff_client, f"{BASE}/saved-replies", {"name": "Thanks", "body": "Thank you."}
    )
    assert made.status_code == 201, made.content
    reply = made.json()
    assert [r["name"] for r in staff_client.get(f"{BASE}/saved-replies").json()] == ["Thanks"]
    archived = patch_json(staff_client, f"{BASE}/saved-replies/{reply['id']}", {"archived": True})
    assert archived.json()["archived_at"] is not None
    assert staff_client.get(f"{BASE}/saved-replies").json() == []
    assert staff_client.delete(f"{BASE}/saved-replies/{reply['id']}").status_code == 204


def test_the_delivery_webhook_needs_the_secret(db, client, settings):
    settings.PORTAL = {**settings.PORTAL, "POSTMARK_INBOUND_SECRET": "s3cret"}
    url = "/webhooks/postmark/delivery/"
    assert client.post(url, data="{}", content_type="application/json").status_code == 401
    auth = "Basic " + base64.b64encode(b"s3cret").decode()
    ok = client.post(
        url,
        data=json.dumps({"RecordType": "Delivery", "MessageID": "nope"}),
        content_type="application/json",
        headers={"Authorization": auth},
    )
    assert ok.status_code == 200 and ok.json() == {"status": "unknown"}


def test_send_and_file_files_the_request_with_the_sent_copy(
    staff_client, staff_user, actor, provider, media, db
):
    agency = sendable_agency(actor, "Email agency")
    draft = drafts_service.create_draft(subject="A letter", created_by_user_id=staff_user.pk)
    recipient = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")
    early = post_json(
        staff_client,
        f"{BASE}/recipients/{recipient.pk}/send-and-file",
        {"subject": "s", "body_text": "b", "to": [{"address": "records@agency.example.org"}]},
    )
    assert early.status_code == 422  # not reserved
    from portal.drafting.services import filing as drafting_filing

    reserved = drafting_filing.reserve_recipient(actor, recipient.pk)
    made = post_json(
        staff_client,
        f"{BASE}/recipients/{recipient.pk}/send-and-file",
        {
            "subject": "Our request",
            "body_text": "Please find our request attached.",
            "to": [{"address": "records@agency.example.org"}],
        },
    )
    assert made.status_code == 201, made.content
    message = made.json()
    assert message["kind"] == "filing" and message["attach_letter"] is True
    Message.objects.filter(pk=message["id"]).update(send_after=None)
    done = send.deliver(message["id"])
    assert done.outcome == "sent", done.message.error
    payload = provider.calls[0][1]
    # P27: the agency gets the file under the name stage 4 shows, and the kept copy has it too.
    name = letter_service.letter_filename(LetterRecipient.objects.get(pk=recipient.pk))
    assert name.endswith(".pdf") and name != send.LETTER_FILENAME
    assert [a["Name"] for a in payload["Attachments"]] == [name]
    assert list(
        Attachment.objects.filter(source_id=done.message.source_id).values_list(
            "filename", flat=True
        )
    ) == [name]
    assert base64.b64decode(payload["Attachments"][0]["Content"])[:4] == b"%PDF"
    request = Request.objects.get(pk=reserved.request_id)
    assert request.current_status_id == "filed"
    submission = request.events.get(event_type_id="submission")
    assert submission.source_id == done.message.source_id
    reserved.refresh_from_db()
    assert reserved.status == "filed"
    # The letter is on the request's files, from the sent copy.

    assert Attachment.objects.filter(source_id=done.message.source_id).count() == 1


@pytest.fixture
def staff_client(client, staff_user):
    client.force_login(staff_user)
    return client
