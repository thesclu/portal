"""The seam: a draft becomes requests, one per recipient, in two steps: reserve, then file.

Composition, not new ledger machinery. Everything statutory still happens inside
`records.services.filing.file_request`, which is the ONE door a request is filed through,
whichever screen calls it.

RESERVE, THEN FILE (P10). The design reserves a tracking number and an alias before anything is
sent, because the cover message goes out from the alias and a web form needs it as its contact
address. So a recipient has two moments:

  reserve_recipient   the letter is rendered and frozen into a `records.requests` row in
                      `pending_filing`; the recipient gets `request_id` and the revision the draft
                      was started from, and stays `pending`. Refused while any placeholder in the
                      letter is unresolved: a letter addressed to nobody is not reserved.
  file                `file_request` on that request. It may be called from here
                      (`file_recipient`, `file_draft`) or from the request's own page in records,
                      and `records` knows nothing of `drafting`. So the recipient is stamped
                      `filed` by a callback this app registers with `records_filing.after_filed`
                      (see `apps.py`), which runs inside `file_request`'s transaction.

THE LOCKS, which are the point of this module.
Reserving is a check-then-act on the recipient: read "pending, no request", do irreversible work
(`reserve_request` mints a sequential tracking number and a unique inbound alias EACH TIME), write
`request_id`. Two concurrent calls -- a double click, a retried POST -- would both pass the check
and one request would be orphaned: a burnt tracking number, a live alias routing replies to the
wrong place, and nothing deletes in `records`. Hence `select_for_update` on the recipient row and
a re-read under it. No request exists yet, so there is nothing else to lock.

Filing is serialised by the REQUEST's lock, which `file_request` takes first. `file_recipient`
does not lock the recipient before calling it; the callback writes the recipient afterwards. **One
lock order across the seam: the request row, then the recipient.** A second caller, through either
door, finds the request no longer `pending_filing` and gets `InvalidTransition`. P3b's F1 was the
opposite order (recipient, then request) meeting itself across two waves and deadlocking; do not
reintroduce it. A recipient lock IS held when a request lock is taken in one case, and it is
safe only for the reason given: `file_recipient` on a recipient it has just reserved in the same
transaction, whose request is invisible to every other transaction until this one commits, so
nothing can be waiting on it.

A WAVE (`file_draft`) is one transaction, and takes the locks of every ALREADY-RESERVED request it
will file before it files any, in id order. Under the API's `ATOMIC_REQUESTS` its locks are all
held to the end of the response anyway; taking them one recipient at a time, in recipient order,
deadlocked against `access.on_person_disabled`, which takes several requests' locks in id order.
Anything that holds more than one request lock takes them in id order.

ONLY FILING IS TOLD ACROSS THE SEAM. A reserved request can also be withdrawn, or have its status
overridden, from its own page, and no callback announces that. So the recipient's stored status is
brought back into line by `reconcile_recipient`, which every action here runs first: a request that
is withdrawn makes its recipient `skipped`, one that has moved on past filing makes it `filed`.
Between such a change and the next action the stored status can lag; the API returns the request's
own status beside it for that reason.

REVISIONS are not minted here at all (DECISIONS, "Design v2"). A draft records the revision it was
started from; reservation copies it onto the recipient; filing mints nothing.

TRANSACTION BOUNDARY for a wave (`file_draft`): one savepoint per recipient. Nine agencies must
not roll back eight good filings because the ninth row is malformed.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from uuid import UUID

from django.db import DatabaseError, transaction
from django.utils import timezone

from portal.core.services import review as core_review
from portal.drafting import render
from portal.drafting.models import LetterDraft, LetterDraftItem, LetterRecipient
from portal.drafting.services import fork as fork_service
from portal.drafting.services import library as library_service
from portal.drafting.services.common import PROSE_ITEM_NUMBER, draft_item_rows, get_or_404
from portal.drafting.services.common import letter_of_draft as _letter_of_draft
from portal.records.actors import Actor
from portal.records.models import Agency, AgencyContact, AuditLog, Request, RequestItem
from portal.records.services import edits as edits_service
from portal.records.services import filing as records_filing
from portal.records.services import items as items_service
from portal.records.services import requests as requests_service
from portal.records.services import status as status_service
from portal.records.services.common import (
    InvalidTransition,
    NoCalendarMapped,
    NotFound,
    ServiceError,
    agency_calendar,
    local_date,
    lock_request,
    require_human,
)
from portal.records.services.status import PRE_FILING

logger = logging.getLogger(__name__)


# Postgres's own error text names every column of the failing row (the P3 gate caught it leaking
# a tracking number and an inbound alias in one response). `reason` is returned to the client, so
# a database refusal gets this fixed string and the detail goes to the log instead.
CONSTRAINT_REASON = "refused by a database constraint; see the server log"

# What a letter says about how it was sent, after the word "VIA" that the shell itself carries
# (the design's shell reads "VIA {{CHANNEL_METHOD}}"). A portal's is its own kind, upper-cased,
# from the agency; a portal whose kind is not on record resolves to nothing and so is reported as
# unresolved, rather than this module inventing a phrase for it.
_CHANNEL_METHOD = {"email": "EMAIL", "mail": "U.S. MAIL"}


@dataclass
class RecipientOutcome:
    recipient_id: UUID
    agency_id: UUID | None
    filed: bool
    request_id: UUID | None = None
    tracking_number: str | None = None
    # Why this recipient was not filed: 'held', 'skipped', 'already filed', or the service error.
    # See the note at the bottom of this module about where a failure does and does not survive.
    reason: str | None = None


@dataclass
class FileDraftResult:
    draft_id: UUID
    # The revision the draft was started from, which is what every recipient points at.
    revision_id: UUID | None
    revision: int | None
    outcomes: list[RecipientOutcome]

    @property
    def filed_count(self) -> int:
        return sum(1 for o in self.outcomes if o.filed)


# ---- what fills the blanks ----------------------------------------------------------------------


def letter_date(agency: Agency, when: datetime) -> date:
    """The civil date `when` falls on where the agency is, as `file_request` reads a filing date.
    An agency whose law has no calendar mapped falls back to the UTC date."""
    try:
        return local_date(when, agency_calendar(agency))
    except NoCalendarMapped:
        return when.astimezone(UTC).date()


def _written(on: date) -> str:
    return f"{on:%B} {on.day}, {on.year}"


def _channel_method(channel: str, agency: Agency) -> str:
    if channel == "portal":
        return (agency.portal_kind or "").upper()
    return _CHANNEL_METHOD.get(channel, "")


def _address(channel: str, agency: Agency) -> str:
    """Where this letter goes, for the channel it goes by. For the post it is the two postal
    lines, and blank unless both are on record: half an address is not one."""
    if channel == "mail":
        lines = [agency.mailing_street or "", agency.mailing_city_state_zip or ""]
        return "\n".join(lines) if all(lines) else ""
    if channel == "email":
        return agency.default_request_email or ""
    if channel == "portal":
        return agency.portal_url or ""
    return agency.default_request_email or agency.portal_url or ""


def agency_values(
    agency: Agency,
    *,
    channel: str,
    contact: AgencyContact | None = None,
    overrides: dict | None = None,
    when: datetime | None = None,
) -> dict[str, str]:
    """Every placeholder's value for a letter to this agency by this channel, dated `when`.

    `RECIPIENT_OFFICE` is the chosen contact when there is one, else the agency's own recipient
    office. Blank values are dropped by `render.agency_placeholders`, so they are reported as
    unresolved instead of printing as nothing.
    """
    return render.agency_placeholders(
        agency_name=agency.name,
        agency_abbreviation=agency.short_name or "",
        recipient_office=(contact.name if contact else "") or agency.recipient_office or "",
        address=_address(channel, agency),
        street_address=agency.mailing_street or "",
        city_state_zip=agency.mailing_city_state_zip or "",
        channel_method=_channel_method(channel, agency),
        filing_date=_written(letter_date(agency, when or timezone.now())),
        overrides=overrides,
    )


def placeholders_for(
    recipient: LetterRecipient, agency: Agency, *, when: datetime | None = None
) -> dict[str, str]:
    contact = recipient.agency_contact if recipient.agency_contact_id else None
    return agency_values(
        agency,
        channel=recipient.channel,
        contact=contact,
        overrides=recipient.placeholder_overrides,
        when=when,
    )


@dataclass(frozen=True)
class Preview:
    text: str
    # Placeholders that would still be standing in the letter if it went out now.
    unresolved: tuple[str, ...]


def preview(recipient_id: UUID) -> Preview:
    """The letter this recipient would receive, or, once reserved, the letter it WILL receive.

    Before reservation it is rendered now, through the same function reservation uses, so "the
    preview is what goes out" is a property of the code. After reservation the letter is frozen in
    `requests.request_text` and that is returned as it is: it is dated when it was reserved, and
    editing the draft or the agency afterwards does not change it.
    """
    recipient = get_or_404(LetterRecipient, recipient_id, "recipient")
    if recipient.request_id is not None:
        return Preview(
            text=Request.objects.get(pk=recipient.request_id).request_text, unresolved=()
        )
    letter = _letter_of_draft(LetterDraft.objects.get(pk=recipient.draft_id))
    placeholders = placeholders_for(recipient, recipient.agency)
    return Preview(
        text=render.render_text(letter, placeholders),
        unresolved=render.unresolved_placeholders(letter, placeholders),
    )


def preview_for_agency(draft_id: UUID, agency_id: UUID, *, channel: str | None = None) -> Preview:
    """ "Preview as": the draft as it would read for an agency that is not a recipient yet. The
    channel is the one given, else the agency's own filing method."""
    draft = get_or_404(LetterDraft, draft_id, "draft")
    agency = get_or_404(Agency, agency_id, "agency")
    letter = _letter_of_draft(draft)
    placeholders = agency_values(agency, channel=channel or agency.filing_method or "")
    return Preview(
        text=render.render_text(letter, placeholders),
        unresolved=render.unresolved_placeholders(letter, placeholders),
    )


