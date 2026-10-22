"""A message as ONE reader sees it (P22).

A message's text is stored as written. What a reader is sent is its PARTS: plain text, @mentions,
links to pages of the portal, links elsewhere, and references to requests. A request is named in
a message either by its address (`/requests/<id>`, alone or as a full portal URL) or by its
tracking number (`REQ-2026-000071`), and it is resolved for each reader against the visibility
rule (`records/services/access.py::visible_q`, the one rule, which the row-level policies repeat):

  a request the reader may see       its tracking number, linking to it, with "subject · agency"
  anything else                      a neutral "a request" chip: no number, no subject, no link

"Anything else" includes a number or an id that names no request at all, so a reader cannot use a
message to learn whether a request exists. The raw text is therefore never sent to anyone but
its author (who needs it to edit, and wrote it); everyone else gets the parts.

A deleted message has no parts and no files, only who deleted it. Its words are in its versions,
which only the legal director's discovery route reads.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.db.models import Q

from portal.core.chat import access
from portal.core.models import ChatAttachment, ChatMention, ChatMessage, User
from portal.core.services.identity import records_role_of
from portal.core.services.presence import initials

PREVIEW_CHARS = 140
HIDDEN_REQUEST = "a request"

# The first segment of a portal address a message may link to: every tool's pages. A path that
# starts with anything else ("and/or", "/tmp") is text.
PAGE_ROOTS = (
    "requests",
    "docket",
    "inbox",
    "tasks",
    "calendar",
    "agencies",
    "records",
    "templates",
    "reports",
    "users",
    "activity",
    "settings",
    "search",
    "new",
    "drafts",
    "approvals",
    "my-requests",
    "suggest",
    "documents",
)
TRACKING = re.compile(r"(?<![\w-])REQ-\d{4}-\d{6}(?![\w-])")
URL = re.compile(r"https?://[^\s<>\"'`]+")
PATH = re.compile(
    r"(?<![\w/.~:-])/(?:" + "|".join(re.escape(r) for r in PAGE_ROOTS) + r")(?![\w-])"
    r"[^\s<>\"'`]*"
)
REQUEST_PATH = re.compile(
    r"^/requests/([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})(?=$|[/?#])"
)
# A sentence's own punctuation after a link is not part of it.
_TRAILING = ".,;:!?)]}'\""


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    kind: str  # mention | tracking | url | path
    text: str
    value: str = ""  # the user id, the number, the path or the URL


def person(user: User) -> dict[str, Any]:
    return {"id": user.pk, "name": user.name, "initials": initials(user.name)}


def _portal_origin() -> tuple[str, str]:
    parts = urlsplit(settings.PORTAL["PUBLIC_URL"])
    return parts.scheme, parts.netloc


def _trim(text: str) -> str:
    while text and text[-1] in _TRAILING:
        text = text[:-1]
    return text


def _mention_tokens(body: str, people: Iterable[User]) -> list[_Token]:
    """Where each mentioned person's `@Full Name` (or `@First`) is in the text."""
    out: list[_Token] = []
    lowered = body.lower()
    for user in people:
        full = (user.name or "").strip()
        names = [full] + ([full.split(" ")[0]] if " " in full else [])
        for name in names:
            if not name:
                continue
            needle = "@" + name.lower()
            at = lowered.find(needle)
            found = False
            while at != -1:
                end = at + len(needle)
                if end == len(body) or not (body[end].isalnum() or body[end] == "_"):
                    out.append(_Token(at, end, "mention", body[at:end], str(user.pk)))
                    found = True
                at = lowered.find(needle, end)
            if found:
                break
    return out


def tokens(body: str, mentioned: Iterable[User] = ()) -> list[_Token]:
    """Every link, reference and mention in the text, left to right, none overlapping."""
    found: list[_Token] = list(_mention_tokens(body, mentioned))
    scheme, netloc = _portal_origin()
    for match in URL.finditer(body):
        text = _trim(match.group(0))
        parts = urlsplit(text)
        if parts.scheme == scheme and parts.netloc == netloc:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            found.append(_Token(match.start(), match.start() + len(text), "path", text, path))
        else:
            found.append(_Token(match.start(), match.start() + len(text), "url", text, text))
    for match in PATH.finditer(body):
        text = _trim(match.group(0))
        found.append(_Token(match.start(), match.start() + len(text), "path", text, text))
    for match in TRACKING.finditer(body):
        found.append(_Token(match.start(), match.end(), "tracking", match.group(0), match.group(0)))
    found.sort(key=lambda t: (t.start, -(t.end - t.start)))
    out: list[_Token] = []
    reach = 0
    for token in found:
        if token.start >= reach and token.end > token.start:
            out.append(token)
            reach = token.end
    return out


