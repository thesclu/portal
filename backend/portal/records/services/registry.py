"""Export the registry (043): the docket rows a person may see, as text with commas, as JSON, or
as a PDF summary, over the dates they chose; and the activity line that says it happened.

One set of fields for all three (`FIELDS`), read once per row by `registry_rows`, so the three
files can never disagree about what a request is. The PDF is that same table set on landscape
pages through the portal's one PDF path (`mail/pdf.py`), with a title, the date it was made and
the range: no other words, nothing legal.

Who may export what is the docket's rule (the API scopes the rows before they reach here). The
activity line names the format, the count and the range, never a request: an export of rows the
reader of the log may not see would otherwise name them.
"""

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from typing import Any

from portal.core.services import activity as core_activity
from portal.records.actors import Actor
from portal.records.models import RecordsUser, Request, StatusDefinition

FORMATS = ("csv", "json", "pdf")
ACTION = "registry_exported"

# The fields of one request, in file order: what the Docket shows, one request per line.
FIELDS = (
    "tracking_number",
    "agency",
    "subject",
    "status",
    "status_changed_at",
    "next_deadline",
    "days_to_next_deadline",
    "filed_at",
    "sensitivity",
    "assigned_to",
    "requester",
    "agency_reference",
    "alias",
)

# The PDF's column heads: the same fields in words.
HEADS = {
    "tracking_number": "Tracking no.",
    "agency": "Agency",
    "subject": "Subject",
    "status": "Status",
    "status_changed_at": "Status since",
    "next_deadline": "Next deadline",
    "days_to_next_deadline": "Dates left",
    "filed_at": "Filed",
    "sensitivity": "Sensitivity",
    "assigned_to": "Assigned to",
    "requester": "Requester",
    "agency_reference": "Agency ref",
    "alias": "Request address",
}


@dataclass(frozen=True)
class Span:
    """The dates a person chose: when a request was made, both inclusive, in their own zone."""

    since: date | None = None
    until: date | None = None

    def words(self) -> str:
        """ "all time", "from Sep 1, 2026", "until Sep 30, 2026" or "Sep 1, 2026 – Sep 30, 2026"."""
        if self.since is None and self.until is None:
            return "all time"
        if self.until is None:
            return f"from {_day(self.since)}"
        if self.since is None:
            return f"until {_day(self.until)}"
        return f"{_day(self.since)} – {_day(self.until)}"


def _day(value: date | None) -> str:
    return f"{value:%b} {value.day}, {value.year}" if value else ""


def registry_rows(rows: Sequence[Request]) -> list[dict[str, Any]]:
    """Each request's fields (`FIELDS`), as the files hold them: instants in ISO form, an empty
    value as "" (the JSON keeps null for a missing date or count)."""
    people = {
        u.pk: u.name
        for u in RecordsUser.objects.filter(
            pk__in={r.assigned_to_id for r in rows if r.assigned_to_id}
            | {r.requester_id for r in rows}
        )
    }
    out: list[dict[str, Any]] = []
    for r in rows:
        days = getattr(r, "days_to_next_deadline", None)
        out.append(
            {
                "tracking_number": r.tracking_number,
                "agency": r.agency.name,
                "subject": r.subject,
                "status": r.current_status_id,
                "status_changed_at": _iso(r.current_status_changed_at),
                "next_deadline": _iso(r.next_deadline_at),
                "days_to_next_deadline": days,
                "filed_at": _iso(r.filed_at),
                "sensitivity": r.sensitivity,
                "assigned_to": people.get(r.assigned_to_id) if r.assigned_to_id else None,
                "requester": people.get(r.requester_id),
                "agency_reference": r.agency_reference_number,
                "alias": r.inbound_alias_email,
            }
        )
    return out


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value else None


