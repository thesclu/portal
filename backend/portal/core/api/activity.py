"""P15b: the activity log, for administrators. Filters by person, action and time; the same rows
as text with commas for a spreadsheet.

P23b: each line names what it is on (190's "On": the request's tracking number, the agency's name,
the letter's subject, "CSV · all time"…), read for the person asking under each thing's own
visibility rule: a request through `records/api/permissions.py`, a draft through the drafting
rule, a chat channel through chat's. What the reader may not see is named by its kind only.
"""

from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import Query, Router, Schema

from portal.core.api.users import require_admin
from portal.core.models import Activity
from portal.core.services import activity

router = Router(tags=["activity"])


class ActivityOut(Schema):
    id: UUID
    at: datetime
    actor_user_id: UUID | None
    who: str
    action: str
    summary: str
    subject_type: str
    subject_id: UUID | None
    request_id: UUID | None
    # P23b: what the line is on, by name, as far as the reader may know (190's "On"), and where
    # that thing's page is when there is one the reader may open.
    on: str = ""
    on_link: str | None = None


class ActivityPage(Schema):
    items: list[ActivityOut]
    total: int
    actions: list[str]


def _out(row: Activity, on: tuple[str, str | None] = ("", None)) -> ActivityOut:
    return ActivityOut(
        id=row.pk,
        at=row.at,
        actor_user_id=row.actor_id,
        who=row.actor.name if row.actor_id else (row.system or "System"),
        action=row.action,
        summary=row.summary,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        request_id=row.request_id,
        on=on[0],
        on_link=on[1],
    )


# The kind of thing a line is on, in a word: what "On" says when the thing has no name the reader
# may be told (190 draws "Users" for a role change).
KIND_WORDS = {
    "request": "Request",
    "user": "Users",
    "setting": "Settings",
    "rule": "Rules",
    "calendar": "Calendars",
    "law": "Rules",
    "draft": "Letter",
    "review": "Approval",
    "suggestion": "Suggestion",
    "access_request": "Access",
    "agency": "Agency",
    "agencies": "Agencies",
    "message": "Message",
    "source": "Mail",
    "closure": "Closure days",
    "template": "Template",
    "task": "Task",
    "agreement": "Agreement",
    "export": "Registry",
    "chat": "Team chat",
    "chat_channel": "Team chat",
    "chat_message": "Team chat",
    "chat_attachment": "Team chat",
    "challenge": "Request",
    "production_review": "Request",
}


