"""The portal's side of the seam with the drafting tool (BUILD_PLAN P21c; DECISIONS 49).

WHEN. Only while the drafting tool is in use: the administrator's switch in Settings → System
(`drafting.fork_enabled`) is on AND the server has the two shared secrets and both addresses
(`ForkConfig.in_use`). Otherwise nothing here runs and the portal drafts in its own editor exactly
as before (P19b–P19h, which stays). Signing in to the fork and ending its sessions are not here at
all (P25b): the fork signs people in through Keycloak's `drafting` client, and Keycloak's own
back-channel logout ends its sessions when the portal signs someone out, deactivates them or
signs them out everywhere (DECISIONS 53).

THE FLOW, each step signed as the person who clicked (never a superuser):
  stage 1 "Use"     `start_wave`: the wave for a new draft, keyed by the draft's id, from the
                    template it mirrors, a past request's master, or blank on the organisation's
                    usual letterhead, with its first access list. A source that lives only in the
                    portal (a template never made in the drafting tool, a past request written in
                    the portal's editor) keeps the draft in the portal's editor.
  stage 3 reserve   `require_complete` before (the master filled for this agency, in memory: a
                    letter with a blank is not reserved), then `make_letters` after: the fork's
                    merge, one document per agency (name, address, channel, date, and the reserved
                    tracking number and request address), and the request's words written from
                    the agency's .docx.
  stage 4 filing    `refresh_before_filing`: the letter's current version; if it changed since the
                    request's words were written, they are written again (an audited edit), before
                    an intern's approval is checked, so an approval of older words does not clear
                    newer ones. Email: the PDF the message carries is the fork's
                    (`letter_for_mail`); the filing event is recorded exactly as before.
  any time          the fork's webhooks (`receive_webhook`) keep the mirror's versions current and
                    feed the Files tab and the Suggestions badge; `push_wave_access` (a job) sends
                    each wave's access list whenever who may see its requests changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from portal.core.services.settings import get_setting
from portal.drafting import docx_text, mike
from portal.drafting.mike import ForkClient, ForkError, PortalPerson
from portal.drafting.models import ForkEvent, LetterDraft, LetterRecipient, LetterTemplate
from portal.records import roles
from portal.records.actors import Actor
from portal.records.models import RecordsUser, Request
from portal.records.services import edits as edits_service
from portal.records.services.access import Viewer
from portal.records.services.common import (
    InvalidTransition,
    ServiceError,
    lock_request,
    require_human,
)

logger = logging.getLogger(__name__)

ENABLED_KEY = "drafting.fork_enabled"
API_URL_KEY = "drafting.fork_api_url"
WEB_URL_KEY = "drafting.fork_web_url"
LETTERHEAD_KEY = "drafting.fork_letterhead_id"
SETTING_KEYS = (ENABLED_KEY, API_URL_KEY, WEB_URL_KEY, LETTERHEAD_KEY)

# The note on the audited edit that writes a reserved request's words from the drafting tool.
TEXT_NOTE = "Written from the letter in the drafting tool."
# A merge value's name: the fork refuses any other form (SCLU.md 9.2, 9.9 item 6).
_VALUE_NAME = re.compile(r"[A-Z0-9_]+")
# What the reserve check fills the two reserved values with: the merge itself has the real ones.
_RESERVED_STAND_INS = {"TRACKING_NUMBER": "REQ-0000-000000", "REPLY_TO": "reserved@example.org"}

# Tests hand the client a fake transport here (httpx.MockTransport) and a sleep that returns.
_transport: httpx.BaseTransport | None = None
_sleep = None


class DraftingToolUnavailable(ServiceError):
    """The drafting tool could not be reached, or failed (answered 503 by the API)."""


class DraftingToolRefused(ServiceError):
    """The drafting tool refused the step, with the reason in words (answered 422)."""


# ---- configuration -----------------------------------------------------------------------------


def _url_or_blank(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    return text


@dataclass(frozen=True)
class ForkConfig:
    enabled: bool
    api_url: str
    web_url: str
    api_url_default: str
    web_url_default: str
    issuer: str
    letterhead_id: str
    timeout: float
    service_secret: str = field(repr=False)
    webhook_secret: str = field(repr=False)

    @staticmethod
    def _set(secret: str) -> bool:
        return len(secret) >= mike.MIN_SECRET_LENGTH

    @property
    def secrets_set(self) -> dict[str, bool]:
        return {
            "service": self._set(self.service_secret),
            "webhook": self._set(self.webhook_secret),
        }

    @property
    def ready(self) -> bool:
        """Everything the server needs, whatever the switch says."""
        return bool(self.api_url and self.web_url) and all(self.secrets_set.values())

    @property
    def in_use(self) -> bool:
        return self.enabled and self.ready

    @property
    def web_origin(self) -> str:
        return origin_of(self.web_url)


def origin_of(url: str) -> str:
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def config() -> ForkConfig:
    env = settings.DRAFTING
    api_default = _url_or_blank(env.get("API_URL"))
    web_default = _url_or_blank(env.get("WEB_URL"))
    letterhead = str(get_setting(LETTERHEAD_KEY) or "").strip()
    return ForkConfig(
        enabled=get_setting(ENABLED_KEY) is True,
        api_url=_url_or_blank(get_setting(API_URL_KEY)) or api_default,
        web_url=_url_or_blank(get_setting(WEB_URL_KEY)) or web_default,
        api_url_default=api_default,
        web_url_default=web_default,
        # P25b: the people a call names are the realm's (the fork's SCLU_KEYCLOAK_ISSUER).
        issuer=settings.KEYCLOAK["ISSUER"],
        letterhead_id=letterhead,
        timeout=float(env.get("TIMEOUT_SECONDS") or 30.0),
        service_secret=str(env.get("SERVICE_SECRET") or ""),
        webhook_secret=str(env.get("WEBHOOK_SECRET") or ""),
    )


def in_use() -> bool:
    return config().in_use


def check_address(value: str, what: str) -> str:
    """A Settings address: empty (the server's default), or http(s) with a host and no
    credentials, query or fragment. Returned without a trailing slash."""
    text = value.strip().rstrip("/")
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ServiceError(f"{what} is an address starting http:// or https://.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ServiceError(f"{what} carries no name, password, query or fragment.")
    return text


def client(cfg: ForkConfig | None = None) -> ForkClient:
    cfg = cfg or config()
    extra: dict[str, Any] = {}
    if _sleep is not None:
        extra["sleep"] = _sleep
    return ForkClient(
        cfg.api_url,
        cfg.service_secret,
        cfg.issuer,
        timeout=cfg.timeout,
        transport=_transport,
        **extra,
    )


# ---- errors in words -----------------------------------------------------------------------------

_OPERATOR = "An administrator should check Settings → System."


def in_words(error: ForkError) -> ServiceError:
    """The fork's refusal as the API's answer. Its own sentence for what a person can act on; the
    operator's errors say so; nothing of the fork's internals."""
    if error.transient:
        return DraftingToolUnavailable(
            "The drafting tool could not be reached just now. Try again in a moment."
        )
    if isinstance(error, mike.ServiceCallsOff):
        return DraftingToolRefused(
            f"The drafting tool is not taking the portal's calls. {_OPERATOR}"
        )
    if isinstance(error, mike.InvalidSignature):
        return DraftingToolRefused(
            f"The drafting tool did not accept the portal's signature (the shared secret differs). "
            f"{_OPERATOR}"
        )
    if isinstance(error, mike.StaleTimestamp):
        return DraftingToolRefused(
            f"The drafting tool and the portal disagree about the time. {_OPERATOR}"
        )
    if isinstance(error, mike.WaveKeyConflict):
        return DraftingToolRefused(
            "This letter was already started in the drafting tool from something else."
        )
    if isinstance(error, mike.PersonNotLinked):
        return DraftingToolRefused(
            "Your sign-in account is still being set up, so the drafting tool cannot be told "
            "who you are yet. Try again in a moment."
        )
    if isinstance(error, mike.NoAccess):
        return DraftingToolRefused("The drafting tool says you cannot open that letter.")
    if isinstance(error, mike.RoleRefused):
        return DraftingToolRefused("The drafting tool refused this for your role.")
    if error.code == "template_archived":
        return DraftingToolRefused("That template is archived in the drafting tool.")
    return DraftingToolRefused(f"The drafting tool refused this: {error.detail}")


# ---- who ---------------------------------------------------------------------------------------


def _user(actor_or_user: Any):
    from portal.core.models import User

    if isinstance(actor_or_user, Actor):
        user_id = require_human(actor_or_user, "Working in the drafting tool")
        return User.objects.get(pk=user_id)
    return actor_or_user


def person_for(actor_or_user: Any) -> PortalPerson:
    return PortalPerson.of(_user(actor_or_user))


# ---- a wave's access list (SCLU.md 15) -----------------------------------------------------------


@dataclass(frozen=True)
class WaveAccess:
    people: list[PortalPerson]
    privileged: bool


def wave_access(draft: LetterDraft) -> WaveAccess:
    """Who besides the fork's broad roles may open this draft's wave, from the portal's own rule.

    Every active person with a team role is asked `draft_visible_to` (the rule
    `require_draft_scope` applies, which asks `records.services.access` about every request the
    draft names); the yeses are the list, less the people the fork lets in anyway: an
    administrator always, staff and staff attorneys while the wave is not privileged. Requesters
    are never asked (the fork refuses a list with one on it). `privileged` is the draft's own flag
    or that of any request it names."""
    from portal.drafting.api.permissions import draft_is_privileged, draft_visible_to

    privileged = draft_is_privileged(draft)
    broad = {roles.ADMIN} if privileged else set(roles.FULL_STAFF)
    people: list[PortalPerson] = []
    for person in _active_team():
        if person.role in broad or person.role == roles.REQUESTER:
            continue
        if draft_visible_to(Viewer(user_id=person.pk, role=person.role), draft):
            account = _account(person.pk)
            if not account.keycloak_sub:
                # Not in Keycloak yet, so not signed in to the fork either (it names people as
                # Keycloak does). The lists are sent again once they are (keycloak.admin._link).
                logger.warning(
                    "wave list for %s leaves out %s: no Keycloak account yet", draft.pk, person.pk
                )
                continue
            people.append(PortalPerson.of(account))
    assert all(p.sclu_role != roles.REQUESTER for p in people)
    return WaveAccess(people=people, privileged=privileged)


def _active_ids():
    """Active accounts, as a subquery (a join from `records.users` to `core.users` would name two
    tables `users`, which Postgres refuses as ambiguous)."""
    from portal.core.models import User

    return User.objects.filter(is_active=True).values("pk")


def _active_team():
    return RecordsUser.objects.filter(role__in=roles.TEAM, pk__in=_active_ids()).order_by("pk")


def _account(user_id: UUID):
    from portal.core.models import User

    return User.objects.get(pk=user_id)


def _signer_for_access(draft: LetterDraft, actor_user_id: UUID | None):
    """Who the access call is signed as: the person whose change caused it when they are an active
    administrator, else an active administrator, else that person or the draft's author (the
    fork accepts any person it maps, 15.9 item 4; an administrator keeps working if that tightens).
    """

    def usable(user_id: UUID | None, *, admin: bool) -> Any:
        if user_id is None:
            return None
        person = _active_team().filter(pk=user_id).first()
        if person is None or (admin and person.role != roles.ADMIN):
            return None
        account = _account(user_id)
        # P25b: the tool names the signer by their Keycloak sub; someone without one cannot sign.
        return account if account.keycloak_sub else None

    found = usable(actor_user_id, admin=True)
    if found is None:
        from portal.core.models import User

        linked = User.objects.filter(keycloak_sub__isnull=False).values("pk")
        admin = (
            _active_team()
            .filter(role=roles.ADMIN, pk__in=linked)
            .values_list("pk", flat=True)
            .first()
        )
        found = usable(admin, admin=True)
    return (
        found or usable(actor_user_id, admin=False) or usable(draft.created_by_user_id, admin=False)
    )


def push_wave_access(draft_id: UUID, actor_user_id: UUID | None = None) -> dict[str, Any] | None:
    """The job's body: send this wave's whole list (`PUT /sclu/waves/:id/access`). Returns the
    fork's answer, or None when there was nothing to do. Raises a transient `ForkError` for the
    job to retry; logs and gives up on anything a retry would not fix."""
    cfg = config()
    if not cfg.in_use:
        logger.info("wave access for draft %s not sent: the drafting tool is not in use", draft_id)
        return None
    draft = LetterDraft.objects.filter(pk=draft_id).first()
    if draft is None or draft.fork_wave_id is None:
        return None
    signer = _signer_for_access(draft, actor_user_id)
    if signer is None:
        logger.error("wave access for draft %s not sent: nobody to sign it as", draft_id)
        return None
    access = wave_access(draft)
    try:
        answer = client(cfg).set_wave_access(
            PortalPerson.of(signer),
            str(draft.fork_wave_id),
            access.people,
            privileged=access.privileged,
        )
    except ForkError as error:
        if error.transient:
            raise
        logger.error(
            "wave access for draft %s refused by the drafting tool: %s (reason %s, index %s)",
            draft_id,
            error,
            error.body.get("reason"),
            error.body.get("index"),
        )
        return None
    logger.info(
        "wave access for draft %s: %s people, privileged %s, %s added, %s removed",
        draft_id,
        answer.get("people"),
        answer.get("privileged"),
        answer.get("added"),
        answer.get("removed"),
    )
    return answer


def enqueue_access_sync(draft_ids: Iterable[UUID], actor_user_id: UUID | None = None) -> int:
    """Defer one access job per wave, coalesced: a wave with a job still waiting gets no second
    one (the waiting job reads the list when it runs). Returns how many were deferred."""
    from procrastinate.exceptions import AlreadyEnqueued

    from portal.drafting.jobs import sync_wave_access

    deferred = 0
    for draft_id in draft_ids:
        try:
            with transaction.atomic():
                sync_wave_access.configure(queueing_lock=f"wave-access:{draft_id}").defer(
                    draft_id=str(draft_id),
                    actor_user_id=str(actor_user_id) if actor_user_id else None,
                )
            deferred += 1
        except AlreadyEnqueued:
            continue
    return deferred


def waves_naming(request_ids: frozenset[UUID] | None):
    """The drafts with a wave whose audience a change to these requests can move (None: all)."""
    drafts = LetterDraft.objects.filter(fork_wave_id__isnull=False)
    if request_ids is not None:
        named = LetterRecipient.objects.filter(request_id__in=request_ids).values("draft_id")
        drafts = drafts.filter(Q(source_request_id__in=request_ids) | Q(pk__in=named))
    return drafts.values_list("pk", flat=True)


def on_access_changed(request_ids: frozenset[UUID] | None) -> None:
    """`records.services.access.after_access_changed`: after the commit, a sync for every wave the
    change can touch. Nothing while the drafting tool is not in use."""

    def run() -> None:
        if not in_use():
            return
        enqueue_access_sync(list(waves_naming(request_ids)))

    transaction.on_commit(run)


def on_draft_changed(draft_id: UUID, actor_user_id: UUID | None = None) -> None:
    """A draft's own sensitivity changed, or it was reserved as a new request: sync its wave."""

    def run() -> None:
        waved = LetterDraft.objects.filter(pk=draft_id, fork_wave_id__isnull=False).exists()
        if waved and in_use():
            enqueue_access_sync([draft_id], actor_user_id)

    transaction.on_commit(run)


