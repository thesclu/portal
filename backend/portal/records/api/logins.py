"""P19g: an agency's stored portal login (design 2c). Reading the metadata is staff's; storing,
revealing and clearing are recorded in the activity log."""

from datetime import datetime
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema

from portal.records.api.permissions import require_human_actor, require_staff
from portal.records.services import logins as logins_service

router = Router(tags=["logins"])


class AgencyLoginOut(Schema):
    agency_id: UUID
    login: str
    has_password: bool
    notes: str | None
    changed_at: datetime | None
    changed_by_name: str | None


class AgencyLoginIn(Schema):
    login: str
    # Left out: the stored password is kept. Empty string: it is removed.
    password: str | None = None
    notes: str | None = None


class AgencyLoginRevealOut(Schema):
    password: str


@router.get("/agencies/{agency_id}/login", response={200: AgencyLoginOut, 204: None})
def get_login(request: HttpRequest, agency_id: UUID):
    require_staff(request)
    found = logins_service.view(agency_id)
    if found is None:
        return 204, None
    return 200, AgencyLoginOut(**found.__dict__)


@router.put("/agencies/{agency_id}/login", response=AgencyLoginOut)
def put_login(request: HttpRequest, agency_id: UUID, payload: AgencyLoginIn) -> AgencyLoginOut:
    require_staff(request)
    found = logins_service.set_login(
        require_human_actor(request),
        agency_id,
        login=payload.login,
        password=payload.password,
        notes=payload.notes,
    )
    return AgencyLoginOut(**found.__dict__)


@router.post("/agencies/{agency_id}/login/reveal", response=AgencyLoginRevealOut)
def reveal_login(request: HttpRequest, agency_id: UUID) -> AgencyLoginRevealOut:
    require_staff(request)
    password = logins_service.reveal(require_human_actor(request), agency_id)
    return AgencyLoginRevealOut(password=password)


@router.delete("/agencies/{agency_id}/login", response={204: None})
def clear_login(request: HttpRequest, agency_id: UUID):
    require_staff(request)
    logins_service.clear_login(require_human_actor(request), agency_id)
    return 204, None
