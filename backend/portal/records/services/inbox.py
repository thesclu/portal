"""What the Inbox lists: messages, in buckets. Read only; nothing here writes anything.

A message is a source. The Inbox has three buckets today (Sent and Drafts arrive with P11):

  flagged  flagged and not reviewed: someone has to decide what to do with it.
  new      not flagged, not reviewed, and attached to a request by at least one non-voided inbound
           event: it arrived and landed, and nobody has looked at it yet.
  all      every inbound source, whatever has happened to it since.

"Inbound" is read from the events: a source is inbound if it has an inbound event (voided or not:
it still arrived) or has no event at all (mail that came in and has not been placed). A source whose
events are all outbound or internal is a message we sent, not one we received.

Review moves a message out of `flagged` and `new` and leaves it in `all`; unreviewing moves it back.
Resolving a message without attaching it is a kind of review. Every bucket is a query over rows that
exist, and the counts for the navigation come from the same `_bucket_sources` that lists a bucket,
so a badge and the list under it cannot disagree (BUILD_PLAN rule 3: nothing is cached or counted
by a trigger).

`viewer` is who is asking (a `Viewer`, or a person's id). A message attached to a privileged
request the viewer is not on the list of is not listed and not counted; unattached mail stays
visible to all staff (`access.source_visible_q`, where the rule is written). A message the viewer
does see can also sit on a request they do not: that request's event is left off the row, and the
filters and the search cannot reach through it, so neither a row nor a search hit ever names a
request the viewer may not see. The counts ignore the search and the filters on purpose: a badge
that changed as you typed would stop being a badge.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet, Subquery, TextField
from django.db.models.functions import Cast

from portal.records.models import Attachment, AuditLog, Event, RecordsUser, Source, SourceNote
from portal.records.services import access
from portal.records.services import sources as sources_service
from portal.records.services.access import Viewer
from portal.records.services.common import NotFound, ServiceError

BUCKETS = ("flagged", "new", "all")
# How a bucket may be ordered (the Inbox's sort menu, P20d). Each falls back to newest first.
#   newest      when it arrived, newest first (the default)
#   oldest      when it arrived, oldest first
#   agency      the agency of the request it landed on, A to Z; unplaced mail last
#   request     the tracking number of the request it landed on; unplaced mail last
#   unreviewed  what nobody has reviewed yet first ("New first"), then newest first
SORTS = ("newest", "oldest", "agency", "request", "unreviewed")


@dataclass(frozen=True)
class InboxRow:
    """One message with what the list shows beside it."""

    source: Source
    attachments: list[Attachment]
    # Its non-voided events, each with `request` and `request.agency` loaded: where it landed.
    events: list[Event]
    note_count: int
    assignee_name: str | None
    # P23b. Who last moved it to Flagged and when (140), from the activity log; None when nothing
    # recorded it (a message flagged before the line was kept).
    flagged_by: str | None = None
    flagged_at: datetime | None = None
    # P23b. Who recorded what its first event means and when (143 "by Dana Nolan · 8:31 AM"),
    # from the audit line `classify` writes on the event's type; None while it is plain mail.
    recorded_by: str | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True)
class InboxPage:
    bucket: str
    rows: list[InboxRow]
    # Messages in this bucket that match the filters, before paging.
    total: int
    # Messages in each bucket, unfiltered, from the same query that lists them.
    counts: dict[str, int]


def _any_event() -> QuerySet[Event]:
    return Event.objects.filter(source_id=OuterRef("pk"))


def _live_events() -> QuerySet[Event]:
    return _any_event().filter(voided_at__isnull=True)


def _bucket_sources(bucket: str) -> QuerySet[Source]:
    """The rows of one bucket, newest first. The ONE definition of what each bucket holds."""
    if bucket == "flagged":
        return Source.objects.filter(flagged_for_review=True, reviewed_at__isnull=True)
    if bucket == "new":
        return Source.objects.filter(
            flagged_for_review=False,
            reviewed_at__isnull=True,
        ).filter(Exists(_live_events().filter(direction="inbound")))
    if bucket == "all":
        return Source.objects.filter(
            Exists(_any_event().filter(direction="inbound")) | ~Exists(_any_event())
        )
    raise ServiceError(f"Unknown inbox bucket {bucket!r}; expected one of {BUCKETS}.")


def _visible_bucket(viewer: Viewer, bucket: str) -> QuerySet[Source]:
    """A bucket as this viewer sees it. The list and the badge both start here."""
    return _bucket_sources(bucket).filter(access.source_visible_q(viewer))


def _filtered(
    rows: QuerySet[Source],
    viewer: Viewer,
    *,
    agency_id: UUID | None,
    request_id: UUID | None,
    assignee_id: UUID | None,
    q: str | None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> QuerySet[Source]:
    # When it arrived (P23b, the search's "Any time" chip, 154): `since` inclusive, `until`
    # exclusive, both instants the caller worked out from the person's own dates.
    if since is not None:
        rows = rows.filter(captured_at__gte=since)
    if until is not None:
        rows = rows.filter(captured_at__lt=until)
    # Where a message landed, as far as this viewer may know: its live events on requests they
    # may see. Every filter and the search below go through this, never `_live_events()` bare.
    landed = _live_events().filter(access.visible_q(viewer, "request__"))
    if agency_id is not None:
        rows = rows.filter(Exists(landed.filter(request__agency_id=agency_id)))
    if request_id is not None:
        rows = rows.filter(Exists(landed.filter(request_id=request_id)))
    if assignee_id is not None:
        rows = rows.filter(assigned_to_id=assignee_id)
    text = (q or "").strip()
    if text:
        # What a person can find a message by: its own text and flag note, whatever the inbound
        # mail kept of the sender and subject in `raw_metadata`, its file names, its notes, and
        # where it landed. File CONTENTS are not searched; that needs extracted text.
        rows = rows.annotate(_metadata_text=Cast("raw_metadata", TextField())).filter(
            Q(flag_note__icontains=text)
            | Q(external_ref__icontains=text)
            | Q(message_id__icontains=text)
            | Q(_metadata_text__icontains=text)
            | Exists(Attachment.objects.filter(source_id=OuterRef("pk"), filename__icontains=text))
            | Exists(
                SourceNote.objects.filter(
                    source_id=OuterRef("pk"), voided_at__isnull=True, body__icontains=text
                )
            )
            | Exists(
                landed.filter(
                    Q(display_body__icontains=text)
                    | Q(request__tracking_number__icontains=text)
                    | Q(request__subject__icontains=text)
                    | Q(request__agency_reference_number__icontains=text)
                    | Q(request__agency__name__icontains=text)
                )
            )
        )
    return rows


def bucket_counts(viewer: Viewer | UUID) -> dict[str, int]:
    """Messages in each bucket, as this viewer sees it. The navigation's badges."""
    viewer = access.as_viewer(viewer)
    return {bucket: _visible_bucket(viewer, bucket).count() for bucket in BUCKETS}


