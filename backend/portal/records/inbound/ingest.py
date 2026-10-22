"""What happens to a message, in two steps that fail differently.

ARRIVAL (`receive`), in the webhook's own transaction, before the delivery is acknowledged:
the source row is made and the message is PLACED by the address it was sent to, or flagged. So a
message is on its request from the first moment it exists. That matters twice. A message for a
privileged request is never, even briefly, unattached mail that every staff member can open
(P5 review, F3). And a message never sits in no Inbox bucket waiting for a job that may not run.
Placing is one indexed lookup of an address; nothing about the message's content is decided.

THE JOB (`ingest_source`), afterwards and retried: the attachments are stored, the text is read
for mentions of other requests, and `sources.ingested_at` is stamped. If it never succeeds the
message is still on its request and readable; what is missing is its files, and the job's last
failure flags it so somebody knows (`flag_failed`).

Arrival must not fail for anything about the content, because a failure there is a 500 and
Postmark then retries and gives up. So placement runs in a savepoint: if recording the event is
refused for any reason, the source is kept, flagged, and the delivery is still acknowledged.

IDEMPOTENT. `ingested_at` is what says the job has run; nothing a person does to a message can
make it look done (F7). Two runs at once are serialised by an advisory lock keyed on the source's
id, NOT a row lock on the source: `record_event` locks the request and then the source (HANDOFF
convention 14). The job itself takes no request lock at all.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from portal.core.services import notifications as core_notifications
from portal.core.services.settings import get_setting
from portal.records import storage
from portal.records.actors import Actor
from portal.records.inbound import match as match_module
from portal.records.inbound.eml import parse_eml
from portal.records.inbound.pages import count_pdf_pages
from portal.records.inbound.parse import InboundMessage, parse_postmark
from portal.records.models import Attachment, Event, Request, Source
from portal.records.services import events as events_service
from portal.records.services import sources as sources_service
from portal.records.services.common import NotFound, ServiceError

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "postmark_webhook"
UNREADABLE_NOTE = "This delivery could not be read as a message. The original is kept."
UNPLACED_NOTE = (
    "This message could not be placed automatically. The original is kept; place it by hand."
)
DATE_NOTE = (
    "The date this message gives for itself is not believable, so it is dated when it arrived."
)
DAMAGED_NOTE = "An attachment of this message did not arrive whole. The original is kept."
FAILED_NOTE = (
    "The files of this message could not be extracted. The original is kept; download it to see "
    "them."
)
TOO_LARGE_NOTE = (
    "This message was larger than the portal accepts, so its attachments were not kept. Its "
    "sender, subject and text are here; ask the agency to send the files another way."
)
TOO_LARGE_UNREADABLE_NOTE = (
    "This delivery was larger than the portal accepts and could not be read. Nothing of it is "
    "kept but this note."
)
# A clock that is a little ahead is ordinary; a message from next year is not.
_FUTURE_SKEW = timedelta(hours=26)
# Nothing this system handles predates this; an .eml that says otherwise has no usable date.
_EARLIEST = datetime(1990, 1, 1, tzinfo=UTC)


@dataclass
class IngestResult:
    source: Source
    event: Event | None = None
    attachments: list[Attachment] = field(default_factory=list)
    # False when the job had already finished with this message and nothing was done.
    ingested: bool = True


def metadata_of(message: InboundMessage) -> dict[str, object]:
    """What the Inbox shows and searches beside a message. Addresses and a subject, no body."""
    return {
        "from": message.from_addr,
        "to": message.to,
        "cc": message.cc,
        "subject": message.subject,
        "date": message.date.isoformat() if message.date else None,
        "attachment_count": len(message.attachments),
        "from_name": message.from_name,
    }


def believable_date(
    message: InboundMessage, captured_at: datetime, *, delivered: bool
) -> datetime | None:
    """The message's own date, if it can be one. A sender writes the Date header, and an event's
    `occurred_at` is on an append-only ledger: a date in the year 9999 was stored, could not be
    read back, and broke every page that listed it (P5 review, F4). A DELIVERED message was sent
    shortly before it arrived, within a window that is a setting; an imported file may be old."""
    date = message.date
    if date is None or date > captured_at + _FUTURE_SKEW or date < _EARLIEST:
        return None
    if delivered:
        window = timedelta(hours=int(get_setting("records.inbound_date_window_hours")))
        if date < captured_at - window:
            return None
    return date


def load_message(source: Source) -> InboundMessage | None:
    """The message a source's stored original holds, or None if it is not mail or cannot be read."""
    name = source.raw_storage_url or ""
    try:
        with storage.open_stored(name) as handle:
            data = handle.read()
        if name.endswith(".eml"):
            return parse_eml(data)
        payload = json.loads(data)
    except (FileNotFoundError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") == "uploaded_document":
        return None
    return parse_postmark(payload)


def receive(
    actor: Actor,
    message: InboundMessage | None,
    *,
    source_id: UUID,
    source_type: str,
    raw_storage_url: str,
    external_ref: str,
    too_large: bool = False,
) -> tuple[Source, Event | None]:
    """Make the source row for a stored original and place it. Call inside a transaction.

    `message` is None for a delivery that could not be read at all: it is kept and flagged.
    `too_large` says the delivery was past the size the webhook reads and what is stored is what
    could be kept of it without its files (`postmark.salvage`): it is placed as usual and flagged
    `attachment_too_large`, which outranks any other reason, because evidence is missing.
    Nothing here raises for anything about the content.
    """
    metadata = None
    if message is not None:
        try:
            metadata = json.loads(json.dumps(metadata_of(message)))
        except (TypeError, ValueError):
            metadata = None
    source = sources_service.register_source(
        actor,
        source_type=source_type,
        raw_storage_url=raw_storage_url,
        external_ref=external_ref,
        message_id=message.message_id if message else None,
        raw_metadata=metadata,
        source_id=source_id,
    )
    if message is None:
        if too_large:
            sources_service.flag_source(
                actor, source.pk, TOO_LARGE_UNREADABLE_NOTE, "attachment_too_large"
            )
        else:
            sources_service.flag_source(actor, source.pk, UNREADABLE_NOTE, "other")
        return Source.objects.get(pk=source.pk), None

    event = None
    flag: tuple[str, str] | None = None
    try:
        with transaction.atomic():
            placement = match_module.place(message)
            if placement.flag_reason is not None:
                flag = (placement.note, placement.flag_reason)
            if placement.request is not None:
                occurred_at = believable_date(
                    message, source.captured_at, delivered=source_type == "postmark_email"
                )
                if occurred_at is None and message.date is not None and flag is None:
                    flag = (DATE_NOTE, "other")
                event = events_service.record_event(
                    actor,
                    placement.request.pk,
                    event_type="correspondence",
                    channel="email",
                    direction="inbound",
                    occurred_at=occurred_at or source.captured_at,
                    display_body=message.body,
                    source_id=source.pk,
                )
    except (ServiceError, DatabaseError, ValueError, RecursionError):
        # Whatever it was, it was about the content, and the delivery must still be acknowledged.
        logger.exception("inbound message %s could not be placed; flagged", source.pk)
        event, flag = None, (UNPLACED_NOTE, "other")
    if too_large:
        flag = (TOO_LARGE_NOTE, "attachment_too_large")
    if flag is not None:
        sources_service.flag_source(actor, source.pk, *flag)
    core_notifications.notify_many(
        core_notifications.staff_who_want("arrived"),
        "arrived",
        "New mail arrived in the Inbox",
        link=f"/inbox?bucket=all&message={source_id}",
        subject_type="source",
        subject_id=source_id,
    )
    _queue_assistant_check(source.pk)
    return Source.objects.get(pk=source.pk), event


def _queue_assistant_check(source_id: UUID) -> None:
    """P19d: after this transaction lands, the assistant's constant check runs in the worker."""
    if str(get_setting("ai.general_provider") or "off").lower() in ("off", "none", ""):
        return
    from portal.core import jobs as core_jobs

    transaction.on_commit(lambda: core_jobs.check_inbound.defer(source_id=str(source_id)))


def _lock(source_id: UUID) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"ingest:{source_id}"])


