import json
from uuid import UUID

from django.http import HttpRequest
from ninja import File, Form, Router, UploadedFile
from ninja.errors import HttpError

from portal.records.api.permissions import (
    require_human_actor,
    require_records_admin,
    require_staff,
    require_team,
    scope_visible_requests,
    viewer,
)
from portal.records.api.schemas import (
    AgencyDetailOut,
    AgencyImportFieldOut,
    AgencyImportPreviewOut,
    AgencyImportResultOut,
    AgencyImportRowOut,
    AgencyIn,
    AgencyOut,
    AgencyPatchIn,
    AgencyRowOut,
    ClosureOut,
    ClosuresAddedOut,
    ClosuresIn,
    ContactIn,
    ContactOut,
    ContactPatchIn,
    MergeIn,
    NoteIn,
    ResponseRecordOut,
)
from portal.records.api.serializers import docket_row_out
from portal.records.models import Agency, AgencyClosure, AgencyContact
from portal.records.services import agency_import as import_service
from portal.records.services import agency_record as agency_record_service
from portal.records.services import closures as closures_service
from portal.records.services import directory as directory_service
from portal.records.services import reports as reports_service
from portal.records.services import requests as requests_service
from portal.records.services.common import ServiceError

router = Router(tags=["agencies"])


def _agency_out(agency: Agency) -> AgencyOut:
    return AgencyOut(
        id=agency.pk,
        name=agency.name,
        jurisdiction=agency.jurisdiction,
        records_law=agency.records_law,
        portal_automation=agency.portal_automation,
        default_request_email=agency.default_request_email,
        portal_url=agency.portal_url,
        notes=agency.notes,
        created_at=agency.created_at,
        short_name=agency.short_name,
        recipient_office=agency.recipient_office,
        mailing_street=agency.mailing_street,
        mailing_city_state_zip=agency.mailing_city_state_zip,
        filing_method=agency.filing_method,
        portal_kind=agency.portal_kind,
        request_email_verified_at=agency.request_email_verified_at,
        merged_into_agency_id=agency.merged_into_id,
        merged_at=agency.merged_at,
        imported_from=agency.imported_from,
    )


def _closure_out(row: AgencyClosure) -> ClosureOut:
    return ClosureOut(
        id=row.pk,
        agency_id=row.agency_id,
        agency_name=row.agency.name,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        reason=row.reason,
        citation=row.citation,
        created_at=row.created_at,
        voided_at=row.voided_at,
        void_note=row.void_note,
    )


def _contact_out(contact: AgencyContact) -> ContactOut:
    return ContactOut(
        id=contact.pk,
        agency_id=contact.agency_id,
        name=contact.name,
        email=contact.email,
        phone=contact.phone,
        title=contact.title,
        notes=contact.notes,
        is_primary=contact.is_primary,
        copy_on_every_request=contact.copy_on_every_request,
        bad_address_at=contact.bad_address_at,
        bad_address_note=contact.bad_address_note,
        created_at=contact.created_at,
    )


@router.get("/agencies", response=list[AgencyRowOut])
def list_agencies(request: HttpRequest, include_merged: bool = False) -> list[AgencyRowOut]:
    """Every agency by name. One merged into another is left out unless asked for (P16). Each
    row carries its open and total requests and its median time to a determination (163), over
    the requests the caller may see only, so a request hidden from them moves no figure."""
    require_team(request)
    rows = Agency.objects.order_by("name")
    if not include_merged:
        rows = rows.filter(merged_into__isnull=True)
    figures = reports_service.agency_figures(viewer(request))
    out = []
    for agency in rows:
        found = figures.get(agency.pk)
        out.append(
            AgencyRowOut(
                **_agency_out(agency).model_dump(),
                open_requests=found.open_requests if found else 0,
                requests_total=found.requests_total if found else 0,
                median_days_to_determination=found.median_days_to_determination if found else None,
            )
        )
    return out


# ---- P16: closure days and merging. Registered before `/agencies/{agency_id}` so the literal
# segments are not read as an id. Administrators only: a closure's citation and the fact that
# two rows are one agency are theirs to state.


@router.get("/agencies/closures", response=list[ClosureOut])
def list_closures(
    request: HttpRequest, agency_id: UUID | None = None, include_past: bool = False
) -> list[ClosureOut]:
    require_team(request)
    return [
        _closure_out(c) for c in closures_service.closures_of(agency_id, include_past=include_past)
    ]


