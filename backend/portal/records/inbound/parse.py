"""Postmark's inbound JSON, read into one shape the rest of ingest uses.

Total by intent: mail arrives malformed often enough to matter, and a message that cannot be
parsed perfectly must still be kept and put in front of a person. Every field falls back to
something empty rather than raising; only a body that is not a JSON object at all is refused, and
that by the webhook, before this is called.
"""

import base64
import binascii
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any

from portal.records.aliases import normalize_address


@dataclass(frozen=True)
class InboundAttachment:
    name: str
    content_type: str
    size: int
    content: bytes
    # The file as delivered could not be decoded whole. What was recovered is kept; the message
    # is flagged, and the untouched original still has every byte that arrived.
    damaged: bool = False


@dataclass(frozen=True)
class InboundMessage:
    message_id: str | None
    external_ref: str | None
    from_addr: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    original_recipient: str | None
    subject: str
    text_body: str
    html_body: str
    headers: dict[str, str]
    date: datetime | None
    in_reply_to: str | None
    references: list[str]
    attachments: list[InboundAttachment] = field(default_factory=list)
    # The sender's display name as the message gives it ("Records Unit"), or "".
    from_name: str = ""
    # Every header in the order the message carries them, names as written: what "Show original"
    # lists. `headers` above is the same set keyed by lower-cased name, for lookups.
    header_list: list[tuple[str, str]] = field(default_factory=list)

    @property
    def body(self) -> str:
        """What a person reads: the text part, or the HTML part with its markup taken out, or,
        for a message that is nothing but files, their names."""
        text = self.text_body.strip() or strip_html(self.html_body)
        if not text and self.attachments:
            text = "Attachments: " + ", ".join(a.name or "(unnamed)" for a in self.attachments)
        return text


# The most HTML this will turn into text. A message body past it is cut, not refused: the
# original is kept whole, and nobody reads a megabyte of markup in an event's body.
MAX_HTML_CHARS = 1_000_000
_SKIPPED_ELEMENTS = ("script", "style", "head")
_LINE_BREAKS = frozenset(
    {"br", "/p", "/div", "/li", "/tr", "/h1", "/h2", "/h3", "/h4", "/h5", "/h6"}
)


def strip_html(html: str) -> str:
    """Readable text from an HTML-only message. Not a renderer and not a sanitiser: the result is
    stored and shown as plain text, never as markup.

    ONE forward pass with `str.find`, never a regex over the whole document. Anyone can send mail
    here, and the first version's patterns took time quadratic in the length of a message built
    to make them (P5 review, F5), with the worker stuck inside a transaction. Every `find` below
    starts where the last one ended, and an element that is opened and never closed ends the scan.
    """
    source = (html or "")[:MAX_HTML_CHARS]
    lowered = source.lower()
    out: list[str] = []
    position = 0
    while True:
        opened = source.find("<", position)
        if opened == -1:
            out.append(source[position:])
            break
        out.append(source[position:opened])
        closed = source.find(">", opened + 1)
        if closed == -1:
            break  # an unclosed tag: nothing after it is text
        tag = lowered[opened + 1 : closed].strip()
        name = tag.split(None, 1)[0].rstrip("/") if tag else ""
        position = closed + 1
        if name in _SKIPPED_ELEMENTS:
            end = lowered.find(f"</{name}", position)
            if end == -1:
                break  # never closed: the rest is script or style, not text
            after = source.find(">", end)
            if after == -1:
                break
            position = after + 1
            out.append(" ")
        elif name in _LINE_BREAKS or tag.rstrip("/").strip() == "br":
            out.append("\n")
    text = unescape("".join(out))
    lines = [" ".join(line.replace("\xa0", " ").split()) for line in text.splitlines()]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()


def parse_date(value: str | None) -> datetime | None:
    """An RFC 2822 date with a zone, or None. A date with no zone is not a moment and is dropped
    rather than guessed at; the caller falls back to when the message was captured."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return parsed if parsed.tzinfo is not None else None


def clean(value: str) -> str:
    """`value` without the two things Postgres will not store in text or jsonb: a NUL, and a
    surrogate with no partner. A sender controls the subject, the addresses and the ids, and one
    such character used to turn a delivery into a 500, which is how mail is lost (review F1)."""
    if "\x00" in value:
        value = value.replace("\x00", "")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        value = value.encode("utf-8", "replace").decode("utf-8")
    return value


def _text(value: Any) -> str:
    return clean(value) if isinstance(value, str) else ""


def _emails(full: Any, flat: Any) -> list[str]:
    """Addresses from Postmark's `ToFull`-style list, else from the flat comma-separated field."""
    found: list[str] = []
    if isinstance(full, list):
        for entry in full:
            if isinstance(entry, dict) and _text(entry.get("Email")).strip():
                found.append(normalize_address(entry["Email"]))
    if not found and isinstance(flat, str):
        found = [normalize_address(part) for part in flat.split(",") if part.strip()]
    return [address for address in found if address]


def _header_list(raw: Any) -> list[tuple[str, str]]:
    """Postmark's `Headers` as (name, value) pairs, in order, names as written."""
    out: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                name = _text(entry.get("Name")).strip()
                if name:
                    out.append((name, _text(entry.get("Value"))))
    return out


def _headers(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Header names lower-cased; when one repeats, the first is kept."""
    out: dict[str, str] = {}
    for name, value in pairs:
        out.setdefault(name.lower(), value)
    return out


def _attachments(raw: Any) -> list[InboundAttachment]:
    out: list[InboundAttachment] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        encoded = "".join(_text(entry.get("Content")).split())
        damaged = False
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            damaged = True
            try:
                content = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=False)
            except (binascii.Error, ValueError):
                content = b""
        declared = entry.get("ContentLength")
        if (
            isinstance(declared, int)
            and not isinstance(declared, bool)
            and declared != len(content)
        ):
            damaged = True
        out.append(
            InboundAttachment(
                name=_text(entry.get("Name")),
                content_type=_text(entry.get("ContentType")),
                size=len(content),
                content=content,
                damaged=damaged,
            )
        )
    return out


def message_ids(value: str | None) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value or "")


def parse_postmark(payload: dict[str, Any]) -> InboundMessage:
    header_list = _header_list(payload.get("Headers"))
    headers = _headers(header_list)
    from_full = payload.get("FromFull")
    from_addr = ""
    from_name = _text(payload.get("FromName")).strip()
    if isinstance(from_full, dict):
        from_addr = normalize_address(_text(from_full.get("Email")))
        from_name = _text(from_full.get("Name")).strip() or from_name
    from_addr = from_addr or normalize_address(_text(payload.get("From")))
    original = normalize_address(_text(payload.get("OriginalRecipient")))
    return InboundMessage(
        message_id=headers.get("message-id", "").strip() or None,
        external_ref=_text(payload.get("MessageID")).strip() or None,
        from_addr=from_addr,
        to=_emails(payload.get("ToFull"), payload.get("To")),
        cc=_emails(payload.get("CcFull"), payload.get("Cc")),
        bcc=_emails(payload.get("BccFull"), payload.get("Bcc")),
        original_recipient=original or None,
        subject=_text(payload.get("Subject")),
        text_body=_text(payload.get("TextBody")),
        html_body=_text(payload.get("HtmlBody")),
        headers=headers,
        date=parse_date(_text(payload.get("Date")) or headers.get("date")),
        in_reply_to=(message_ids(headers.get("in-reply-to")) or [None])[0],
        references=message_ids(headers.get("references")),
        attachments=_attachments(payload.get("Attachments")),
        from_name=from_name,
        header_list=header_list,
    )
