"""An .eml file read into the same `InboundMessage` the Postmark path produces.

The import boundary: mail that reached SCLU some other way (a forwarded message saved to a file)
comes in through here and is then handled exactly as delivered mail is. `external_ref` is None,
because that is Postmark's own id for a delivery; the message's identity is its Message-ID header.
"""

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses

from portal.records.aliases import normalize_address
from portal.records.inbound.parse import (
    InboundAttachment,
    InboundMessage,
    clean,
    message_ids,
    parse_date,
)


def _addresses(message: EmailMessage, header: str) -> list[str]:
    values = [str(value) for value in message.get_all(header, [])]
    return [normalize_address(address) for _, address in getaddresses(values) if address]


def _part_text(part: EmailMessage | None) -> str:
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError, KeyError):
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    return content if isinstance(content, str) else ""


def parse_eml(data: bytes) -> InboundMessage:
    message = BytesParser(policy=policy.default).parsebytes(data)
    headers: dict[str, str] = {}
    header_list: list[tuple[str, str]] = []
    for name, value in message.items():
        headers.setdefault(name.lower(), str(value))
        header_list.append((clean(str(name)), clean(str(value))))
    attachments = []
    for part in message.iter_attachments():
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            content = payload
        elif part.get_content_type() == "message/rfc822" and part.is_multipart():
            # A forwarded message travels as a nested message, which has no payload to decode.
            content = part.get_payload(0).as_bytes()
        else:
            content = b""
        attachments.append(
            InboundAttachment(
                name=clean(part.get_filename() or ""),
                content_type=part.get_content_type(),
                size=len(content),
                content=content,
            )
        )
    sender = _addresses(message, "from")
    named = [name for name, _ in getaddresses([str(v) for v in message.get_all("from", [])])]
    delivered = _addresses(message, "delivered-to") or _addresses(message, "x-original-to")
    headers = {name: clean(value) for name, value in headers.items()}
    return InboundMessage(
        message_id=headers.get("message-id", "").strip() or None,
        external_ref=None,
        from_addr=sender[0] if sender else "",
        to=_addresses(message, "to"),
        cc=_addresses(message, "cc"),
        bcc=_addresses(message, "bcc"),
        original_recipient=delivered[0] if delivered else None,
        subject=clean(str(message.get("subject", ""))),
        text_body=clean(_part_text(message.get_body(preferencelist=("plain",)))),
        html_body=clean(_part_text(message.get_body(preferencelist=("html",)))),
        headers=headers,
        date=parse_date(headers.get("date")),
        in_reply_to=(message_ids(headers.get("in-reply-to")) or [None])[0],
        references=message_ids(headers.get("references")),
        attachments=attachments,
        from_name=clean(named[0].strip()) if named else "",
        header_list=header_list,
    )
