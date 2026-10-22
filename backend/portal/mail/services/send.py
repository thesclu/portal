"""Send is pressed; the message waits its undo window; the job hands it to the provider; the
sent copy becomes a records source and event. That last step is THE SEAM, and it is one-way.

  queue(actor, id)     draft -> queued, `send_after` = now + mail.undo_send_seconds; the job is
                       deferred for then. Refused with nothing in To or no subject.
  cancel(actor, id)    queued -> draft, while the window is open. The job finds it is no longer
                       queued and does nothing.
  deliver(id)          the job. queued -> sending -> sent (or failed). Under the request's lock
                       and then the message's (the lock order in services/common.py):
                         1. the payload: From the alias, the recipients, the text, the request's
                            files picked for it, the letter as a PDF when asked;
                         2. the provider accepts it (or refuses: failed, with its sentence);
                         3. the seam: what was handed over is stored as the untouched original,
                            a `sent_email` source is registered with it, an outbound event is
                            recorded on the request with that source, and the PDF, if any, is
                            an attachment of that source so the Files tab holds it. The copy is
                            not marked reviewed: a system sender could not, and the Inbox keeps
                            sent copies out of New on their type instead;
                         4. a filing message files its recipient's request with that source
                            (`drafting.filing.file_recipient`), which is `file_request` with
                            the sent copy on the submission event.
                       Idempotent: a message that is not `queued` is left alone, so a job run
                       twice, or run after a cancel, does nothing.

A failed send is `failed` with `error`; a person edits and queues it again (`requeue`). The job
retries only transient transport errors; a provider refusal is final until the message changes.
"""

import base64
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from portal.core.services import activity as core_activity
from portal.core.services import review as core_review
from portal.core.services.settings import get_setting
from portal.drafting import render
from portal.drafting.mike import ForkError
from portal.drafting.models import LetterDraft, LetterRecipient, LetterShell
from portal.drafting.services import filing as drafting_filing
from portal.drafting.services import fork as fork_service
from portal.drafting.services import letter as letter_service
from portal.mail import pdf
from portal.mail.models import Message, MessageKind, MessageStatus, RecipientKind
from portal.mail.services import transport
from portal.mail.services.common import get_message, lock_message
from portal.records import storage
from portal.records.actors import Actor
from portal.records.models import Request
from portal.records.services import challenges as challenges_service
from portal.records.services import events as events_service
from portal.records.services import sources as sources_service
from portal.records.services.common import InvalidTransition, ServiceError, lock_request

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "mail_sender"
LETTER_FILENAME = "request-letter.pdf"
EVENT_TYPE = {MessageKind.FOLLOWUP: "follow_up"}


def letter_name(message: Message) -> str:
    """The attached letter's file name: for a letter's own filing, the name stage 4 shows
    (`letter_filename`, P27: the agency was sent `request-letter.pdf` while the page named it
    otherwise); for anything else, LETTER_FILENAME."""
    if message.letter_recipient_id is None:
        return LETTER_FILENAME
    recipient = (
        LetterRecipient.objects.select_related("agency", "draft")
        .filter(pk=message.letter_recipient_id)
        .first()
    )
    if recipient is None:
        return LETTER_FILENAME
    return storage.safe_filename(letter_service.letter_filename(recipient))


@dataclass(frozen=True)
class Delivered:
    message: Message
    outcome: str  # sent | failed | skipped | early


def _check_sendable(message: Message) -> None:
    if not message.recipients.filter(kind=RecipientKind.TO).exists():
        raise ServiceError("Add at least one address in To.")
    if not message.subject.strip():
        raise ServiceError("Give the message a subject.")


def _defer(message_id: UUID, send_after) -> None:
    from portal.mail.jobs import deliver_message

    deliver_message.configure(schedule_at=send_after).defer(message_id=str(message_id))


@transaction.atomic
def queue(actor: Actor, message_id: UUID, *, delay_seconds: int | None = None) -> Message:
    message = lock_message(message_id)
    if message.status not in (MessageStatus.DRAFT, MessageStatus.FAILED):
        raise InvalidTransition(f"This message is {message.status}; it cannot be sent again.")
    _check_sendable(message)
    if actor.is_human:
        core_review.require_cleared(actor.user_id, "message", message.pk, message.updated_at)
    delay = int(get_setting("mail.undo_send_seconds")) if delay_seconds is None else delay_seconds
    now = timezone.now()
    send_after = now + timedelta(seconds=max(0, delay))
    Message.objects.filter(pk=message_id).update(
        status=MessageStatus.QUEUED,
        queued_at=now,
        send_after=send_after,
        error=None,
        updated_at=now,
    )
    _defer(message_id, send_after)
    return get_message(message_id)