def _flag_if_unseen(actor: Actor, source_id: UUID, note: str, reason: str) -> None:
    """Flag a message for something the job found, unless a person has already dealt with it or
    it is already flagged for something else (the first reason stands; the candidates and the
    original are there either way)."""
    source = Source.objects.get(pk=source_id)
    if source.reviewed_at is None and not source.flagged_for_review:
        sources_service.flag_source(actor, source_id, note, reason)


@transaction.atomic
def ingest_source(source_id: UUID, actor: Actor | None = None) -> IngestResult:
    """The job's work: store the attachments, read the text for other requests, stamp it done."""
    actor = actor or Actor.for_system(SYSTEM_ACTOR)
    _lock(source_id)
    source = Source.objects.filter(pk=source_id).first()
    if source is None:
        raise NotFound(f"No source {source_id}.")
    event = Event.objects.filter(source_id=source_id, voided_at__isnull=True).first()
    if source.ingested_at is not None:
        return IngestResult(source=source, event=event, ingested=False)

    message = load_message(source)
    stored_names: list[str] = []
    attachments: list[Attachment] = []
    try:
        if message is not None:
            for attachment in message.attachments:
                name = storage.safe_filename(attachment.name, fallback="attachment")
                stored = storage.store_attachment(source_id, name, [attachment.content])
                stored_names.append(stored.storage_name)
                mime = storage.guess_mime(name, attachment.content_type)
                attachments.append(
                    sources_service.add_attachment(
                        actor,
                        source_id,
                        filename=name,
                        storage_url=stored.storage_name,
                        mime_type=mime,
                        size_bytes=stored.size,
                        checksum_sha256=stored.sha256,
                        page_count=(
                            count_pdf_pages(attachment.content)
                            if mime == "application/pdf"
                            else None
                        ),
                    )
                )
            if any(a.damaged for a in message.attachments):
                _flag_if_unseen(actor, source_id, DAMAGED_NOTE, "other")
            placed_on = {event.request_id} if event is not None else set()
            if event is not None and match_module.mentions(message, placed_on, for_flag=True):
                _flag_if_unseen(actor, source_id, match_module.MENTIONS_NOTE, "may_cover_others")
        Source.objects.filter(pk=source_id).update(ingested_at=timezone.now())
    except BaseException:
        storage.delete_stored(stored_names)
        raise
    return IngestResult(
        source=Source.objects.get(pk=source_id), event=event, attachments=attachments
    )


