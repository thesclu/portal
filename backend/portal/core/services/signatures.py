"""A person's own handwritten signature (P20f): drawn on the pad or uploaded as an image.

Settings → Profile → Your signature. One per person, kept in `core.signatures` with the bytes in
Django's `default_storage` under a path computed here, never one a caller chose:

    signatures/<user_id>/<sha256[:16]>.<png|jpg>

Replacing a signature stores a new file and points the row at it; the old file is left where it
is. What the bytes are is read from the bytes (the PNG signature or the JPEG start-of-image
marker), never from the file's name or the browser's claim, and the width and height come from
the image's own header. Nothing here puts a signature on a letter or a message: that is a later
part. The organisation's letterhead never carries one.

Only the person themself, or a portal administrator, may read or change it.
"""

import hashlib
import struct
from dataclasses import dataclass
from uuid import UUID

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from portal.core.models import Signature, User
from portal.core.services import activity
from portal.records.actors import Actor
from portal.records.services.common import NotAllowed, NotFound, ServiceError
from portal.records.storage import safe_filename

# The largest signature image accepted, in bytes: two megabytes. A drawn signature is a few
# kilobytes; a phone photo of one on paper fits well inside this once cropped.
MAX_BYTES = 2 * 1024 * 1024

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
PNG, JPEG = "image/png", "image/jpeg"
_EXTENSION = {PNG: "png", JPEG: "jpg"}
SOURCES = (Signature.DRAWN, Signature.UPLOADED)
DRAWN_FILE_NAME = "signature.png"

# JPEG start-of-frame markers carry the image's size. SOF0 (baseline) and SOF2 (progressive) are
# what cameras and browsers write; the other SOFn are read the same way. 0xC4, 0xC8 and 0xCC share
# the range but are not frames.
_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Markers with no length after them: TEM, the restart markers and a stray start-of-image.
_STANDALONE = frozenset({0x01, 0xD8, *range(0xD0, 0xD8)})


@dataclass(frozen=True)
class ImageInfo:
    content_type: str
    width: int | None
    height: int | None


def sniff(data: bytes) -> str | None:
    """`image/png` or `image/jpeg` from the first bytes, or None."""
    if data.startswith(PNG_MAGIC):
        return PNG
    if data.startswith(JPEG_MAGIC):
        return JPEG
    return None


def png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from the IHDR chunk, which the format puts first."""
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return (width, height) if width and height else None


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from the first start-of-frame segment, walking the markers from the
    start-of-image. None when the walk reaches the image data, or the end, without one."""
    index = 2
    end = len(data)
    while index + 1 < end:
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:  # fill byte before a marker
            index += 1
            continue
        if marker in _STANDALONE:
            index += 2
            continue
        if marker in (0xD9, 0xDA):  # end of image, start of scan: no frame header came first
            return None
        if index + 4 > end:
            return None
        (length,) = struct.unpack(">H", data[index + 2 : index + 4])
        if length < 2:
            return None
        if marker in _SOF_MARKERS:
            if index + 9 > end:
                return None
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return (width, height) if width and height else None
        index += 2 + length
    return None


def inspect(data: bytes) -> ImageInfo:
    """What the bytes are, and how big the picture is. Refuses anything but a PNG or a JPEG."""
    content_type = sniff(data)
    if content_type is None:
        raise ServiceError("A signature is a PNG or JPEG image.")
    size = png_size(data) if content_type == PNG else jpeg_size(data)
    return ImageInfo(
        content_type=content_type,
        width=size[0] if size else None,
        height=size[1] if size else None,
    )


def get(user_id: UUID) -> Signature | None:
    return Signature.objects.select_related("updated_by").filter(pk=user_id).first()


def may_use(actor_user: User, target_user_id: UUID) -> bool:
    """The person themself, or a portal administrator."""
    return actor_user.pk == target_user_id or bool(actor_user.is_staff)


def check_may_use(actor_user: User, target_user_id: UUID) -> None:
    if not may_use(actor_user, target_user_id):
        raise NotAllowed("Only the person themself or a portal administrator may do this.")


def target(actor_user: User, target_user_id: UUID) -> User:
    """The person whose signature it is, once the caller may reach it. The permission comes
    first, so a refusal says nothing about whether the person exists."""
    check_may_use(actor_user, target_user_id)
    user = User.objects.filter(pk=target_user_id).first()
    if user is None:
        raise NotFound(f"No person {target_user_id}.")
    return user


def _storage_name(user_id: UUID, sha256: str, content_type: str) -> str:
    return f"signatures/{user_id}/{sha256[:16]}.{_EXTENSION[content_type]}"


@transaction.atomic
def save(
    actor_user: User,
    target_user_id: UUID,
    data: bytes,
    *,
    source: str,
    file_name: str | None = None,
) -> Signature:
    """Store `data` as the person's signature, replacing any they had. A drawn one is a PNG; an
    uploaded one a PNG or a JPEG. Leaves an activity line."""
    user = target(actor_user, target_user_id)
    if not user.is_active:
        raise NotAllowed("This person's account is not active; their signature cannot change.")
    if source not in SOURCES:
        raise ServiceError(f"Unknown signature source {source!r}; expected one of {SOURCES}.")
    if not data:
        raise ServiceError("The signature file is empty.")
    if len(data) > MAX_BYTES:
        raise ServiceError("The signature image is larger than two megabytes.")
    info = inspect(data)
    if source == Signature.DRAWN and info.content_type != PNG:
        raise ServiceError("A drawn signature is sent as a PNG.")
    sha256 = hashlib.sha256(data).hexdigest()
    stored = default_storage.save(
        _storage_name(user.pk, sha256, info.content_type), ContentFile(data)
    )
    name = (
        DRAWN_FILE_NAME
        if source == Signature.DRAWN
        else safe_filename(file_name, fallback=f"signature.{_EXTENSION[info.content_type]}")
    )
    fields = {
        "storage_name": stored,
        "content_type": info.content_type,
        "source": source,
        "file_name": name,
        "width": info.width,
        "height": info.height,
        "size": len(data),
        "sha256": sha256,
        "updated_at": timezone.now(),
        "updated_by": actor_user,
    }
    row, _ = Signature.objects.update_or_create(user=user, defaults=fields)
    how = "drawn" if source == Signature.DRAWN else "uploaded"
    summary = (
        f"{user.name}'s signature was {how}"
        if actor_user.pk == user.pk
        else f"{actor_user.name} changed {user.name}'s signature ({how})"
    )
    activity.record(
        Actor.human(actor_user),
        "signature_changed",
        summary,
        subject_type="user",
        subject_id=user.pk,
        data={"source": source, "size": len(data), "sha256": sha256},
    )
    return Signature.objects.select_related("updated_by").get(pk=row.pk)
