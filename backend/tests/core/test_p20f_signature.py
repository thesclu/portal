"""P20f: a person's own signature, drawn on the pad or uploaded, read back by them or an
administrator and by nobody else. Every image here is built in the test."""

import struct
import uuid
import zlib

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from portal.core.models import Activity, Signature
from portal.core.services import identity, signatures
from portal.records.services.common import NotAllowed, NotFound, ServiceError


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def png(width: int = 3, height: int = 2) -> bytes:
    """A real, tiny, all-white PNG."""
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return (
        signatures.PNG_MAGIC
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def jpeg(width: int = 5, height: int = 4, *, frame: int = 0xC0) -> bytes:
    """A JPEG's markers as far as the frame header: SOI, an APP0 segment, a fill byte, a frame
    header with the size, then the end. Enough for anything that reads the header."""
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00"
    return (
        b"\xff\xd8"
        + b"\xff\xe0"
        + struct.pack(">H", len(app0) + 2)
        + app0
        + b"\xff"  # a fill byte before the next marker
        + bytes([0xFF, frame])
        + struct.pack(">H", len(sof) + 2)
        + sof
        + b"\xff\xd9"
    )


def _file(data: bytes, name: str = "sig.png", content_type: str = "image/png"):
    return {"file": SimpleUploadedFile(name, data, content_type=content_type)}


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _base(user) -> str:
    return f"/api/core/users/{user.pk}/signature"


# ---- reading the image's header ----------------------------------------------------------


def test_the_size_is_read_from_a_png_and_from_baseline_and_progressive_jpegs() -> None:
    assert signatures.inspect(png(3, 2)) == signatures.ImageInfo("image/png", 3, 2)
    assert signatures.inspect(jpeg(5, 4)) == signatures.ImageInfo("image/jpeg", 5, 4)
    assert signatures.inspect(jpeg(640, 480, frame=0xC2)).width == 640
    # The magic bytes are enough to be accepted; a header that cannot be read leaves the size
    # unknown rather than refusing the image.
    assert signatures.inspect(signatures.PNG_MAGIC + b"\x00" * 8) == signatures.ImageInfo(
        "image/png", None, None
    )
    assert signatures.jpeg_size(b"\xff\xd8\xff\xda\x00\x02") is None  # scan before any frame
    with pytest.raises(ServiceError, match="PNG or JPEG"):
        signatures.inspect(b"GIF89a....")


# ---- drawing and uploading ------------------------------------------------------------------


@pytest.mark.app_role
def test_a_drawn_signature_is_stored_and_read_back_byte_for_byte(db, staff_user, media) -> None:
    client = _client(staff_user)
    empty = client.get(_base(staff_user))
    assert empty.status_code == 200
    assert empty.json() == {
        "user_id": str(staff_user.pk),
        "has_image": False,
        "source": None,
        "file_name": None,
        "content_type": None,
        "width": None,
        "height": None,
        "size": None,
        "updated_at": None,
        "updated_by_name": None,
    }
    assert client.get(f"{_base(staff_user)}/image").status_code == 404

    drawn = png(120, 40)
    made = client.post(f"{_base(staff_user)}/draw", _file(drawn, "blob"))
    assert made.status_code == 200, made.content
    body = made.json()
    assert body["has_image"] is True and body["source"] == "drawn"
    assert body["file_name"] == "signature.png" and body["content_type"] == "image/png"
    assert (body["width"], body["height"], body["size"]) == (120, 40, len(drawn))
    assert body["updated_by_name"] == staff_user.name and body["updated_at"]
    assert client.get(_base(staff_user)).json() == body

    image = client.get(f"{_base(staff_user)}/image")
    assert image.status_code == 200 and image.getvalue() == drawn
    assert image["Content-Type"] == "image/png"
    assert image["Cache-Control"] == "private, no-store"
    assert image["X-Content-Type-Options"] == "nosniff"
    assert image["Content-Disposition"].startswith("inline;")

    row = Signature.objects.get(user=staff_user)
    assert row.storage_name.startswith(f"signatures/{staff_user.pk}/{row.sha256[:16]}")
    assert row.storage_name.endswith(".png")


def test_an_upload_takes_a_png_or_a_jpeg_and_replacing_keeps_one_row(db, staff_user, media) -> None:
    client = _client(staff_user)
    photo = jpeg(300, 90)
    up = client.post(
        f"{_base(staff_user)}/upload", _file(photo, "../../My <Sig>.jpg", "image/jpeg")
    )
    assert up.status_code == 200, up.content
    body = up.json()
    assert body["source"] == "uploaded" and body["content_type"] == "image/jpeg"
    assert body["file_name"] == "My _Sig_.jpg"
    assert (body["width"], body["height"]) == (300, 90)
    image = client.get(f"{_base(staff_user)}/image")
    assert image.getvalue() == photo and image["Content-Type"] == "image/jpeg"
    first = Signature.objects.get(user=staff_user).storage_name

    again = client.post(f"{_base(staff_user)}/upload", _file(png(4, 4), "scan.png"))
    assert again.status_code == 200 and again.json()["content_type"] == "image/png"
    assert Signature.objects.filter(user=staff_user).count() == 1
    second = Signature.objects.get(user=staff_user).storage_name
    # A new file, pointed at; the old one is left where it was (nothing deletes stored files).
    assert second != first and default_storage.exists(first) and default_storage.exists(second)
    assert client.get(f"{_base(staff_user)}/image").getvalue() == png(4, 4)


def test_what_is_not_a_signature_image_is_refused(db, staff_user, media) -> None:
    client = _client(staff_user)
    base = _base(staff_user)
    not_image = client.post(f"{base}/upload", _file(b"%PDF-1.7 hello", "sig.pdf", "image/png"))
    assert not_image.status_code == 422 and "PNG or JPEG" in not_image.json()["detail"]
    oversize = signatures.PNG_MAGIC + b"\x00" * signatures.MAX_BYTES
    too_big = client.post(f"{base}/upload", _file(oversize))
    assert too_big.status_code == 422 and "two megabytes" in too_big.json()["detail"]
    # The pad sends a PNG; a JPEG sent as a drawing is refused.
    as_drawn = client.post(f"{base}/draw", _file(jpeg(), "sig.jpg", "image/jpeg"))
    assert as_drawn.status_code == 422 and "PNG" in as_drawn.json()["detail"]
    empty = client.post(f"{base}/draw", _file(b"", "empty.png"))
    assert empty.status_code == 422
    with pytest.raises(ServiceError, match="empty"):
        signatures.save(staff_user, staff_user.pk, b"", source="uploaded", file_name="x.png")
    with pytest.raises(ServiceError, match="source"):
        signatures.save(staff_user, staff_user.pk, png(), source="scanned")
    assert not Signature.objects.exists()
    assert not Activity.objects.filter(action="signature_changed").exists()


# ---- who may ----------------------------------------------------------------------------------


def test_only_the_person_and_an_administrator_may_read_or_change_it(
    db, staff_user, colleague_user, admin_user, media
) -> None:
    signatures.save(staff_user, staff_user.pk, png(), source="drawn")
    base = _base(staff_user)

    colleague = _client(colleague_user)
    assert colleague.get(base).status_code == 403
    assert colleague.get(f"{base}/image").status_code == 403
    assert colleague.post(f"{base}/upload", _file(png())).status_code == 403
    assert colleague.post(f"{base}/draw", _file(png())).status_code == 403
    # A refusal says nothing about whether the person exists.
    nobody = "/api/core/users/00000000-0000-0000-0000-000000000000/signature"
    assert colleague.get(nobody).status_code == 403

    anonymous = Client()
    assert anonymous.get(base).status_code == 401
    assert anonymous.get(f"{base}/image").status_code == 401
    assert anonymous.post(f"{base}/draw", _file(png())).status_code == 401

    admin = _client(admin_user)
    assert admin.get(base).json()["source"] == "drawn"
    assert admin.get(f"{base}/image").status_code == 200
    changed = admin.post(f"{base}/upload", _file(jpeg(), "signed.jpg", "image/jpeg"))
    assert changed.status_code == 200 and changed.json()["updated_by_name"] == admin_user.name
    assert admin.get(nobody).status_code == 404
    assert Signature.objects.get(user=staff_user).updated_by_id == admin_user.pk
    assert Signature.objects.filter(user=colleague_user).count() == 0


def test_an_inactive_persons_signature_cannot_change(db, staff_user, admin_user, media) -> None:
    identity.deactivate_user(admin_user, staff_user)
    staff_user.refresh_from_db()
    with pytest.raises(NotAllowed, match="not active"):
        signatures.save(admin_user, staff_user.pk, png(), source="drawn")
    with pytest.raises(NotAllowed):
        signatures.target(staff_user, admin_user.pk)
    with pytest.raises(NotFound):
        signatures.target(admin_user, uuid.UUID(int=0))
    refused = _client(admin_user).post(f"{_base(staff_user)}/draw", _file(png()))
    assert refused.status_code == 403


# ---- the activity line ------------------------------------------------------------------------


def test_each_change_leaves_an_activity_line_about_the_person(
    db, staff_user, admin_user, media
) -> None:
    _client(staff_user).post(f"{_base(staff_user)}/draw", _file(png()))
    _client(admin_user).post(
        f"{_base(staff_user)}/upload", _file(jpeg(), "signed.jpg", "image/jpeg")
    )
    rows = list(Activity.objects.filter(action="signature_changed").order_by("at"))
    assert [(r.actor_id, r.subject_type, r.subject_id) for r in rows] == [
        (staff_user.pk, "user", staff_user.pk),
        (admin_user.pk, "user", staff_user.pk),
    ]
    assert rows[0].summary == f"{staff_user.name}'s signature was drawn"
    assert rows[1].summary == f"{admin_user.name} changed {staff_user.name}'s signature (uploaded)"
    assert rows[1].data["source"] == "uploaded" and rows[1].request_id is None