# ---- stage 1: the wave -----------------------------------------------------------------------


def wave_source(draft: LetterDraft) -> dict[str, str] | None:
    """Where a new wave for this draft comes from, as `create_wave` takes it, or None when the
    draft belongs in the portal's editor (its source lives only in the portal)."""
    if draft.source_template_id is not None:
        template = LetterTemplate.objects.filter(pk=draft.source_template_id).first()
        if template is None or template.fork_template_id is None:
            return None
        return {"template_id": str(template.fork_template_id)}
    if draft.source_request_id is not None:
        past = (
            LetterRecipient.objects.filter(request_id=draft.source_request_id)
            .order_by("-created_at")
            .first()
        )
        if past is None:
            return None
        master_id = (
            LetterDraft.objects.filter(pk=past.draft_id)
            .values_list("fork_master_document_id", flat=True)
            .first()
        )
        return {"master_id": str(master_id)} if master_id else None
    letterhead = config().letterhead_id
    try:
        return {"letterhead_id": str(UUID(letterhead))} if letterhead else {}
    except ValueError:
        logger.warning("drafting.fork_letterhead_id is not an id; a blank letter has none")
        return {}


def start_wave(draft: LetterDraft, actor_or_user: Any) -> LetterDraft:
    """Stage 1 "Use" while the drafting tool is in use: its wave, as the person who clicked, with
    its first access list. A refusal is raised (the caller's transaction then keeps no draft)."""
    cfg = config()
    if not cfg.in_use or draft.fork_wave_id is not None:
        return draft
    source = wave_source(draft)
    if source is None:
        return draft
    access = wave_access(draft)
    try:
        created, wave = client(cfg).create_wave(
            person_for(actor_or_user),
            key=str(draft.pk),
            name=(draft.subject or "Letter").strip()[:200] or "Letter",
            access=access.people,
            privileged=access.privileged,
            **source,
        )
    except ForkError as error:
        logger.info("draft %s: the drafting tool refused the wave: %s", draft.pk, error)
        raise in_words(error) from error
    LetterDraft.objects.filter(pk=draft.pk).update(
        fork_wave_id=wave["id"],
        fork_folder_id=wave.get("folder_id"),
        fork_master_document_id=wave["master_document_id"],
        fork_master_version_id=wave.get("master_version_id"),
        fork_master_version_number=1,
    )
    if not created:
        # The same key again: the fork kept the list it had; send the one the portal has now.
        on_draft_changed(draft.pk)
    return LetterDraft.objects.get(pk=draft.pk)


