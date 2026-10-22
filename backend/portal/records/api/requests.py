from datetime import date
from typing import Annotated
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import File, Form, Query, Router, Status, UploadedFile
from ninja.errors import HttpError

from portal.core.services.settings import get_setting
from portal.records.api import challenges as challenges_api
from portal.records.api import inbox as inbox_api
from portal.records.api import review as review_api
from portal.records.api.permissions import (
    assignee_filter,
    require_entity_scope,
    require_human_actor,
    require_may_release,
    require_named_source_scope,
    require_records_admin,
    require_request_scope,
    require_team,
    require_viewable_request,
    scope_visible_requests,
    viewer,
)
from portal.records.api.schemas import (
    AddItemIn,
    AssignRequestIn,
    AttachmentOut,
    AuditLogOut,
    DocketHistoryOut,
    DocketHistoryPointOut,
    DocketSummaryOut,
    DocumentIn,
    DocumentOut,
    DueOnDateOut,
    EditIn,
    FileRequestIn,
    FilingPreviewIn,
    FilingPreviewOut,
    NarrowIn,
    PlannedDeadlineOut,
    ProvisionGapOut,
    RequestDetailOut,
    RequestItemOut,
    RequestOut,
    RequestsPage,
    ReserveRequestIn,
    SourceNoteOut,
    StatusChangeOut,
    StatusIn,
)
from portal.records.api.serializers import (
    attachment_out,
    deadline_out,
    docket_row_out,
    event_out,
    fee_out,
    note_out,
    request_out,
    source_out,
    status_change_out,
    task_out,
)
from portal.records.models import (
    Attachment,
    Deadline,
    Event,
    Fee,
    Request,
    RequestItem,
    SourceNote,
    Task,
)
from portal.records.services import challenges as challenges_service
from portal.records.services import docket_summary as summary_service
from portal.records.services import edits as edits_service
from portal.records.services import filing as filing_service
from portal.records.services import items as items_service
from portal.records.services import links as links_service
from portal.records.services import record_pdf
from portal.records.services import registry as registry_service
from portal.records.services import requests as requests_service
from portal.records.services import review as review_service
from portal.records.services import sources as sources_service
from portal.records.services import status as status_service

router = Router(tags=["requests"])

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


_STATUS_HELP = "One status code, or several separated by commas."
_DueBefore = Annotated[date | None, Query(description="Next deadline before this date.")]
_DueAfter = Annotated[date | None, Query(description="Next deadline on or after this date.")]
_OPEN_HELP = "Only requests whose status is not terminal (not denied, closed or withdrawn)."
# P23b: the registry export's range, by when a request was made (reserved), in the caller's zone.
_MadeSince = Annotated[date | None, Query(description="Made on or after this date (yours).")]
_MadeUntil = Annotated[date | None, Query(description="Made on or before this date (yours).")]


def _ids(value: str | None) -> list[UUID] | None:
    """The `ids` query parameter: request ids separated by commas, or absent."""
    if value is None:
        return None
    try:
        return [UUID(part) for part in requests_service.split_list(value)]
    except ValueError:
        raise HttpError(422, "ids must be request ids separated by commas.") from None


def _docket(
    request: HttpRequest,
    *,
    status: str | None,
    agency_id: UUID | None,
    due_before: date | None,
    due_after: date | None,
    open: bool,
    q: str | None,
    assignee: str | None,
    sensitivity: str | None,
    sort: str,
    ids: list[UUID] | None = None,
):
    """The docket as this caller may see it: one query for the list and the export alike."""
    return scope_visible_requests(
        request,
        requests_service.docket(
            status=status,
            agency_id=agency_id,
            due_before=due_before,
            due_after=due_after,
            open_only=open,
            ids=ids,
            q=q,
            assignee_id=assignee_filter(request, assignee),
            sensitivity=sensitivity,
            sort=sort,
        ),
    )