@transaction.atomic
def flag_failed(source_id: UUID) -> None:
    """The job's last attempt failed. Say so on the message, in a transaction of its own, so the
    failure that rolled the job back does not also roll this back. It stays un-ingested, so
    running the job again later still does the work."""
    if Source.objects.filter(pk=source_id).exists():
        _flag_if_unseen(Actor.for_system(SYSTEM_ACTOR), source_id, FAILED_NOTE, "other")


def candidates(source: Source) -> list[Request]:
    """The requests a message may ALSO be about, worked out now from its stored original. The
    caller filters them to what the person asking may see; nothing here is stored."""
    message = load_message(source)
    return match_module.candidates(message) if message is not None else []


@transaction.atomic
def import_eml(actor: Actor, data: bytes) -> IngestResult:
    """Bring in one .eml file: keep it, place it, and extract its files, exactly as delivered mail.

    The same file imported twice is one source, and so is a message that already arrived by
    delivery (same Message-ID): the second is told about the first and nothing is recorded."""
    reference = f"eml:{sha256(data).hexdigest()}"
    message = parse_eml(data)
    existing = Source.objects.filter(external_ref=reference).first()
    if existing is None and message.message_id:
        existing = (
            Source.objects.filter(message_id=message.message_id).order_by("captured_at").first()
        )
    if existing is not None:
        return IngestResult(
            source=existing,
            event=Event.objects.filter(source_id=existing.pk, voided_at__isnull=True).first(),
            ingested=False,
        )
    source_id = uuid4()
    raw_name = storage.store_raw(source_id, "eml", data)
    try:
        source, _ = receive(
            actor,
            message,
            source_id=source_id,
            source_type="imported_eml",
            raw_storage_url=raw_name,
            external_ref=reference,
        )
        return ingest_source(source.pk, actor)
    except BaseException:
        storage.delete_stored([raw_name])
        raise
