"""A request's record as a PDF (P23b; 069 "Export the record as PDF").

What the request page holds, set on US Letter pages through the portal's one PDF path
(`mail/pdf.py`): the header (tracking number, subject, agency, status, the agency's number,
when it was made and filed, who has it), the items, the deadlines with the legal basis exactly as
each row stores it, the status ledger (voided rows marked, never dropped), and the thread's
messages, each with its date, direction, way, kind, who it was from and what the ledger says it
was. Nothing is worded here beyond the headings: no legal text, no summary of what anything means.

Who may export it is who may see the request (the API checks that first), and nothing in it
reaches past the request: a message on this request that also sits on another request is listed
from this request's own event, so the other request is never named. Internal notes are not
messages and are left out. Times are in the reader's own zone, labelled. Exporting is an activity
line, like the registry export.
"""

from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

from portal.core.services import activity as core_activity
from portal.records.actors import Actor
from portal.records.models import (
    AgencyContact,
    Deadline,
    DeadlineDefinition,
    Event,
    EventDefinition,
    RecordsUser,
    Request,
    RequestItem,
    StatusChange,
    StatusDefinition,
)

# The events of the thread: what came and went, not the team's own notes.
MESSAGE_DIRECTIONS = ("inbound", "outbound")
WAYS = {"email": "Email", "portal": "Portal", "mail": "Post", "call": "Call", "system": "Portal"}


@dataclass(frozen=True)
class RecordFile:
    filename: str
    data: bytes


def _when(value: datetime | None, zone: ZoneInfo) -> str:
    if value is None:
        return ""
    at = value.astimezone(zone)
    clock = f"{at:%I:%M %p}".lstrip("0")
    return f"{_day(at.date())} {clock}"


def _day(value: date | None) -> str:
    return f"{value:%b} {value.day}, {value.year}" if value else ""


def _cells(values: list[str]) -> str:
    return "".join(f"<td>{escape(v)}</td>" for v in values)


def _table(heads: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f"<p class='quiet'>{escape(empty)}</p>"
    head = "".join(f"<th>{escape(h)}</th>" for h in heads)
    body = "".join(f"<tr>{_cells(r)}</tr>" for r in rows)
    return f"<table repeat='1'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def record_html(request: Request, zone: ZoneInfo, org_name: str, made_at: datetime) -> str:
    statuses = dict(StatusDefinition.objects.values_list("code", "label"))
    kinds = dict(DeadlineDefinition.objects.values_list("code", "label"))
    event_kinds = dict(EventDefinition.objects.values_list("code", "label"))
    people_ids = {request.requester_id, request.assigned_to_id}
    events = list(
        Event.objects.filter(request_id=request.pk, direction__in=MESSAGE_DIRECTIONS).order_by(
            "occurred_at", "id"
        )
    )
    people_ids |= {e.actor_user_id for e in events}
    people = dict(
        RecordsUser.objects.filter(pk__in={p for p in people_ids if p}).values_list("pk", "name")
    )
    contacts = dict(
        AgencyContact.objects.filter(
            pk__in={e.actor_agency_contact_id for e in events if e.actor_agency_contact_id}
        ).values_list("pk", "name")
    )
    items = list(
        RequestItem.objects.filter(request_id=request.pk).order_by("sort_order", "item_number")
    )
    numbers = {i.pk: i.item_number for i in items}
    zone_name = made_at.astimezone(zone).tzname() or str(zone)

    facts = [
        ("Tracking number", request.tracking_number),
        ("Agency", request.agency.name),
        ("Status", statuses.get(request.current_status_id, request.current_status_id)),
        ("Agency's reference", request.agency_reference_number or "—"),
        ("Made", _when(request.created_at, zone)),
        ("Filed", _when(request.filed_at, zone) or "Not yet"),
        ("Assigned to", people.get(request.assigned_to_id, "Nobody")),
        ("Requester", people.get(request.requester_id, "")),
        ("Sensitivity", request.sensitivity.capitalize()),
    ]
    header = "".join(
        f"<tr><th class='fact'>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in facts
    )
    item_rows = [[i.item_number, i.description, i.provision or ""] for i in items]
    deadline_rows = [
        [
            kinds.get(d.deadline_type, d.deadline_type),
            _day(d.due_at),
            d.status.capitalize(),
            d.basis.replace("_", " "),
            d.legal_basis or "",
            f"Item {numbers[d.request_item_id]}"
            if d.request_item_id in numbers
            else "Whole request",
        ]
        for d in Deadline.objects.filter(request_id=request.pk).order_by("due_at", "created_at")
    ]
    ledger_rows = [
        [
            statuses.get(c.status_id, c.status_id),
            _when(c.effective_at, zone),
            c.reason.replace("_", " "),
            c.note or "",
            "Voided" if c.voided_at else "",
        ]
        for c in StatusChange.objects.filter(request_id=request.pk).order_by("effective_at")
    ]
    message_rows = [
        [
            _when(e.occurred_at, zone),
            "In" if e.direction == "inbound" else "Out",
            WAYS.get(e.channel, e.channel.capitalize()),
            event_kinds.get(e.event_type_id, e.event_type_id),
            contacts.get(e.actor_agency_contact_id)
            or people.get(e.actor_user_id)
            or ("The agency" if e.direction == "inbound" else ""),
            e.display_body[:600] + ("…" if len(e.display_body) > 600 else ""),
            "Voided" if e.voided_at else "",
        ]
        for e in events
    ]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: letter; margin: 16mm; }}
body {{ font-family: Helvetica; font-size: 8.5pt; color: #0b1f3a; }}
h1 {{ font-size: 15pt; margin: 0 0 1mm 0; }}
h2 {{ font-size: 10.5pt; margin: 6mm 0 2mm 0; }}
p {{ margin: 0 0 2mm 0; }}
.quiet {{ color: #4f5964; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; border-bottom: 0.6pt solid #0b1f3a; padding: 1.2mm 1mm; }}
td {{ border-bottom: 0.3pt solid #d5d9df; padding: 1.2mm 1mm; vertical-align: top; }}
th.fact {{ width: 34mm; border-bottom: 0.3pt solid #d5d9df; font-weight: normal; color: #4f5964; }}
</style></head><body>
<h1>{escape(request.subject)}</h1>
<p class="quiet">{escape(org_name)} · the record as of {escape(_when(made_at, zone))} \
({escape(zone_name)})</p>
<table>{header}</table>
<h2>Items</h2>
{_table(["No.", "What is asked", "Provision"], item_rows, "No items.")}
<h2>Deadlines</h2>
{_table(["Kind", "Due", "State", "From", "Legal basis", "Applies to"], deadline_rows, "None.")}
<h2>Status history</h2>
{_table(["Status", "From", "Why", "Note", ""], ledger_rows, "None.")}
<h2>Messages</h2>
{_table(["When", "", "Way", "Kind", "From", "What it says", ""], message_rows, "None yet.")}
</body></html>"""


def export_record(
    actor: Actor, request: Request, *, zone: ZoneInfo, org_name: str, made_at: datetime
) -> RecordFile:
    """The PDF and its file name, and the activity line that says it was exported."""
    from portal.mail.pdf import pdf_from_html

    data = pdf_from_html(record_html(request, zone, org_name, made_at))
    core_activity.record(
        actor,
        "record_exported",
        f"Exported the record of {request.tracking_number}",
        subject_type="request",
        subject_id=request.pk,
        request_id=request.pk,
    )
    return RecordFile(filename=f"{request.tracking_number}-record.pdf", data=data)