@router.post("/agencies/closures", response={201: ClosuresAddedOut})
def add_closures(request: HttpRequest, payload: ClosuresIn):
    require_records_admin(request)
    added = closures_service.add_closures(
        require_human_actor(request),
        agency_ids=payload.agency_ids,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        reason=payload.reason,
        citation=payload.citation,
    )
    rows = AgencyClosure.objects.filter(pk__in=[c.pk for c in added.closures]).select_related(
        "agency"
    )
    return 201, ClosuresAddedOut(
        closures=[_closure_out(c) for c in rows],
        flagged_deadlines=added.flagged_deadlines,
        tasks_raised=added.tasks_raised,
    )


@router.post("/agencies/closures/{closure_id}/void", response=ClosureOut)
def void_closure(request: HttpRequest, closure_id: UUID, payload: NoteIn) -> ClosureOut:
    require_records_admin(request)
    closures_service.void_closure(require_human_actor(request), closure_id, payload.note)
    return _closure_out(AgencyClosure.objects.select_related("agency").get(pk=closure_id))


@router.post("/agencies/merge", response=AgencyOut)
def merge_agencies(request: HttpRequest, payload: MergeIn) -> AgencyOut:
    require_records_admin(request)
    merged = directory_service.merge_agencies(
        require_human_actor(request),
        keep_id=payload.keep_id,
        merge_id=payload.merge_id,
        note=payload.note,
    )
    return _agency_out(merged)


@router.post("/agencies/{agency_id}/unmerge", response=AgencyOut)
def unmerge_agency(request: HttpRequest, agency_id: UUID, payload: NoteIn) -> AgencyOut:
    require_records_admin(request)
    return _agency_out(
        directory_service.unmerge_agency(require_human_actor(request), agency_id, payload.note)
    )


# ---- P20e: importing a spreadsheet of agencies (168) and editing a contact (171). Registered
# before `/agencies/{agency_id}` with the routes above. Staff and administrators; a requester is
# refused by `require_staff`. The file is read from the upload, never from a path.


def _upload_bytes(upload: UploadedFile) -> bytes:
    data = upload.read(import_service.MAX_BYTES + 1)
    return data if isinstance(data, bytes) else data.encode()


def _json_form(value: str | None, what: str) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ServiceError(f"The {what} sent is not readable.") from None
    if not isinstance(parsed, dict):
        raise ServiceError(f"The {what} sent is not readable.")
    return parsed


def _decisions(value: str | None) -> dict[int, str]:
    parsed = _json_form(value, "choices for matching rows") or {}
    try:
        return {int(number): str(choice) for number, choice in parsed.items()}
    except ValueError:
        raise ServiceError("A choice names a row that is not a number.") from None


@router.post("/agencies/import/preview", response=AgencyImportPreviewOut)
def preview_agency_import(
    request: HttpRequest,
    file: File[UploadedFile],
    mapping: Form[str | None] = None,
) -> AgencyImportPreviewOut:
    """What importing this file would do, row by row: new, matching an agency already here (by
    name and jurisdiction, merged names included), or a problem. Writes nothing. `mapping` is a
    JSON object of column to field (null: not imported); left out, the columns are matched by
    their headers."""
    require_staff(request)
    found = import_service.preview(_upload_bytes(file), _json_form(mapping, "column choices"))
    return AgencyImportPreviewOut(
        filename=file.name or "",
        headers=found.headers,
        mapping=found.mapping,
        fields=[
            AgencyImportFieldOut(key=key, label=label)
            for key, label in import_service.IMPORT_FIELDS.items()
        ],
        rows=[
            AgencyImportRowOut(
                number=row.number,
                values=row.values,
                status=row.status,  # type: ignore[arg-type]
                problems=list(row.problems),
                match_agency_id=row.match_id,
                match_agency_name=row.match_name,
                fills=list(row.fills),
            )
            for row in found.rows
        ],
        new=found.count("new"),
        matches=found.count("match"),
        problems=found.count("problem"),
    )


