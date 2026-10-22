"""Reserving a request, and the two read helpers a list screen needs.

Reservation is deliberately separable from filing: a web form that asks for a reply-to address,
or a letter whose own text names the alias, needs the tracking number and the alias to exist
before anything is sent. So the row, its id, its tracking number and its alias are all created
here, `filed_at` stays null, and `pending_filing` is the first ledger row.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.db import IntegrityError, connection, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from portal.core.services import notifications as core_notifications
from portal.records import aliases
from portal.records.actors import Actor
from portal.records.models import Request, RequestItem, writable
from portal.records.services import access as access_service
from portal.records.services import audit as audit_service
from portal.records.services import status as status_service
from portal.records.services.common import (
    InvalidTransition,
    NoCalendarMapped,
    NotFound,
    RuleData,
    ServiceError,
    agency_calendar,
    local_date,
    lock_request,
    require_assignable,
    require_human,
    rule_data,
)

ALIAS_ATTEMPTS = 5


@dataclass
class NewItem:
    item_number: str
    description: str
    parent_item_number: str | None = None
    # The order the items are read in, carried across from a drafting letter. Free-text
    # item_number cannot supply it: '10' sorts ahead of '2'.
    sort_order: int = 0
    # The rules-package provision this item has its own clock under (request_items.provision), so
    # `file_request` computes the same deadlines whichever door files the request. Blank means none.
    provision: str | None = None


def _next_tracking_number() -> str:
    """The sequence lives in the database and the app role cannot touch it directly; the function
    is SECURITY DEFINER so this call works anyway. Never reimplement the format here."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT records.next_tracking_number()")
        return str(cursor.fetchone()[0])


def _create_items(request: Request, items: Sequence[NewItem]) -> list[RequestItem]:
    by_number: dict[str, RequestItem] = {}
    created: list[RequestItem] = []
    with writable():
        for item in items:
            parent = None
            if item.parent_item_number is not None:
                parent = by_number.get(item.parent_item_number)
                if parent is None:
                    raise ServiceError(
                        f"Item {item.item_number!r} names parent {item.parent_item_number!r}, "
                        "which is not an earlier item in the same batch."
                    )
            row = RequestItem.objects.create(
                request=request,
                item_number=item.item_number,
                description=item.description,
                parent_item=parent,
                sort_order=item.sort_order,
                provision=(item.provision or "").strip() or None,
            )
            by_number[item.item_number] = row
            created.append(row)
    return created


@transaction.atomic
def reserve_request(
    actor: Actor,
    *,
    agency_id: UUID,
    requester_user_id: UUID,
    subject: str,
    request_text: str,
    items: Sequence[NewItem] = (),
    origin: str = "filed",
    sensitivity: str = "internal",
) -> Request:
    _refuse_merged(agency_id)
    now = timezone.now()
    tracking_number = _next_tracking_number()
    request: Request | None = None
    for attempt in range(ALIAS_ATTEMPTS):
        try:
            with transaction.atomic(), writable():
                request = Request.objects.create(
                    tracking_number=tracking_number,
                    origin=origin,
                    requester_id=requester_user_id,
                    agency_id=agency_id,
                    inbound_alias_email=aliases.new_alias_email(),
                    subject=subject,
                    request_text=request_text,
                    current_status_id="pending_filing",
                    sensitivity=sensitivity,
                    created_at=now,
                    filed_at=None,
                    updated_at=now,
                )
            break
        except IntegrityError:
            if attempt == ALIAS_ATTEMPTS - 1:
                raise
    assert request is not None
    _create_items(request, items)
    status_service._append_status_change(
        request,
        "pending_filing",
        reason="manual_override",
        actor=actor,
        note="reserved",
        allow_system_manual=True,
    )
    # A privileged request nobody is on the list of would be invisible to everyone but its
    # requester of record from its first moment (BUILD_PLAN P9, rule 4).
    access_service.on_reserved(actor, request)
    return Request.objects.get(pk=request.pk)


def get_request(request_id: UUID) -> Request:
    request = Request.objects.filter(pk=request_id).first()
    if request is None:
        raise NotFound(f"No request {request_id}.")
    return request


@transaction.atomic
def assign_request(
    actor: Actor, request_id: UUID, user_id: UUID | None, *, notify: bool = False
) -> Request:
    """Say who is working a request, or that nobody is (`None`). Audited; human only.

    The new assignee is always told in the portal (the bell). `notify` is the assign dialog's
    "Tell them by email": only then is the same notification also emailed, and still only when
    the person's own preference for assignments says email (Settings -> Notifications).

    `requester_user_id` is who the request is FOR and never changes; this is who has it. The row
    is locked first so two reassignments cannot both believe they replaced the same person, which
    would leave an audit trail that contradicts itself.
    """
    require_human(actor, "Assigning a request")
    request = lock_request(request_id)
    person = require_assignable(user_id) if user_id is not None else None
    if request.assigned_to_id == user_id:
        return request
    access_service.require_assignee_can_view(request, user_id)
    Request.objects.filter(pk=request_id).update(assigned_to_id=user_id, updated_at=timezone.now())
    if user_id is not None:
        core_notifications.notify(
            user_id,
            "assigned",
            f"{request.tracking_number} was assigned to you",
            link=f"/requests/{request_id}",
            subject_type="request",
            subject_id=request_id,
            request_id=request_id,
            actor_user_id=actor.user_id,
            email=notify,
        )
    audit_service.record_audit(
        actor,
        entity_type="requests",
        entity_id=request_id,
        field="assigned_to_user_id",
        old_value=request.assigned_to_id,
        new_value=user_id,
        note=f"Assigned to {person.name}." if person is not None else "Unassigned.",
    )
    return Request.objects.get(pk=request_id)