def draft_items_as_new_items(
    draft_id: UUID, all_rows: Sequence[LetterDraftItem] | None = None
) -> list[requests_service.NewItem]:
    """Translate draft items into `reserve_request`'s batch, parents first.

    Two different linkages meet here. Draft items link to a parent by `parent_item_id` (a uuid);
    `NewItem` resolves a parent by `item_number`, and only against an EARLIER item in the same
    batch. So the numbers have to be unambiguous and the order has to be topological.

    Nothing in the schema enforces unique `(draft_id, item_number)`, so two draft items really can
    share a number -- and then `reserve_request` would silently pick whichever it saw first as the
    parent. Refuse before calling rather than let that happen quietly.
    """
    if all_rows is None:
        all_rows = draft_item_rows(draft_id)
    rows = [row for row in all_rows if row.item_number != PROSE_ITEM_NUMBER]
    seen: dict[str, UUID] = {}
    for row in rows:
        if row.item_number in seen:
            raise ServiceError(
                f"Two items in this draft are both numbered {row.item_number!r}. Item numbers are "
                "how a parent is named when the request is created, so they have to be unique "
                "within a draft before it can be filed."
            )
        seen[row.item_number] = row.pk

    by_id = {row.pk: row for row in rows}
    children: dict[UUID | None, list] = {}
    for row in rows:
        parent = row.parent_item_id if row.parent_item_id in by_id else None
        children.setdefault(parent, []).append(row)

    ordered: list[requests_service.NewItem] = []

    def walk(parent_id: UUID | None) -> None:
        for row in children.get(parent_id, ()):
            parent_number = by_id[row.parent_item_id].item_number if parent_id is not None else None
            ordered.append(
                requests_service.NewItem(
                    item_number=row.item_number,
                    description=row.body,
                    parent_item_number=parent_number,
                    sort_order=row.sort_order,
                    provision=row.provision,
                )
            )
            walk(row.pk)

    walk(None)
    if len(ordered) != len(rows):
        # A parent chain that never reaches a root: a self-parent, or a cycle. The DFS above would
        # simply never emit those rows, so filing would drop asks from the letter without a word.
        raise ServiceError(
            "Some items in this draft have a parent chain that loops back on itself, so they "
            "cannot be put in an order where every parent comes before its children."
        )
    return ordered


