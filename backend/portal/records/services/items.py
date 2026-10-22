"""A request's items after it exists: narrowing what they ask for, and adding another.

Items are numbered, legal text that agencies answer one by one, so changing one is never a quiet
edit. Both functions are human only, need a note, take the request's lock first (two people editing
one request's items must not interleave) and leave an audit line per change, because "what did we
ask for, and who changed it" is exactly the question a dispute turns on.
"""

from collections.abc import Mapping
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.records.actors import Actor
from portal.records.models import Event, RequestItem, writable
from portal.records.services import audit as audit_service
from portal.records.services import edits as edits_service
from portal.records.services import events as events_service
from portal.records.services.common import NotFound, ServiceError, lock_request, require_human
from portal.records.services.requests import NewItem


def _need_note(note: str, what: str) -> None:
    if not note or not note.strip():
        raise ServiceError(f"{what} needs a note saying why.")


@transaction.atomic
def narrow_items(actor: Actor, request_id: UUID, edits: Mapping[UUID, str], note: str) -> Event:
    """The "Narrow the request" dialog: new wording for some items, one `narrowing` event for all.

    Each new wording goes through `edit_with_audit`, so every item gets its own audit line carrying
    the old text and the note. Then ONE `narrowing` event records that the request was narrowed,
    with the note as its body: the timeline shows one thing happened, the audit log shows what.
    Everything is one transaction, so a refusal on the last item leaves the first untouched.

    The ids must all belong to THIS request: the request id in the URL is what the caller was
    allowed to touch, so an item of another request named in the body is refused, not edited.
    Items whose wording does not actually change are skipped, and if none changes there is
    nothing to record and the call is refused.
    """
    require_human(actor, "Narrowing a request")
    _need_note(note, "Narrowing a request")
    if not edits:
        raise ServiceError("Nothing to narrow: name at least one item and its new wording.")
    lock_request(request_id)
    items = {
        item.pk: item
        for item in RequestItem.objects.filter(request_id=request_id, pk__in=list(edits))
    }
    unknown = sorted(str(pk) for pk in set(edits) - set(items))
    if unknown:
        raise NotFound(f"No such item on this request: {', '.join(unknown)}.")
    changed = [item for item in items.values() if edits[item.pk] != item.description]
    if not changed:
        raise ServiceError("No item's wording changed, so there is nothing to narrow.")
    for item in sorted(changed, key=lambda i: (i.sort_order, i.item_number)):
        wording = edits[item.pk]
        if not wording or not wording.strip():
            raise ServiceError(f"Item {item.item_number!r} cannot be narrowed to nothing.")
        edits_service.edit_with_audit(actor, "request_items", item.pk, "description", wording, note)
    # A narrowing is an act on the ledger, not a message to or from anyone, so it is recorded as
    # an internal event on the system channel. Writing to the agency is P11.
    return events_service.record_event(
        actor,
        request_id,
        event_type="narrowing",
        channel="system",
        direction="internal",
        occurred_at=timezone.now(),
        display_body=note,
        actor_user_id=actor.user_id,
    )


@transaction.atomic
def add_item(actor: Actor, request_id: UUID, item: NewItem, note: str) -> RequestItem:
    """The "Add a request item" action. The new item goes last unless `sort_order` says otherwise.

    `item_number` must be new on this request: a parent is found by number, and a person reading
    "item 3" must not have two to choose from. Adding an item opens NO deadline, even when it
    names a `provision` and the request is already filed; `file_request` is the only thing that
    computes clocks, and it has already run. The provision is recorded for the item and nothing
    else.
    """
    require_human(actor, "Adding a request item")
    _need_note(note, "Adding a request item")
    number = item.item_number.strip()
    if not number or not item.description.strip():
        raise ServiceError("An item needs a number and a description.")
    lock_request(request_id)
    existing = list(RequestItem.objects.filter(request_id=request_id))
    if any(row.item_number == number for row in existing):
        raise ServiceError(f"Item {number!r} already exists on this request.")
    parent = None
    if item.parent_item_number is not None:
        parent = next((row for row in existing if row.item_number == item.parent_item_number), None)
        if parent is None:
            raise ServiceError(
                f"Item {number!r} names parent {item.parent_item_number!r}, which is not an "
                "item of this request."
            )
    sort_order = item.sort_order or max((row.sort_order for row in existing), default=-1) + 1
    with writable():
        row = RequestItem.objects.create(
            request_id=request_id,
            item_number=number,
            description=item.description,
            parent_item=parent,
            sort_order=sort_order,
            provision=(item.provision or "").strip() or None,
        )
    audit_service.record_audit(
        actor,
        entity_type="request_items",
        entity_id=row.pk,
        field="description",
        old_value=None,
        new_value=row.description,
        note=note,
    )
    return row
