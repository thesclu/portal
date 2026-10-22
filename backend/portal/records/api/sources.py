from email.utils import format_datetime
from uuid import UUID

from django.http import FileResponse, HttpRequest
from ninja import Router, Status
from ninja.errors import HttpError

from portal.records import storage
from portal.records.api.permissions import (
    require_attachment_scope,
    require_event_scope,
    require_human_actor,
    require_request_scope,
    require_source_note_scope,
    require_source_scope,
    scope_visible_requests,
    scope_visible_sources,
)
from portal.records.api.schemas import (
    AssignIn,
    AssignSourceIn,
    AttachmentOut,
    CandidateOut,
    EventOut,
    FlagSourceIn,
    MessageHeaderOut,
    MessageOut,
    ResolveSourceIn,
    SourceNoteIn,
    SourceNoteOut,
    SourceOut,
    SourceRecordsIn,
    SourcesPage,
    VoidIn,
)
from portal.records.api.serializers import attachment_out, event_out, note_out, source_out
from portal.records.inbound import ingest as inbound_ingest
from portal.records.models import Source
from portal.records.services import assignment as assignment_service
from portal.records.services import sources as sources_service

router = Router(tags=["sources"])

MAX_LIMIT = 200
DEFAULT_LIMIT = 50

# What a download may be served AS. Every download is an attachment (the browser saves it, it never
# renders it in this origin) and carries `nosniff`, but the stored type is still only believed for
# types that are inert; anything else is served as opaque bytes. A file's own claim about what it
# is cannot make the portal render a page of its choosing.
_SERVED_AS = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/tiff",
    "text/plain",
    "text/csv",
}
_RAW_TYPES = {"json": "application/json", "eml": "message/rfc822"}