# ---- reserve ------------------------------------------------------------------------------------


@transaction.atomic
def reserve_recipient(
    actor: Actor, recipient_id: UUID, *, requester_user_id: UUID | None = None
) -> LetterRecipient:
    """Freeze this recipient's letter into a reserved request. The recipient stays `pending`.

    Refused, naming them, while any placeholder in the letter is unresolved. (Until P10 the
    preview reported them and filing went ahead anyway.)
    """
    # One table in the FROM, deliberately -- the same single-table shape `common.lock_request`
    # uses. Adding `select_related` here breaks two ways: `agency_contact` is nullable, so the
    # join is an outer one and Postgres refuses FOR UPDATE on its nullable side; and narrowing it
    # with `of=("self",)` emits `FOR UPDATE OF "drafting"."letter_recipients"`, which Postgres
    # rejects because FOR UPDATE OF wants an unqualified relation name and every db_table in this
    # app is schema-qualified.
    recipient = LetterRecipient.objects.select_for_update().filter(pk=recipient_id).first()
    if recipient is None:
        raise NotFound(f"No recipient {recipient_id}.")
    # THE RE-CHECK, after the lock: the second of two concurrent calls sees the request the first
    # one made and stops.
    if recipient.request_id is not None:
        raise InvalidTransition("This recipient already has a reserved request.")
    if recipient.status != LetterRecipient.PENDING:
        raise InvalidTransition(
            f"This recipient is {recipient.status}; only a pending one can be reserved."
        )
    draft = LetterDraft.objects.get(pk=recipient.draft_id)
    requester_id = requester_user_id or draft.created_by_user_id
    if requester_id is None:
        raise ServiceError(
            "This draft has no author and no requester was named, so there is nobody to file it "
            "on behalf of."
        )
    # ONE read of the draft's items, from which both the letter and the request's items are made.
    rows = draft_item_rows(draft.pk)
    items = draft_items_as_new_items(draft.pk, rows)
    agency = recipient.agency
    letter = _letter_of_draft(draft, rows)
    placeholders = placeholders_for(recipient, agency)
    unresolved = render.unresolved_placeholders(letter, placeholders)
    if unresolved:
        raise ServiceError(
            f"The letter to {agency.name} still has blanks nothing fills in: "
            f"{', '.join(unresolved)}. Complete the agency's details, or give this recipient its "
            "own value, and reserve again."
        )
    if fork_service.is_fork_draft(draft):
        # P21c: the letter is in the drafting tool; the same rule, asked of its master filled for
        # this agency (in memory, nothing stored).
        fork_service.require_complete(actor, draft, recipient)
    revision_id = draft.source_revision_id
    if revision_id is None and draft.source_template_id is not None:
        # A draft started before drafts recorded their revision. The best that can still be said
        # is what the template says now; before P10 this is exactly what filing recorded.
        revision_id = library_service.mint_or_reuse_revision(
            draft.source_template_id,
            created_by_user_id=actor.user_id,
            summary=library_service.STARTED_SUMMARY,
        ).pk
        LetterDraft.objects.filter(pk=draft.pk).update(source_revision_id=revision_id)
    request = requests_service.reserve_request(
        actor,
        agency_id=recipient.agency_id,
        requester_user_id=requester_id,
        # The subject and each ask are stored on the request as well as inside its text, and are
        # what the docket and the request page show. They get the same values the text got, so
        # no `{{NAME}}` is left standing anywhere on a reserved request.
        subject=render.resolve(letter.subject, placeholders),
        request_text=render.render_text(letter, placeholders),
        items=[
            replace(item, description=render.resolve(item.description, placeholders))
            for item in items
        ],
        sensitivity=draft.sensitivity,
    )
    LetterRecipient.objects.filter(pk=recipient_id).update(
        request_id=request.pk, template_revision_id=revision_id
    )
    if fork_service.is_fork_draft(draft):
        # P21c: then the drafting tool makes this agency's letter (its merge, with the tracking
        # number and request address just reserved), and the request's words are read from it.
        # A failure leaves the reservation standing and is said in stage 4, where it is retried.
        made = fork_service.make_letters(
            actor, draft, [LetterRecipient.objects.get(pk=recipient_id)]
        )
        if recipient_id in made.failed:
            logger.warning("recipient %s: %s", recipient_id, made.failed[recipient_id])
        # The draft names one more request: who may open its wave can have moved.
        fork_service.on_draft_changed(draft.pk, actor.user_id)
    return LetterRecipient.objects.get(pk=recipient_id)


