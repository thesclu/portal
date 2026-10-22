"""Who may see a draft.

A draft is not a request and has no access list. Two things decide it, and both must hold:

1. Its own rule (BUILD_PLAN P9, "Drafting"): **a draft whose `sensitivity` is `privileged` is
   visible to the person who created it, and to nobody else but an administrator** (P20h: an
   administrator sees every privileged request, 37a, so the letter of one too). Every other
   draft is open to admins, staff and staff attorneys. A limited-scope attorney or an intern
   (P20h) sees a draft they started, one an intern started (an intern only), or one that names a
   request; in every case only when they may also see every request it names (rule 2).
2. **A draft is the text of the requests it names.** A request started as a draft and filed from
   it carries the draft's subject, items and wording; a draft started from a request copied them.
   So a draft is visible only to someone who may see EVERY request it names: the one it was
   started from (`source_request_id`) and each one it was filed as (`recipients.request_id`).
   Sealing a request therefore seals the draft it came from, for everyone off the request's list,
   the draft's creator included. Without this the drafting routes would serve a privileged
   request's text to all staff under another name.

Who may see a REQUEST is still decided only in `records/api/permissions.py`; this module asks it
(`visible_request_ids`) and never compares a request's sensitivity itself. When a privileged draft
is filed, each request it produces is reserved by the person filing, who is therefore put on its
access list by `records.services.access.on_reserved`.

Every drafting route that takes a draft, or an id that resolves to one, passes through a function
here, and the census in `tests/api/test_p9_census.py` holds them to it.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from django.http import HttpRequest
from ninja.errors import HttpError

from portal.drafting.models import (
    LetterDraft,
    LetterDraftCitation,
    LetterDraftItem,
    LetterRecipient,
)
from portal.drafting.services.common import get_or_404
from portal.records import roles
from portal.records.api.permissions import (
    PrivilegedRefusal,
    require_named_request_scope,
    require_team,
    visible_request_ids,
)
from portal.records.models import RecordsUser, Sensitivity
from portal.records.services import access
from portal.records.services.access import Viewer

_PRIVILEGED = Sensitivity.PRIVILEGED.value


def _named_requests(drafts: Sequence[LetterDraft]) -> dict[UUID, set[UUID]]:
    """For each draft, the requests it names: what it was started from and what it was filed as."""
    named: dict[UUID, set[UUID]] = {d.pk: set() for d in drafts}
    for draft in drafts:
        if draft.source_request_id is not None:
            named[draft.pk].add(draft.source_request_id)
    filed = LetterRecipient.objects.filter(draft_id__in=named, request_id__isnull=False)
    for draft_id, request_id in filed.values_list("draft_id", "request_id"):
        named[draft_id].add(request_id)
    return named


def _scoped_rule(request: HttpRequest, role: str) -> Q:
    """For a limited-scope attorney or an intern, the drafts they may consider at all before rule
    2: their own, an intern's (an intern only), or one that names a request (started from one, or
    reserved as one)."""
    rule = Q(created_by_user_id=request.user.id)
    if role == roles.INTERN:
        rule |= Q(
            created_by_user_id__in=list(
                RecordsUser.objects.filter(role=roles.INTERN).values_list("pk", flat=True)
            )
        )
    named = Q(source_request_id__isnull=False) | Q(
        pk__in=LetterRecipient.objects.filter(request_id__isnull=False).values("draft_id")
    )
    return rule | named


def scope_visible_drafts(request: HttpRequest, rows: QuerySet[LetterDraft]) -> list[LetterDraft]:
    """The drafts the caller may see, in the order given. A fixed handful of queries."""
    role = require_team(request)
    own_rule = ~Q(sensitivity=_PRIVILEGED) | Q(created_by_user_id=request.user.id)
    if role == roles.ADMIN:
        own_rule = Q()
    if role in roles.GRANT_SCOPED:
        own_rule &= _scoped_rule(request, role)
    drafts = list(rows.filter(own_rule))
    named = _named_requests(drafts)
    seen = visible_request_ids(request, set().union(*named.values()))
    out = []
    for draft in drafts:
        if not named[draft.pk] <= seen:
            continue
        if role in roles.GRANT_SCOPED and not _own_or_intern(request, role, draft):
            # Reached only by naming requests: it must name at least one, all of them visible.
            if not named[draft.pk]:
                continue
        out.append(draft)
    return out


def _own_or_intern(request: HttpRequest, role: str, draft: LetterDraft) -> bool:
    return _own_or_intern_for(request.user.id, role, draft)


def _own_or_intern_for(user_id: UUID, role: str, draft: LetterDraft) -> bool:
    if draft.created_by_user_id == user_id:
        return True
    if role != roles.INTERN or draft.created_by_user_id is None:
        return False
    return RecordsUser.objects.filter(pk=draft.created_by_user_id, role=roles.INTERN).exists()


def draft_visible_to(viewer: Viewer, draft: LetterDraft) -> bool:
    """Whether this person may see this draft: exactly what `require_draft_scope` lets through,
    for a person who is not the one asking (P21c: the drafting tool's access list for a wave is
    made of the people this answers yes for). The two rules of this module's docstring, the
    request half asked of `records.services.access` (never decided here)."""
    role = viewer.role
    if role not in roles.TEAM:
        return False
    if (
        draft.sensitivity == _PRIVILEGED
        and draft.created_by_user_id != viewer.user_id
        and role != roles.ADMIN
    ):
        return False
    named = _named_requests([draft])[draft.pk]
    if named - access.visible_ids(viewer, named):
        return False
    if role in roles.GRANT_SCOPED and not named:
        return _own_or_intern_for(viewer.user_id, role, draft)
    return True


def draft_is_privileged(draft: LetterDraft) -> bool:
    """Whether the draft, or any request it names, is privileged: what the drafting tool is told
    as a wave's `privileged` flag (P21c), read from each thing's own flag."""
    if draft.sensitivity == _PRIVILEGED:
        return True
    named = _named_requests([draft])[draft.pk]
    from portal.records.models import Request

    return any(access.is_privileged(r) for r in Request.objects.filter(pk__in=named))


# For a limited-scope attorney or an intern, one answer for a draft (or a part of one) they may not
# see and for one that does not exist, naming nothing: not the id they asked for, and above all
# not the id of the draft a part belongs to.
_SCOPED_NOT_FOUND = "Not found."


def require_draft_scope(request: HttpRequest, draft_id: UUID) -> LetterDraft:
    role = require_team(request)
    scoped = role in roles.GRANT_SCOPED
    if scoped:
        found = LetterDraft.objects.filter(pk=draft_id).first()
        if found is None:
            raise HttpError(404, _SCOPED_NOT_FOUND)
        draft = found
    else:
        draft = get_or_404(LetterDraft, draft_id, "draft")
    # One rule, `draft_visible_to` (P21c: the drafting tool's access lists are made of it too).
    # What a refusal looks like is the caller's: staff get the privileged refusal, a limited-scope
    # attorney or an intern the 404 that names nothing.
    if draft_visible_to(Viewer(user_id=request.user.id, role=role), draft):
        return draft
    raise HttpError(404, _SCOPED_NOT_FOUND) if scoped else PrivilegedRefusal()


def _require_part_scope(request: HttpRequest, model: Any, pk: UUID, what: str) -> Any:
    role = require_team(request)
    if role in roles.GRANT_SCOPED:
        row = model.objects.filter(pk=pk).first()
        if row is None:
            raise HttpError(404, _SCOPED_NOT_FOUND)
    else:
        row = get_or_404(model, pk, what)
    require_draft_scope(request, row.draft_id)
    return row


def require_draft_item_scope(request: HttpRequest, item_id: UUID) -> LetterDraftItem:
    return _require_part_scope(request, LetterDraftItem, item_id, "draft item")


def require_draft_citation_scope(request: HttpRequest, citation_id: UUID) -> LetterDraftCitation:
    return _require_part_scope(request, LetterDraftCitation, citation_id, "draft citation")


def require_recipient_scope(request: HttpRequest, recipient_id: UUID) -> LetterRecipient:
    return _require_part_scope(request, LetterRecipient, recipient_id, "recipient")


def require_source_request_scope(request: HttpRequest, from_request_id: UUID | None) -> None:
    """Starting a draft from a past request copies its subject and items: a way to read it."""
    if from_request_id is not None:
        require_named_request_scope(request, from_request_id)


def require_may_seal(request: HttpRequest, draft: LetterDraft, sensitivity: str | None) -> None:
    """Making a draft privileged hands it to its creator alone. Anyone else doing that would be
    locking themselves out of what they are editing, and a draft with no recorded creator would
    become invisible to everyone."""
    if sensitivity == _PRIVILEGED and draft.created_by_user_id != request.user.id:
        raise HttpError(422, "Only the person who started a draft can make it privileged.")
