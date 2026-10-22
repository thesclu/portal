"""The untouched original. A source belongs to no request -- events do.

One inbound letter answering three requests is one row here and three events. That is why
`register_source` is idempotent on `external_ref`: a re-delivered webhook must find the row it
already made, not manufacture a second copy of the same evidence.

Around the source sits what the Inbox does with a message before, or instead of, attaching it:
flag it (and say why), hand it to someone, mark it reviewed or unreviewed, write notes on it, say
it was handled without attaching it to anything, and mark which of its files are the records.
"Add a document" is the same thing from the other side: a person brings a file in and it becomes
one source, its attachments and one event on a request.

A source is locked (`FOR UPDATE`) by anything that changes its review state, so two people acting
on one message cannot leave an audit trail that contradicts itself. The lock order across the
ledger is the request, then the source, and nothing here takes a request lock after a source lock.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone

from portal.core.services import activity as core_activity
from portal.core.services import notifications as core_notifications
from portal.core.services.settings import get_setting
from portal.records import storage
from portal.records.actors import Actor
from portal.records.models import (
    AgencyContact,
    Attachment,
    AttachmentRole,
    Event,
    Source,
    SourceNote,
    SourceType,
    writable,
)
from portal.records.services import access as access_service
from portal.records.services import audit as audit_service
from portal.records.services import events as events_service
from portal.records.services.common import (
    InvalidTransition,
    NotFound,
    ServiceError,
    lock_request,
    require_assignable,
    require_human,
)

# The Inbox's vocabularies, as Python tuples rather than CHECKs (BUILD_PLAN rule 5): why a message
# was flagged (`sources.flag_reason`, written by `flag_source`) and how a message that was never
# attached to a request was dealt with (`sources.resolution`, written by
# `resolve_without_attaching`). GET /definitions serves both to the Inbox's pickers.
#
# The flag reasons, each said in plain words on the Inbox row (DECISIONS 30i): something we sent
# bounced; mail not sent to any request's address; mail on one request that names others; a reply
# to mail we sent that went to another address than the request's own (`inbound.match`); a
# delivery too large to keep whole, kept without its files (`inbound.postmark`); the assistant
# thinks it is misfiled (P19d); anything else.
FLAG_REASONS = (
    "delivery_failed",
    "no_matching_request",
    "may_cover_others",
    "reply_to_ignored",
    "attachment_too_large",
    "assistant",
    "other",
)
RESOLUTIONS = ("duplicate", "automated_reply", "suspicious", "other")

# "Log communication" is not a set of event types, it is `correspondence` on a channel. Of its four
# menu items two carry a document, and this is the channel each arrived on: a letter or scan is
# `mail` (a `scanned_mail` source), a portal update is `portal` (a `portal_capture` source), a file
# of email is `email`. The other two, a phone call and a meeting or visit, have no document and are
# `POST /requests/{id}/events` with channel `call` and `in_person`. `other` has no entry on purpose:
# a document of unknown kind must say which channel it came by, not have one guessed for it.
DOCUMENT_CHANNELS = {
    SourceType.SCANNED_MAIL.value: "mail",
    SourceType.PORTAL_CAPTURE.value: "portal",
    SourceType.IMPORTED_EML.value: "email",
    SourceType.POSTMARK_EMAIL.value: "email",
}


@dataclass
class IncomingFile:
    """One file arriving by hand. `chunks` is read once, as it is stored."""

    name: str
    chunks: Iterable[bytes]
    content_type: str | None = None


@dataclass(frozen=True)
class AddedDocument:
    source: Source
    attachments: list[Attachment]
    event: Event


def _get(source_id: UUID) -> Source:
    source = Source.objects.filter(pk=source_id).first()
    if source is None:
        raise NotFound(f"No source {source_id}.")
    return source


def _locked(source_id: UUID) -> Source:
    """The source under `FOR UPDATE`, so review, assignment and resolution cannot interleave."""
    source = Source.objects.select_for_update().filter(pk=source_id).first()
    if source is None:
        raise NotFound(f"No source {source_id}.")
    return source


def _need(text: str | None, what: str) -> str:
    if text is None or not text.strip():
        raise ServiceError(f"{what} needs some text.")
    return text.strip()


def _has_live_event(source_id: UUID) -> bool:
    return Event.objects.filter(source_id=source_id, voided_at__isnull=True).exists()


@transaction.atomic
def register_source(
    actor: Actor,
    *,
    source_type: str,
    raw_storage_url: str,
    external_ref: str | None = None,
    message_id: str | None = None,
    raw_metadata: dict[str, Any] | None = None,
    flagged: bool = False,
    flag_note: str | None = None,
    captured_at: datetime | None = None,
    source_id: UUID | None = None,
) -> Source:
    """`source_id` lets a caller that has already stored the raw original under an id (inbound
    mail stores first, so the path can carry it) make the row with that same id."""
    if external_ref is not None:
        existing = Source.objects.filter(external_ref=external_ref).first()
        if existing is not None:
            return existing
    try:
        with transaction.atomic(), writable():
            return Source.objects.create(
                id=source_id or uuid4(),
                source_type=source_type,
                external_ref=external_ref,
                message_id=message_id,
                raw_metadata=raw_metadata,
                raw_storage_url=raw_storage_url,
                flagged_for_review=flagged,
                flag_note=flag_note,
                captured_at=captured_at or timezone.now(),
            )
    except IntegrityError:
        # Lost the race: another caller inserted the same external_ref between our check above
        # and our insert. The row it made is what a re-delivered webhook must find -- return it
        # unchanged rather than raising the loser's raw IntegrityError.
        if external_ref is None:
            raise
        existing = Source.objects.filter(external_ref=external_ref).first()
        if existing is None:
            raise
        return existing


@transaction.atomic
def add_attachment(
    actor: Actor,
    source_id: UUID,
    *,
    filename: str,
    storage_url: str,
    mime_type: str | None = None,
    size_bytes: int | None = None,
    role: str = "attachment",
    checksum_sha256: str | None = None,
    page_count: int | None = None,
) -> Attachment:
    """`page_count` is counted at ingest and is null for a file that cannot be counted."""
    _get(source_id)
    with writable():
        return Attachment.objects.create(
            source_id=source_id,
            filename=filename,
            storage_url=storage_url,
            mime_type=mime_type,
            size_bytes=size_bytes,
            role=role,
            checksum_sha256=checksum_sha256,
            page_count=page_count,
        )


@transaction.atomic
def flag_source(actor: Actor, source_id: UUID, note: str, reason: str = "other") -> Source:
    """Put a message in front of a person, and say why (`reason`, one of `FLAG_REASONS`).

    Any actor may flag: inbound mail flags what it cannot place, a person flags what looks wrong.
    A reviewed message is refused rather than silently reopened. Review clears the flag, so
    "reviewed and flagged" is a state the ledger never holds, and the way back in is
    `mark_source_unreviewed`, which leaves who had reviewed it on the record. Flagging a message
    that is already flagged replaces its reason and note.
    """
    if reason not in FLAG_REASONS:
        raise ServiceError(f"Unknown flag reason {reason!r}; expected one of {FLAG_REASONS}.")
    text = _need(note, "A flag")
    source = _locked(source_id)
    if source.reviewed_at is not None:
        raise InvalidTransition(
            f"Source {source_id} is reviewed; mark it unreviewed before flagging it."
        )
    Source.objects.filter(pk=source_id).update(
        flagged_for_review=True, flag_note=text, flag_reason=reason
    )
    _record_flagged(actor, source_id, reason)
    core_notifications.notify_many(
        core_notifications.staff_who_want("flagged"),
        "flagged",
        f"A message was flagged: {text[:80]}",
        link=f"/inbox?bucket=flagged&message={source_id}",
        subject_type="source",
        subject_id=source_id,
        actor_user_id=actor.user_id,
    )
    return _get(source_id)


@transaction.atomic
def mark_source_reviewed(actor: Actor, source_id: UUID) -> Source:
    user_id = require_human(actor, "Marking a source reviewed")
    _get(source_id)
    Source.objects.filter(pk=source_id).update(
        flagged_for_review=False, reviewed_at=timezone.now(), reviewed_by_id=user_id
    )
    return _get(source_id)


@transaction.atomic
def mark_source_unreviewed(actor: Actor, source_id: UUID) -> Source:
    """Take a message back out of "handled". Human only; a message that is not reviewed is left as
    it is.

    The review and any resolution are about to be cleared and nothing else holds them, so each
    gets an audit line carrying its old value first (the way `reopen_task` keeps who completed a
    task). The message then goes back where it was waiting. One with an event on a request is
    simply unreviewed, which puts it in the Inbox's New; one with none (it was resolved without
    attaching, or its event was voided) has nowhere else to be seen, so it goes back to Flagged,
    keeping its old reason and note if it had them.
    """
    require_human(actor, "Marking a source unreviewed")
    source = _locked(source_id)
    if source.reviewed_at is None:
        return source
    note = "Marked unreviewed."
    for field, old in (
        ("reviewed_at", source.reviewed_at),
        ("reviewed_by_user_id", source.reviewed_by_id),
        ("resolution", source.resolution),
        ("resolution_note", source.resolution_note),
    ):
        if old is not None:
            audit_service.record_audit(
                actor,
                entity_type="sources",
                entity_id=source_id,
                field=field,
                old_value=old,
                new_value=None,
                note=note,
            )
    changes: dict[str, Any] = {
        "reviewed_at": None,
        "reviewed_by_id": None,
        "resolution": None,
        "resolution_note": None,
    }
    if not _has_live_event(source_id):
        changes.update(
            flagged_for_review=True,
            flag_reason=source.flag_reason or "other",
            flag_note=source.flag_note or "Marked unreviewed; it is not attached to a request.",
        )
    Source.objects.filter(pk=source_id).update(**changes)
    if changes.get("flagged_for_review"):
        _record_flagged(actor, source_id, changes["flag_reason"])
    return _get(source_id)


def _record_flagged(actor: Actor, source_id: UUID, reason: str) -> None:
    """The activity line that says who moved a message to Flagged and when (P23b; the Inbox's
    "Maya just moved this to Flagged", 140). A person or a named process (inbound mail, a bounce,
    the assistant). The summary names the reason only, never the message's words or where it
    landed; the request it is on is kept as `request_id`, so the Activity page's visibility rule
    decides who is told which request."""
    landed = (
        Event.objects.filter(source_id=source_id, voided_at__isnull=True)
        .order_by("occurred_at", "id")
        .values_list("request_id", flat=True)
        .first()
    )
    core_activity.record(
        actor,
        "message_flagged",
        f"Moved a message to Flagged ({reason.replace('_', ' ')})",
        subject_type="source",
        subject_id=source_id,
        request_id=landed,
        data={"reason": reason},
    )


def flagged_by(source_ids: Iterable[UUID]) -> dict[UUID, tuple[str, datetime]]:
    """Who last moved each of these messages to Flagged, and when: the newest `message_flagged`
    activity line per message, the person's name or the process's. A message flagged before the
    line was written (P23b) has none and is absent."""
    from portal.core.models import Activity

    ids = list(source_ids)
    if not ids:
        return {}
    rows = (
        Activity.objects.filter(action="message_flagged", subject_type="source", subject_id__in=ids)
        .select_related("actor")
        .order_by("subject_id", "-at")
        .distinct("subject_id")
    )
    return {
        row.subject_id: (row.actor.name if row.actor_id else _process_name(row.system), row.at)
        for row in rows
        if row.subject_id is not None
    }


# How a named process reads where a person's name would (140's "Maya just moved this").
_PROCESS_NAMES = {
    "postmark_webhook": "Incoming mail",
    "postmark_delivery": "A delivery report",
    "mail_sender": "Outgoing mail",
    "assistant": "The assistant",
}


def _process_name(system: str) -> str:
    return _PROCESS_NAMES.get(system, "The portal")


@transaction.atomic
def assign_source(actor: Actor, source_id: UUID, user_id: UUID | None) -> Source:
    """Hand a message to a person, or to nobody (`None`). Human only; audited.

    Not the same thing as `POST /sources/{id}/assign`, which attaches a message to a request
    (`assignment.assign_source_to_request`). This says who is dealing with it.
    """
    require_human(actor, "Assigning a source")
    source = _locked(source_id)
    person = require_assignable(user_id) if user_id is not None else None
    if source.assigned_to_id == user_id:
        return source
    access_service.require_assignee_can_view_source(source_id, user_id)
    Source.objects.filter(pk=source_id).update(assigned_to_id=user_id)
    if user_id is not None:
        core_notifications.notify(
            user_id,
            "assigned",
            "A message in the Inbox was assigned to you",
            link=f"/inbox?bucket=all&message={source_id}",
            subject_type="source",
            subject_id=source_id,
            actor_user_id=actor.user_id,
        )
    audit_service.record_audit(
        actor,
        entity_type="sources",
        entity_id=source_id,
        field="assigned_to_user_id",
        old_value=source.assigned_to_id,
        new_value=user_id,
        note=f"Assigned to {person.name}." if person is not None else "Unassigned.",
    )
    return _get(source_id)


@transaction.atomic
def resolve_without_attaching(
    actor: Actor, source_id: UUID, resolution: str, note: str | None
) -> Source:
    """ "Mark handled without attaching": a duplicate, an automated reply, something suspicious.

    Human only. Refused when the message has a non-voided event, because then it IS attached to a
    request and was handled by attaching it; to do this to such a message, void that event first.
    It also marks the message reviewed (which clears its flag), and records who decided and why.
    A note is required for `other` and optional otherwise. `mark_source_unreviewed` undoes it.

    The source is locked and its events are read under that lock; `events.record_event` takes the
    same lock for a source it is given and refuses a resolved one, so a message cannot be attached
    and resolved at once.
    """
    user_id = require_human(actor, "Resolving a source without attaching it")
    if resolution not in RESOLUTIONS:
        raise ServiceError(f"Unknown resolution {resolution!r}; expected one of {RESOLUTIONS}.")
    text = (note or "").strip() or None
    if resolution == "other" and text is None:
        raise ServiceError("Handling a message for 'other' reasons needs a note saying why.")
    source = _locked(source_id)
    if source.resolution is not None:
        raise InvalidTransition(
            f"Source {source_id} was already handled ({source.resolution}); "
            "mark it unreviewed to change that."
        )
    if _has_live_event(source_id):
        raise InvalidTransition(
            f"Source {source_id} is attached to a request, so it cannot be handled without "
            "attaching. Void its event first if it was attached by mistake."
        )
    Source.objects.filter(pk=source_id).update(
        resolution=resolution,
        resolution_note=text,
        flagged_for_review=False,
        reviewed_at=timezone.now(),
        reviewed_by_id=user_id,
    )
    audit_service.record_audit(
        actor,
        entity_type="sources",
        entity_id=source_id,
        field="resolution",
        old_value=None,
        new_value=resolution,
        note=text or f"Handled without attaching: {resolution}.",
    )
    return _get(source_id)


@transaction.atomic
def add_source_note(actor: Actor, source_id: UUID, body: str) -> SourceNote:
    """A note on a message, before or after it belongs to any request. Human only: a note has an
    author, and the table will not take one without."""
    user_id = require_human(actor, "Writing a note")
    text = _need(body, "A note")
    _get(source_id)
    with writable():
        note = SourceNote.objects.create(source_id=source_id, author_id=user_id, body=text)
    for person in core_notifications.mentioned(text, exclude=user_id):
        core_notifications.notify(
            person.pk,
            "mention",
            f"{_name_of(user_id)} mentioned you in a note",
            link=f"/inbox?bucket=all&message={source_id}",
            subject_type="source_note",
            subject_id=note.pk,
            actor_user_id=user_id,
        )
    return note


@transaction.atomic
def void_source_note(actor: Actor, note_id: UUID, reason: str) -> SourceNote:
    """A mistaken note is voided, never deleted or rewritten. Any staff member may void one; who did
    it is the audit line, since the table records only when and why."""
    require_human(actor, "Voiding a note")
    why = _need(reason, "Voiding a note")
    note = SourceNote.objects.select_for_update().filter(pk=note_id).first()
    if note is None:
        raise NotFound(f"No note {note_id}.")
    if note.voided_at is not None:
        raise InvalidTransition(f"Note {note_id} is already voided.")
    voided_at = timezone.now()
    SourceNote.objects.filter(pk=note_id).update(voided_at=voided_at, void_reason=why)
    audit_service.record_audit(
        actor,
        entity_type="source_notes",
        entity_id=note_id,
        field="voided_at",
        old_value=None,
        new_value=voided_at.isoformat(),
        note=why,
    )
    return SourceNote.objects.get(pk=note_id)


def list_source_notes(source_id: UUID, *, include_voided: bool = True) -> list[SourceNote]:
    """One message's notes, in the order they were written."""
    _get(source_id)
    rows = SourceNote.objects.filter(source_id=source_id)
    if not include_voided:
        rows = rows.filter(voided_at__isnull=True)
    return list(rows.order_by("created_at"))