def _ordered(rows: QuerySet[Source], viewer: Viewer, sort: str) -> QuerySet[Source]:
    """`rows` in the order `sort` names (`SORTS`). Agency and request read the first live event
    on a request this viewer may see, so the order never says where a message landed that they
    may not know."""
    if sort not in SORTS:
        raise ServiceError(f"Unknown inbox sort {sort!r}; expected one of {SORTS}.")
    newest = ("-captured_at", "-created_at", "-id")
    if sort == "oldest":
        return rows.order_by("captured_at", "created_at", "id")
    if sort == "unreviewed":
        return rows.order_by(F("reviewed_at").asc(nulls_first=True), *newest)
    if sort in ("agency", "request"):
        landed = (
            _live_events()
            .filter(access.visible_q(viewer, "request__"))
            .order_by("occurred_at", "id")
        )
        field = "request__agency__name" if sort == "agency" else "request__tracking_number"
        rows = rows.annotate(_landed_on=Subquery(landed.values(field)[:1]))
        return rows.order_by(F("_landed_on").asc(nulls_last=True), *newest)
    return rows.order_by(*newest)


def inbox(
    viewer: Viewer | UUID,
    bucket: str,
    *,
    agency_id: UUID | None = None,
    request_id: UUID | None = None,
    assignee_id: UUID | None = None,
    q: str | None = None,
    sort: str = "newest",
    limit: int | None = None,
    offset: int = 0,
    since: datetime | None = None,
    until: datetime | None = None,
) -> InboxPage:
    """One bucket, newest first unless `sort` says otherwise, narrowed by agency, request,
    assignee and a search.

    `agency_id` and `request_id` match messages with a non-voided event on that agency's or that
    request; unattached mail has neither, so it drops out of those two filters. `limit` and
    `offset` page the result; the rows of a page cost a fixed handful of queries however many
    there are.
    """
    viewer = access.as_viewer(viewer)
    matching = _ordered(
        _filtered(
            _visible_bucket(viewer, bucket),
            viewer,
            agency_id=agency_id,
            request_id=request_id,
            assignee_id=assignee_id,
            q=q,
            since=since,
            until=until,
        ),
        viewer,
        sort,
    )
    total = matching.count()
    page = matching[offset : offset + limit] if limit is not None else matching[offset:]
    return InboxPage(
        bucket=bucket,
        rows=_rows(list(page), viewer),
        total=total,
        counts=bucket_counts(viewer),
    )