def letter_changed_at(recipient: LetterRecipient) -> datetime | None:
    """When this recipient's letter last changed: its draft's last save, or, once reserved, the
    last audited edit of the request's subject, text or items (adding an item writes one too),
    whichever is later. An intern's
    approval older than this no longer clears the letter (P20h)."""
    times: list[datetime] = []
    draft_saved = (
        LetterDraft.objects.filter(pk=recipient.draft_id)
        .values_list("updated_at", flat=True)
        .first()
    )
    if draft_saved is not None:
        times.append(draft_saved)
    if recipient.request_id is not None:
        item_ids = RequestItem.objects.filter(request_id=recipient.request_id).values("pk")
        edited = (
            (
                AuditLog.objects.filter(
                    entity_type="requests",
                    entity_id=recipient.request_id,
                    field_name__in=("subject", "request_text"),
                )
                | AuditLog.objects.filter(entity_type="request_items", entity_id__in=item_ids)
            )
            .order_by("-edited_at")
            .values_list("edited_at", flat=True)
            .first()
        )
        if edited is not None:
            times.append(edited)
    return max(times, default=None)


REFRESH_NOTE = "The letter changed before it was submitted for approval again."


@transaction.atomic
def refresh_reserved(actor: Actor, recipient_id: UUID) -> bool:
    """Freeze a reserved, unfiled recipient's letter again from its draft (P20h, 238).

    An intern whose letter was sent back edits the draft ("Edit letter") and submits again; what
    an approver reads and what goes out is the request's frozen text, so it is brought back into
    line here first: the subject and text through the audited edit, each changed item's wording
    the same way, each new item added. An item taken out of the draft cannot be taken out of a
    reserved request (nothing deletes in records), so that is refused, before anything is written.
    Returns whether anything changed. Lock order: the request, then (nothing else).
    """
    require_human(actor, "Refreshing a reserved letter")
    recipient = get_or_404(LetterRecipient, recipient_id, "recipient")
    if recipient.request_id is None:
        raise InvalidTransition("This letter is not reserved yet; there is nothing to refresh.")
    request = lock_request(recipient.request_id)
    if request.current_status_id not in PRE_FILING:
        raise InvalidTransition(f"{request.tracking_number} is already filed; its letter is out.")
    draft = LetterDraft.objects.get(pk=recipient.draft_id)
    if fork_service.is_fork_draft(draft):
        # P21c: the letter is the drafting tool's, not the portal draft's (which holds no words):
        # its words are read again from the agency's letter there.
        before = request.request_text
        fork_service.refresh_before_filing(actor, recipient)
        return Request.objects.get(pk=request.pk).request_text != before
    rows = draft_item_rows(draft.pk)
    items = draft_items_as_new_items(draft.pk, rows)
    agency = recipient.agency
    letter = _letter_of_draft(draft, rows)
    placeholders = placeholders_for(recipient, agency)
    unresolved = render.unresolved_placeholders(letter, placeholders)
    if unresolved:
        raise ServiceError(
            f"The letter to {agency.name} still has blanks nothing fills in: "
            f"{', '.join(unresolved)}. Complete them and submit again."
        )
    existing = {i.item_number: i for i in RequestItem.objects.filter(request_id=request.pk)}
    wanted = {item.item_number for item in items}
    removed = sorted(set(existing) - wanted)
    if removed:
        raise ServiceError(
            f"Item {', '.join(removed)} is no longer in the letter, but a reserved request keeps "
            "its items. Put it back, or narrow its wording instead."
        )
    changed = False
    subject = render.resolve(letter.subject, placeholders)
    text = render.render_text(letter, placeholders)
    if subject != request.subject:
        edits_service.edit_with_audit(
            actor, "requests", request.pk, "subject", subject, REFRESH_NOTE
        )
        changed = True
    if text != request.request_text:
        edits_service.edit_with_audit(
            actor, "requests", request.pk, "request_text", text, REFRESH_NOTE
        )
        changed = True
    for item in items:
        wording = render.resolve(item.description, placeholders)
        row = existing.get(item.item_number)
        if row is None:
            items_service.add_item(
                actor, request.pk, replace(item, description=wording), REFRESH_NOTE
            )
            changed = True
        elif row.description != wording:
            edits_service.edit_with_audit(
                actor, "request_items", row.pk, "description", wording, REFRESH_NOTE
            )
            changed = True
    return changed


