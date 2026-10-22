"""Shared by the mail services: the address rules and the one lock order.

LOCK ORDER, fixed here and nowhere else: the REQUEST row first (`records.lock_request`), then
the message row. Sending takes both; composing takes only the message's; the webhook takes
only the message's (it moves no status and touches no request row). Anything that holds a
request lock and then needs a message lock takes them in that order.
"""

import re
from uuid import UUID

from django.conf import settings
from django.db import transaction

from portal.mail.models import Message
from portal.records.services.common import NotFound, ServiceError

_ADDRESS = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def clean_address(raw: str) -> str:
    """One address, lower-cased, with a display name stripped: `Name <a@b.c>` -> `a@b.c`."""
    text = (raw or "").strip()
    if "<" in text and text.endswith(">"):
        text = text[text.rindex("<") + 1 : -1].strip()
    text = text.lower()
    if not _ADDRESS.match(text):
        raise ServiceError(f"{raw.strip()!r} is not an email address.")
    return text


def is_ours(address: str) -> bool:
    """A request alias, or anything else at the inbound domain: never a recipient of ours."""
    domain = str(settings.PORTAL["INBOUND_EMAIL_DOMAIN"]).lower()
    return address.lower().endswith("@" + domain)


def get_message(message_id: UUID) -> Message:
    message = Message.objects.filter(pk=message_id).first()
    if message is None:
        raise NotFound(f"No message {message_id}.")
    return message


def lock_message(message_id: UUID) -> Message:
    assert transaction.get_connection().in_atomic_block
    message = Message.objects.select_for_update().filter(pk=message_id).first()
    if message is None:
        raise NotFound(f"No message {message_id}.")
    return message
