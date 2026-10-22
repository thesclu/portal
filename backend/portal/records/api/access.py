"""Who has been given a request: the list, giving, taking away; and asking for access (P13).

Since P20h a grant is how a limited-scope attorney or an intern sees an ordinary request, as well
as how anyone but an administrator sees a privileged one, and only an administrator gives or takes
one (37a). Anyone who may see the request may read its list (the request page shows it, 231).
Deciding an ask for access is giving access, so it is an administrator's too, and only they list
the asks. Every door here is decided in `permissions.py`, like every other.
"""

from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from portal.records.api.permissions import (
    require_access_scope,
    require_human_actor,
    require_records_admin,
    require_request_scope,
    require_share_scope,
    require_staff,
    require_viewable_request,
    viewer,
)
from portal.records.api.schemas import (
    AccessIn,
    AccessOut,
    AccessRequestOut,
    AskAccessIn,
    DecideAccessIn,
    UnlistedOut,
)
from portal.records.models import AccessRequest, Request, RequestAccess
from portal.records.services import access as access_service
from portal.records.services.common import NotFound

router = Router(tags=["access"])


def access_out(row: RequestAccess) -> AccessOut:
    return AccessOut(
        id=row.pk,
        request_id=row.request_id,
        user_id=row.user_id,
        user_name=row.user.name,
        granted_by_user_id=row.granted_by_id,
        note=row.note,
        granted_at=row.granted_at,
        revoked_at=row.revoked_at,
        revoked_by_user_id=row.revoked_by_id,
    )


@router.get("/requests/{request_id}/access", response=list[AccessOut])
def list_access(request: HttpRequest, request_id: UUID) -> list[AccessOut]:
    """Who this request has been given to, oldest first: on a privileged request the people who
    see it besides administrators and its requester of record; on any request, the limited-scope
    attorneys and interns it was given to (and anyone else an administrator listed)."""
    require_request_scope(request, request_id)
    return [access_out(row) for row in access_service.active_access(request_id)]


@router.post("/requests/{request_id}/access", response={201: AccessOut})
def grant_access(request: HttpRequest, request_id: UUID, payload: AccessIn):
    """Give this request to someone on the team. Administrators only (403 for anyone else); 409
    if the person already has it; 422 for a requester or someone deactivated."""
    require_share_scope(request, request_id, payload.user_id)
    actor = require_human_actor(request)
    row = access_service.grant_access(actor, request_id, payload.user_id, payload.note)
    return Status(201, access_out(row))


@router.post("/access/{access_id}/revoke", response=AccessOut)
def revoke_access(request: HttpRequest, access_id: UUID) -> AccessOut:
    """End one person's access. Administrators only. 409 when they are the last person on a
    privileged request's list who can still open it."""
    require_records_admin(request)
    require_access_scope(request, access_id)
    actor = require_human_actor(request)
    return access_out(access_service.revoke_access(actor, access_id))


@router.get("/access/unlisted", response=list[UnlistedOut])
def list_unlisted(request: HttpRequest) -> list[UnlistedOut]:
    """Administrators only: the privileged requests the caller is NOT on the list of, by tracking
    number and nothing else. It opens none of them. To open one, the administrator adds themselves
    with POST /requests/{id}/access, which leaves a row saying they did."""
    require_records_admin(request)
    return [
        UnlistedOut(request_id=r.pk, tracking_number=r.tracking_number)
        for r in access_service.unlisted_privileged(viewer(request))
    ]


# ---- asking for access (P13) ------------------------------------------------------------------


def _tracking_number(request_id: UUID) -> str:
    """The tracking number of a request, read through the policies or, for a privileged one the
    caller is not on, through the sealed lookup (what the refusal itself may name)."""
    found = Request.objects.filter(pk=request_id).values_list("tracking_number", flat=True).first()
    return found or access_service.sealed_tracking_number(request_id) or ""


def access_request_out(row: AccessRequest) -> AccessRequestOut:
    # A row read with its request joined carries it; an asker's own ask on a request they cannot
    # read must not join it (the policies would hide the ask too), so it is looked up.
    joined = row._state.fields_cache.get("request")
    return AccessRequestOut(
        id=row.pk,
        request_id=row.request_id,
        tracking_number=joined.tracking_number if joined else _tracking_number(row.request_id),
        user_id=row.user_id,
        user_name=row.user.name,
        reason=row.reason,
        created_at=row.created_at,
        decided_at=row.decided_at,
        decided_by_user_id=row.decided_by_id,
        decision=row.decision,
        decision_note=row.decision_note,
    )


@router.post("/requests/{request_id}/access-requests", response={201: AccessRequestOut})
def ask_access(request: HttpRequest, request_id: UUID, payload: AskAccessIn):
    """The one door a staff member reaches on a privileged request they cannot see: asking for
    it. Not `require_request_scope`, on purpose. A request that does not exist is a 404 as
    elsewhere for staff; one the caller already sees is refused by the service (409)."""
    require_staff(request)
    # P17: under the policies the asker cannot read the row they ask about, so "does it exist"
    # is also asked of the sealed lookup (P20f: this check alone 404'd every real ask).
    if (
        not Request.objects.filter(pk=request_id).exists()
        and access_service.sealed_tracking_number(request_id) is None
    ):
        raise NotFound(f"No request {request_id}.")
    actor = require_human_actor(request)
    row = access_service.ask_access(actor, request_id, payload.reason)
    # Built from the row just written: under the policies the asker cannot read it back (an ask
    # hangs off a request they are not on).
    return 201, access_request_out(row)


@router.get("/requests/{request_id}/access-requests", response=list[AccessRequestOut])
def list_access_requests(request: HttpRequest, request_id: UUID) -> list[AccessRequestOut]:
    """Every ask on this request, for an administrator (who decides them)."""
    require_records_admin(request)
    require_request_scope(request, request_id)
    rows = AccessRequest.objects.filter(request_id=request_id).select_related("request", "user")
    return [access_request_out(r) for r in rows.order_by("created_at")]


@router.get("/access-requests/pending", response=list[AccessRequestOut])
def pending_access_requests(request: HttpRequest) -> list[AccessRequestOut]:
    """Every open ask, for an administrator; empty for anyone else on staff."""
    require_staff(request)
    return [access_request_out(r) for r in access_service.pending_asks_for(viewer(request))]


@router.get("/access-requests/mine", response=list[AccessRequestOut])
def my_access_requests(request: HttpRequest) -> list[AccessRequestOut]:
    """The caller's own asks and their answers. Names the tracking numbers they asked about,
    which they already had from the page that refused them."""
    require_staff(request)
    return [access_request_out(r) for r in access_service.my_asks(request.user.id)]


@router.post("/access-requests/{ask_id}/decide", response=AccessRequestOut)
def decide_access_request(request: HttpRequest, ask_id: UUID, payload: DecideAccessIn):
    require_records_admin(request)
    row = AccessRequest.objects.filter(pk=ask_id).first()
    if row is None:
        raise NotFound(f"No access request {ask_id}.")
    require_viewable_request(request, row.request_id)
    actor = require_human_actor(request)
    access_service.decide_access(actor, ask_id, allow=payload.allow, note=payload.note)
    return access_request_out(
        AccessRequest.objects.select_related("request", "user").get(pk=ask_id)
    )