@router.get("/requests", response=RequestsPage)
def list_requests(
    request: HttpRequest,
    status: str | None = Query(None, description=_STATUS_HELP),
    agency_id: UUID | None = None,
    due_before: _DueBefore = None,
    due_after: _DueAfter = None,
    open: bool = Query(False, description=_OPEN_HELP),
    q: str | None = None,
    assignee: str | None = Query(None, description="`me`, or a person's id."),
    sensitivity: str | None = None,
    sort: str = Query("deadline", description="deadline, newest, status_changed or agency."),
    limit: int = Query(
        DEFAULT_LIMIT,
        ge=0,
        description=f"Page size; 0 returns an empty page. Silently clamped to at most {MAX_LIMIT}.",
    ),
    offset: int = Query(0, ge=0, description="Row offset."),
) -> RequestsPage:
    rows = _docket(
        request,
        status=status,
        agency_id=agency_id,
        due_before=due_before,
        due_after=due_after,
        open=open,
        q=q,
        assignee=assignee,
        sensitivity=sensitivity,
        sort=sort,
    )
    # `ge=0` above rejects a negative limit/offset with a 422 before this line ever runs -- there's
    # nothing sensible to clamp a negative page size or offset TO. The remaining clamp is only the
    # ceiling: 0 is a valid limit (an empty page), 200 is the largest page this endpoint will ever
    # build, and anything above it is silently capped rather than rejected, matching every other
    # boundary this endpoint already had.
    limit = min(limit, MAX_LIMIT)
    page = rows[offset : offset + limit]
    items = [docket_row_out(r) for r in page]
    return RequestsPage(items=items, total=len(rows), limit=limit, offset=offset)


@router.get("/requests/export")
def export_requests(
    request: HttpRequest,
    status: str | None = Query(None, description=_STATUS_HELP),
    agency_id: UUID | None = None,
    due_before: _DueBefore = None,
    due_after: _DueAfter = None,
    open: bool = Query(False, description=_OPEN_HELP),
    q: str | None = None,
    assignee: str | None = Query(None, description="`me`, or a person's id."),
    sensitivity: str | None = None,
    sort: str = Query("deadline", description="deadline, newest, status_changed or agency."),
    ids: str | None = Query(
        None, description="Only these requests (ids separated by commas): the selected rows."
    ),
    fmt: str = Query("csv", alias="format", description="csv, json or pdf (a summary table)."),
    since: _MadeSince = None,
    until: _MadeUntil = None,
):
    """P15c, "Export the registry" (043): the docket as the caller sees it, with the same filters.
    `ids` exports exactly the rows selected on the Docket; an id the caller may not see is left
    out like any other hidden row. P23b: as text with commas, JSON or a PDF summary (the same
    fields as a plain table), narrowed to the requests made between `since` and `until` (the
    caller's own dates, inclusive), and each export is an activity line. Registered before
    `/requests/{request_id}` so the path is not read as an id."""
    if fmt not in registry_service.FORMATS:
        raise HttpError(422, f"format must be one of {', '.join(registry_service.FORMATS)}.")
    rows = _docket(
        request,
        status=status,
        agency_id=agency_id,
        due_before=due_before,
        due_after=due_after,
        open=open,
        q=q,
        assignee=assignee,
        sensitivity=sensitivity,
        sort=sort,
        ids=_ids(ids),
    )
    start, end = inbox_api.viewer_span(request, since, until)
    rows = [
        r
        for r in rows
        if (start is None or r.created_at >= start) and (end is None or r.created_at < end)
    ]
    span = registry_service.Span(since, until)
    made_at = timezone.now()
    if fmt == "json":
        response = HttpResponse(
            registry_service.as_json(rows, span, made_at),
            content_type="application/json; charset=utf-8",
        )
    elif fmt == "pdf":
        response = HttpResponse(
            registry_service.as_pdf(rows, span, made_at, str(get_setting("org.name"))),
            content_type="application/pdf",
        )
    else:
        response = HttpResponse(
            registry_service.as_csv(rows), content_type="text/csv; charset=utf-8"
        )
    registry_service.record_export(require_human_actor(request), fmt, len(rows), span)
    response["Content-Disposition"] = f'attachment; filename="registry.{fmt}"'
    response["Cache-Control"] = "private, no-store"
    return response