# ---- file ---------------------------------------------------------------------------------------


def stamp_recipient_filed(actor: Actor, request: Request, filed_at: datetime) -> None:
    """The callback registered with `records_filing.after_filed` (see `apps.py`).

    Runs inside `file_request`'s transaction, under the request's lock, whichever door filed it.
    A request that did not come from a draft matches no row. Lock order: the request (already
    held), then the recipient (this UPDATE).
    """
    # Whatever the recipient's own row said (pending; or skipped, if its withdrawn request was
    # reopened from its page), the letter has now gone out, and the ledger is what is true.
    LetterRecipient.objects.filter(request_id=request.pk).exclude(
        status=LetterRecipient.FILED
    ).update(status=LetterRecipient.FILED, filed_at=filed_at, hold_reason=None)


def reconcile_recipient(recipient_id: UUID) -> LetterRecipient:
    """Bring a reserved recipient's stored status back into line with its request.

    Filing is announced across the seam; nothing else is. A reserved request can be withdrawn, or
    moved on by a status override, from its own page, and the recipient would otherwise sit
    `pending` for good: not fileable, not skippable, retried by every wave. Called at the start of
    every action here, inside a transaction. Lock order: the request, then the recipient.

      request withdrawn                 -> recipient `skipped`
      request anywhere past filing      -> recipient `filed`, dated as the request is, or now
      request still pending filing, or
      waiting for approval (P20h)       -> nothing to do: the letter is not out yet
    """
    recipient = get_or_404(LetterRecipient, recipient_id, "recipient")
    if recipient.request_id is None or recipient.status == LetterRecipient.FILED:
        return recipient
    request = lock_request(recipient.request_id)
    if request.current_status_id in PRE_FILING:
        return recipient
    rows = LetterRecipient.objects.filter(pk=recipient_id).exclude(status=LetterRecipient.FILED)
    if request.current_status_id == "withdrawn":
        rows.update(status=LetterRecipient.SKIPPED, hold_reason=None)
    else:
        rows.update(
            status=LetterRecipient.FILED,
            filed_at=request.filed_at or timezone.now(),
            hold_reason=None,
        )
    return LetterRecipient.objects.get(pk=recipient_id)