def is_fork_draft(draft: LetterDraft) -> bool:
    return draft.fork_wave_id is not None


def require_in_use_for(draft: LetterDraft) -> ForkConfig:
    cfg = config()
    if not cfg.in_use:
        raise InvalidTransition(
            "This letter is in the drafting tool, which is switched off in Settings → System. "
            "Switch it back on to reserve or file it."
        )
    return cfg


# ---- stage 3: the agency letters -----------------------------------------------------------


def merge_values(recipient: LetterRecipient, request: Request | None = None) -> dict[str, str]:
    """What the merge fills for this agency: the portal's own placeholders (filing.agency_values,
    dated as the request was reserved), with the per-agency overrides the fork accepts, and the
    reserved tracking number and request address (`TRACKING_NUMBER`, `REPLY_TO`)."""
    from portal.drafting.services.filing import placeholders_for

    when = request.created_at if request is not None else None
    values = {
        name: value
        for name, value in placeholders_for(recipient, recipient.agency, when=when).items()
        if _VALUE_NAME.fullmatch(name)
    }
    if request is not None:
        values["TRACKING_NUMBER"] = request.tracking_number
        if request.inbound_alias_email:
            values["REPLY_TO"] = request.inbound_alias_email
    return values


def require_complete(actor: Actor, draft: LetterDraft, recipient: LetterRecipient) -> None:
    """Before a reserve: the master filled for this agency in memory (nothing stored). Refused,
    naming them, while a placeholder would stand in its letter (the portal's rule, P10)."""
    cfg = require_in_use_for(draft)
    values = {**merge_values(recipient), **_RESERVED_STAND_INS}
    try:
        _, unfilled, _ = client(cfg).preview(
            person_for(actor), str(draft.fork_master_document_id), values, fmt="docx"
        )
    except ForkError as error:
        raise in_words(error) from error
    if unfilled:
        raise ServiceError(
            f"The letter to {recipient.agency.name} still has blanks nothing fills in: "
            f"{', '.join(unfilled)}. Complete the agency's details, or give this recipient its "
            "own value, and reserve again."
        )


