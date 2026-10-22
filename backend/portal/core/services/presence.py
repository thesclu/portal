"""Who's online (P19a): a heartbeat per open tab, and the list the top bar shows."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from portal.core.models import Presence, User
from portal.core.services.settings import get_setting

ONLINE = "online"
AWAY = "away"


@dataclass(frozen=True)
class Seen:
    user: User
    path: str
    label: str
    seen_at: datetime
    state: str


def beat(user: User, *, path: str, label: str) -> None:
    Presence.objects.update_or_create(
        user=user,
        defaults={
            "path": (path or "")[:300],
            "label": (label or "")[:200],
            "seen_at": timezone.now(),
        },
    )


def online(now: datetime | None = None) -> list[Seen]:
    """Everyone seen within the gone window, online first, then away; by name within each."""
    now = now or timezone.now()
    away_after = timedelta(minutes=int(get_setting("core.presence_away_minutes") or 5))
    gone_after = timedelta(minutes=int(get_setting("core.presence_gone_minutes") or 30))
    rows = (
        Presence.objects.filter(seen_at__gte=now - gone_after, user__is_active=True)
        .select_related("user")
        .order_by("user__name")
    )
    out = [
        Seen(
            user=row.user,
            path=row.path,
            label=row.label,
            seen_at=row.seen_at,
            state=ONLINE if now - row.seen_at <= away_after else AWAY,
        )
        for row in rows
    ]
    return sorted(out, key=lambda s: (s.state != ONLINE, s.user.name.lower()))


def initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    return "".join(p[0].upper() for p in parts[:2]) or "?"


# ---- what a reader is told about where someone is (DECISIONS 33c; P22) -------------------------
#
# owner, 2026-10-16: "no one sees what specific request they are on except for an admin". Who's
# online (inside team chat) and the presence list show everyone else the DESTINATION only: Home,
# Docket, Inbox · Flagged, "A request". An administrator sees the page's own name, and for a
# request its tracking number and agency. A page's own name (the label the tab sends) is never
# shown to anyone else, because a page names what is on it.

DESTINATIONS = {
    "": "Home",
    "docket": "Docket",
    "requests": "A request",
    "inbox": "Inbox",
    "tasks": "Tasks",
    "approvals": "Tasks",
    "calendar": "Calendar",
    "agencies": "Agencies",
    "records": "Records",
    "search": "Records",
    "templates": "Templates",
    "reports": "Reports",
    "users": "Users",
    "activity": "Users",
    "settings": "Settings",
    "new": "New request",
    "drafts": "New request",
    "my-requests": "My requests",
    "suggest": "Suggest a request",
    "setup": "Getting started",
}
INBOX_LISTS = {"new": "New", "flagged": "Flagged", "all": "All", "sent": "Sent", "drafts": "Drafts"}
ON_A_REQUEST = "A request"


@dataclass(frozen=True)
class Where:
    """Where one person is, as one reader may be told it."""

    text: str
    # The request they are on, for an administrator only.
    request_id: str | None = None
    request_label: str | None = None
    on_request: bool = False


def _split(path: str) -> tuple[str, list[str], dict[str, str]]:
    from urllib.parse import parse_qsl, urlsplit

    parts = urlsplit(path or "/")
    segments = [s for s in parts.path.split("/") if s]
    return (segments[0] if segments else ""), segments, dict(parse_qsl(parts.query))


def request_on(path: str) -> str | None:
    """The request id a path is the page of, or None."""
    root, segments, _ = _split(path)
    return segments[1] if root == "requests" and len(segments) > 1 else None


def destination(path: str) -> str:
    """The words for where a path is, naming nothing on the page."""
    root, _, query = _split(path)
    words = DESTINATIONS.get(root, "The portal")
    if root == "inbox":
        bucket = INBOX_LISTS.get(query.get("bucket", "flagged"))
        return f"Inbox · {bucket}" if bucket else words
    return words


def destination_path(path: str) -> str:
    """The path cut to its destination ("/inbox", "/requests"), for a reader who may not be told
    the page itself."""
    root, _, _ = _split(path)
    return f"/{root}" if root else "/"


def where(seen: Seen, *, admin: bool, requests: dict[str, Any] | None = None) -> Where:
    request_id = request_on(seen.path)
    if not admin:
        return Where(text=destination(seen.path), on_request=request_id is not None)
    if request_id is not None:
        row = (requests or {}).get(request_id)
        if row is not None:
            return Where(
                text=f"{row.tracking_number} · {row.agency.name}",
                request_id=str(row.pk),
                request_label=row.tracking_number,
                on_request=True,
            )
        return Where(text=ON_A_REQUEST, on_request=True)
    label = " · ".join(part.strip() for part in (seen.label or "").split("›") if part.strip())
    return Where(text=label or destination(seen.path))


def requests_seen(rows: list[Seen]) -> dict[str, Any]:
    """The requests these people are on, for an administrator's reading."""
    from uuid import UUID

    from portal.records.models import Request

    ids = set()
    for row in rows:
        found = request_on(row.path)
        if found:
            try:
                ids.add(UUID(found))
            except ValueError:
                continue
    if not ids:
        return {}
    return {str(r.pk): r for r in Request.objects.filter(pk__in=ids).select_related("agency")}