def as_csv(rows: Sequence[Request]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(FIELDS)
    for row in registry_rows(rows):
        writer.writerow(["" if row[f] is None else row[f] for f in FIELDS])
    return buffer.getvalue()


def as_json(rows: Sequence[Request], span: Span, made_at: datetime) -> str:
    """The rows under the export's own facts: when it was made, the range, the count."""
    found = registry_rows(rows)
    return json.dumps(
        {
            "exported_at": made_at.isoformat(),
            "made_since": span.since.isoformat() if span.since else None,
            "made_until": span.until.isoformat() if span.until else None,
            "count": len(found),
            "fields": list(FIELDS),
            "requests": found,
        },
        indent=2,
        ensure_ascii=False,
    )


def as_pdf(rows: Sequence[Request], span: Span, made_at: datetime, org_name: str) -> bytes:
    """A plain table of the same fields on landscape US Letter pages."""
    from portal.mail.pdf import pdf_from_html

    found = registry_rows(rows)
    statuses = dict(StatusDefinition.objects.values_list("code", "label"))
    head = "".join(f'<th width="{WIDTHS[f]}%">{escape(HEADS[f])}</th>' for f in FIELDS)
    body = "".join(
        "<tr>" + "".join(f"<td>{_cell(f, row[f], statuses)}</td>" for f in FIELDS) + "</tr>"
        for row in found
    )
    count = f"{len(found)} {'request' if len(found) == 1 else 'requests'}"
    facts = " · ".join(
        (
            escape(org_name),
            count,
            f"made {escape(span.words())}",
            f"exported {made_at:%Y-%m-%d %H:%M} UTC",
        )
    )
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: letter landscape; margin: 14mm; }}
body {{ font-family: Helvetica; font-size: 7pt; color: #0b1f3a; }}
h1 {{ font-size: 13pt; margin: 0 0 2mm 0; }}
p {{ margin: 0 0 4mm 0; color: #4f5964; font-size: 8pt; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; border-bottom: 0.6pt solid #0b1f3a; padding: 1.2mm 1mm; }}
td {{ border-bottom: 0.3pt solid #d5d9df; padding: 1mm; vertical-align: top; }}
</style></head><body>
<h1>Registry export</h1>
<p>{facts}</p>
<table repeat="1"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
</body></html>"""
    return pdf_from_html(html)


# Each column's share of the landscape page.
WIDTHS = {
    "tracking_number": 9,
    "agency": 9,
    "subject": 13,
    "status": 9,
    "status_changed_at": 7,
    "next_deadline": 7,
    "days_to_next_deadline": 5,
    "filed_at": 7,
    "sensitivity": 6,
    "assigned_to": 7,
    "requester": 7,
    "agency_reference": 6,
    "alias": 8,
}


def _cell(field: str, value: object, statuses: dict[str, str]) -> str:
    """One field as the summary sets it: the status by its label, an instant by its date, the
    request address broken before its domain so a narrow column can hold it."""
    if value is None or value == "":
        return ""
    text = str(value)
    if field == "status":
        return escape(statuses.get(text, text))
    if field in ("status_changed_at", "filed_at", "next_deadline"):
        return escape(text[:10])
    if field == "alias" and "@" in text:
        local, _, domain = text.partition("@")
        return f"{escape(local)}<br/>@{escape(domain)}"
    return escape(text)


def record_export(actor: Actor, fmt: str, count: int, span: Span) -> None:
    """The activity line (P23b; P20f noted exports wrote none). System → Data reads the newest
    one as "Last registry export"; the Activity page names it "CSV · all time"."""
    core_activity.record(
        actor,
        ACTION,
        "Exported the registry",
        subject_type="export",
        data={
            "format": fmt,
            "count": count,
            "since": span.since.isoformat() if span.since else None,
            "until": span.until.isoformat() if span.until else None,
            "range": span.words(),
        },
    )


def last_export() -> dict[str, Any] | None:
    """The newest export: who, the format and when (199 "Last registry export")."""
    from portal.core.models import Activity

    row = Activity.objects.filter(action=ACTION).select_related("actor").order_by("-at").first()
    if row is None:
        return None
    return {
        "who": row.actor.name if row.actor_id else (row.system or "System"),
        "format": str(row.data.get("format") or "csv"),
        "at": row.at,
        "count": row.data.get("count"),
        "range": str(row.data.get("range") or ""),
    }