@dataclass
class MergeOutcome:
    made: list[UUID] = field(default_factory=list)
    failed: dict[UUID, str] = field(default_factory=dict)


def make_letters(
    actor: Actor, draft: LetterDraft, recipients: Iterable[LetterRecipient]
) -> MergeOutcome:
    """The fork's merge for these reserved recipients (one document each, keyed by the recipient),
    their ids stored, and each request's words written from its letter. A refusal or an outage is
    reported per recipient, never raised: the reservations stand, and the letters can be made
    again (`POST …/fork/merge`)."""
    outcome = MergeOutcome()
    rows = [r for r in recipients if r.request_id is not None]
    if not rows:
        return outcome
    cfg = require_in_use_for(draft)
    requests = {r.pk: r for r in Request.objects.filter(pk__in=[row.request_id for row in rows])}
    documents = [
        {"key": str(row.pk), "values": merge_values(row, requests[row.request_id])} for row in rows
    ]
    person = person_for(actor)
    fork = client(cfg)
    try:
        answer = fork.merge(person, str(draft.fork_master_document_id), documents)
    except ForkError as error:
        reason = str(in_words(error))
        logger.warning("draft %s: the merge failed: %s", draft.pk, error)
        return MergeOutcome(failed={row.pk: reason for row in rows})
    by_key = {str(d.get("key")): d for d in answer.get("documents") or []}
    # Each letter's words lock its request: several at once go in request-id order (the one lock
    # order for more than one request, filing.py).
    for row in sorted(rows, key=lambda r: str(r.request_id)):
        entry = by_key.get(str(row.pk))
        if entry is None or entry.get("status") == "failed" or not entry.get("document_id"):
            error = (entry or {}).get("error") or {}
            why = error.get("detail") or "no answer"
            outcome.failed[row.pk] = f"The drafting tool could not make this letter: {why}"
            continue
        LetterRecipient.objects.filter(pk=row.pk).update(
            fork_document_id=entry["document_id"],
            fork_version_id=entry.get("version_id"),
            fork_version_number=entry.get("version_number"),
        )
        try:
            _write_words(actor, row.pk, fork, person, entry["document_id"], entry.get("version_id"))
        except ForkError as error:
            logger.warning("recipient %s: its words were not read: %s", row.pk, error)
        outcome.made.append(row.pk)
    return outcome