def _write_record_marks(
    actor: Actor,
    source_id: UUID,
    wanted: set[UUID],
    note: str,
    *,
    keep_existing: bool,
) -> list[Attachment]:
    """The one writer behind both ways of marking files as records. It takes the source's lock and
    reads the current roles under it, so a decision made from what is already marked (keep it, or
    drop it) cannot be made from a stale read. `keep_existing` says whether a file that is a record
    and is not named stays one."""
    require_human(actor, "Marking files as records")
    _locked(source_id)
    attachments = list(Attachment.objects.filter(source_id=source_id).order_by("created_at", "id"))
    stranger = sorted(str(pk) for pk in wanted - {a.pk for a in attachments})
    if stranger:
        raise ServiceError(f"Not attachments of this message: {', '.join(stranger)}.")
    for attachment in attachments:
        if attachment.pk in wanted:
            role = AttachmentRole.PRODUCED_RECORD.value
        elif attachment.role == AttachmentRole.PRODUCED_RECORD and not keep_existing:
            role = AttachmentRole.ATTACHMENT.value
        else:
            role = attachment.role
        if role == attachment.role:
            continue
        Attachment.objects.filter(pk=attachment.pk).update(role=role)
        audit_service.record_audit(
            actor,
            entity_type="attachments",
            entity_id=attachment.pk,
            field="role",
            old_value=attachment.role,
            new_value=role,
            note=note,
        )
    return list(Attachment.objects.filter(source_id=source_id).order_by("created_at", "id"))


