"""The Postmark inbound webhook: authenticate, keep the delivery exactly as it came, defer the job.

Its whole job is that mail Postmark has handed over is never lost: once this returns 200 the
delivery is on disk untouched, a source row points at it, and it is on the request it was
addressed to or flagged for a person (`ingest.receive`). Its files are extracted afterwards by the
job. Nothing about the CONTENT can make this fail: a body that is not even JSON is kept and
flagged.

So the only refusals are about the caller and the transport, never the content: 401 without the
secret, 411 with no Content-Length or no body. Anything else is 200, because a 4xx or 5xx makes
Postmark retry and then give up, and the message is gone.

A delivery past the size this system will read (`records.inbound_max_payload_bytes`) used to be
refused with 413, which lost it silently. Now only that many bytes are read, and what can be kept
of them is (`salvage`): every top-level field that arrived whole except `Attachments`, so the
sender, the addresses, the subject and the text survive and the message is placed by its address
as any other. That, not the delivery, is what is stored, and the message is flagged
`attachment_too_large` so a person asks for the files another way (P20d).

The secret is HTTP Basic, `user:secret`, compared in constant time. An empty setting refuses
everyone: an unset secret must not mean an open door.

A delivery is identified by Postmark's `MessageID` (the body's own hash when it has none). Two
deliveries of one message are serialised by an advisory lock on that id, so the second finds the
first's source and is answered `duplicate`.
"""

import base64
import binascii
import hmac
import json
import logging
from hashlib import sha256
from uuid import uuid4

from django.conf import settings
from django.db import connection, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from portal.core import rls
from portal.core.services.settings import get_setting
from portal.records import storage
from portal.records.actors import Actor
from portal.records.inbound import ingest
from portal.records.inbound.ingest import SYSTEM_ACTOR
from portal.records.inbound.parse import parse_postmark
from portal.records.jobs import ingest_postmark_source
from portal.records.models import Source

logger = logging.getLogger(__name__)


def _authorised(request: HttpRequest) -> bool:
    expected = str(settings.PORTAL.get("POSTMARK_INBOUND_SECRET") or "")
    if not expected:
        return False
    scheme, _, encoded = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        given = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError):
        return False
    return hmac.compare_digest(given, expected.encode())


def _read(request: HttpRequest, limit: int) -> tuple[bytes, bool]:
    """The body, read from the stream so Django's own (much smaller) cap on a request body does
    not apply, and whether it was longer than `limit`. A longer one comes back as its first
    `limit` bytes; the rest is never read."""
    data = request.read(limit + 1)
    if len(data) > limit:
        return data[:limit], True
    return data, False


def salvage(head: bytes) -> dict | None:
    """What can be kept of the first bytes of a Postmark delivery that was cut short: each
    top-level field that arrived whole, in order, except `Attachments`, which is never kept (it is
    why the delivery was too large, and a file cut short is not evidence). None when the bytes do
    not even begin a JSON object.

    A JSON scanner over the top level only (`raw_decode` reads each value), not a regex, and it
    stops at the first value that is cut short. Postmark sends the files last, so in practice
    everything but the files survives; a message whose own text is past the limit keeps the
    fields before it."""
    text = head.decode("utf-8", "ignore")
    decoder = json.JSONDecoder()
    position = len(text) - len(text.lstrip())
    if not text.startswith("{", position):
        return None
    position += 1
    kept: dict = {}
    while True:
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if position >= len(text) or text[position] == "}":
            return kept
        try:
            key, position = decoder.raw_decode(text, position)
            while position < len(text) and text[position] in " \t\r\n":
                position += 1
            if not isinstance(key, str) or not text.startswith(":", position):
                return kept
            position += 1
            while position < len(text) and text[position] in " \t\r\n":
                position += 1
            if key == "Attachments":
                return kept
            value, position = decoder.raw_decode(text, position)
        except (ValueError, RecursionError):
            return kept
        kept[key] = value


@csrf_exempt
@require_POST
def inbound(request: HttpRequest) -> HttpResponse:
    rls.set_system_context()
    if not _authorised(request):
        return JsonResponse({"detail": "Unauthorized."}, status=401)
    if not request.headers.get("Content-Length"):
        # Without it Django reads nothing, and an empty body would be stored as "a delivery" and
        # every later one answered `duplicate`. This is about the transport, not the content, so
        # a refusal (and Postmark's retry) is the right outcome.
        return JsonResponse({"detail": "Content-Length is required."}, status=411)
    limit = int(get_setting("records.inbound_max_payload_bytes"))
    body, too_large = _read(request, limit)
    if not body:
        return JsonResponse({"detail": "Empty body."}, status=411)

    if too_large:
        logger.warning("inbound delivery larger than the configured limit; kept without its files")
        payload = salvage(body)
        # What is stored is what was kept, so the message reads back from it like any other.
        body = json.dumps(payload).encode() if payload is not None else body
    else:
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError):
            payload = None
    try:
        message = parse_postmark(payload) if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 -- the parser is meant to be total; if it is not, keep the mail
        logger.exception("inbound delivery could not be parsed; it will be kept and flagged")
        message = None
    reference = (message.external_ref if message else None) or f"sha256:{sha256(body).hexdigest()}"
    reference = reference[:500]
    actor = Actor.for_system(SYSTEM_ACTOR)

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"delivery:{reference}"])
        existing = Source.objects.filter(external_ref=reference).first()
        if existing is not None:
            return JsonResponse({"status": "duplicate", "source_id": str(existing.pk)})
        source_id = uuid4()
        raw_name = storage.store_raw(source_id, "postmark_json", body)
        try:
            # Placed (or flagged) HERE, before the delivery is acknowledged: see ingest.py.
            source, _ = ingest.receive(
                actor,
                message,
                source_id=source_id,
                source_type="postmark_email",
                raw_storage_url=raw_name,
                external_ref=reference,
                too_large=too_large,
            )
            if message is not None:
                # Deferred on this connection, inside this transaction: the job exists if and
                # only if the source does. It stores the files and reads the text.
                ingest_postmark_source.defer(source_id=str(source.pk))
        except BaseException:
            storage.delete_stored([raw_name])
            raise
    return JsonResponse({"status": "stored", "source_id": str(source.pk)})
