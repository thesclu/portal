"""The agreements signed in the new-user setup (P20i), under /api/core/agreements.

A person agrees to the version in force (the address and browser are taken from the request) and
reads their own signed copies; a portal administrator lists every version, publishes the next
one, and reads anyone's signed copy. Names no request.
"""

from datetime import datetime
from uuid import UUID

from django.http import FileResponse, HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from portal.core.api.auth import _client_of
from portal.core.api.users import require_admin
from portal.core.models import AgreementAcceptance, User
from portal.core.rls import setup_session_auth
from portal.core.services import agreements as agreements_service
from portal.records import storage

router = Router(tags=["agreements"])


class AgreementVersionOut(Schema):
    id: UUID
    kind: str
    title: str
    version: int
    # The text as published: one clause per paragraph, a lead in `**`.
    body: str
    is_placeholder: bool
    is_current: bool
    published_at: datetime
    published_by_name: str | None
    signed_count: int


class AgreementPublishIn(Schema):
    kind: str
    body: str


class AcceptanceOut(Schema):
    id: UUID
    agreement_id: UUID
    kind: str
    title: str
    version: int
    user_id: UUID
    user_name: str
    signed_name: str
    signed_title: str
    accepted_at: datetime
    ip_address: str
    user_agent: str


def _caller(request: HttpRequest) -> User:
    user = request.user
    if not isinstance(user, User):  # session auth has already refused anyone signed out
        raise HttpError(401, "Unauthorized")
    return user


def acceptance_out(row: AgreementAcceptance) -> AcceptanceOut:
    return AcceptanceOut(
        id=row.pk,
        agreement_id=row.agreement_id,
        kind=row.agreement.kind,
        title=agreements_service.title_of(row.agreement.kind),
        version=row.agreement.version,
        user_id=row.user_id,
        user_name=row.user.name,
        signed_name=row.signed_name,
        signed_title=row.signed_title,
        accepted_at=row.accepted_at,
        ip_address=row.ip_address,
        user_agent=row.user_agent,
    )


@router.get("", response=list[AgreementVersionOut])
def list_agreements(request: HttpRequest) -> list[AgreementVersionOut]:
    """Every version of each agreement, newest first within a kind (administrators)."""
    require_admin(request)
    rows = list(agreements_service.versions())
    newest: dict[str, int] = {}
    for row in rows:
        newest[row.kind] = max(newest.get(row.kind, 0), row.version)
    return [
        AgreementVersionOut(
            id=row.pk,
            kind=row.kind,
            title=agreements_service.title_of(row.kind),
            version=row.version,
            body=row.body,
            is_placeholder=row.is_placeholder,
            is_current=row.version == newest[row.kind],
            published_at=row.published_at,
            published_by_name=row.published_by.name if row.published_by else None,
            signed_count=row.signed,  # type: ignore[attr-defined]  # the annotation
        )
        for row in rows
    ]


@router.post("", response=AgreementVersionOut)
def publish_agreement(request: HttpRequest, payload: AgreementPublishIn) -> AgreementVersionOut:
    """Publish the next version of an agreement (administrators). Everyone it applies to is asked
    to agree to it at their next sign-in."""
    require_admin(request)
    row = agreements_service.publish(_caller(request), payload.kind, payload.body)
    return AgreementVersionOut(
        id=row.pk,
        kind=row.kind,
        title=agreements_service.title_of(row.kind),
        version=row.version,
        body=row.body,
        is_placeholder=row.is_placeholder,
        is_current=True,
        published_at=row.published_at,
        published_by_name=row.published_by.name if row.published_by else None,
        signed_count=0,
    )


@router.post("/{agreement_id}/accept", auth=setup_session_auth, response=AcceptanceOut)
def accept_agreement(request: HttpRequest, agreement_id: UUID) -> AcceptanceOut:
    """Agree to this version, signed with the caller's stored signature. The client address and
    the browser are recorded with it, and a PDF of what was signed is kept."""
    row = agreements_service.accept(
        _caller(request),
        agreement_id,
        ip_address=_client_of(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    return acceptance_out(row)


@router.get("/acceptances", response=list[AcceptanceOut])
def list_acceptances(
    request: HttpRequest, user_id: UUID | None = None, agreement_id: UUID | None = None
) -> list[AcceptanceOut]:
    """The caller's signed agreements; anyone's, or everyone's for one version, for an
    administrator."""
    rows = agreements_service.acceptances(
        _caller(request), user_id=user_id, agreement_id=agreement_id
    )
    return [acceptance_out(row) for row in rows]


@router.get("/acceptances/{acceptance_id}/pdf")
def acceptance_pdf(request: HttpRequest, acceptance_id: UUID):
    """The PDF of exactly what was signed: the person themself or an administrator; anyone else
    is answered as if it did not exist."""
    row = agreements_service.readable(_caller(request), acceptance_id)
    try:
        handle = storage.open_stored(row.pdf_storage_name)
    except FileNotFoundError:
        raise HttpError(404, "The signed copy's file is missing.") from None
    response = FileResponse(handle, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{storage.safe_filename(agreements_service.pdf_file_name(row))}"'
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