def _write_words(
    actor: Actor,
    recipient_id: UUID,
    fork: ForkClient,
    person: PortalPerson,
    document_id: str,
    version_id: str | None,
) -> None:
    """The reserved request's words from this version of its letter, when they differ (an audited
    edit, as `refresh_reserved` writes one), and the .docx's SHA-256 kept beside the recipient."""
    answer = fork.fetch_docx(person, str(document_id), str(version_id) if version_id else None)
    digest = hashlib.sha256(answer.body).hexdigest()
    recipient = LetterRecipient.objects.get(pk=recipient_id)
    if recipient.fork_text_sha256 == digest or recipient.request_id is None:
        return
    try:
        words = docx_text.extract(answer.body)
    except docx_text.NotALetter as exc:
        logger.warning("recipient %s: the letter could not be read (%s)", recipient_id, exc)
        return
    request = lock_request(recipient.request_id)
    if words and words != request.request_text:
        edits_service.edit_with_audit(
            actor, "requests", request.pk, "request_text", words, TEXT_NOTE
        )
    LetterRecipient.objects.filter(pk=recipient_id).update(fork_text_sha256=digest)


def letters_to_make(draft: LetterDraft) -> list[LetterRecipient]:
    """Reserved, not yet out, and without a letter in the drafting tool."""
    return list(
        LetterRecipient.objects.filter(
            draft_id=draft.pk,
            request_id__isnull=False,
            fork_document_id__isnull=True,
            status__in=(LetterRecipient.PENDING, LetterRecipient.HELD),
        ).order_by("created_at", "id")
    )