@transaction.atomic
def file_recipient(
    actor: Actor,
    recipient_id: UUID,
    *,
    filed_at: datetime | None = None,
    channel: str | None = None,
    agency_reference_number: str | None = None,
    request_provision: str | None = None,
    requester_user_id: UUID | None = None,
    source_id: UUID | None = None,
    cleared: bool = False,
    tracking_number: str | None = None,
    fork_version_id: str | None = None,
) -> LetterRecipient:
    """File one recipient's letter, reserving it first if it has not been. `source_id` is the
    sent copy when the portal itself sent it (P11), carried onto the submission event; a
    carrier's `tracking_number` (P19e) is written into that event's text.

    P20h: an intern files only a letter an approver has approved, unchanged since (the recipient's
    approval, `core/services/review.py`); every other role files freely. `cleared=True` says the
    caller established that already (the mail worker filing a message that was queued on its
    approval); either way `file_request` is then told the filing is released.

    P21c: for a letter written in the drafting tool, `fork_version_id` names the version that was
    sent (the mail worker's, whose PDF it attached); otherwise the letter's current version.

    The recipient row is NOT locked here before `file_request`: see the module docstring. A second
    call on a letter that is already out gets `InvalidTransition` from `file_request` itself.
    """
    recipient = reconcile_recipient(recipient_id)
    fork_draft = fork_service.is_fork_draft(LetterDraft.objects.get(pk=recipient.draft_id))
    if (
        fork_draft
        and recipient.request_id is not None
        and recipient.status == LetterRecipient.PENDING
    ):
        # P21c: the letter is the drafting tool's; its words on the request are made this
        # version's first (an audited edit when they changed), so an approval of older words
        # does not clear newer ones below.
        recipient = fork_service.refresh_before_filing(actor, recipient, version_id=fork_version_id)
    if not cleared and actor.is_human:
        core_review.require_cleared(
            actor.user_id, "recipient", recipient.pk, letter_changed_at(recipient)
        )
    if recipient.status == LetterRecipient.FILED:
        raise InvalidTransition("This recipient's letter has already been filed.")
    if recipient.status == LetterRecipient.SKIPPED:
        raise InvalidTransition(
            "This recipient was skipped"
            + (
                " and its request withdrawn; it cannot be sent from here."
                if recipient.request_id is not None
                else "; set it back to pending before filing it."
            )
        )
    if recipient.status == LetterRecipient.HELD:
        raise InvalidTransition(
            f"This recipient is held ({recipient.hold_reason}); set it back to pending first."
        )
    if channel and channel != recipient.channel:
        if recipient.request_id is not None:
            # The letter's own text says how it was sent and where to ("VIA EMAIL", the address).
            raise ServiceError(
                f"This letter was reserved to go by {recipient.channel} and says so; it cannot "
                f"be filed here as {channel}."
            )
        # Not reserved yet: the letter has not been written, so it is written for this channel.
        LetterRecipient.objects.filter(pk=recipient_id).update(channel=channel)
    if recipient.request_id is None:
        recipient = reserve_recipient(actor, recipient_id, requester_user_id=requester_user_id)
        if fork_draft and recipient.fork_document_id is None:
            # Reserved just now and the drafting tool could not make its letter: nothing to send.
            recipient = fork_service.refresh_before_filing(actor, recipient)
    assert recipient.request_id is not None
    tracking = (tracking_number or "").strip()
    records_filing.file_request(
        actor,
        recipient.request_id,
        filed_at=filed_at or timezone.now(),
        channel=recipient.channel,
        request_provision=request_provision,
        source_id=source_id,
        display_body=(
            f"Request submitted. Tracking number {tracking}." if tracking else "Request submitted."
        ),
        released=True,  # an intern's clearance was checked above, or by the caller (`cleared`)
    )
    if agency_reference_number and agency_reference_number.strip():
        edits_service.edit_with_audit(
            actor,
            "requests",
            recipient.request_id,
            "agency_reference_number",
            agency_reference_number.strip(),
            "Recorded when the letter was filed.",
        )
    return LetterRecipient.objects.get(pk=recipient_id)