def _request_ref(token: _Token) -> tuple[UUID | None, str | None]:
    """(request id, tracking number) a token names, either or neither."""
    if token.kind == "tracking":
        return None, token.value
    if token.kind == "path":
        match = REQUEST_PATH.match(token.value)
        if match:
            return UUID(match.group(1)), None
    return None, None


@dataclass
class _Requests:
    by_id: dict[UUID, Any]
    by_number: dict[str, Any]


def _resolve(reader: User, ids: set[UUID], numbers: set[str]) -> _Requests:
    """The named requests this reader may see; the rest are simply absent."""
    if not ids and not numbers:
        return _Requests({}, {})
    from portal.records.models import Request
    from portal.records.services.access import Viewer, visible_q

    viewer = Viewer(user_id=reader.pk, role=records_role_of(reader))
    rows = (
        Request.objects.filter(visible_q(viewer))
        .filter(Q(pk__in=ids) | Q(tracking_number__in=numbers))
        .select_related("agency")
    )
    found = list(rows)
    return _Requests({r.pk: r for r in found}, {r.tracking_number: r for r in found})


def _parts(body: str, toks: Sequence[_Token], known: _Requests) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    at = 0
    for token in toks:
        if token.start > at:
            out.append({"kind": "text", "text": body[at : token.start]})
        at = token.end
        if token.kind == "mention":
            out.append({"kind": "mention", "text": token.text, "user_id": token.value})
            continue
        request_id, number = _request_ref(token)
        if request_id is not None or number is not None:
            row = known.by_id.get(request_id) if request_id else known.by_number.get(number or "")
            if row is None:
                out.append({"kind": "request", "text": HIDDEN_REQUEST})
            else:
                out.append(
                    {
                        "kind": "request",
                        "text": row.tracking_number,
                        "request_id": row.pk,
                        "title": f"{row.subject} · {row.agency.name}",
                        "to": f"/requests/{row.pk}",
                    }
                )
        elif token.kind == "path":
            out.append({"kind": "page", "text": token.text, "to": token.value})
        else:
            out.append({"kind": "url", "text": token.text, "href": token.value})
    if at < len(body):
        out.append({"kind": "text", "text": body[at:]})
    return out


def preview(parts: Sequence[dict[str, Any]]) -> str:
    """One line for the conversation list, from what the reader may see."""
    text = " ".join("".join(str(p.get("text", "")) for p in parts).split())
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1].rstrip() + "…"


def messages_out(reader: User, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Each message as `reader` sees it. A fixed handful of queries for the whole page."""
    if not messages:
        return []
    ids = [m.pk for m in messages]
    mentioned: dict[UUID, list[User]] = defaultdict(list)
    for mention in ChatMention.objects.filter(message_id__in=ids).select_related("user"):
        mentioned[mention.message_id].append(mention.user)
    files: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for file in ChatAttachment.objects.filter(message_id__in=ids).order_by("created_at"):
        assert file.message_id is not None
        files[file.message_id].append(
            {
                "id": file.pk,
                "file_name": file.file_name,
                "content_type": file.content_type,
                "size": file.size,
            }
        )
    toks = {
        m.pk: tokens(m.body, mentioned[m.pk]) for m in messages if m.deleted_at is None and m.body
    }
    ids_named: set[UUID] = set()
    numbers: set[str] = set()
    for found in toks.values():
        for token in found:
            request_id, number = _request_ref(token)
            if request_id:
                ids_named.add(request_id)
            if number:
                numbers.add(number)
    known = _resolve(reader, ids_named, numbers)
    admin = access.is_admin(reader)
    out: list[dict[str, Any]] = []
    for m in messages:
        deleted = m.deleted_at is not None
        mine = m.author_id == reader.pk
        out.append(
            {
                "id": m.pk,
                "channel_id": m.channel_id,
                "author": person(m.author),
                "created_at": m.created_at,
                "changed_at": m.changed_at,
                "edited": m.edited_at is not None and not deleted,
                "edited_at": m.edited_at if not deleted else None,
                "deleted": deleted,
                "deleted_at": m.deleted_at,
                "deleted_by": person(m.deleted_by) if deleted and m.deleted_by else None,
                "parts": [] if deleted else _parts(m.body, toks.get(m.pk, []), known),
                "body": m.body if mine and not deleted else None,
                "attachments": [] if deleted else files[m.pk],
                "mine": mine,
                "mentions_me": any(u.pk == reader.pk for u in mentioned[m.pk]) and not deleted,
                "can_edit": mine and not deleted,
                "can_delete": not deleted and (mine or admin),
            }
        )
    return out
