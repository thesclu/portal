"""The activity log (P15b): who did what to what, by a person or by a named process.

Written by the services that do the thing, inside their transaction, through `record`. It is
not the audit log: `audit_log` is one field's old and new value and refuses a system author;
this is a sentence a person can read ("Reviewed a production", "Exported the registry") with
enough ids to link it. Append-only; nothing edits or deletes a row.
"""

import csv
import io
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from portal.core.models import Activity
from portal.records.actors import Actor


def record(
    actor: Actor | None,
    action: str,
    summary: str,
    *,
    subject_type: str = "",
    subject_id: UUID | None = None,
    request_id: UUID | None = None,
    data: dict[str, Any] | None = None,
) -> Activity:
    return Activity.objects.create(
        actor_id=actor.user_id if actor and actor.is_human else None,
        system=(actor.system or "") if actor and not actor.is_human else "",
        action=action,
        summary=summary,
        subject_type=subject_type,
        subject_id=subject_id,
        request_id=request_id,
        data=data or {},
    )


def rows(
    *,
    actor_id: UUID | None = None,
    system_only: bool = False,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    request_id: UUID | None = None,
) -> QuerySet[Activity]:
    out = Activity.objects.select_related("actor").order_by("-at")
    if actor_id is not None:
        out = out.filter(actor_id=actor_id)
    if system_only:
        out = out.filter(actor__isnull=True)
    if action:
        out = out.filter(action=action)
    if since is not None:
        out = out.filter(at__gte=since)
    if until is not None:
        out = out.filter(at__lt=until)
    if request_id is not None:
        out = out.filter(request_id=request_id)
    return out


def actions() -> list[str]:
    return list(Activity.objects.order_by("action").values_list("action", flat=True).distinct())


def export(rows_: QuerySet[Activity]) -> str:
    """The same rows as text with commas, for a spreadsheet."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["when", "who", "action", "summary", "subject_type", "subject_id", "request_id"]
    )
    for row in rows_:
        who = row.actor.name if row.actor_id else (row.system or "System")
        writer.writerow(
            [
                row.at.isoformat(),
                who,
                row.action,
                row.summary,
                row.subject_type,
                str(row.subject_id or ""),
                str(row.request_id or ""),
            ]
        )
    return buffer.getvalue()
