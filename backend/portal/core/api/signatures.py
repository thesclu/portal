"""A person's own signature (P20f), under /api/core/users/{user_id}/signature.

Settings → Profile → Your signature: drawn on the pad (the browser sends the canvas as a PNG) or
uploaded as a PNG or JPEG. Readable and changeable by the person themself and by a portal
administrator; anyone else is refused. Names no request.
"""

from datetime import datetime
from uuid import UUID

from django.http import FileResponse, HttpRequest
from ninja import File, Router, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from portal.core.models import Signature, User
from portal.core.rls import own_setup_session_auth
from portal.core.services import signatures as signatures_service
from portal.records import storage

router = Router(tags=["users"])


class SignatureOut(Schema):
    user_id: UUID
    has_image: bool
    # `drawn` or `uploaded`; every field below is null while the person has none.
    source: str | None
    file_name: str | None
    content_type: str | None
    width: int | None
    height: int | None
    size: int | None
    updated_at: datetime | None
    updated_by_name: str | None


def signature_out(user_id: UUID, row: Signature | None) -> SignatureOut:
    if row is None:
        return SignatureOut(
            user_id=user_id,
            has_image=False,
            source=None,
            file_name=None,
            content_type=None,
            width=None,
            height=None,
            size=None,
            updated_at=None,
            updated_by_name=None,
        )
    return SignatureOut(
        user_id=user_id,
        has_image=True,
        source=row.source,
        file_name=row.file_name,
        content_type=row.content_type,
        width=row.width,
        height=row.height,
        size=row.size,
        updated_at=row.updated_at,
        updated_by_name=row.updated_by.name if row.updated_by else None,
    )


def _caller(request: HttpRequest) -> User:
    user = request.user
    if not isinstance(user, User):  # session auth has already refused anyone signed out
        raise HttpError(401, "Unauthorized")
    return user


def _read(upload: UploadedFile) -> bytes:
    """At most one byte past the limit, so an oversize file is refused without reading all of
    it; the service says why."""
    return upload.read(signatures_service.MAX_BYTES + 1)


@router.get("/{user_id}/signature", auth=own_setup_session_auth, response=SignatureOut)
def get_signature(request: HttpRequest, user_id: UUID) -> SignatureOut:
    """The person's signature, or `has_image: false` when they have none."""
    signatures_service.target(_caller(request), user_id)
    return signature_out(user_id, signatures_service.get(user_id))


@router.get("/{user_id}/signature/image", auth=own_setup_session_auth)
def get_signature_image(request: HttpRequest, user_id: UUID):
    signatures_service.target(_caller(request), user_id)
    row = signatures_service.get(user_id)
    if row is None:
        raise HttpError(404, "This person has no signature.")
    try:
        handle = storage.open_stored(row.storage_name)
    except FileNotFoundError:
        raise HttpError(404, "This person's signature file is missing.") from None
    response = FileResponse(handle, content_type=row.content_type)
    response["Content-Disposition"] = f'inline; filename="{storage.safe_filename(row.file_name)}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/{user_id}/signature/upload", auth=own_setup_session_auth, response=SignatureOut)
def upload_signature(
    request: HttpRequest,
    user_id: UUID,
    file: UploadedFile = File(...),  # noqa: B008 -- ninja's own idiom for a multipart file
) -> SignatureOut:
    """A PNG or JPEG chosen from the person's computer."""
    row = signatures_service.save(
        _caller(request),
        user_id,
        _read(file),
        source=Signature.UPLOADED,
        file_name=file.name,
    )
    return signature_out(user_id, row)


@router.post("/{user_id}/signature/draw", auth=own_setup_session_auth, response=SignatureOut)
def draw_signature(
    request: HttpRequest,
    user_id: UUID,
    file: UploadedFile = File(...),  # noqa: B008 -- ninja's own idiom for a multipart file
) -> SignatureOut:
    """What the pad drew: the canvas as a PNG (`toBlob`)."""
    row = signatures_service.save(_caller(request), user_id, _read(file), source=Signature.DRAWN)
    return signature_out(user_id, row)
