"""P12: production review, the Records screen's listing and download, and request links."""

import zipfile
from tempfile import SpooledTemporaryFile
from typing import Literal
from uuid import UUID

from django.http import FileResponse, HttpRequest
from ninja import Query, Router
from ninja.errors import HttpError

from portal.records import storage
from portal.records.api.permissions import (
    require_attachment_scope,
    require_event_scope,
    require_human_actor,
    require_named_request_scope,
    require_request_scope,
    require_team,
    require_viewable_request,
    viewer,
)
from portal.records.api.schemas import (
    FileLinkOut,
    ItemReviewOut,
    LinkIn,
    LinkOut,
    ProducedRecordOut,
    ProducedRecordsOut,
    RequestItemOut,
    ReviewIn,
    ReviewOut,
    ReviewSheetOut,
    VoidLinkIn,
)
from portal.records.api.serializers import attachment_out
from portal.records.models import Attachment, AttachmentItem, ProductionReview, RequestItem
from portal.records.models import RequestLink as RequestLinkRow
from portal.records.services import links as links_service
from portal.records.services import produced as produced_service
from portal.records.services import review as review_service
from portal.records.services.common import NotFound

router = Router(tags=["review"])

# A set is for carrying a handful of records out, not for mirroring the archive.
SET_LIMIT = 100


def item_review_out(row) -> ItemReviewOut:
    return ItemReviewOut(
        request_item_id=row.request_item_id,
        item_number=row.request_item.item_number,
        disposition=row.disposition,
        exemption_cited=row.exemption_cited,
        cited_where=row.cited_where,
        note=row.note,
    )


def review_out(review: ProductionReview) -> ReviewOut:
    rows = review.rows.select_related("request_item").order_by("request_item__sort_order")
    return ReviewOut(
        id=review.pk,
        request_id=review.request_id,
        event_id=review.event_id,
        reviewed_by_user_id=review.reviewed_by_id,
        reviewed_at=review.reviewed_at,
        note=review.note,
        outcome=review.outcome,
        status_change_id=review.status_change_id,
        supersedes_review_id=review.supersedes_id,
        rows=[item_review_out(r) for r in rows],
    )


def file_link_out(link: AttachmentItem) -> FileLinkOut:
    return FileLinkOut(
        id=link.pk,
        attachment_id=link.attachment_id,
        request_id=link.request_id,
        request_item_id=link.request_item_id,
        review_id=link.review_id,
        created_at=link.created_at,
    )


def link_out(link: RequestLinkRow) -> LinkOut:
    return LinkOut(
        id=link.pk,
        from_request_id=link.from_request_id,
        from_tracking_number=link.from_request.tracking_number,
        from_subject=link.from_request.subject,
        to_request_id=link.to_request_id,
        to_tracking_number=link.to_request.tracking_number,
        to_subject=link.to_request.subject,
        kind=link.kind,
        reason=link.reason,
        created_by_user_id=link.created_by_id,
        created_at=link.created_at,
        voided_at=link.voided_at,
        void_reason=link.void_reason,
    )


# ---- reviews ----------------------------------------------------------------------------------


@router.get("/requests/{request_id}/reviews", response=list[ReviewOut])
def list_reviews(request: HttpRequest, request_id: UUID) -> list[ReviewOut]:
    require_team(request)
    require_viewable_request(request, request_id)
    return [review_out(r) for r in review_service.reviews_of(request_id)]


@router.get("/events/{event_id}/review-sheet", response=ReviewSheetOut)
def review_sheet(request: HttpRequest, event_id: UUID) -> ReviewSheetOut:
    """What a review of this production starts from. Reads only; the dialog is built from it."""
    event = require_event_scope(request, event_id)
    files = (
        list(Attachment.objects.filter(source_id=event.source_id).order_by("created_at", "id"))
        if event.source_id
        else []
    )
    items = RequestItem.objects.filter(request_id=event.request_id).order_by(
        "sort_order", "item_number"
    )
    current = review_service.current_dispositions(event.request_id)
    linked = {
        link.attachment_id: link.request_item_id
        for link in review_service.file_links_of(event.request_id)
    }
    return ReviewSheetOut(
        event_id=event.pk,
        files=[attachment_out(a) for a in files],
        items=[
            RequestItemOut(
                id=i.pk,
                item_number=i.item_number,
                description=i.description,
                parent_item_id=i.parent_item_id,
                sort_order=i.sort_order,
                provision=i.provision,
                disposition=current[i.pk].disposition if i.pk in current else None,
            )
            for i in items
        ],
        current={str(item_id): item_review_out(row) for item_id, row in current.items()},
        linked_files={str(a.pk): linked[a.pk] for a in files if a.pk in linked},
    )