@transaction.atomic
def set_attachment_roles(
    actor: Actor,
    source_id: UUID,
    record_attachment_ids: Iterable[UUID],
    note: str = "Marked files as records.",
) -> list[Attachment]:
    """ "Mark files as records": exactly these attachments of the source are the produced records.

    The ids are the whole answer, not a list to add to: a file that was a record and is not named
    goes back to `attachment`. A `supporting_document` that is not named is left as it is. Ids that
    are not attachments of THIS source are refused, not skipped. Human only; one audit line per
    file whose role changes. Returns every attachment of the source, as it now stands.

    This is the ONE door that replaces the set (`POST /sources/{id}/records`). A source can answer
    several requests, and a file's role belongs to the source, so anything that marks files on
    behalf of ONE request must use `mark_attachments_as_records` instead.
    """
    return _write_record_marks(
        actor, source_id, set(record_attachment_ids), note, keep_existing=False
    )


@transaction.atomic
def mark_attachments_as_records(
    actor: Actor,
    source_id: UUID,
    record_attachment_ids: Iterable[UUID],
    note: str = "Marked files as records.",
) -> list[Attachment]:
    """Mark these attachments of the source as produced records, IN ADDITION to the ones already
    marked. Nothing is ever unmarked here: a file that is a record and is not named stays one, so
    recording one request's production cannot undo what was decided for another request that the
    same message answers. A file that is already a record changes nothing and writes no audit line.
    Same refusals, audit and return value as `set_attachment_roles`; the existing marks are read
    under the source's lock, not before it.
    """
    return _write_record_marks(
        actor, source_id, set(record_attachment_ids), note, keep_existing=True
    )