def _rows(sources: Sequence[Source], viewer: Viewer) -> list[InboxRow]:
    ids = [s.pk for s in sources]
    attachments: dict[UUID, list[Attachment]] = {pk: [] for pk in ids}
    for attachment in Attachment.objects.filter(source_id__in=ids).order_by("created_at", "id"):
        attachments[attachment.source_id].append(attachment)
    events: dict[UUID, list[Event]] = {pk: [] for pk in ids}
    live = Event.objects.filter(source_id__in=ids, voided_at__isnull=True).filter(
        access.visible_q(viewer, "request__")
    )
    for event in live.select_related("request", "request__agency").order_by("occurred_at", "id"):
        if event.source_id is not None:
            events[event.source_id].append(event)
    notes = dict(
        SourceNote.objects.filter(source_id__in=ids, voided_at__isnull=True)
        .values_list("source_id")
        .annotate(n=Count("id"))
    )
    # Two queries rather than select_related: records.users and core.users are both named `users`.
    names = dict(
        RecordsUser.objects.filter(
            pk__in={s.assigned_to_id for s in sources if s.assigned_to_id is not None}
        ).values_list("pk", "name")
    )
    flagged = sources_service.flagged_by(s.pk for s in sources if s.flagged_for_review)
    recorded = _recorded_by([found[0].pk for found in events.values() if found])
    rows = []
    for s in sources:
        by, at = flagged.get(s.pk, (None, None))
        first = events[s.pk][0] if events[s.pk] else None
        rec_by, rec_at = recorded.get(first.pk, (None, None)) if first is not None else (None, None)
        rows.append(
            InboxRow(
                source=s,
                attachments=attachments[s.pk],
                events=events[s.pk],
                note_count=notes.get(s.pk, 0),
                assignee_name=names.get(s.assigned_to_id) if s.assigned_to_id else None,
                flagged_by=by,
                flagged_at=at,
                recorded_by=rec_by,
                recorded_at=rec_at,
            )
        )
    return rows


def _recorded_by(event_ids: Sequence[UUID]) -> dict[UUID, tuple[str | None, datetime]]:
    """Who last changed each of these events' type, and when: the newest `events.event_type`
    audit line per event (`classify` writes it). One query for a page of rows."""
    if not event_ids:
        return {}
    lines = (
        AuditLog.objects.filter(
            entity_type="events", entity_id__in=list(event_ids), field_name="events.event_type"
        )
        .select_related("edited_by")
        .order_by("entity_id", "-edited_at")
        .distinct("entity_id")
    )
    return {
        line.entity_id: (line.edited_by.name if line.edited_by_id else None, line.edited_at)
        for line in lines
    }


def bucket_of(viewer: Viewer | UUID, source_id: UUID) -> tuple[InboxRow, list[str]]:
    """One message as an Inbox row, and the buckets it is in now (P23b): a message opened by its
    address that is past the loaded page is read whole, and one that has left the list it was
    opened from is told apart from one that is simply further down. The row is built exactly as a
    list row is (the same events, filtered by the same visibility), and the buckets come from the
    one definition of what each holds, so the answer cannot disagree with the list.

    The caller has already checked the viewer may see the message; this refuses one they may not
    anyway (NotFound), so it cannot be used without that check."""
    viewer = access.as_viewer(viewer)
    source = Source.objects.filter(pk=source_id).filter(access.source_visible_q(viewer)).first()
    if source is None:
        raise NotFound(f"No message {source_id}.")
    buckets = [
        bucket for bucket in BUCKETS if _bucket_sources(bucket).filter(pk=source_id).exists()
    ]
    return _rows([source], viewer)[0], buckets