@router.get("/requests/summary", response=DocketSummaryOut)
def docket_summary(request: HttpRequest) -> DocketSummaryOut:
    """The Docket's stat strip in one read, counted under the list's own visibility rule. "Today"
    is the caller's civil date in their own time zone."""
    require_team(request)
    who = viewer(request)
    today = summary_service.viewer_today(summary_service.viewer_zone(request.user.timezone))
    figures = summary_service.summary(who, today)
    return DocketSummaryOut(
        today=figures.today,
        overdue=figures.overdue,
        due_by_date=[DueOnDateOut(date=d.date, count=d.count) for d in figures.due_by_date],
        producing=figures.producing,
        producing_unreviewed=figures.producing_unreviewed,
        waiting_on_agency=figures.waiting_on_agency,
        assigned_to_me=figures.assigned_to_me,
        all_open=figures.all_open,
        producing_statuses=list(summary_service.PRODUCING_STATUSES),
        unreviewed_statuses=list(summary_service.UNREVIEWED_STATUSES),
        waiting_statuses=list(summary_service.WAITING_ON_AGENCY_STATUSES),
    )


@router.get("/requests/history", response=DocketHistoryOut)
def docket_history(
    request: HttpRequest,
    span: int | None = Query(
        None,
        description=(
            "How many calendar dates, ending today, to count. Default: the setting "
            f"`{summary_service.HISTORY_SPAN_SETTING}`; at most {summary_service.MAX_HISTORY_SPAN}."
        ),
    ),
) -> DocketHistoryOut:
    """The two sparklines: for each date, how many requests the caller may see were open and how
    many overdue at its end, rebuilt from the status ledger. Nothing is stored."""
    require_team(request)
    zone = summary_service.viewer_zone(request.user.timezone)
    width = summary_service.clamp_span(span)
    points = summary_service.history(
        viewer(request), summary_service.viewer_today(zone), width, zone
    )
    return DocketHistoryOut(
        span=width,
        points=[DocketHistoryPointOut(date=p.date, overdue=p.overdue, open=p.open) for p in points],
    )


@router.get("/requests/assignee-counts", response=dict[str, int])
def assignee_counts(request: HttpRequest) -> dict[str, int]:
    """Each person's open requests as the caller may see them (the assign dialog's "4 open"), by
    the person's id. Someone with none is absent."""
    require_team(request)
    counts = summary_service.assignee_counts(viewer(request))
    return {str(user_id): n for user_id, n in counts.items()}


@router.post("/requests", response={201: RequestOut})
def create_request(request: HttpRequest, payload: ReserveRequestIn):
    require_team(request)
    actor = require_human_actor(request)
    items = [
        requests_service.NewItem(
            i.item_number, i.description, i.parent_item_number, i.sort_order, i.provision
        )
        for i in payload.items
    ]
    req = requests_service.reserve_request(
        actor,
        agency_id=payload.agency_id,
        requester_user_id=payload.requester_user_id,
        subject=payload.subject,
        request_text=payload.request_text,
        items=items,
        sensitivity=payload.sensitivity,
    )
    return 201, request_out(req)


def _request_detail_out(req: Request) -> RequestDetailOut:
    request_id = req.pk
    items = RequestItem.objects.filter(request_id=request_id).order_by("sort_order", "item_number")
    events = Event.objects.filter(request_id=request_id).order_by("occurred_at")
    deadlines = Deadline.objects.filter(request_id=request_id).order_by("due_at")
    tasks = Task.objects.filter(request_id=request_id).order_by("created_at")
    fees = Fee.objects.filter(request_id=request_id).order_by("created_at")
    dispositions = review_service.current_dispositions(request_id)
    return RequestDetailOut(
        **request_out(req).model_dump(),
        agency_name=req.agency.name,
        items=[
            RequestItemOut(
                id=i.pk,
                item_number=i.item_number,
                description=i.description,
                parent_item_id=i.parent_item_id,
                sort_order=i.sort_order,
                provision=i.provision,
                disposition=dispositions[i.pk].disposition if i.pk in dispositions else None,
                disposition_at=(
                    dispositions[i.pk].review.event.occurred_at if i.pk in dispositions else None
                ),
                exemption_cited=(
                    dispositions[i.pk].exemption_cited if i.pk in dispositions else None
                ),
                disposition_note=dispositions[i.pk].note if i.pk in dispositions else None,
            )
            for i in items
        ],
        events=[event_out(e) for e in events],
        deadlines=[deadline_out(d) for d in deadlines],
        status_history=[status_change_out(c) for c in status_service.status_history(request_id)],
        tasks=[task_out(t) for t in tasks],
        fees=[fee_out(f) for f in fees],
        links=[review_api.link_out(link) for link in links_service.links_of(request_id)],
        file_links=[review_api.file_link_out(f) for f in review_service.file_links_of(request_id)],
        challenges=[
            challenges_api.challenge_out(c) for c in challenges_service.challenges_of(request_id)
        ],
    )