@transaction.atomic
def add_document(
    actor: Actor,
    request_id: UUID,
    *,
    source_type: str,
    received_at: datetime,
    files: Sequence[IncomingFile],
    display_body: str = "",
    channel: str | None = None,
    contact_id: UUID | None = None,
) -> AddedDocument:
    """ "Add a document": a person brings a scan, a portal download or an email file in.

    One source, its attachments and one inbound `correspondence` event on the request, in one
    transaction. The channel comes from what the document is (`DOCUMENT_CHANNELS`), or from
    `channel` when it is `other`. The person who added it has by definition looked at it, so the
    source is marked reviewed; saying what it MEANS is `classify`'s job, afterwards.

    The untouched original of a hand-added document is a small manifest of what was uploaded
    (`store_raw`, kind `upload_manifest`): there is no email to keep, and `raw_storage_url` cannot
    be empty. File names are sanitised once, here, and the sanitised name is the only one kept.
    Size and count limits are settings. Files are written to storage before the rows, so on any
    failure the ones already stored are removed again; a file left behind by a crash between the
    two costs space and nothing else.

    `contact_id` (P23b, 102 "From") is who at the agency sent it: one of the request's agency's
    contacts (or of an agency merged into it), kept as the event's `actor_agency_contact_id`.
    """
    user_id = require_human(actor, "Adding a document")
    if source_type not in SourceType.values:
        raise ServiceError(
            f"Unknown source type {source_type!r}; expected one of {SourceType.values}."
        )
    if received_at.tzinfo is None:
        raise ServiceError("received_at needs a time zone; an instant without one is a guess.")
    channel = channel or DOCUMENT_CHANNELS.get(source_type)
    if channel is None:
        raise ServiceError(
            f"A document of type {source_type!r} needs a channel saying how it arrived."
        )
    body = (display_body or "").strip()
    incoming = list(files)
    if not incoming and not body:
        raise ServiceError("Add at least one file, or some text saying what the document is.")
    max_files = int(get_setting("records.upload_max_files"))
    max_bytes = int(get_setting("records.upload_max_file_bytes"))
    if len(incoming) > max_files:
        raise ServiceError(f"Too many files in one submission; the limit is {max_files}.")

    request = lock_request(request_id)
    if contact_id is not None:
        from portal.records.services.directory import same_agency_ids

        if not AgencyContact.objects.filter(
            pk=contact_id, agency_id__in=same_agency_ids(request.agency_id)
        ).exists():
            raise ServiceError("That contact is not one of this request's agency.")
    now = timezone.now()
    source_id = uuid4()
    stored_names: list[str] = []
    try:
        kept: list[tuple[IncomingFile, storage.StoredFile, str]] = []
        for upload in incoming:
            name = storage.safe_filename(upload.name)
            try:
                stored = storage.store_attachment(
                    source_id, name, upload.chunks, max_bytes=max_bytes
                )
            except storage.UploadTooLarge:
                raise ServiceError(f"{name} is larger than the size limit for one file.") from None
            stored_names.append(stored.storage_name)
            kept.append((upload, stored, name))
        manifest = {
            "kind": "uploaded_document",
            "source_type": source_type,
            "received_at": received_at.isoformat(),
            "added_by_user_id": str(user_id),
            "display_body": body,
            "files": [
                {"filename": name, "sha256": stored.sha256, "size_bytes": stored.size}
                for _, stored, name in kept
            ],
        }
        raw_name = storage.store_raw(
            source_id, "upload_manifest", json.dumps(manifest, indent=2).encode(), when=now
        )
        stored_names.append(raw_name)
        with writable():
            source = Source.objects.create(
                id=source_id,
                source_type=source_type,
                raw_storage_url=raw_name,
                captured_at=now,
                reviewed_at=now,
                reviewed_by_id=user_id,
            )
            attachments = [
                Attachment.objects.create(
                    source_id=source_id,
                    filename=name,
                    storage_url=stored.storage_name,
                    mime_type=storage.guess_mime(name, upload.content_type),
                    size_bytes=stored.size,
                    checksum_sha256=stored.sha256,
                )
                for upload, stored, name in kept
            ]
        event = events_service.record_event(
            actor,
            request_id,
            event_type="correspondence",
            channel=channel,
            direction="inbound",
            occurred_at=received_at,
            display_body=body or f"Added: {', '.join(a.filename for a in attachments)}",
            source_id=source_id,
            actor_agency_contact_id=contact_id,
        )
    except BaseException:
        storage.delete_stored(stored_names)
        raise
    return AddedDocument(source=source, attachments=attachments, event=event)


def _name_of(user_id: UUID) -> str:
    from portal.core.models import User

    row = User.objects.filter(pk=user_id).first()
    return row.name if row else "Someone"
