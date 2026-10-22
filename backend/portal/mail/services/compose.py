"""Composing: drafts, their recipients and files, and the prefill for a reply or a forward.

A message always belongs to a request, and its From is that request's own alias (DECISIONS 14),
so the agency's reply lands on the request without any Reply-To trick. Everything here works on a
DRAFT; once Send is pressed (`send.queue`) the message is the job's, and only `send.cancel` gives
it back.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.core.services import review as core_review
from portal.mail.models import (
    Message,
    MessageAttachment,
    MessageKind,
    MessageRecipient,
    MessageStatus,
    RecipientKind,
)
from portal.mail.services.common import clean_address, get_message, is_ours, lock_message
from portal.records.actors import Actor
from portal.records.inbound import ingest as inbound_ingest
from portal.records.models import AgencyContact, Attachment, Event, Request, Source
from portal.records.services.common import InvalidTransition, NotFound, ServiceError

MAX_RECIPIENTS = 50


@dataclass(frozen=True)
class Address:
    address: str
    name: str | None = None


@dataclass
class Prefill:
    """What a reply or a forward starts from. Addresses are what the message carried, minus our
    own; the body of a forward quotes the original."""

    request_id: UUID
    from_address: str
    subject: str
    body_text: str
    to: list[Address] = field(default_factory=list)
    cc: list[Address] = field(default_factory=list)
    attachment_ids: list[UUID] = field(default_factory=list)


def _request_of_source(source_id: UUID) -> Request:
    event = (
        Event.objects.filter(source_id=source_id, voided_at__isnull=True)
        .select_related("request")
        .order_by("occurred_at")
        .first()
    )
    if event is None:
        raise InvalidTransition(
            "This message is not on a request yet. Attach it to one, then reply from there."
        )
    return event.request


def _alias_of(request: Request) -> str:
    if not request.inbound_alias_email:
        raise ServiceError(f"{request.tracking_number} has no request address to send from.")
    return request.inbound_alias_email


def _parse(addresses: Iterable[str | Address]) -> list[Address]:
    out: list[Address] = []
    seen: set[str] = set()
    for item in addresses:
        given = item if isinstance(item, Address) else Address(item)
        address = clean_address(given.address)
        if is_ours(address) or address in seen:
            continue
        seen.add(address)
        out.append(Address(address, (given.name or "").strip() or None))
    return out


def prefill(source_id: UUID, *, forward: bool = False) -> Prefill:
    """Reply: To the sender, Cc whoever else was on it, subject "Re:". Forward: nobody, subject
    "Fwd:", the original quoted under a header line. Both from the request's alias."""
    source = Source.objects.filter(pk=source_id).first()
    if source is None:
        raise NotFound(f"No source {source_id}.")
    request = _request_of_source(source_id)
    parsed = inbound_ingest.load_message(source)
    meta = source.raw_metadata or {}
    from_addr = parsed.from_addr if parsed else str(meta.get("from") or "")
    subject = parsed.subject if parsed else str(meta.get("subject") or "")
    body = parsed.text_body if parsed else ""
    date = parsed.date if parsed else None
    prefix = "Fwd: " if forward else "Re: "
    if subject and not subject.lower().startswith(prefix.lower().strip()):
        subject = prefix + subject
    elif not subject:
        subject = prefix.strip() + " " + request.subject
    if forward:
        when = f" · {date:%b %-d, %Y, %-I:%M %p}" if date else ""
        quoted = "\n".join(f"> {line}" for line in body.splitlines()) if body else ""
        text = f"\n\n---------- Forwarded message ----------\nFrom: {from_addr}{when}\n\n{quoted}"
        to: list[Address] = []
        cc: list[Address] = []
        files = list(Attachment.objects.filter(source_id=source_id).values_list("pk", flat=True))
    else:
        text = ""
        to = _parse([from_addr]) if from_addr else []
        cc = _parse([*(parsed.to if parsed else []), *(parsed.cc if parsed else [])])
        cc = [a for a in cc if a.address not in {t.address for t in to}]
        files = []
    return Prefill(
        request_id=request.pk,
        from_address=_alias_of(request),
        subject=subject,
        body_text=text,
        to=to,
        cc=cc,
        attachment_ids=files,
    )


def agency_copy_addresses(request: Request) -> list[Address]:
    """The agency's contacts marked "copy on every request", for Cc on a message to the agency."""
    rows = AgencyContact.objects.filter(
        agency_id=request.agency_id, copy_on_every_request=True, bad_address_at__isnull=True
    ).exclude(email__isnull=True)
    return _parse([Address(c.email or "", c.name) for c in rows])


