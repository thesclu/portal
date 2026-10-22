"""Where the bytes of a source and its attachments live, and what a stored file is called.

Every file a person uploads, and every raw message and attachment inbound mail will store (P5),
goes through here, because a file name is attacker-controlled text that ends up in three places: a
path on disk, a database column, and a `Content-Disposition` header. `safe_filename` is the one
rule for all three. The layout is the one P5's brief names:

    sources/<yyyy>/<mm>/<source_id>.<json|eml>             the untouched original (`store_raw`)
    attachments/<source_id>/<sha256[:16]>-<safe filename>   one file (`store_attachment`)
    chat/<channel_id>/<sha256[:16]>-<safe filename>         a file sent in team chat (P22)

Nothing in a stored path is taken from the caller except the sanitised name: the directories are a
uuid and a hash this module computed. The returned storage name is relative, and is what goes in
`sources.raw_storage_url` / `attachments.storage_url`.

Storage is Django's `default_storage` (the local media directory in development, S3 when
`STORAGE_S3_BUCKET` is set). It is not part of the ledger: nothing here writes a records row.
"""

import hashlib
import mimetypes
import re
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO
from uuid import UUID

from django.core.exceptions import SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import default_storage

# The longest a sanitised name may be, counted in BYTES of UTF-8, not characters. A stored name is
# `<16 hex>-<name>` (17 bytes before it) and Django's storage adds `_<7 characters>` when a name is
# taken, so the longest path segment this module asks a filesystem for is 17 + 150 + 8 = 175 bytes,
# whatever the script the name is written in. ext4, xfs and btrfs cap ONE segment at 255 BYTES, so
# that leaves 80 bytes in hand. Counting characters instead let a name of 300 CJK characters
# through as a 459-byte segment (P8b review F6). The cap keeps a short extension and trims the stem.
MAX_NAME_BYTES = 150
# An extension longer than this is not an extension worth keeping; it is cut with the rest.
_MAX_EXTENSION_CHARS = 16
_MAX_EXTENSION_BYTES = 32
# How many times `safe_filename` will clean a name before it gives up on it (see there).
_MAX_PASSES = 6

# Characters that are legal in a file name but dangerous where this one ends up: the angle
# brackets and quotes in an HTML attribute or a header, the rest on a filesystem a person may
# later copy the file to.
_UNSAFE = str.maketrans(dict.fromkeys('<>:"|?*', "_"))
# Control, format (the bidirectional overrides that make "gpj.exe" read as ".jpg"), surrogate,
# private-use, unassigned and the line and paragraph separators.
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_MIME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$")

RAW_KINDS = {
    "postmark_json": "json",
    "eml": "eml",
    "upload_manifest": "json",
    # P11: what was handed to the provider, as sent.
    "sent_json": "json",
}

_CHUNK = 64 * 1024


class UploadTooLarge(ValueError):
    """A file went past the size the caller allowed. Raised while streaming, so the rest of a huge
    upload is never read."""


@dataclass(frozen=True)
class StoredFile:
    storage_name: str
    sha256: str
    size: int


def _clean(name: str) -> str:
    """One cleaning pass. Not the public rule: `safe_filename` runs it until the name stops
    changing, so a pass that left something for the next one could not break idempotence."""
    # Drop first, THEN normalise: removing a zero-width character between a letter and its accent
    # leaves a string that is not in NFC, which normalising first would not have seen.
    text = "".join(ch for ch in name if unicodedata.category(ch) not in _DROPPED_CATEGORIES)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\\", "/").split("/")[-1]
    text = re.sub(r"\s+", " ", text).translate(_UNSAFE)
    # Dots and spaces go from BOTH ends in one step. Chained strips (strip, then lstrip the dots,
    # then strip again) left a dot behind whenever dots and spaces alternated: `". .htaccess"`
    # came out as `".htaccess"`, a hidden file. It also strips a trailing dot or space, which
    # Windows drops from a name on its own.
    text = text.strip(". ")
    return _cap_bytes(text).strip(". ")


def _cap_bytes(text: str) -> str:
    """At most `MAX_NAME_BYTES` bytes of UTF-8, cut between characters, a short extension kept."""
    if len(text.encode()) <= MAX_NAME_BYTES:
        return text
    stem, dot, extension = text.rpartition(".")
    if (
        dot
        and stem
        and 0 < len(extension) <= _MAX_EXTENSION_CHARS
        and len(extension.encode()) <= _MAX_EXTENSION_BYTES
    ):
        return _take_bytes(stem, MAX_NAME_BYTES - len(extension.encode()) - 1) + "." + extension
    return _take_bytes(text, MAX_NAME_BYTES)


def _take_bytes(text: str, limit: int) -> str:
    """The longest prefix of `text` whose UTF-8 encoding is at most `limit` bytes."""
    used = 0
    for index, ch in enumerate(text):
        used += len(ch.encode())
        if used > limit:
            return text[:index]
    return text


