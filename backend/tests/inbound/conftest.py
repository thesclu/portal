"""Fixtures for the inbound mail tests. Synthetic addresses and text only.

`postmark_inbound.json` is hand-written from Postmark's documented inbound shape; the string
`ALIAS_PLACEHOLDER` in it stands for the address a test wants the message sent to.
"""

import base64
import io
import json
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone
from portal.records.actors import Actor
from portal.records.services import requests as requests_service

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "postmark_inbound.json"
SECRET = "postmark:a-test-secret"
URL = "/webhooks/postmark/inbound/"


@pytest.fixture(autouse=True)
def _inbound_settings(settings, media):
    settings.PORTAL = {**settings.PORTAL, "POSTMARK_INBOUND_SECRET": SECRET}


def payload_for(*to: str, **changes) -> dict:
    """The fixture delivery, addressed to `to`, with any top-level fields replaced."""
    text = FIXTURE.read_text().replace("ALIAS_PLACEHOLDER", to[0] if to else "nobody@example.org")
    payload = json.loads(text)
    # The fixture's own date is long past; a delivered message says it was sent a moment ago.
    payload["Date"] = format_datetime(timezone.now() - timedelta(minutes=5))
    if len(to) != 1:
        payload["ToFull"] = [{"Email": address, "Name": "", "MailboxHash": ""} for address in to]
        payload["To"] = ", ".join(to)
        payload["OriginalRecipient"] = to[0] if to else ""
    payload.update(changes)
    return payload


def basic(secret: str = SECRET) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(secret.encode()).decode()}


def deliver(payload, *, secret: str = SECRET, client: Client | None = None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return (client or Client()).post(
        URL, data=body, content_type="application/json", headers=basic(secret)
    )


def pdf_bytes(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def attachment(name: str, content: bytes, content_type: str = "application/octet-stream") -> dict:
    return {
        "Name": name,
        "Content": base64.b64encode(content).decode(),
        "ContentType": content_type,
        "ContentLength": len(content),
        "ContentID": "",
    }


@pytest.fixture
def owner(staff_user):
    return Actor.human(staff_user)


def _reserve(owner, agency, requester, subject, sensitivity="internal"):
    return requests_service.reserve_request(
        owner,
        agency_id=agency.pk,
        requester_user_id=requester.pk,
        subject=subject,
        request_text="Body.",
        sensitivity=sensitivity,
    )


@pytest.fixture
def request_one(db, owner, agency, staff_user):
    return _reserve(owner, agency, staff_user, "First request")


@pytest.fixture
def request_two(db, owner, agency, staff_user):
    return _reserve(owner, agency, staff_user, "Second request")


@pytest.fixture
def sealed_request(db, owner, agency, staff_user):
    return _reserve(owner, agency, staff_user, "SEALED request", "privileged")
