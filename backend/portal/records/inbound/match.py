"""Which request a message is for.

The answer comes from the ALIASES it was sent to. Every request has its own random address at the
inbound domain (`records/aliases.py`), given out in the letter, so a reply addressed to one alias
is a reply about one request. Nothing else places a message: not the sender, not the subject, not
a tracking number in the text. Those are how a person recognises a message; they are not proof of
which request it answers, and a wrong automatic placement is worse than a flag.

`place` decides by the ADDRESS alone, and is what the webhook runs the moment a message arrives:

    no alias that resolves           -> nothing recorded; flag `no_matching_request`, or
                                        `reply_to_ignored` when its In-Reply-To or References
                                        name a message the portal sent (`replied_to`)
    exactly one alias that resolves  -> the event goes on that request
    two or more aliases that resolve -> nothing recorded; flag `may_cover_others`

`mentions` reads the TEXT, later, in the job: a message placed on one request whose text also
names another (by its alias, or by the agency's own reference number for a request that is still
open) is ALSO flagged `may_cover_others`.

**Candidates are never written down.** The requests a flagged message might also be about are
worked out again each time someone looks (`candidates`, behind GET /sources/{id}/candidates) and
filtered to what that person may see. A flag note that named them would show a privileged
request's tracking number to everyone who can see the message. For the same reason the stored
flag itself never depends on a PRIVILEGED request: `mentions(..., for_flag=True)` leaves them
out, or the difference between "flagged" and "not flagged" would tell anyone who can send mail
here whether a guessed reference number belongs to a privileged request (P5 review, F10). People
on that request's list still see it among the candidates.
"""

import re
from dataclasses import dataclass, field

from django.conf import settings

from portal.records.aliases import ALPHABET
from portal.records.inbound.parse import InboundMessage
from portal.records.models import Request, Source
from portal.records.services import access

_LOCAL = rf"[{ALPHABET}]{{4}}(?:-[{ALPHABET}]{{4}}){{2}}"
# An agency's reference number shorter than this is too likely to occur in a message by chance.
_MIN_REFERENCE = 5


def _domain() -> str:
    return str(settings.PORTAL["INBOUND_EMAIL_DOMAIN"]).lower()


def _alias_pattern() -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9._-])({_LOCAL})@{re.escape(_domain())}(?![A-Za-z0-9-])", re.I
    )


def find_aliases(message: InboundMessage) -> set[str]:
    """The aliases this message was ADDRESSED to: every recipient field, and the address Postmark
    says it was actually delivered for."""
    suffix = "@" + _domain()
    recipients = [*message.to, *message.cc, *message.bcc]
    if message.original_recipient:
        recipients.append(message.original_recipient)
    pattern = _alias_pattern()
    return {
        address.lower()
        for address in recipients
        if address.lower().endswith(suffix) and pattern.fullmatch(address.lower())
    }


def mentioned_aliases(text: str) -> set[str]:
    """Aliases that appear anywhere in a piece of text."""
    return {f"{local.lower()}@{_domain()}" for local in _alias_pattern().findall(text or "")}


@dataclass(frozen=True)
class Placement:
    # The one request the message was addressed to, or None.
    request: Request | None
    # Every request it was addressed to (more than one means it was placed on none).
    addressed: list[Request] = field(default_factory=list)
    # Why it needs a person, as a `FLAG_REASONS` word, or None when it simply landed.
    flag_reason: str | None = None
    # A sentence for the flag. Names no request.
    note: str = ""


REPLY_TO_NOTE = (
    "This message answers one the portal sent, but it was sent to another address instead of "
    "the request's own."
)


def _id_local(message_id: str) -> str:
    """`<b8e5...@mtasv.net>` -> `b8e5...`: the part a provider's own message id is."""
    return message_id.strip().strip("<>").split("@", 1)[0].strip().lower()