@transaction.atomic
def skip_recipient(actor: Actor, recipient_id: UUID, note: str | None = None) -> LetterRecipient:
    """Decide not to send this recipient its letter.

    If it was already reserved, its request exists in `records` for good, so skipping also
    withdraws that request, by a person, with the note saying why. The request's lock is taken
    first and its status read under it, so a skip and a filing of the same letter cannot both
    succeed: whichever takes the lock second is refused.
    """
    require_human(actor, "Skipping a recipient")
    recipient = reconcile_recipient(recipient_id)
    if recipient.status == LetterRecipient.SKIPPED and recipient.request_id is not None:
        # Already skipped and its request already withdrawn (here, or from the request's page).
        return recipient
    if recipient.status == LetterRecipient.FILED:
        raise InvalidTransition(
            "This recipient has been filed; its letter is already out the door."
        )
    if recipient.request_id is None:
        locked = LetterRecipient.objects.select_for_update().get(pk=recipient_id)
        if locked.request_id is not None:
            raise InvalidTransition("This recipient was reserved a moment ago; try again.")
        LetterRecipient.objects.filter(pk=recipient_id).update(status=LetterRecipient.SKIPPED)
        return LetterRecipient.objects.get(pk=recipient_id)
    if not note or not note.strip():
        raise ServiceError(
            "This recipient's request is already reserved, so skipping it withdraws that request; "
            "that needs a note saying why."
        )
    request = lock_request(recipient.request_id)
    if request.current_status_id != "pending_filing":
        raise InvalidTransition(
            f"{request.tracking_number} is {request.current_status_id!r}, not 'pending_filing'; "
            "it can no longer be withdrawn from here."
        )
    status_service.override_status(actor, request.pk, "withdrawn", note)
    LetterRecipient.objects.filter(pk=recipient_id).exclude(status=LetterRecipient.FILED).update(
        status=LetterRecipient.SKIPPED
    )
    return LetterRecipient.objects.get(pk=recipient_id)