@transaction.atomic
def cancel(actor: Actor, message_id: UUID) -> Message:
    message = lock_message(message_id)
    if message.status != MessageStatus.QUEUED:
        raise InvalidTransition(
            f"This message is {message.status}; only one still waiting to go can be taken back."
        )
    Message.objects.filter(pk=message_id).update(
        status=MessageStatus.DRAFT, queued_at=None, send_after=None, updated_at=timezone.now()
    )
    return get_message(message_id)


def _payload(
    message: Message,
    request: Request,
    *,
    letter_pdf: bytes | None = None,
    letter_file: str = LETTER_FILENAME,
) -> tuple[dict[str, Any], bytes | None]:
    org = str(get_setting("org.name") or "").strip()
    sender = f"{org} <{message.from_address}>" if org else message.from_address
    by_kind: dict[str, list[str]] = {k: [] for k in RecipientKind.values}
    for recipient in message.recipients.all():
        shown = f"{recipient.name} <{recipient.address}>" if recipient.name else recipient.address
        by_kind[recipient.kind].append(shown)
    signature = str(get_setting("org.signature") or "").strip()
    body = message.body_text.rstrip()
    if signature:
        body = f"{body}\n\n{signature}"
    attachments: list[dict[str, str]] = []
    for link in message.files.select_related("attachment"):
        file = link.attachment
        with storage.open_stored(file.storage_url) as handle:
            content = handle.read()
        attachments.append(
            {
                "Name": storage.safe_filename(file.filename),
                "Content": base64.b64encode(content).decode(),
                "ContentType": file.mime_type or "application/octet-stream",
            }
        )
    letter_bytes: bytes | None = None
    if message.attach_letter and letter_pdf is not None:
        # P21c: a letter written in the drafting tool goes as the drafting tool's own PDF.
        letter_bytes = letter_pdf
    elif message.attach_letter:
        shell = _shell_for(message)
        # P19b: the frozen text, set on the letterhead's page (seal, font, margins).
        letter_bytes = pdf.pdf_from_html(
            render.text_page_html(
                request.request_text or "",
                request.tracking_number,
                letter_service.page_layout(shell),
                for_pdf=True,
            )
        )
    if letter_bytes is not None:
        attachments.append(
            {
                "Name": letter_file,
                "Content": base64.b64encode(letter_bytes).decode(),
                "ContentType": "application/pdf",
            }
        )
    payload: dict[str, Any] = {
        "From": sender,
        "To": ", ".join(by_kind["to"]),
        "Subject": message.subject,
        "TextBody": body,
        "ReplyTo": message.from_address,
        "MessageStream": str(settings.PORTAL.get("POSTMARK_MESSAGE_STREAM") or "outbound"),
        "Headers": [{"Name": "X-SCLU-Request", "Value": request.tracking_number}],
    }
    if by_kind["cc"]:
        payload["Cc"] = ", ".join(by_kind["cc"])
    if by_kind["bcc"]:
        payload["Bcc"] = ", ".join(by_kind["bcc"])
    if attachments:
        payload["Attachments"] = attachments
    return payload, letter_bytes


def _shell_for(message: Message) -> LetterShell | None:
    if message.letter_recipient_id:
        draft = message.letter_recipient.draft
        if draft.shell_id:
            return LetterShell.objects.filter(pk=draft.shell_id).first()
    return LetterShell.objects.filter(is_default=True, archived_at__isnull=True).first()


def _actor_for(message: Message) -> Actor:
    if message.created_by_id is not None:
        return Actor(user_id=message.created_by_id)
    return Actor.for_system(SYSTEM_ACTOR)