def _write_recipients(message: Message, to, cc, bcc) -> None:
    rows = [(RecipientKind.TO, a) for a in _parse(to)]
    rows += [(RecipientKind.CC, a) for a in _parse(cc)]
    rows += [(RecipientKind.BCC, a) for a in _parse(bcc)]
    if len(rows) > MAX_RECIPIENTS:
        raise ServiceError(f"A message goes to at most {MAX_RECIPIENTS} addresses.")
    MessageRecipient.objects.filter(message_id=message.pk).delete()
    # One address goes once, under the first kind it was given: To wins over Cc over Bcc.
    seen: set[str] = set()
    for kind, address in rows:
        if address.address in seen:
            continue
        seen.add(address.address)
        MessageRecipient.objects.create(
            message_id=message.pk, kind=kind, address=address.address, name=address.name
        )


def _write_files(message: Message, attachment_ids: Sequence[UUID]) -> None:
    wanted = list(dict.fromkeys(attachment_ids))
    if wanted:
        own = set(
            Attachment.objects.filter(
                pk__in=wanted,
                source__events__request_id=message.request_id,
                source__events__voided_at__isnull=True,
            ).values_list("pk", flat=True)
        )
        if set(wanted) - own:
            raise ServiceError("A file named is not one of this request's files.")
    MessageAttachment.objects.filter(message_id=message.pk).delete()
    for attachment_id in wanted:
        MessageAttachment.objects.create(message_id=message.pk, attachment_id=attachment_id)


@transaction.atomic
def create_draft(
    actor: Actor,
    request_id: UUID,
    *,
    kind: str = MessageKind.NEW,
    subject: str = "",
    body_text: str = "",
    to: Iterable[str | Address] = (),
    cc: Iterable[str | Address] = (),
    bcc: Iterable[str | Address] = (),
    attachment_ids: Sequence[UUID] = (),
    in_reply_to_source_id: UUID | None = None,
    letter_recipient_id: UUID | None = None,
    attach_letter: bool = False,
) -> Message:
    if kind not in MessageKind.values:
        raise ServiceError(f"Unknown message kind {kind!r}; expected one of {MessageKind.values}.")
    request = Request.objects.filter(pk=request_id).first()
    if request is None:
        raise NotFound(f"No request {request_id}.")
    message = Message.objects.create(
        request_id=request_id,
        letter_recipient_id=letter_recipient_id,
        in_reply_to_source_id=in_reply_to_source_id,
        kind=kind,
        from_address=_alias_of(request),
        subject=(subject or "").strip(),
        body_text=body_text or "",
        attach_letter=attach_letter,
        created_by_id=actor.user_id,
    )
    _write_recipients(message, to, cc, bcc)
    _write_files(message, attachment_ids)
    return message


@transaction.atomic
def update_draft(
    actor: Actor,
    message_id: UUID,
    *,
    subject: str | None = None,
    body_text: str | None = None,
    to: Iterable[str | Address] | None = None,
    cc: Iterable[str | Address] | None = None,
    bcc: Iterable[str | Address] | None = None,
    attachment_ids: Sequence[UUID] | None = None,
    attach_letter: bool | None = None,
) -> Message:
    message = lock_message(message_id)
    if message.status != MessageStatus.DRAFT:
        raise InvalidTransition(
            f"This message is {message.status}; only a draft can be edited. Take it back first."
        )
    _refuse_while_waiting(message_id)
    changes: dict = {"updated_at": timezone.now()}
    if subject is not None:
        changes["subject"] = subject.strip()
    if body_text is not None:
        changes["body_text"] = body_text
    if attach_letter is not None:
        changes["attach_letter"] = attach_letter
    Message.objects.filter(pk=message_id).update(**changes)
    if to is not None or cc is not None or bcc is not None:
        current = list(MessageRecipient.objects.filter(message_id=message_id))
        keep = lambda kind: [Address(r.address, r.name) for r in current if r.kind == kind]  # noqa: E731
        _write_recipients(
            message,
            to if to is not None else keep(RecipientKind.TO),
            cc if cc is not None else keep(RecipientKind.CC),
            bcc if bcc is not None else keep(RecipientKind.BCC),
        )
    if attachment_ids is not None:
        _write_files(message, attachment_ids)
    return get_message(message_id)


@transaction.atomic
def discard_draft(actor: Actor, message_id: UUID) -> None:
    """Delete a draft. The schema's trigger refuses anything that was ever queued."""
    message = lock_message(message_id)
    if message.status != MessageStatus.DRAFT:
        raise InvalidTransition(f"This message is {message.status} and cannot be discarded.")
    _refuse_while_waiting(message_id)
    message.delete()


def _refuse_while_waiting(message_id: UUID) -> None:
    """What an approver is reading is what goes out (P20h): a message waiting for approval is not
    edited or discarded under them. Withdrawing it gives it back."""
    if core_review.open_approval("message", message_id) is not None:
        raise InvalidTransition(
            "This message is waiting for approval. Withdraw it first to change it."
        )


def messages_for(request_id: UUID) -> list[Message]:
    return list(
        Message.objects.filter(request_id=request_id)
        .prefetch_related("recipients", "files")
        .order_by("-created_at")
    )
