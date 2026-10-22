"""Reports (BUILD_PLAN P15c, design 1y): the year's figures, computed on the spot over the
requests the viewer may see. Nothing here is stored and nothing here is a rule: every number is a
count or a difference of two dates the ledger already holds.

"Determination" means the first status change into one of DETERMINATION_STATUSES after filing.
"On time" compares that change's date with the due date of the request's statutory determination
deadline as the ledger recorded it (whatever rule produced that date); a request with no such
deadline is left out of the share. The design's dashed "statute" line is not drawn: that would be
a legal number in the UI, which is owner's to give (DECISIONS 20).
"""

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import median
from uuid import UUID

from django.db.models import Sum
from django.db.models.functions import ExtractYear
from django.utils import timezone

from portal.records.models import Agency, Deadline, Fee, Request, StatusChange
from portal.records.services import access
from portal.records.services.access import Viewer

DETERMINATION_STATUSES = ("determination_issued", "no_records_claimed", "denied")
OVERDUE_STATUSES = ("determination_overdue", "production_overdue")
DETERMINATION_DEADLINE = "statutory_determination"
OUTCOME_STATUSES = {
    "produced": "production_received",
    "partial": "partial_production",
    "denied": "denied",
    "no_records": "no_records_claimed",
}


@dataclass(frozen=True)
class AgencyLine:
    agency_id: UUID
    agency_name: str
    filed: int
    determinations: int
    median_days_to_determination: int | None
    produced: int
    partial: int
    denied: int
    no_records: int


@dataclass(frozen=True)
class Summary:
    year: int
    agency_id: UUID | None
    years: list[int]
    filed: int
    determinations: int
    median_days_to_determination: int | None
    on_time: int
    judged: int  # determinations that had a deadline to be judged against
    overdue_now: int
    fees_paid_cents: int
    agencies: list[AgencyLine]


@dataclass(frozen=True)
class _Determination:
    request_id: UUID
    on: date
    filed_on: date


def _determinations(requests: list[Request]) -> dict[UUID, _Determination]:
    """The first determination per request, by effective date, voided rows left out."""
    filed_on = {r.pk: r.filed_at.date() for r in requests if r.filed_at}
    out: dict[UUID, _Determination] = {}
    rows = (
        StatusChange.objects.filter(
            request_id__in=list(filed_on), status_id__in=DETERMINATION_STATUSES, voided_at=None
        )
        .order_by("request_id", "effective_at")
        .values_list("request_id", "effective_at", "triggering_event__occurred_at")
    )
    # When the agency answered is the event's date; `effective_at` is when the ledger heard of
    # it, which can be much later for a determination entered after the fact.
    for request_id, effective_at, occurred_at in rows:
        on = (occurred_at or effective_at).date()
        if request_id not in out and on >= filed_on[request_id]:
            out[request_id] = _Determination(request_id, on, filed_on[request_id])
    return out


def _deadline_due(request_ids: list[UUID]) -> dict[UUID, date]:
    """Each request's statutory determination due date: the newest one that was not superseded."""
    out: dict[UUID, date] = {}
    rows = (
        Deadline.objects.filter(request_id__in=request_ids, deadline_type=DETERMINATION_DEADLINE)
        .exclude(status="superseded")
        .order_by("request_id", "-created_at")
        .values_list("request_id", "due_at")
    )
    for request_id, due_at in rows:
        out.setdefault(request_id, due_at)
    return out


def _median_days(spans: list[int]) -> int | None:
    return round(median(spans)) if spans else None


def years_available(viewer: Viewer) -> list[int]:
    found = (
        Request.objects.filter(access.visible_q(viewer), filed_at__isnull=False)
        .annotate(year=ExtractYear("filed_at"))
        .values_list("year", flat=True)
        .distinct()
    )
    years = sorted({int(y) for y in found}, reverse=True)
    this_year = timezone.now().year
    return years if this_year in years else [this_year, *years]