def _download(storage_name: str, filename: str, content_type: str) -> FileResponse:
    try:
        opened = storage.open_stored(storage_name)
    except FileNotFoundError:
        raise HttpError(404, "That file is not in storage.") from None
    response = FileResponse(
        opened, as_attachment=True, filename=filename, content_type=content_type
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@router.get("/sources", response=SourcesPage)
def list_sources(
    request: HttpRequest,
    flagged: bool | None = None,
    unreviewed: bool | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> SourcesPage:
    queryset = scope_visible_sources(request, Source.objects.all())
    if flagged is not None:
        queryset = queryset.filter(flagged_for_review=flagged)
    if unreviewed:
        queryset = queryset.filter(reviewed_at__isnull=True)
    rows = list(queryset.order_by("-captured_at"))
    limit = min(max(limit, 1), MAX_LIMIT)
    offset = max(offset, 0)
    page = rows[offset : offset + limit]
    return SourcesPage(
        items=[source_out(s) for s in page], total=len(rows), limit=limit, offset=offset
    )


@router.get("/sources/{source_id}", response=SourceOut)
def get_source(request: HttpRequest, source_id: UUID) -> SourceOut:
    return source_out(require_source_scope(request, source_id))


@router.post("/sources/{source_id}/assign", response={201: EventOut})
def assign(request: HttpRequest, source_id: UUID, payload: AssignSourceIn):
    require_source_scope(request, source_id)
    require_request_scope(request, payload.request_id)
    if payload.from_event_id is not None:
        require_event_scope(request, payload.from_event_id)
    actor = require_human_actor(request)
    event = assignment_service.assign_source_to_request(
        actor,
        source_id,
        payload.request_id,
        mode=payload.mode,
        from_event_id=payload.from_event_id,
        reason=payload.reason,
    )
    return 201, event_out(event)


@router.post("/sources/{source_id}/reviewed", response=SourceOut)
def mark_reviewed(request: HttpRequest, source_id: UUID) -> SourceOut:
    require_source_scope(request, source_id)
    actor = require_human_actor(request)
    source = sources_service.mark_source_reviewed(actor, source_id)
    return source_out(source)


@router.post("/sources/{source_id}/flag", response=SourceOut)
def flag(request: HttpRequest, source_id: UUID, payload: FlagSourceIn) -> SourceOut:
    """Put a message in front of a person and say why (`reason`: GET /definitions' flag_reasons)."""
    require_source_scope(request, source_id)
    actor = require_human_actor(request)
    return source_out(sources_service.flag_source(actor, source_id, payload.note, payload.reason))


@router.post("/sources/{source_id}/assignee", response=SourceOut)
def assign_to_person(request: HttpRequest, source_id: UUID, payload: AssignIn) -> SourceOut:
    """Say who is dealing with a message; `user_id: null` unassigns. Not `/assign`, which attaches
    the message to a request."""
    require_source_scope(request, source_id)
    actor = require_human_actor(request)
    return source_out(sources_service.assign_source(actor, source_id, payload.user_id))


@router.post("/sources/{source_id}/unreview", response=SourceOut)
def unreview(request: HttpRequest, source_id: UUID) -> SourceOut:
    """Take a message back out of "reviewed". Who had reviewed it stays in the audit log."""
    require_source_scope(request, source_id)
    actor = require_human_actor(request)
    return source_out(sources_service.mark_source_unreviewed(actor, source_id))


@router.post("/sources/{source_id}/resolve", response=SourceOut)
def resolve(request: HttpRequest, source_id: UUID, payload: ResolveSourceIn) -> SourceOut:
    """ "Mark handled without attaching". Refused (409) while the message has a non-voided event."""
    require_source_scope(request, source_id)
    actor = require_human_actor(request)
    return source_out(
        sources_service.resolve_without_attaching(
            actor, source_id, payload.resolution, payload.note
        )
    )


@router.get("/sources/{source_id}/notes", response=list[SourceNoteOut])
def list_notes(
    request: HttpRequest, source_id: UUID, include_voided: bool = True
) -> list[SourceNoteOut]:
    require_source_scope(request, source_id)
    notes = sources_service.list_source_notes(source_id, include_voided=include_voided)
    return [note_out(n) for n in notes]


@router.post("/sources/{source_id}/notes", response={201: SourceNoteOut})
def add_note(request: HttpRequest, source_id: UUID, payload: SourceNoteIn):
    require_source_scope(request, source_id)
    actor = require_human_actor(request)
    return Status(201, note_out(sources_service.add_source_note(actor, source_id, payload.body)))


@router.post("/source-notes/{note_id}/void", response=SourceNoteOut)
def void_note(request: HttpRequest, note_id: UUID, payload: VoidIn) -> SourceNoteOut:
    """A mistaken note is voided, never deleted."""
    require_source_note_scope(request, note_id)
    actor = require_human_actor(request)
    return note_out(sources_service.void_source_note(actor, note_id, payload.reason))


@router.post("/sources/{source_id}/records", response=list[AttachmentOut])
def mark_records(
    request: HttpRequest, source_id: UUID, payload: SourceRecordsIn
) -> list[AttachmentOut]:
    """ "Mark files as records": exactly the named attachments are the records. Returns every file
    of the message as it now stands."""
    require_source_scope(request, source_id)
    actor = require_human_actor(request)
    rows = sources_service.set_attachment_roles(actor, source_id, payload.record_attachment_ids)
    return [attachment_out(a) for a in rows]


@router.get("/attachments/{attachment_id}/download")
def download_attachment(request: HttpRequest, attachment_id: UUID):
    attachment = require_attachment_scope(request, attachment_id)
    served_as = attachment.mime_type if attachment.mime_type in _SERVED_AS else None
    return _download(
        attachment.storage_url,
        storage.safe_filename(attachment.filename),
        served_as or "application/octet-stream",
    )


@router.get("/sources/{source_id}/raw")
def download_raw(request: HttpRequest, source_id: UUID):
    """The untouched original of a message (the stored JSON or .eml), as a download."""
    source = require_source_scope(request, source_id)
    extension = source.raw_storage_url.rpartition(".")[2].lower()
    return _download(
        source.raw_storage_url,
        f"{source.pk}.{extension if extension in _RAW_TYPES else 'bin'}",
        _RAW_TYPES.get(extension, "application/octet-stream"),
    )


@router.get("/sources/{source_id}/candidates", response=list[CandidateOut])
def list_candidates(request: HttpRequest, source_id: UUID) -> list[CandidateOut]:
    """The requests a message may also be about: ones its text names by their address or by the
    agency's own reference number, or the several it was addressed to. Worked out now, from the
    stored original, and limited to requests the caller may see; nothing about a candidate is
    stored on the message."""
    source = require_source_scope(request, source_id)
    found = scope_visible_requests(request, inbound_ingest.candidates(source))
    return [
        CandidateOut(
            request_id=r.pk,
            tracking_number=r.tracking_number,
            subject=r.subject,
            agency_id=r.agency_id,
            agency_name=r.agency.name,
            agency_reference_number=r.agency_reference_number,
        )
        for r in found
    ]


@router.get("/sources/{source_id}/message", response=MessageOut)
def read_message(request: HttpRequest, source_id: UUID) -> MessageOut:
    """A message as a person reads it: who it is from and to, its subject, its date and its text,
    read now from the stored original. This is the only way to read mail that is attached to no
    request yet (an attached message's text is also on its event). `readable` is false for a
    source that is not mail, or whose original cannot be read; download the original instead."""
    source = require_source_scope(request, source_id)
    message = inbound_ingest.load_message(source)
    if message is None:
        return MessageOut(readable=False)
    given = {name.lower() for name, _ in message.header_list}
    fields = [
        (
            "From",
            f"{message.from_name} <{message.from_addr}>"
            if message.from_name
            else message.from_addr,
        ),
        ("To", ", ".join(message.to)),
        ("Cc", ", ".join(message.cc)),
        ("Subject", message.subject),
        ("Date", format_datetime(message.date) if message.date else ""),
    ]
    headers = [*message.header_list, *((n, v) for n, v in fields if v and n.lower() not in given)]
    return MessageOut(
        readable=True,
        from_addr=message.from_addr,
        from_name=message.from_name,
        to=message.to,
        cc=message.cc,
        subject=message.subject,
        date=message.date,
        body=message.body,
        headers=[MessageHeaderOut(name=n, value=v) for n, v in headers],
    )