# How the docket can be ordered. `deadline` is the working order: soonest date first, requests
# with no active deadline last.
DOCKET_SORTS = {
    "deadline": (F("next_deadline_at").asc(nulls_last=True), "-created_at"),
    "newest": ("-created_at",),
    "status_changed": (F("current_status_changed_at").desc(nulls_last=True), "-created_at"),
    "agency": ("agency__name", "-created_at"),
}


def split_list(value: str | None) -> list[str]:
    """A comma-separated query parameter as its non-empty parts, in order."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def docket(
    *,
    status: str | None = None,
    agency_id: UUID | None = None,
    due_before: date | None = None,
    due_after: date | None = None,
    open_only: bool = False,
    ids: Sequence[UUID] | None = None,
    q: str | None = None,
    assignee_id: UUID | None = None,
    sensitivity: str | None = None,
    sort: str = "deadline",
) -> Sequence[Request]:
    """The list screen's query. It reads the cache columns, which is what they exist for.

    `status` is one code or several separated by commas (the stat boxes that count a family of
    statuses filter by all of them). `due_before` is exclusive and `due_after` inclusive, so the
    two together are a span of dates. `open_only` leaves out every terminal status, the same
    test Home and the stat strip's "All open" use (`status_definitions.is_terminal`). `ids`
    narrows to exactly these requests (the bulk bar's export of the selected rows).

    `select_related("agency")` because `api/requests.py`'s docket row reads `r.agency.name` for
    every row -- without it, each row is its own query for the agency (measured: 56 queries over
    50 rows, 50 of them the identical agency lookup).

    Each returned row also carries `days_to_next_deadline`; see `_attach_days_to_next_deadline`.
    """
    rows = Request.objects.select_related("agency").annotate(item_count=Count("items"))
    codes = split_list(status)
    if codes:
        rows = rows.filter(current_status__in=codes)
    if open_only:
        rows = rows.filter(current_status__is_terminal=False)
    if ids is not None:
        rows = rows.filter(pk__in=list(ids))
    if agency_id is not None:
        # P16: an agency merged into this one is the same agency when read.
        from portal.records.services.directory import same_agency_ids

        rows = rows.filter(agency_id__in=same_agency_ids(agency_id))
    if assignee_id is not None:
        rows = rows.filter(assigned_to_id=assignee_id)
    if sensitivity is not None:
        rows = rows.filter(sensitivity=sensitivity)
    if due_before is not None:
        rows = rows.filter(next_deadline_at__isnull=False, next_deadline_at__lt=due_before)
    if due_after is not None:
        rows = rows.filter(next_deadline_at__isnull=False, next_deadline_at__gte=due_after)
    if q:
        rows = rows.filter(
            Q(tracking_number__icontains=q)
            | Q(subject__icontains=q)
            | Q(agency_reference_number__icontains=q)
            | Q(inbound_alias_email__icontains=q)
        )
    if sort not in DOCKET_SORTS:
        raise ServiceError(f"Unknown sort {sort!r}; expected one of {tuple(DOCKET_SORTS)}.")
    found = list(rows.order_by(*DOCKET_SORTS[sort]))
    _attach_days_to_next_deadline(found)
    return found


def _attach_days_to_next_deadline(rows: Sequence[Request]) -> None:
    """Set `days_to_next_deadline` on each row: next_deadline_at minus today, signed.

    "Today" is the civil date it is NOW where the agency is, from the agency's own calendar, not
    the server's date or the viewer's -- the same reading of a date `file_request` uses to start a
    clock. Positive is time left, negative is time overdue. It is a subtraction of two dates the
    calendar already gives; no count is written here, and nothing is stored: it is a different
    answer tomorrow.

    None when the request has no active deadline, or when its agency's law has no calendar mapped
    (a data-entry gap that must not turn the whole docket into a 500). The rule files are loaded
    at most once per call and only if some row needs them, and the agency's date once per law.
    """
    now = timezone.now()
    data: RuleData | None = None
    today_by_law: dict[str, date | None] = {}
    for row in rows:
        row.days_to_next_deadline = None
        if row.next_deadline_at is None:
            continue
        law = row.agency.records_law or ""
        if law not in today_by_law:
            data = data or rule_data()
            try:
                today_by_law[law] = local_date(now, agency_calendar(row.agency, data))
            except NoCalendarMapped:
                today_by_law[law] = None
        today = today_by_law[law]
        if today is not None:
            row.days_to_next_deadline = (row.next_deadline_at - today).days


def export_registry(rows: Sequence[Request]) -> str:
    """The docket rows as text with commas: what the screen shows, one request per line. The
    fields and the other two formats are `services/registry.py`'s (P23b)."""
    from portal.records.services import registry

    return registry.as_csv(rows)


def _refuse_merged(agency_id: UUID) -> None:
    """A request is reserved against the agency that is kept, never the duplicate (P16)."""
    from portal.records.models import Agency

    found = Agency.objects.filter(pk=agency_id).values_list("merged_into_id", "name").first()
    if found is not None and found[0] is not None:
        raise InvalidTransition(f"{found[1]} was merged into another agency; file with that one.")
