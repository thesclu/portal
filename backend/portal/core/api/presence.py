"""P19a: the heartbeat and "who's online" (design 1g, 1i)."""

from datetime import datetime
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema

from portal.core.models import Presence
from portal.core.services import presence
from portal.records import roles
from portal.records.api.permissions import records_role, require_team

router = Router(tags=["presence"])


class PresenceIn(Schema):
    path: str = ""
    label: str = ""


class SeenOut(Schema):
    user_id: UUID
    name: str
    initials: str
    path: str
    label: str
    state: str
    seen_at: datetime


class PresenceOut(Schema):
    online: int
    people: list[SeenOut]


def _out(request: HttpRequest, own_path: str) -> PresenceOut:
    """The list as this caller may read it (DECISIONS 33c, enforced here since P22): an
    administrator gets each page and its name; everyone else the destination only ("/inbox",
    "Inbox · Flagged"), except that someone on the very request the caller has open keeps that
    page, so "Viewing now" on a request page still works without naming any other request."""
    rows = presence.online()
    admin = records_role(request) == roles.ADMIN
    own_request = presence.request_on(own_path)

    def path_of(row: presence.Seen) -> str:
        if admin:
            return row.path
        on = presence.request_on(row.path)
        if on is not None and on == own_request:
            return row.path
        return presence.destination_path(row.path)

    return PresenceOut(
        online=sum(1 for r in rows if r.state == presence.ONLINE),
        people=[
            SeenOut(
                user_id=r.user.pk,
                name=r.user.name,
                initials=presence.initials(r.user.name),
                path=path_of(r),
                label=r.label if admin else presence.destination(r.path),
                state=r.state,
                seen_at=r.seen_at,
            )
            for r in rows
        ],
    )


@router.post("", response=PresenceOut)
def heartbeat(request: HttpRequest, payload: PresenceIn) -> PresenceOut:
    """Every signed-in tab sends one a minute; the answer is the list, so one call does both."""
    presence.beat(request.user, path=payload.path, label=payload.label)
    return _out(request, payload.path) if _is_staff(request) else PresenceOut(online=0, people=[])


@router.get("", response=PresenceOut)
def who_is_online(request: HttpRequest) -> PresenceOut:
    require_team(request)
    own = Presence.objects.filter(user_id=request.user.pk).values_list("path", flat=True).first()
    return _out(request, own or "")


def _is_staff(request: HttpRequest) -> bool:
    try:
        require_team(request)
    except Exception:  # noqa: BLE001 -- a requester sends a heartbeat and sees nobody
        return False
    return True