@router.post("/agencies/import", response=AgencyImportResultOut)
def import_agencies(
    request: HttpRequest,
    file: File[UploadedFile],
    mapping: Form[str | None] = None,
    decisions: Form[str | None] = None,
) -> AgencyImportResultOut:
    """Import the file: every new row is added, a matching row is filled in only where the
    agency is blank and only if `decisions` (JSON, row number to "update" or "skip") says so,
    a row with a problem is left out. All in one transaction; one activity line; nothing is
    deleted or overwritten."""
    require_staff(request)
    result = import_service.import_agencies(
        require_human_actor(request),
        _upload_bytes(file),
        filename=file.name or "",
        mapping=_json_form(mapping, "column choices"),
        decisions=_decisions(decisions),
    )
    return AgencyImportResultOut(
        created=result.created,
        updated=result.updated,
        skipped=result.skipped,
        problems=result.problems,
        summary=result.summary,
    )


@router.patch("/agencies/contacts/{contact_id}", response=ContactOut)
def update_contact(request: HttpRequest, contact_id: UUID, payload: ContactPatchIn) -> ContactOut:
    """Edit a contact: a field left out is left alone; each change is an audit line with `note`.
    Making a contact the primary one clears the previous primary in the same transaction."""
    require_staff(request)
    fields = payload.model_dump(exclude_unset=True, exclude={"note"})
    contact = directory_service.update_contact(
        require_human_actor(request), contact_id, payload.note, **fields
    )
    return _contact_out(contact)


@router.get("/agencies/{agency_id}", response=AgencyDetailOut)
def get_agency(request: HttpRequest, agency_id: UUID) -> AgencyDetailOut:
    """The agency page: the agency, its requests (newest first) and its response record.

    The requests go through the same scoping the docket uses and the response record is computed
    over only the requests that survive it, so a request hidden from this viewer cannot move a
    figure they are shown."""
    require_team(request)
    agency = Agency.objects.filter(pk=agency_id).first()
    if agency is None:
        raise HttpError(404, f"No agency {agency_id}.")
    # The docket's agency filter already folds in the agencies merged into this one (P16).
    rows = scope_visible_requests(request, requests_service.docket(agency_id=agency_id))
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    record = agency_record_service.response_record(agency, [r.pk for r in rows])
    return AgencyDetailOut(
        **_agency_out(agency).model_dump(),
        requests=[docket_row_out(r) for r in rows],
        merged_from=[
            _agency_out(a) for a in Agency.objects.filter(merged_into_id=agency_id).order_by("name")
        ],
        closures=[
            _closure_out(c) for c in closures_service.closures_of(agency_id, include_past=True)
        ],
        response_record=ResponseRecordOut(
            median_days_to_determination=record.median_days_to_determination,
            determinations_on_time=record.determinations_on_time,
            determinations_total=record.determinations_total,
            extensions_taken=record.extensions_taken,
        ),
    )


@router.post("/agencies", response={201: AgencyOut})
def create_agency(request: HttpRequest, payload: AgencyIn):
    require_staff(request)
    actor = require_human_actor(request)
    agency = directory_service.create_agency(actor, **payload.model_dump())
    return 201, _agency_out(agency)


@router.patch("/agencies/{agency_id}", response=AgencyOut)
def update_agency(request: HttpRequest, agency_id: UUID, payload: AgencyPatchIn) -> AgencyOut:
    require_staff(request)
    actor = require_human_actor(request)
    fields = payload.model_dump(exclude_unset=True, exclude={"note"})
    agency = directory_service.update_agency(actor, agency_id, payload.note, **fields)
    return _agency_out(agency)


@router.get("/agencies/{agency_id}/contacts", response=list[ContactOut])
def list_contacts(request: HttpRequest, agency_id: UUID) -> list[ContactOut]:
    require_team(request)
    if not Agency.objects.filter(pk=agency_id).exists():
        raise HttpError(404, f"No agency {agency_id}.")
    contacts = AgencyContact.objects.filter(agency_id=agency_id).order_by("name")
    return [_contact_out(c) for c in contacts]


@router.post("/agencies/{agency_id}/contacts", response={201: ContactOut})
def create_contact(request: HttpRequest, agency_id: UUID, payload: ContactIn):
    require_staff(request)
    actor = require_human_actor(request)
    contact = directory_service.create_contact(actor, agency_id, **payload.model_dump())
    return 201, _contact_out(contact)
