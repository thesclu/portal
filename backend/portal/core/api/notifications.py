"""P15b: the bell, and what is emailed."""

from datetime import datetime
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema

from portal.core.models import Notification
from portal.core.services import notifications
from portal.records.api.permissions import require_team

router = Router(tags=["notifications"])


class NotificationOut(Schema):
    id: UUID
    kind: str
    text: str
    link: str
    request_id: UUID | None
    created_at: datetime
    read_at: datetime | None


class NotificationsOut(Schema):
    unread: int
    items: list[NotificationOut]


class ReadIn(Schema):
    ids: list[UUID] | None = None  # None: everything


class PreferencesOut(Schema):
    email: dict[str, bool]
    daily_summary: bool
    kinds: list[str]


class PreferencesIn(Schema):
    email: dict[str, bool] | None = None
    daily_summary: bool | None = None


def _out(row: Notification) -> NotificationOut:
    return NotificationOut(
        id=row.pk,
        kind=row.kind,
        text=row.text,
        link=row.link,
        request_id=row.request_id,
        created_at=row.created_at,
        read_at=row.read_at,
    )


@router.get("", response=NotificationsOut)
def list_notifications(request: HttpRequest, limit: int = 50) -> NotificationsOut:
    rows = notifications.recent(request.user, limit=max(1, min(limit, 200)))
    return NotificationsOut(
        unread=Notification.objects.filter(user=request.user, read_at=None).count(),
        items=[_out(r) for r in rows],
    )


@router.post("/read", response=NotificationsOut)
def mark_read(request: HttpRequest, payload: ReadIn) -> NotificationsOut:
    notifications.mark_read(request.user, payload.ids)
    return list_notifications(request)


@router.get("/preferences", response=PreferencesOut)
def get_preferences(request: HttpRequest) -> PreferencesOut:
    require_team(request)
    found = notifications.preferences(request.user)
    return PreferencesOut(
        email=found["email"], daily_summary=found["daily_summary"], kinds=list(notifications.KINDS)
    )


@router.put("/preferences", response=PreferencesOut)
def put_preferences(request: HttpRequest, payload: PreferencesIn) -> PreferencesOut:
    require_team(request)
    found = notifications.set_preferences(
        request.user, email=payload.email, daily_summary=payload.daily_summary
    )
    return PreferencesOut(
        email=found["email"], daily_summary=found["daily_summary"], kinds=list(notifications.KINDS)
    )