@transaction.atomic
def deliver(message_id: UUID, *, now=None) -> Delivered:
    now = now or timezone.now()
    head = get_message(message_id)
    request = lock_request(head.request_id)  # the request first; then the message
    message = lock_message(message_id)
    if message.status != MessageStatus.QUEUED:
        return Delivered(message, "skipped")
    if message.send_after and message.send_after > now:
        return Delivered(message, "early")
    Message.objects.filter(pk=message_id).update(status=MessageStatus.SENDING, updated_at=now)

    # P21c: a filing letter written in the drafting tool goes as the drafting tool's PDF, of the
    # version current now, fetched as the person who sent it; that version is what is filed.
    fork_letter: fork_service.MailLetter | None = None
    if message.attach_letter and message.letter_recipient_id:
        recipient = LetterRecipient.objects.get(pk=message.letter_recipient_id)
        if fork_service.is_fork_draft(LetterDraft.objects.get(pk=recipient.draft_id)):
            failure: str | None = None
            try:
                fork_letter = fork_service.letter_for_mail(_actor_for(message), recipient)
            except ForkError as exc:
                if exc.transient:
                    # Back to the queue for the job's retry; nothing else moved.
                    Message.objects.filter(pk=message_id).update(status=MessageStatus.QUEUED)
                    raise transport.TransportError(str(exc), permanent=False) from exc
                failure = str(fork_service.in_words(exc))
            except ServiceError as exc:
                failure = str(exc)
            if failure is not None:
                Message.objects.filter(pk=message_id).update(
                    status=MessageStatus.FAILED, error=failure, updated_at=timezone.now()
                )
                return Delivered(get_message(message_id), "failed")

    # One name for the copy sent and the copy kept (a send across the agency's midnight would
    # otherwise date them differently).
    name = letter_name(message)
    payload, letter_bytes = _payload(
        message, request, letter_pdf=fork_letter.pdf if fork_letter else None, letter_file=name
    )
    try:
        result = transport.send(payload)
    except transport.TransportError as exc:
        if not exc.permanent:
            # Back to the queue for the job's retry; nothing else moved.
            Message.objects.filter(pk=message_id).update(status=MessageStatus.QUEUED)
            raise
        Message.objects.filter(pk=message_id).update(
            status=MessageStatus.FAILED, error=str(exc), updated_at=timezone.now()
        )
        return Delivered(get_message(message_id), "failed")

    # ---- the seam ----
    actor = _actor_for(message)
    sent_at = timezone.now()
    stored = storage.store_raw(message.pk, "sent_json", _as_stored(payload, result).encode())
    source = sources_service.register_source(
        actor,
        source_type="sent_email",
        raw_storage_url=stored,
        external_ref=f"sent:{result.provider_message_id or message.pk}",
        raw_metadata={
            "from": message.from_address,
            "to": payload.get("To", ""),
            "cc": payload.get("Cc", ""),
            "subject": message.subject,
            "kind": message.kind,
            "test_send": result.test_send,
        },
        captured_at=sent_at,
    )
    if letter_bytes is not None:
        kept = storage.store_attachment(source.pk, name, [letter_bytes])
        sources_service.add_attachment(
            actor,
            source.pk,
            filename=name,
            storage_url=kept.storage_name,
            mime_type="application/pdf",
            size_bytes=kept.size,
            role="supporting_document",
            checksum_sha256=kept.sha256,
        )
    if message.kind == MessageKind.FILING and message.letter_recipient_id:
        drafting_filing.file_recipient(
            actor,
            message.letter_recipient_id,
            filed_at=sent_at,
            channel="email",
            source_id=source.pk,
            cleared=True,  # checked at send-and-file, before the message was queued
            fork_version_id=fork_letter.version_id if fork_letter else None,
        )
    else:
        events_service.record_event(
            actor,
            request.pk,
            event_type=EVENT_TYPE.get(message.kind, "correspondence"),
            channel="email",
            direction="outbound",
            occurred_at=sent_at,
            display_body=message.body_text,
            source_id=source.pk,
            actor_user_id=message.created_by_id,
        )
    # P14: a challenge's letter going out is what makes the challenge sent.
    challenges_service.on_message_sent(
        actor, message.pk, source_id=source.pk, sent_at=sent_at, body=message.body_text
    )
    core_activity.record(
        actor,
        "sent_mail",
        f"Sent “{message.subject[:60]}” on {request.tracking_number}",
        subject_type="message",
        subject_id=message.pk,
        request_id=request.pk,
        data={"kind": message.kind, "test_send": result.test_send},
    )
    Message.objects.filter(pk=message_id).update(
        status=MessageStatus.SENT,
        sent_at=sent_at,
        test_send=result.test_send,
        provider_message_id=result.provider_message_id or None,
        source_id=source.pk,
        error=None,
        updated_at=sent_at,
    )
    return Delivered(get_message(message_id), "sent")


def _as_stored(payload: dict[str, Any], result: transport.SendResult) -> str:
    """What was handed over, minus file contents (the files are the request's, already stored)."""
    import json

    slim = dict(payload)
    if "Attachments" in slim:
        slim["Attachments"] = [
            {"Name": a["Name"], "ContentType": a["ContentType"]} for a in slim["Attachments"]
        ]
    return json.dumps(
        {
            "kind": "sent_email",
            "payload": slim,
            "provider_message_id": result.provider_message_id,
            "test_send": result.test_send,
        },
        indent=2,
    )


def stuck(now=None, *, older_than_minutes: int = 10) -> list[UUID]:
    """Queued messages whose window closed a while ago and that no job delivered: re-deferred."""
    now = now or timezone.now()
    return list(
        Message.objects.filter(
            status=MessageStatus.QUEUED, send_after__lt=now - timedelta(minutes=older_than_minutes)
        ).values_list("pk", flat=True)
    )