@transaction.atomic
def file_draft(
    actor: Actor,
    draft_id: UUID,
    *,
    filed_at: datetime | None = None,
    requester_user_id: UUID | None = None,
    recipient_ids: Sequence[UUID] | None = None,
    request_provision: str | None = None,
) -> FileDraftResult:
    """File every pending recipient of this draft, reserving where needed, each in its own
    savepoint. `held` and `skipped` recipients stay behind and can be filed later, alone."""
    draft = get_or_404(LetterDraft, draft_id, "draft")
    # P20h: an intern's recipients each need their own approval; `file_recipient` asks it per
    # recipient, so a wave files the approved ones and reports the rest with the reason.
    if (requester_user_id or draft.created_by_user_id) is None:
        raise ServiceError(
            "This draft has no author and no requester was named, so there is nobody to file it "
            "on behalf of."
        )
    # Validate the item translation ONCE, before anything irreversible: a duplicate item number is
    # a property of the draft, so failing on the first recipient and filing the other eight would
    # be the worst possible outcome.
    draft_items_as_new_items(draft_id)

    rows = LetterRecipient.objects.filter(draft_id=draft_id)
    if recipient_ids is not None:
        wanted = set(recipient_ids)
        # `pk__in` silently drops an id that names no row of this draft -- a typo, an id copied
        # from the wrong draft, or a recipient already deleted -- instead of saying so.
        found = set(rows.filter(pk__in=wanted).values_list("id", flat=True))
        unknown = wanted - found
        if unknown:
            raise ServiceError(
                f"recipient_ids named {sorted(str(u) for u in unknown)}, which are not "
                "recipients of this draft."
            )
        rows = rows.filter(pk__in=wanted)

    # Every request this wave may file that already exists, locked now, in id order: see "A
    # WAVE" in the module docstring. Requests the wave reserves itself need no such care.
    for request_id in sorted(
        rows.filter(request_id__isnull=False)
        .exclude(status=LetterRecipient.FILED)
        .values_list("request_id", flat=True)
    ):
        lock_request(request_id)
    outcomes = [
        _file_one(actor, recipient_id, filed_at, requester_user_id, request_provision)
        for recipient_id in list(rows.order_by("created_at", "id").values_list("id", flat=True))
    ]
    revision = draft.source_revision
    return FileDraftResult(
        draft_id=draft_id,
        revision_id=revision.pk if revision else None,
        revision=revision.revision if revision else None,
        outcomes=outcomes,
    )


def _file_one(
    actor: Actor,
    recipient_id: UUID,
    filed_at: datetime | None,
    requester_user_id: UUID | None,
    request_provision: str | None,
) -> RecipientOutcome:
    if not LetterRecipient.objects.filter(pk=recipient_id).exists():
        return RecipientOutcome(recipient_id, None, False, reason="no such recipient")
    row = reconcile_recipient(recipient_id)
    if row.status != LetterRecipient.PENDING:
        return RecipientOutcome(
            recipient_id=recipient_id,
            agency_id=row.agency_id,
            filed=False,
            request_id=row.request_id,
            reason=f"already {row.status}",
        )
    try:
        with transaction.atomic():
            filed = file_recipient(
                actor,
                recipient_id,
                filed_at=filed_at,
                request_provision=request_provision,
                requester_user_id=requester_user_id,
            )
    except (ServiceError, DatabaseError) as exc:
        # The savepoint rolled back, so nothing this call reserved or filed survives it. The other
        # recipients are unaffected, which is the whole reason each one gets its own savepoint.
        #
        # DatabaseError, not IntegrityError: a deadlock or a serialization failure is an
        # OperationalError, and one of those escaping here used to roll back the whole wave (P3b,
        # F1). IntegrityError is a DatabaseError subclass, so it is still covered.
        #
        # An expected refusal is logged quietly; a database error with a full traceback, whatever
        # its subtype, so a genuine bug does not become a per-recipient "reason" nobody is paged
        # for.
        if isinstance(exc, DatabaseError):
            logger.exception("filing recipient %s failed", recipient_id)
        else:
            logger.info("filing recipient %s refused: %s", recipient_id, exc)
        # Never str(exc) for a database error: Postgres's own text names every column of the
        # failing row. ServiceError text is ours and safe to echo.
        reason = CONSTRAINT_REASON if isinstance(exc, DatabaseError) else str(exc)
        return RecipientOutcome(
            recipient_id=recipient_id, agency_id=row.agency_id, filed=False, reason=reason
        )
    request = Request.objects.get(pk=filed.request_id)
    return RecipientOutcome(
        recipient_id=recipient_id,
        agency_id=filed.agency_id,
        filed=True,
        request_id=request.pk,
        tracking_number=request.tracking_number,
    )


# A GAP, FLAGGED RATHER THAN FILLED.
# `letter_recipients.status` is pending | held | filed | skipped. There is no vocabulary for
# "attempted and failed", so a recipient whose filing errored stays `pending` with nothing in the
# database saying why. The obvious candidate -- a `records.tasks` row -- cannot be used: its
# `request_id` is NOT NULL (db/records/schema.sql:367) and a failed filing has no request, because
# the per-recipient savepoint rolls the reservation back. Adding either a drafting column or a
# nullable task target is a schema change and needs owner's review, so neither was invented here.
# What survives instead: the failure is returned in `RecipientOutcome.reason` (the API renders it
# per recipient, so the operator sees exactly which agency did not go out and why) and logged with
# a full traceback by the `logger.exception` above. What does NOT survive is a durable record --
# come back tomorrow and the recipient looks merely unfiled.