def summary(viewer: Viewer, *, year: int | None = None, agency_id: UUID | None = None) -> Summary:
    year = year or timezone.now().year
    visible = Request.objects.filter(access.visible_q(viewer)).select_related("agency")
    if agency_id is not None:
        visible = visible.filter(agency_id=agency_id)
    filed = list(visible.filter(filed_at__year=year).order_by("agency__name", "filed_at"))
    determined = _determinations(filed)
    due = _deadline_due(list(determined))

    spans = [(d.on - d.filed_on).days for d in determined.values()]
    on_time = sum(
        1 for d in determined.values() if d.request_id in due and d.on <= due[d.request_id]
    )
    judged = sum(1 for d in determined.values() if d.request_id in due)

    by_agency: dict[UUID, list[Request]] = defaultdict(list)
    for r in filed:
        by_agency[r.agency_id].append(r)
    lines: list[AgencyLine] = []
    for agency_key, rows in by_agency.items():
        agency_spans = [
            (determined[r.pk].on - determined[r.pk].filed_on).days
            for r in rows
            if r.pk in determined
        ]
        counts = {
            key: sum(1 for r in rows if r.current_status_id == status)
            for key, status in OUTCOME_STATUSES.items()
        }
        lines.append(
            AgencyLine(
                agency_id=agency_key,
                agency_name=rows[0].agency.name,
                filed=len(rows),
                determinations=len(agency_spans),
                median_days_to_determination=_median_days(agency_spans),
                **counts,
            )
        )
    lines.sort(key=lambda line: (-line.filed, line.agency_name))

    fees = Fee.objects.filter(
        access.visible_q(viewer, "request__"), status="paid", paid_at__year=year
    )
    if agency_id is not None:
        fees = fees.filter(request__agency_id=agency_id)
    paid = fees.aggregate(total=Sum("amount_cents"))["total"] or 0

    return Summary(
        year=year,
        agency_id=agency_id,
        years=years_available(viewer),
        filed=len(filed),
        determinations=len(determined),
        median_days_to_determination=_median_days(spans),
        on_time=on_time,
        judged=judged,
        overdue_now=visible.filter(current_status_id__in=OVERDUE_STATUSES).count(),
        fees_paid_cents=int(paid),
        agencies=lines,
    )


def export(found: Summary) -> str:
    """The outcomes table as text with commas, one agency per row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "year",
            "agency",
            "filed",
            "determinations",
            "median_days_to_determination",
            "produced",
            "partial",
            "denied",
            "no_records",
        ]
    )
    for line in found.agencies:
        writer.writerow(
            [
                found.year,
                line.agency_name,
                line.filed,
                line.determinations,
                ""
                if line.median_days_to_determination is None
                else line.median_days_to_determination,
                line.produced,
                line.partial,
                line.denied,
                line.no_records,
            ]
        )
    return buffer.getvalue()


@dataclass(frozen=True)
class AgencyFigures:
    """The Agencies list's two figures for one agency (P20e, 163)."""

    requests_total: int
    open_requests: int
    median_days_to_determination: int | None


def agency_figures(viewer: Viewer) -> dict[UUID, AgencyFigures]:
    """Per agency, over the requests the viewer may see and every year: how many there are, how
    many are open (status not terminal, as the Docket counts it), and the median dates from
    filing to the first determination, read exactly as `summary` reads it. An agency merged into
    another counts under the one kept, as its page lists them (P16). A hidden request moves no
    figure, because only visible requests are read."""
    visible = list(
        Request.objects.filter(access.visible_q(viewer)).select_related("current_status")
    )
    kept_of = dict(
        Agency.objects.filter(merged_into__isnull=False).values_list("pk", "merged_into_id")
    )
    determined = _determinations(visible)
    by_agency: dict[UUID, list[Request]] = defaultdict(list)
    for r in visible:
        by_agency[kept_of.get(r.agency_id, r.agency_id)].append(r)
    return {
        agency_id: AgencyFigures(
            requests_total=len(rows),
            open_requests=sum(1 for r in rows if not r.current_status.is_terminal),
            median_days_to_determination=_median_days(
                [
                    (determined[r.pk].on - determined[r.pk].filed_on).days
                    for r in rows
                    if r.pk in determined
                ]
            ),
        )
        for agency_id, rows in by_agency.items()
    }