# ---- stage 4: filing -----------------------------------------------------------------------


def current_version(fork: ForkClient, person: PortalPerson, document_id: str) -> tuple[str, int]:
    listing = fork.versions(person, document_id)
    current = str(listing.get("current_version_id") or "")
    number = next(
        (v.get("version_number") for v in listing.get("versions") or [] if v.get("id") == current),
        None,
    )
    if not current:
        raise mike.ForkRefused(None, "no_version", "the letter has no current version")
    return current, int(number or 0)


def refresh_before_filing(
    actor: Actor, recipient: LetterRecipient, *, version_id: str | None = None
) -> LetterRecipient:
    """Before a fork letter is filed: the agency's letter must exist (made now if the merge did not
    run), and the request's words must be this version's (`version_id`, else the current one)."""
    draft = LetterDraft.objects.get(pk=recipient.draft_id)
    require_in_use_for(draft)
    if recipient.fork_document_id is None:
        made = make_letters(actor, draft, [recipient])
        if recipient.pk in made.failed:
            raise DraftingToolRefused(made.failed[recipient.pk])
        recipient = LetterRecipient.objects.get(pk=recipient.pk)
    fork = client()
    person = person_for(actor)
    try:
        if version_id is None:
            version_id, number = current_version(fork, person, str(recipient.fork_document_id))
            LetterRecipient.objects.filter(pk=recipient.pk).update(
                fork_version_id=version_id, fork_version_number=number
            )
        _write_words(actor, recipient.pk, fork, person, str(recipient.fork_document_id), version_id)
    except ForkError as error:
        raise in_words(error) from error
    return LetterRecipient.objects.get(pk=recipient.pk)


@dataclass(frozen=True)
class MailLetter:
    pdf: bytes
    version_id: str