def replied_to(message: InboundMessage, *, for_flag: bool = False) -> list[Request]:
    """The requests whose mail this message says it answers: its In-Reply-To or References name a
    message the portal sent. A sent message is a `sent_email` source whose `external_ref` is
    `sent:<the provider's id>`, and the provider puts that id in the Message-ID it gives the mail,
    so the reply carries it back. These are evidence for a person, never a placement: a header is
    written by the sender (module docstring). `for_flag=True` leaves privileged requests out, as
    `mentions` does."""
    locals_ = {_id_local(i) for i in [message.in_reply_to or "", *message.references]}
    refs = [f"sent:{local}" for local in locals_ if local]
    if not refs:
        return []
    sent = Source.objects.filter(source_type="sent_email", external_ref__in=refs)
    found = (
        Request.objects.filter(events__source__in=sent)
        .select_related("current_status")
        .distinct()
        .order_by("tracking_number")
    )
    return [r for r in found if not (for_flag and access.is_privileged(r))]


def place(message: InboundMessage) -> Placement:
    """Where a message goes, from the addresses it was sent to and nothing else. Cheap (one
    indexed query, and one more for an unaddressed reply), so the webhook can run it before the
    delivery is acknowledged."""
    addressed = list(
        Request.objects.filter(inbound_alias_email__in=find_aliases(message))
        .select_related("current_status")
        .order_by("tracking_number")
    )
    if not addressed:
        if replied_to(message, for_flag=True):
            return Placement(None, [], "reply_to_ignored", REPLY_TO_NOTE)
        return Placement(
            None, [], "no_matching_request", "This message was not sent to any request's address."
        )
    if len(addressed) > 1:
        return Placement(
            None,
            addressed,
            "may_cover_others",
            f"This message was sent to the addresses of {len(addressed)} requests, so it was not "
            "placed on any of them.",
        )
    request = addressed[0]
    if request.current_status.is_terminal:
        # It is this request's mail, so it goes on it; but a request nobody is watching any
        # more has just received something, and that needs a person to see it.
        return Placement(
            request,
            addressed,
            "other",
            "This message arrived for a request that is already closed or withdrawn.",
        )
    return Placement(request, addressed)


MENTIONS_NOTE = "The text of this message also mentions another request."


def mentions(message: InboundMessage, exclude: set, *, for_flag: bool = False) -> list[Request]:
    """Requests the TEXT of the message names: by alias, or by an agency reference number of a
    request that is still open. `for_flag=True` leaves privileged requests out; see the module
    docstring."""
    text = f"{message.subject}\n{message.body}"
    found = {
        r.pk: r for r in Request.objects.filter(inbound_alias_email__in=mentioned_aliases(text))
    }
    lowered = text.lower()
    open_with_reference = Request.objects.filter(
        agency_reference_number__isnull=False, current_status__is_terminal=False
    ).exclude(agency_reference_number="")
    for request in open_with_reference:
        reference = request.agency_reference_number.strip().lower()
        if len(reference) >= _MIN_REFERENCE and _names(lowered, reference):
            found[request.pk] = request
    return sorted(
        (
            r
            for pk, r in found.items()
            if pk not in exclude and not (for_flag and access.is_privileged(r))
        ),
        key=lambda r: r.tracking_number,
    )


def _names(lowered_text: str, reference: str) -> bool:
    """Whether `reference` occurs in the text as a whole token. `str.find`, not a regex built from
    data an agency typed."""
    start = lowered_text.find(reference)
    while start != -1:
        before = lowered_text[start - 1] if start else " "
        end = start + len(reference)
        after = lowered_text[end] if end < len(lowered_text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = lowered_text.find(reference, start + 1)
    return False


def candidates(message: InboundMessage) -> list[Request]:
    """Every request this message may also be about: the ones it was addressed to when there were
    several, the ones whose mail it answers, and the ones its text names. Unfiltered; the caller
    shows a person only theirs."""
    placement = place(message)
    placed_on = {placement.request.pk} if placement.request is not None else set()
    found = {r.pk: r for r in placement.addressed if r.pk not in placed_on}
    for request in replied_to(message):
        if request.pk not in placed_on:
            found[request.pk] = request
    for request in mentions(message, placed_on):
        found[request.pk] = request
    return sorted(found.values(), key=lambda r: r.tracking_number)