@router.get("/requests/{request_id}", response=RequestDetailOut)
def get_request(request: HttpRequest, request_id: UUID) -> RequestDetailOut:
    req = require_viewable_request(request, request_id)
    return _request_detail_out(req)


@router.get("/requests/{request_id}/record.pdf")
def export_record(request: HttpRequest, request_id: UUID) -> HttpResponse:
    """P23b (069 "Export the record as PDF"): the request's header, items, deadlines, status
    ledger and its thread's messages, as one PDF in the reader's own time zone, through the
    portal's PDF path. Whoever may see the request may export it; each export is an activity
    line."""
    req = require_request_scope(request, request_id)
    made = record_pdf.export_record(
        require_human_actor(request),
        req,
        zone=summary_service.viewer_zone(getattr(request.user, "timezone", None)),
        org_name=str(get_setting("org.name")),
        made_at=timezone.now(),
    )
    response = HttpResponse(made.data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{made.filename}"'
    response["Cache-Control"] = "private, no-store"
    return response


@router.post("/requests/{request_id}/file", response=RequestDetailOut)
def file_request(
    request: HttpRequest, request_id: UUID, payload: FileRequestIn
) -> RequestDetailOut:
    require_request_scope(request, request_id)
    require_may_release(request)
    require_named_source_scope(request, payload.source_id)
    actor = require_human_actor(request)
    result = filing_service.file_request(
        actor,
        request_id,
        filed_at=payload.filed_at,
        channel=payload.channel,
        source_id=payload.source_id,
        request_provision=payload.request_provision,
        item_provisions=payload.item_provisions,
    )
    return _request_detail_out(result.request)


@router.post("/requests/{request_id}/file/preview", response=FilingPreviewOut)
def preview_filing(
    request: HttpRequest, request_id: UUID, payload: FilingPreviewIn
) -> FilingPreviewOut:
    """P23b (083): the deadlines filing at `filed_at` would open, computed by the same rules
    service filing uses, writing nothing. Read only, so open to whoever may see the request."""
    require_request_scope(request, request_id)
    found = filing_service.preview_filing(
        request_id,
        filed_at=payload.filed_at,
        request_provision=payload.request_provision,
        item_provisions=payload.item_provisions,
    )
    return FilingPreviewOut(
        deadlines=[
            PlannedDeadlineOut(
                deadline_type=str(spec.deadline_type),
                due_at=spec.due_at,
                legal_basis=spec.legal_basis,
                provision=spec.provision,
                request_item_id=_as_uuid(spec.request_item_id),
                citation=spec.citation,
            )
            for spec in found.specs
        ],
        unresolved=[
            ProvisionGapOut(
                deadline_type=str(gap.deadline_type),
                provision=gap.provision,
                request_item_id=_as_uuid(gap.request_item_id),
            )
            for gap in found.unresolved
        ],
        problem=found.problem,
    )


def _as_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


@router.post("/requests/{request_id}/status", response=StatusChangeOut)
def set_status(request: HttpRequest, request_id: UUID, payload: StatusIn) -> StatusChangeOut:
    require_request_scope(request, request_id)
    actor = require_human_actor(request)
    change = status_service.override_status(actor, request_id, payload.status, payload.note)
    return status_change_out(change)


@router.post("/requests/{request_id}/edits", response=AuditLogOut)
def edit_entity(request: HttpRequest, request_id: UUID, payload: EditIn) -> AuditLogOut:
    require_entity_scope(request, request_id, payload.entity_type, payload.entity_id)
    if payload.entity_type == "deadlines" and payload.field == "due_at":
        # One of the two doors that move an existing deadline's date (the other is
        # POST /deadlines/{id}/supersede): an administrator's, not any staff member's (P20h: the
        # Director marker is retired and what it guarded is an administrator's, 30l, 42d).
        require_records_admin(request)
    actor = require_human_actor(request)
    audit = edits_service.edit_with_audit(
        actor,
        payload.entity_type,
        payload.entity_id,
        payload.field,
        payload.new_value,
        payload.note,
        status=payload.status,
    )
    return AuditLogOut(
        id=audit.pk,
        entity_type=audit.entity_type,
        entity_id=audit.entity_id,
        field_name=audit.field_name,
        old_value=audit.old_value,
        new_value=audit.new_value,
        edited_by_id=audit.edited_by_id,
        note=audit.note,
        edited_at=audit.edited_at,
    )


@router.post("/requests/{request_id}/assign", response=RequestOut)
def assign_request(request: HttpRequest, request_id: UUID, payload: AssignRequestIn) -> RequestOut:
    """Say who is working this request; `user_id: null` unassigns."""
    require_request_scope(request, request_id)
    actor = require_human_actor(request)
    req = requests_service.assign_request(actor, request_id, payload.user_id, notify=payload.notify)
    return request_out(req)


@router.post("/requests/{request_id}/narrow", response=RequestDetailOut)
def narrow_request(request: HttpRequest, request_id: UUID, payload: NarrowIn) -> RequestDetailOut:
    """New wording for some items, with the reason. Replying to the agency is not part of this."""
    require_request_scope(request, request_id)
    actor = require_human_actor(request)
    items_service.narrow_items(actor, request_id, payload.edits, payload.note)
    return _request_detail_out(requests_service.get_request(request_id))


@router.post("/requests/{request_id}/items", response={201: RequestItemOut})
def add_request_item(request: HttpRequest, request_id: UUID, payload: AddItemIn):
    require_request_scope(request, request_id)
    actor = require_human_actor(request)
    item = items_service.add_item(
        actor,
        request_id,
        requests_service.NewItem(
            payload.item_number,
            payload.description,
            payload.parent_item_number,
            payload.sort_order,
            payload.provision,
        ),
        payload.note,
    )
    return 201, RequestItemOut(
        id=item.pk,
        item_number=item.item_number,
        description=item.description,
        parent_item_id=item.parent_item_id,
        sort_order=item.sort_order,
        provision=item.provision,
    )


@router.post("/requests/{request_id}/documents", response={201: DocumentOut})
def add_document(
    request: HttpRequest,
    request_id: UUID,
    payload: Form[DocumentIn],
    files: Annotated[list[UploadedFile] | None, File()] = None,
):
    """ "Add a document": a scan, a portal download or an email file becomes one source, its
    attachments and one inbound event on this request. Multipart: the fields of `DocumentIn` and any
    number of `files`. File names are sanitised; size and count limits are settings (422 past
    them). Saying what the document MEANS is POST /events/{id}/classify afterwards."""
    require_request_scope(request, request_id)
    actor = require_human_actor(request)
    added = sources_service.add_document(
        actor,
        request_id,
        source_type=payload.source_type,
        received_at=payload.received_at,
        files=[
            sources_service.IncomingFile(f.name or "", f.chunks(), f.content_type)
            for f in files or []
        ],
        display_body=payload.display_body,
        channel=payload.channel,
        contact_id=payload.contact_id,
    )
    return Status(
        201,
        DocumentOut(
            source=source_out(added.source),
            attachments=[attachment_out(a) for a in added.attachments],
            event=event_out(added.event),
        ),
    )


def _source_ids(request_id: UUID) -> list[UUID]:
    """The messages and documents on a request: the sources of its events, voided or not, so a
    file is still reachable from the request it was once filed under."""
    return list(
        Event.objects.filter(request_id=request_id, source_id__isnull=False)
        .values_list("source_id", flat=True)
        .distinct()
    )


@router.get("/requests/{request_id}/attachments", response=list[AttachmentOut])
def list_request_attachments(request: HttpRequest, request_id: UUID) -> list[AttachmentOut]:
    """Every file on this request, oldest first: the Files tab, and the files under each message
    in Correspondence (each carries its `source_id`)."""
    require_request_scope(request, request_id)
    rows = Attachment.objects.filter(source_id__in=_source_ids(request_id))
    return [attachment_out(a) for a in rows.order_by("created_at", "id")]


@router.get("/requests/{request_id}/source-notes", response=list[SourceNoteOut])
def list_request_source_notes(request: HttpRequest, request_id: UUID) -> list[SourceNoteOut]:
    """The notes written on this request's messages. With its `note` events, the Notes tab."""
    require_request_scope(request, request_id)
    rows = SourceNote.objects.filter(source_id__in=_source_ids(request_id))
    return [note_out(n) for n in rows.order_by("created_at", "id")]