def letter_for_mail(actor: Actor, recipient: LetterRecipient) -> MailLetter:
    """The PDF an email filing carries: the agency letter's current version, from the fork, as
    the person sending (its letter made first if the merge never ran). Raises `ForkError` (the
    mail job retries a transient one) or `ServiceError` (said on the message)."""
    draft = LetterDraft.objects.get(pk=recipient.draft_id)
    require_in_use_for(draft)
    if recipient.fork_document_id is None:
        made = make_letters(actor, draft, [recipient])
        if recipient.pk in made.failed:
            raise DraftingToolRefused(made.failed[recipient.pk])
        recipient = LetterRecipient.objects.get(pk=recipient.pk)
    fork = client()
    person = person_for(actor)
    version_id, _ = current_version(fork, person, str(recipient.fork_document_id))
    answer = fork.fetch_pdf(person, str(recipient.fork_document_id), version_id)
    return MailLetter(pdf=answer.body, version_id=version_id)


def fetch_file(
    actor_or_user: Any, document_id: UUID, fmt: str, version_id: str | None = None
) -> mike.ForkResponse:
    """A letter's PDF or .docx, as the person asking (the stage 4 and Files tab downloads)."""
    cfg = config()
    if not cfg.in_use:
        raise InvalidTransition("The drafting tool is switched off in Settings → System.")
    fork = client(cfg)
    person = person_for(actor_or_user)
    try:
        if fmt == "docx":
            return fork.fetch_docx(person, str(document_id), version_id)
        return fork.fetch_pdf(person, str(document_id), version_id)
    except ForkError as error:
        raise in_words(error) from error


# ---- the mirror's view: the Files tab and the Suggestions badge ------------------------------


@dataclass(frozen=True)
class DocumentState:
    revised_at: datetime | None = None
    open_suggestions: int = 0
    verification: dict[str, Any] | None = None


VERSION_EVENTS = ("document.created", "document.version_created", "document.version_replaced")


def document_states(document_ids: Iterable[UUID | None]) -> dict[UUID, DocumentState]:
    """For each document, from the webhooks received: when it last changed, how many review
    comments are open (created less resolved), and the newest citation check."""
    ids = {d for d in document_ids if d is not None}
    if not ids:
        return {}
    rows = ForkEvent.objects.filter(document_id__in=ids).order_by("occurred_at", "received_at")
    revised: dict[UUID, datetime | None] = {}
    opened: dict[UUID, int] = {}
    checked: dict[UUID, dict[str, Any]] = {}
    for row in rows.only("document_id", "event_type", "occurred_at", "received_at", "data"):
        doc = row.document_id
        assert doc is not None
        when = row.occurred_at or row.received_at
        if row.event_type in VERSION_EVENTS or row.event_type == "document.merged":
            revised[doc] = when
        elif row.event_type == "comment.created":
            opened[doc] = opened.get(doc, 0) + 1
        elif row.event_type in ("comment.resolved", "comment.deleted"):
            opened[doc] = opened.get(doc, 0) - 1
        elif row.event_type == "verification.completed":
            checked[doc] = {**(row.data or {}), "at": when.isoformat() if when else None}
    return {
        doc: DocumentState(
            revised_at=revised.get(doc),
            open_suggestions=max(0, opened.get(doc, 0)),
            verification=checked.get(doc),
        )
        for doc in ids
    }


# ---- the fork's webhooks (9.3) ---------------------------------------------------------------