def _kind_word(subject_type: str) -> str:
    if not subject_type:
        return "—"
    return KIND_WORDS.get(subject_type, subject_type.replace("_", " ").capitalize())


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def on_names(request: HttpRequest, rows: Sequence[Activity]) -> dict[UUID, tuple[str, str | None]]:
    """What each line is on, named for the person asking, in a fixed handful of queries.

    A line about a request (it carries `request_id`) names the request's tracking number when the
    reader may see that request, and only its kind otherwise. The rest name their own thing:
    an agency (the directory, which every reader of the log may read), a letter (when the drafting
    rule lets the reader see it), a chat channel (when chat's rule lets them read it), the setting
    or rule or law by its key, an agreement by kind and version, an export by format and range.
    """
    from portal.core.chat import access as chat_access
    from portal.core.models import ChatChannel, Review
    from portal.drafting.api.permissions import scope_visible_drafts
    from portal.drafting.models import LetterDraft
    from portal.mail.models import Message
    from portal.records.api.permissions import visible_request_ids
    from portal.records.models import Agency, Request

    def ids_of(kind: str) -> set[UUID]:
        return {r.subject_id for r in rows if r.subject_type == kind and r.subject_id}

    reviews = {
        r.pk: r for r in Review.objects.filter(pk__in=ids_of("review")) if r.subject_id is not None
    }
    request_ids = {r.request_id for r in rows if r.request_id} | {
        r.request_id for r in reviews.values() if r.request_id
    }
    visible = visible_request_ids(request, request_ids) if request_ids else set()
    tracking = dict(Request.objects.filter(pk__in=visible).values_list("pk", "tracking_number"))
    agencies = dict(Agency.objects.filter(pk__in=ids_of("agency")).values_list("pk", "name"))
    draft_ids = ids_of("draft") | {
        r.subject_id for r in reviews.values() if r.subject_type == "draft"
    }
    drafts = {
        d.pk: d.subject or "Untitled letter"
        for d in scope_visible_drafts(request, LetterDraft.objects.filter(pk__in=draft_ids))
    }
    message_ids = {r.subject_id for r in reviews.values() if r.subject_type == "message"}
    messages = {
        m.pk: m.subject or "A message"
        for m in Message.objects.filter(pk__in=message_ids, request_id__in=visible)
    }
    channel_ids = ids_of("chat_channel") | {
        cid
        for r in rows
        if r.subject_type in ("chat_message", "chat_attachment")
        if (cid := _uuid((r.data or {}).get("channel_id"))) is not None
    }
    channels = {
        c.pk: (f"#{c.name}" if c.name else "Direct message")
        for c in ChatChannel.objects.filter(pk__in=channel_ids)
        if chat_access.can_read(request.user, c)
    }

    def one(row: Activity) -> tuple[str, str | None]:
        data = row.data or {}
        kind = row.subject_type
        if kind == "export":
            fmt = str(data.get("format") or "csv").upper()
            return f"{fmt} · {data.get('range') or 'all time'}", None
        if kind == "agencies":
            return str(data.get("filename") or _kind_word(kind)), "/agencies"
        if kind == "agency" and row.subject_id in agencies:
            return agencies[row.subject_id], f"/agencies/{row.subject_id}"
        if kind == "user":
            return _kind_word(kind), "/users"
        if kind == "setting":
            return str(data.get("key") or _kind_word(kind)), "/settings"
        if kind == "rule" and data.get("rule_id"):
            return str(data["rule_id"]), "/settings/rules"
        if kind == "law" and data.get("law"):
            return str(data["law"]), "/settings/rules"
        if kind == "agreement" and data.get("kind"):
            words = str(data["kind"]).replace("_", " ").capitalize()
            return f"{words} agreement v{data.get('version', '')}".strip(), None
        if kind.startswith("chat_"):
            channel = row.subject_id if kind == "chat_channel" else _uuid(data.get("channel_id"))
            if channel in channels:
                return channels[channel], None
            return _kind_word(kind), None
        if kind == "draft" and row.subject_id in drafts:
            return drafts[row.subject_id], f"/drafts/{row.subject_id}"
        if kind == "review" and row.subject_id in reviews:
            review = reviews[row.subject_id]
            if review.request_id in tracking:
                return tracking[review.request_id], f"/requests/{review.request_id}"
            if review.subject_type == "draft" and review.subject_id in drafts:
                return drafts[review.subject_id], f"/drafts/{review.subject_id}"
            if review.subject_type == "message" and review.subject_id in messages:
                return messages[review.subject_id], None
            return _kind_word(kind), None
        if row.request_id is not None:
            if row.request_id in tracking:
                return tracking[row.request_id], f"/requests/{row.request_id}"
            return _kind_word(kind or "request"), None
        return _kind_word(kind), None

    return {row.pk: one(row) for row in rows}


def _rows(
    actor_id: UUID | None,
    system: bool,
    action: str | None,
    since: date | None,
    until: date | None,
    request_id: UUID | None,
):
    """`since` and `until` are calendar dates in the portal's time zone; `until` is inclusive."""
    tz = timezone.get_current_timezone()
    start = datetime.combine(since, time.min, tzinfo=tz) if since else None
    end = datetime.combine(until, time.max, tzinfo=tz) if until else None
    return activity.rows(
        actor_id=actor_id,
        system_only=system,
        action=action,
        since=start,
        until=end,
        request_id=request_id,
    )


@router.get("", response=ActivityPage)
def list_activity(
    request: HttpRequest,
    actor_id: UUID | None = None,
    system: bool = False,
    action: str | None = None,
    since: date | None = None,
    until: date | None = None,
    request_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ActivityPage:
    require_admin(request)
    rows = _rows(actor_id, system, action, since, until, request_id)
    page = list(rows[offset : offset + limit])
    names = on_names(request, page)
    return ActivityPage(
        items=[_out(r, names[r.pk]) for r in page],
        total=rows.count(),
        actions=activity.actions(),
    )


@router.get("/export")
def export_activity(
    request: HttpRequest,
    actor_id: UUID | None = None,
    system: bool = False,
    action: str | None = None,
    since: date | None = None,
    until: date | None = None,
    request_id: UUID | None = None,
):
    require_admin(request)
    text = activity.export(_rows(actor_id, system, action, since, until, request_id)[:10000])
    fmt = "csv"
    response = HttpResponse(text, content_type=f"text/{fmt}; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="activity.{fmt}"'
    response["Cache-Control"] = "private, no-store"
    return response