@router.post("/requests/{request_id}/reviews", response={201: ReviewOut})
def create_review(request: HttpRequest, request_id: UUID, payload: ReviewIn):
    """Record a production review. Every item of the request gets a disposition; the files named
    become records tied to the request and, where given, an item. Every item produced is the
    person declaring the request fully produced; anything else is a partial production."""
    require_request_scope(request, request_id)
    actor = require_human_actor(request)
    review = review_service.review_production(
        actor,
        request_id,
        event_id=payload.event_id,
        rows=[review_service.ItemReview(**row.model_dump()) for row in payload.rows],
        files=[review_service.FileLink(**link.model_dump()) for link in payload.files],
        note=payload.note,
    )
    return 201, review_out(review)


# ---- the Records screen -----------------------------------------------------------------------


@router.get("/produced", response=ProducedRecordsOut)
def list_produced(
    request: HttpRequest,
    agency_id: UUID | None = None,
    request_id: UUID | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    order: Literal["newest", "oldest"] = Query("newest", description="By when received."),
) -> ProducedRecordsOut:
    """Every produced file across the requests the caller may see, newest first (or oldest
    first: the Records screen's Date order, 039)."""
    require_team(request)
    rows = produced_service.produced_records(
        viewer(request), agency_id=agency_id, request_id=request_id, q=q
    )
    if order == "oldest":
        rows = rows[::-1]
    page = rows[offset : offset + limit]
    return ProducedRecordsOut(
        items=[
            ProducedRecordOut(
                attachment=attachment_out(r.attachment),
                request_id=r.request_id,
                tracking_number=r.tracking_number,
                subject=r.subject,
                agency_id=r.agency_id,
                agency_name=r.agency_name,
                item_id=r.item.pk if r.item else None,
                item_number=r.item.item_number if r.item else None,
                item_description=r.item.description if r.item else None,
            )
            for r in page
        ],
        total=len(rows),
    )


@router.get("/produced/set")
def download_set(
    request: HttpRequest, ids: str = Query(..., description="Attachment ids, comma-separated.")
):
    """ "Download as a set": the named files, as one zip. Every file named must be one the caller
    may see (the same refusal as its own download), so a set never carries a file out that a
    listing would have left out."""
    require_team(request)
    wanted: list[UUID] = []
    for raw in ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            wanted.append(UUID(raw))
        except ValueError:
            raise HttpError(422, f"{raw!r} is not an attachment id.") from None
    if len(wanted) > SET_LIMIT:
        raise HttpError(422, f"A set holds at most {SET_LIMIT} files.")
    if not wanted:
        raise HttpError(422, "Name at least one file.")
    attachments = [require_attachment_scope(request, attachment_id) for attachment_id in wanted]
    attachments.sort(key=lambda a: (a.filename, a.created_at))
    spool = SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    used: set[str] = set()
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as archive:
        for attachment in attachments:
            name = storage.safe_filename(attachment.filename)
            stem, dot, ext = name.rpartition(".")
            n = 1
            while name in used:
                n += 1
                name = f"{stem}-{n}{dot}{ext}" if dot else f"{name}-{n}"
            used.add(name)
            try:
                with storage.open_stored(attachment.storage_url) as stored:
                    archive.writestr(name, stored.read())
            except FileNotFoundError:
                continue
    spool.seek(0)
    response = FileResponse(
        spool, as_attachment=True, filename="records.zip", content_type="application/zip"
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


# ---- request links ----------------------------------------------------------------------------


@router.get("/requests/{request_id}/links", response=list[LinkOut])
def list_links(request: HttpRequest, request_id: UUID) -> list[LinkOut]:
    """Live links in either direction. The other end is named even when the caller may not open
    it: a link is a fact about this request, and the tracking number is already what the
    "You don't have access" page shows."""
    require_team(request)
    require_viewable_request(request, request_id)
    return [link_out(link) for link in links_service.links_of(request_id)]


@router.post("/requests/{request_id}/links", response={201: LinkOut})
def create_link(request: HttpRequest, request_id: UUID, payload: LinkIn):
    require_request_scope(request, request_id)
    require_named_request_scope(request, payload.to_request_id)
    actor = require_human_actor(request)
    link = links_service.link_requests(
        actor, request_id, payload.to_request_id, kind=payload.kind, reason=payload.reason
    )
    link = RequestLinkRow.objects.select_related("from_request", "to_request").get(pk=link.pk)
    return 201, link_out(link)


@router.post("/links/{link_id}/void", response=LinkOut)
def void_link(request: HttpRequest, link_id: UUID, payload: VoidLinkIn) -> LinkOut:
    require_team(request)
    link = (
        RequestLinkRow.objects.select_related("from_request", "to_request")
        .filter(pk=link_id)
        .first()
    )
    if link is None:
        raise NotFound(f"No link {link_id}.")
    require_viewable_request(request, link.from_request_id)
    actor = require_human_actor(request)
    links_service.void_link(actor, link_id, payload.reason)
    link = RequestLinkRow.objects.select_related("from_request", "to_request").get(pk=link_id)
    return link_out(link)
