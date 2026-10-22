"""P15c: Reports. The year's figures and the outcomes table, for staff, over what they may see."""

from uuid import UUID

from django.http import HttpRequest, HttpResponse
from ninja import Query, Router, Schema

from portal.records.api.permissions import require_team, viewer
from portal.records.services import reports as reports_service

router = Router(tags=["reports"])


class ReportAgencyLine(Schema):
    agency_id: UUID
    agency_name: str
    filed: int
    determinations: int
    median_days_to_determination: int | None
    produced: int
    partial: int
    denied: int
    no_records: int


class ReportSummaryOut(Schema):
    year: int
    agency_id: UUID | None
    years: list[int]
    filed: int
    determinations: int
    median_days_to_determination: int | None
    on_time: int
    judged: int
    overdue_now: int
    fees_paid_cents: int
    agencies: list[ReportAgencyLine]


def _out(found: reports_service.Summary) -> ReportSummaryOut:
    return ReportSummaryOut(
        year=found.year,
        agency_id=found.agency_id,
        years=found.years,
        filed=found.filed,
        determinations=found.determinations,
        median_days_to_determination=found.median_days_to_determination,
        on_time=found.on_time,
        judged=found.judged,
        overdue_now=found.overdue_now,
        fees_paid_cents=found.fees_paid_cents,
        agencies=[ReportAgencyLine(**line.__dict__) for line in found.agencies],
    )


@router.get("/reports", response=ReportSummaryOut)
def get_report(
    request: HttpRequest,
    year: int | None = Query(None, ge=2000, le=2100),
    agency_id: UUID | None = None,
) -> ReportSummaryOut:
    require_team(request)
    return _out(reports_service.summary(viewer(request), year=year, agency_id=agency_id))


@router.get("/reports/export")
def export_report(
    request: HttpRequest,
    year: int | None = Query(None, ge=2000, le=2100),
    agency_id: UUID | None = None,
):
    require_team(request)
    found = reports_service.summary(viewer(request), year=year, agency_id=agency_id)
    fmt = "csv"
    response = HttpResponse(
        reports_service.export(found), content_type=f"text/{fmt}; charset=utf-8"
    )
    response["Content-Disposition"] = f'attachment; filename="report-{found.year}.{fmt}"'
    response["Cache-Control"] = "private, no-store"
    return response