def _settle(name: str) -> str:
    """`name` cleaned until a pass changes nothing, or an empty string if it will not settle.

    One pass settles every name seen so far, so the loop is the guarantee and not the plan: it
    makes the rule idempotent by construction. The result is always a fixed point of `_clean`,
    which is what lets the database row, the stored path and the download header agree on one
    name when each of them runs the rule over what the one before it produced."""
    text = name
    for _ in range(_MAX_PASSES):
        cleaned = _clean(text)
        if cleaned == text:
            return text
        text = cleaned
    return ""


def safe_filename(name: str | None, fallback: str = "file") -> str:
    """The last path component of `name`, with nothing in it that can mean anything else.

    Both separators are honoured whatever the platform, so `..\\..\\evil.exe` and `../../evil.exe`
    both become `evil.exe`; control and bidirectional-override characters are removed; leading dots
    and spaces go, however they alternate, so a name can never be `.` or `..` or a hidden file; and
    the length is capped at `MAX_NAME_BYTES` bytes of UTF-8, with a short extension kept. A name
    that is nothing but junk becomes `fallback`, cleaned the same way (and `file` if that is junk
    too), so the result is never empty.

    Idempotent: `safe_filename(safe_filename(x)) == safe_filename(x)`. A file's name is chosen once,
    when it is brought in, and that string is the one the database row keeps, the stored path ends
    with and the download header serves; the later calls that guard the path and the header are
    no-ops on it.
    """
    return _settle(name or "") or _settle(fallback) or "file"


def guess_mime(filename: str, declared: str | None = None) -> str | None:
    """A type for a stored file, from its name; the client's own claim only as a fallback, and
    only if it is shaped like a media type at all. Never trusted for what the download serves."""
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    if declared and _MIME.match(declared.strip()):
        return declared.strip().lower()
    return None


def store_attachment(
    source_id: UUID, filename: str, chunks: Iterable[bytes], *, max_bytes: int | None = None
) -> StoredFile:
    """Stream one file into storage, hashing it as it goes.

    The hash is in the name, so the bytes are read once into a spooled temporary file (memory for a
    small file, disk for a big one) and the name is made after. `max_bytes` stops the read the
    moment the file goes over it and raises `UploadTooLarge`; nothing has been stored by then.
    """
    return _store(f"attachments/{source_id}", filename, chunks, max_bytes=max_bytes)


def store_chat_file(
    channel_id: UUID, filename: str, chunks: Iterable[bytes], *, max_bytes: int | None = None
) -> StoredFile:
    """A file attached in team chat (P22), exactly as `store_attachment` stores one, under the
    conversation's own directory. Nothing in the path is the caller's but the sanitised name."""
    return _store(f"chat/{channel_id}", filename, chunks, max_bytes=max_bytes)


def _store(
    directory: str, filename: str, chunks: Iterable[bytes], *, max_bytes: int | None
) -> StoredFile:
    digest = hashlib.sha256()
    size = 0
    with tempfile.SpooledTemporaryFile(max_size=_CHUNK * 16) as spool:
        for chunk in chunks:
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise UploadTooLarge(size)
            digest.update(chunk)
            spool.write(chunk)
        spool.seek(0)
        sha256 = digest.hexdigest()
        name = f"{directory}/{sha256[:16]}-{safe_filename(filename)}"
        stored = default_storage.save(name, File(spool))
    return StoredFile(storage_name=stored, sha256=sha256, size=size)


def store_raw(source_id: UUID, kind: str, data: bytes, *, when: datetime | None = None) -> str:
    """Save the untouched original of a source and return its storage name."""
    extension = RAW_KINDS.get(kind)
    if extension is None:
        raise ValueError(f"Unknown raw kind {kind!r}; expected one of {sorted(RAW_KINDS)}.")
    stamp = when or datetime.now(UTC)
    name = f"sources/{stamp:%Y}/{stamp:%m}/{source_id}.{extension}"
    with tempfile.SpooledTemporaryFile(max_size=_CHUNK * 16) as spool:
        spool.write(data)
        spool.seek(0)
        return default_storage.save(name, File(spool))


def open_stored(storage_name: str) -> IO[bytes]:
    """Open a stored file for reading, or raise `FileNotFoundError`.

    `storage_name` is a database value, so it is treated as untrusted: the storage backend refuses
    a path that leaves its root with a `SuspiciousFileOperation`, and that refusal is reported the
    same way as a file that is not there. A caller cannot tell the two apart, and must not be able
    to.
    """
    try:
        if not storage_name or not default_storage.exists(storage_name):
            raise FileNotFoundError(storage_name)
        return default_storage.open(storage_name, "rb")
    except SuspiciousFileOperation as exc:
        raise FileNotFoundError(storage_name) from exc


def delete_stored(storage_names: Iterable[str]) -> None:
    """Best-effort removal of files a failed write left behind. Never raises: it runs while
    another error is already on its way out, and an orphaned file costs nothing but space."""
    for name in storage_names:
        try:
            default_storage.delete(name)
        except Exception:  # cleanup must not mask the original error
            pass