class WebhookRefused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def receive_webhook(
    raw: bytes,
    *,
    event_header: str | None,
    delivery_header: str | None,
    timestamp: str | None,
    signature: str | None,
) -> dict[str, Any]:
    """Verify, record once, apply. Returns `{"status": "applied"|"duplicate"|"ignored", ...}`;
    raises `WebhookRefused` for anything not to be trusted (nothing is written then)."""
    cfg = config()
    if not cfg.secrets_set["webhook"]:
        raise WebhookRefused(503, "The portal has no secret for the drafting tool's webhooks.")
    if len(raw) > mike.WEBHOOK_MAX_BYTES:
        raise WebhookRefused(413, "Too large.")
    try:
        mike.verify_webhook(
            cfg.webhook_secret, timestamp=timestamp, signature=signature, raw_body=raw
        )
    except mike.WebhookRejected as rejected:
        logger.warning("drafting tool webhook refused: %s", rejected.reason)
        raise WebhookRefused(401, "Not signed by the drafting tool.") from rejected
    try:
        event = json.loads(raw)
    except ValueError as exc:
        raise WebhookRefused(400, "Not JSON.") from exc
    if not isinstance(event, dict):
        raise WebhookRefused(400, "Not an event.")
    event_id = event.get("id")
    event_type = event.get("type")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if not isinstance(event_id, str) or not 0 < len(event_id) <= 200:
        raise WebhookRefused(400, "No event id.")
    if not isinstance(event_type, str) or not event_type:
        raise WebhookRefused(400, "No event type.")
    if (event_header and event_header != event_type) or (
        delivery_header and delivery_header != event_id
    ):
        raise WebhookRefused(400, "The headers and the event disagree.")
    occurred = (
        parse_datetime(str(event.get("created_at") or "")) if event.get("created_at") else None
    )
    document_id = _uuid(data.get("document_id"))
    try:
        with transaction.atomic():
            ForkEvent.objects.create(
                id=event_id,
                event_type=event_type,
                document_id=document_id,
                occurred_at=occurred,
                received_at=timezone.now(),
                data=data,
            )
    except IntegrityError:
        # Delivered before (the fork retries with the same id; a replay inside the window is the
        # same id too): acknowledged, nothing applied twice.
        return {"status": "duplicate", "id": event_id}
    matched = _apply(event_type, data, document_id)
    return {"status": "applied" if matched is not None else "ignored", "matched": matched or 0}


def _newer(qs, number: Any):
    """Only move a stored version forward: events can arrive out of order across retries."""
    if not isinstance(number, int):
        return qs.none()
    return qs.filter(Q(fork_version_number__isnull=True) | Q(fork_version_number__lte=number))


def _apply(event_type: str, data: dict[str, Any], document_id: UUID | None) -> int | None:
    """Bring the mirror into line with one event. Returns how many rows it touched, or None for an
    event the portal only keeps (a comment, a citation check: read from the store)."""
    version_id = _uuid(data.get("version_id"))
    number = data.get("version_number")
    if event_type in VERSION_EVENTS and document_id is not None:
        if not isinstance(number, int) or isinstance(number, bool):
            return 0
        touched = _newer(
            LetterRecipient.objects.filter(fork_document_id=document_id), number
        ).update(fork_version_id=version_id, fork_version_number=number)
        touched += (
            LetterDraft.objects.filter(fork_master_document_id=document_id)
            .filter(
                Q(fork_master_version_number__isnull=True)
                | Q(fork_master_version_number__lte=number)
            )
            .update(fork_master_version_id=version_id, fork_master_version_number=number)
        )
        return touched
    if event_type == "document.merged" and document_id is not None:
        key = _uuid(data.get("key"))
        master = _uuid(data.get("master_document_id"))
        if key is None or master is None:
            return 0
        rows = LetterRecipient.objects.filter(pk=key, draft__fork_master_document_id=master).filter(
            Q(fork_document_id__isnull=True) | Q(fork_document_id=document_id)
        )
        return _newer(rows, number).update(
            fork_document_id=document_id, fork_version_id=version_id, fork_version_number=number
        )
    return None


# ---- the Templates pages' mirror (`sclu-templates/1` "template") -----------------------------


def mirror_template(
    actor: Actor, *, template_id: UUID, name: str, archived: bool
) -> LetterTemplate:
    """What the Templates frame said about a template it created or changed: the portal's row for
    it (so stage 1 can offer it), its name and whether it is archived. Nothing of its words."""
    if not config().in_use:
        raise InvalidTransition("The drafting tool is switched off in Settings → System.")
    name = name.strip()
    if not name or len(name) > 200:
        raise ServiceError("A template's name is 1 to 200 characters.")
    user_id = require_human(actor, "Recording a template")
    now = timezone.now()
    row = LetterTemplate.objects.filter(fork_template_id=template_id).first()
    archived_at = (row.archived_at or now) if (archived and row) else (now if archived else None)
    if row is None:
        return LetterTemplate.objects.create(
            name=name,
            fork_template_id=template_id,
            archived_at=archived_at,
            created_by_user_id=user_id,
        )
    LetterTemplate.objects.filter(pk=row.pk).update(
        name=name, archived_at=archived_at, updated_at=now
    )
    return LetterTemplate.objects.get(pk=row.pk)
