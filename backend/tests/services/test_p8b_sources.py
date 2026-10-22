"""The services around a message: flag, assign, review and unreview, resolve without attaching,
notes, which files are records, and adding a document by hand."""

import json
from datetime import datetime

import pytest
from portal.core.services.identity import create_user
from portal.records import storage
from portal.records.actors import Actor
from portal.records.models import (
    Attachment,
    AuditLog,
    Event,
    Source,
    SourceNote,
    StatusChange,
)
from portal.records.services import events as events_service
from portal.records.services import sources as sources_service
from portal.records.services.common import (
    InvalidTransition,
    NotAllowed,
    NotFound,
    ServiceError,
)

from tests.p8b_helpers import RECEIVED_AT, add_file, attach, make_source

pytestmark = pytest.mark.usefixtures("as_each_role")


def _audit(entity_id, field=None):
    rows = AuditLog.objects.filter(entity_id=entity_id)
    return list(rows.filter(field_name=field) if field else rows)


# ---- flag_source -------------------------------------------------------------------------------


def test_flag_source_records_the_reason_and_the_note(staff_actor) -> None:
    source = make_source(staff_actor)
    flagged = sources_service.flag_source(
        staff_actor, source.pk, "Nothing on it names a request.", "no_matching_request"
    )
    assert flagged.flagged_for_review is True
    assert (flagged.flag_reason, flagged.flag_note) == (
        "no_matching_request",
        "Nothing on it names a request.",
    )


def test_flag_source_reason_defaults_to_other_and_a_system_actor_may_flag(system_actor) -> None:
    source = make_source(system_actor)
    flagged = sources_service.flag_source(system_actor, source.pk, "Could not place it.")
    assert flagged.flag_reason == "other" and flagged.flagged_for_review is True


def test_flagging_again_replaces_the_reason_and_note(staff_actor) -> None:
    source = make_source(staff_actor, flag=("other", "first"))
    again = sources_service.flag_source(staff_actor, source.pk, "second", "may_cover_others")
    assert (again.flag_reason, again.flag_note) == ("may_cover_others", "second")


@pytest.mark.parametrize("reason", ["nonsense", "", "OTHER"])
def test_flag_source_refuses_a_reason_that_is_not_in_the_list(staff_actor, reason) -> None:
    source = make_source(staff_actor)
    with pytest.raises(ServiceError, match="Unknown flag reason"):
        sources_service.flag_source(staff_actor, source.pk, "why", reason)
    assert Source.objects.get(pk=source.pk).flagged_for_review is False


def test_flag_source_needs_a_note(staff_actor) -> None:
    source = make_source(staff_actor)
    with pytest.raises(ServiceError, match="needs some text"):
        sources_service.flag_source(staff_actor, source.pk, "   ", "other")


def test_flag_source_unknown_source_is_not_found(staff_actor) -> None:
    import uuid

    with pytest.raises(NotFound):
        sources_service.flag_source(staff_actor, uuid.uuid4(), "why", "other")


def test_a_reviewed_source_cannot_be_flagged_until_it_is_unreviewed(staff_actor, reserved) -> None:
    """Review clears the flag, so 'reviewed and flagged' is a state the ledger never holds."""
    source = make_source(staff_actor)
    attach(staff_actor, reserved, source)
    sources_service.mark_source_reviewed(staff_actor, source.pk)
    with pytest.raises(InvalidTransition, match="unreviewed"):
        sources_service.flag_source(staff_actor, source.pk, "look again", "other")
    sources_service.mark_source_unreviewed(staff_actor, source.pk)
    assert sources_service.flag_source(
        staff_actor, source.pk, "look again", "other"
    ).flagged_for_review


# ---- assign_source -----------------------------------------------------------------------------


def test_assign_source_hands_a_message_over_and_audits_it(
    staff_actor, staff_user, colleague_user
) -> None:
    source = make_source(staff_actor)
    assigned = sources_service.assign_source(staff_actor, source.pk, colleague_user.pk)
    assert assigned.assigned_to_id == colleague_user.pk
    (line,) = _audit(source.pk, "sources.assigned_to_user_id")
    assert line.old_value is None and line.new_value == str(colleague_user.pk)
    assert line.edited_by_id == staff_user.pk and "Colleague Person" in line.note

    again = sources_service.assign_source(staff_actor, source.pk, colleague_user.pk)
    assert again.assigned_to_id == colleague_user.pk
    assert len(_audit(source.pk, "sources.assigned_to_user_id")) == 1

    cleared = sources_service.assign_source(staff_actor, source.pk, None)
    assert cleared.assigned_to_id is None
    last = _audit(source.pk, "sources.assigned_to_user_id")[0]
    assert last.new_value is None and last.note == "Unassigned."


def test_assign_source_is_human_only(system_actor, staff_actor, colleague_user) -> None:
    source = make_source(staff_actor)
    with pytest.raises(NotAllowed):
        sources_service.assign_source(system_actor, source.pk, colleague_user.pk)
    assert Source.objects.get(pk=source.pk).assigned_to_id is None


def test_assign_source_refuses_a_requester_and_an_unknown_person(staff_actor) -> None:
    import uuid

    source = make_source(staff_actor)
    requester = create_user(
        email="req-source@example.org",
        name="A Requester",
        records_role="requester",
    )
    with pytest.raises(ServiceError, match="requester"):
        sources_service.assign_source(staff_actor, source.pk, requester.pk)
    with pytest.raises(NotFound):
        sources_service.assign_source(staff_actor, source.pk, uuid.uuid4())
    assert Source.objects.get(pk=source.pk).assigned_to_id is None


# ---- review, unreview --------------------------------------------------------------------------


def test_unreview_clears_the_review_but_keeps_who_had_done_it_in_the_audit_log(
    staff_actor, staff_user, reserved
) -> None:
    source = make_source(staff_actor)
    attach(staff_actor, reserved, source)
    reviewed = sources_service.mark_source_reviewed(staff_actor, source.pk)
    assert reviewed.reviewed_at is not None

    back = sources_service.mark_source_unreviewed(staff_actor, source.pk)
    assert back.reviewed_at is None and back.reviewed_by_id is None
    # It has an event on a request, so it goes back to New, not to Flagged.
    assert back.flagged_for_review is False
    by_field = {a.field_name: a for a in _audit(source.pk)}
    assert set(by_field) == {"sources.reviewed_at", "sources.reviewed_by_user_id"}
    assert by_field["sources.reviewed_by_user_id"].old_value == str(staff_user.pk)
    assert by_field["sources.reviewed_by_user_id"].new_value is None
    assert by_field["sources.reviewed_at"].edited_by_id == staff_user.pk


def test_unreviewing_a_message_that_is_not_reviewed_changes_nothing(staff_actor) -> None:
    source = make_source(staff_actor)
    same = sources_service.mark_source_unreviewed(staff_actor, source.pk)
    assert same.reviewed_at is None and _audit(source.pk) == []


def test_unreview_is_human_only(staff_actor, system_actor, reserved) -> None:
    source = make_source(staff_actor)
    attach(staff_actor, reserved, source)
    sources_service.mark_source_reviewed(staff_actor, source.pk)
    with pytest.raises(NotAllowed):
        sources_service.mark_source_unreviewed(system_actor, source.pk)
    assert Source.objects.get(pk=source.pk).reviewed_at is not None


def test_a_reviewed_message_with_no_live_event_goes_back_to_flagged(staff_actor, reserved) -> None:
    """An attached message whose only event was voided has nowhere else to be seen."""
    source = make_source(staff_actor, flag=("no_matching_request", "unplaced"))
    event = attach(staff_actor, reserved, source)
    sources_service.mark_source_reviewed(staff_actor, source.pk)
    events_service.void_event(staff_actor, event.pk, "attached to the wrong request")

    back = sources_service.mark_source_unreviewed(staff_actor, source.pk)
    assert back.flagged_for_review is True
    assert (back.flag_reason, back.flag_note) == ("no_matching_request", "unplaced")


# ---- resolve_without_attaching -----------------------------------------------------------------


def test_resolving_without_attaching_records_it_and_marks_the_message_reviewed(
    staff_actor, staff_user
) -> None:
    source = make_source(staff_actor, flag=("other", "odd one"))
    done = sources_service.resolve_without_attaching(
        staff_actor, source.pk, "duplicate", "Same PDF as the earlier production."
    )
    assert done.resolution == "duplicate"
    assert done.resolution_note == "Same PDF as the earlier production."
    assert done.flagged_for_review is False
    assert done.reviewed_at is not None and done.reviewed_by_id == staff_user.pk
    (line,) = _audit(source.pk, "sources.resolution")
    assert line.new_value == "duplicate" and line.edited_by_id == staff_user.pk


@pytest.mark.parametrize("resolution", ["duplicate", "automated_reply", "suspicious"])
def test_the_note_is_optional_except_for_other(staff_actor, resolution) -> None:
    source = make_source(staff_actor)
    done = sources_service.resolve_without_attaching(staff_actor, source.pk, resolution, None)
    assert done.resolution == resolution and done.resolution_note is None
    (line,) = _audit(source.pk, "sources.resolution")
    assert resolution in line.note


def test_other_needs_a_note_and_an_unknown_resolution_is_refused(staff_actor) -> None:
    source = make_source(staff_actor)
    with pytest.raises(ServiceError, match="needs a note"):
        sources_service.resolve_without_attaching(staff_actor, source.pk, "other", "  ")
    with pytest.raises(ServiceError, match="Unknown resolution"):
        sources_service.resolve_without_attaching(staff_actor, source.pk, "spam", "note")
    assert Source.objects.get(pk=source.pk).resolution is None


def test_resolving_is_human_only(staff_actor, system_actor) -> None:
    source = make_source(staff_actor)
    with pytest.raises(NotAllowed):
        sources_service.resolve_without_attaching(system_actor, source.pk, "duplicate", "n")
    assert Source.objects.get(pk=source.pk).resolution is None


def test_resolve_without_attaching_refuses_a_source_with_a_non_voided_event(
    staff_actor, reserved
) -> None:
    source = make_source(staff_actor, flag=("other", "x"))
    attach(staff_actor, reserved, source)
    with pytest.raises(InvalidTransition, match="attached to a request"):
        sources_service.resolve_without_attaching(staff_actor, source.pk, "duplicate", "n")
    refreshed = Source.objects.get(pk=source.pk)
    assert refreshed.resolution is None and refreshed.reviewed_at is None


def test_a_source_whose_only_event_was_voided_can_be_resolved(staff_actor, reserved) -> None:
    source = make_source(staff_actor)
    event = attach(staff_actor, reserved, source)
    events_service.void_event(staff_actor, event.pk, "wrong request")
    done = sources_service.resolve_without_attaching(staff_actor, source.pk, "suspicious", "n")
    assert done.resolution == "suspicious"


def test_a_resolved_source_cannot_be_resolved_twice_or_attached_until_unreviewed(
    staff_actor, reserved
) -> None:
    source = make_source(staff_actor)
    sources_service.resolve_without_attaching(staff_actor, source.pk, "duplicate", "n")
    with pytest.raises(InvalidTransition, match="already handled"):
        sources_service.resolve_without_attaching(staff_actor, source.pk, "suspicious", "n")
    # The other half of the lock: an event cannot be recorded on a message that was resolved.
    with pytest.raises(InvalidTransition, match="handled without attaching"):
        attach(staff_actor, reserved, source)
    assert not Event.objects.filter(source_id=source.pk).exists()

    undone = sources_service.mark_source_unreviewed(staff_actor, source.pk)
    assert undone.resolution is None and undone.resolution_note is None
    # Nothing is attached, so it is back where somebody will see it.
    assert undone.flagged_for_review is True
    fields = {a.field_name for a in _audit(source.pk)}
    assert {"sources.resolution", "sources.reviewed_at"} <= fields
    attach(staff_actor, reserved, source)
    assert Event.objects.filter(source_id=source.pk).count() == 1


# ---- notes -------------------------------------------------------------------------------------


def test_a_note_belongs_to_a_message_with_an_author_in_order(
    staff_actor, staff_user, colleague_user
) -> None:
    source = make_source(staff_actor)
    first = sources_service.add_source_note(staff_actor, source.pk, "  Call them first.  ")
    second = sources_service.add_source_note(
        Actor.human(colleague_user), source.pk, "They called back."
    )
    assert first.body == "Call them first." and first.author_id == staff_user.pk
    assert second.author_id == colleague_user.pk
    assert [n.pk for n in sources_service.list_source_notes(source.pk)] == [first.pk, second.pk]


def test_a_note_needs_text_a_person_and_a_message(staff_actor, system_actor) -> None:
    import uuid

    source = make_source(staff_actor)
    with pytest.raises(ServiceError, match="needs some text"):
        sources_service.add_source_note(staff_actor, source.pk, " \n ")
    with pytest.raises(NotAllowed):
        sources_service.add_source_note(system_actor, source.pk, "I am a process")
    with pytest.raises(NotFound):
        sources_service.add_source_note(staff_actor, uuid.uuid4(), "orphan")
    assert SourceNote.objects.count() == 0


def test_voiding_a_note_keeps_it_says_why_and_audits_who(
    staff_actor, colleague_user, staff_user
) -> None:
    source = make_source(staff_actor)
    note = sources_service.add_source_note(staff_actor, source.pk, "Wrong message.")
    voided = sources_service.void_source_note(
        Actor.human(colleague_user), note.pk, "Meant for the other thread."
    )
    assert voided.voided_at is not None and voided.void_reason == "Meant for the other thread."
    assert voided.body == "Wrong message."
    (line,) = _audit(note.pk, "source_notes.voided_at")
    assert line.edited_by_id == colleague_user.pk and line.note == "Meant for the other thread."

    assert [n.pk for n in sources_service.list_source_notes(source.pk)] == [note.pk]
    assert sources_service.list_source_notes(source.pk, include_voided=False) == []
    with pytest.raises(InvalidTransition, match="already voided"):
        sources_service.void_source_note(staff_actor, note.pk, "again")


def test_voiding_a_note_needs_a_reason_a_person_and_a_note_to_void(
    staff_actor, system_actor
) -> None:
    import uuid

    source = make_source(staff_actor)
    note = sources_service.add_source_note(staff_actor, source.pk, "Keep me.")
    with pytest.raises(ServiceError, match="needs some text"):
        sources_service.void_source_note(staff_actor, note.pk, "")
    with pytest.raises(NotAllowed):
        sources_service.void_source_note(system_actor, note.pk, "why")
    with pytest.raises(NotFound):
        sources_service.void_source_note(staff_actor, uuid.uuid4(), "why")
    assert SourceNote.objects.get(pk=note.pk).voided_at is None


# ---- set_attachment_roles ----------------------------------------------------------------------


def test_set_attachment_roles_makes_exactly_the_named_files_the_records(
    staff_actor, staff_user
) -> None:
    source = make_source(staff_actor)
    letter = add_file(staff_actor, source, "cover-letter.pdf")
    contract = add_file(staff_actor, source, "contract.pdf")
    map_file = add_file(staff_actor, source, "map.pdf", role="produced_record")
    supporting = add_file(staff_actor, source, "context.pdf", role="supporting_document")

    after = sources_service.set_attachment_roles(staff_actor, source.pk, [contract.pk])
    roles = {a.filename: a.role for a in after}
    assert roles == {
        "cover-letter.pdf": "attachment",
        "contract.pdf": "produced_record",
        # It WAS a record and is not named, so it goes back.
        "map.pdf": "attachment",
        # Not named and not a record: left alone.
        "context.pdf": "supporting_document",
    }
    changed = {a.entity_id: a for a in AuditLog.objects.filter(field_name="attachments.role")}
    assert set(changed) == {contract.pk, map_file.pk}
    assert changed[contract.pk].new_value == "produced_record"
    assert changed[map_file.pk].old_value == "produced_record"
    assert all(a.edited_by_id == staff_user.pk for a in changed.values())
    assert letter.pk not in changed and supporting.pk not in changed

    # Naming nothing clears the records.
    cleared = sources_service.set_attachment_roles(staff_actor, source.pk, [])
    assert {a.role for a in cleared if a.filename in {"contract.pdf", "map.pdf"}} == {"attachment"}


def test_set_attachment_roles_refuses_a_file_of_another_message_and_changes_nothing(
    staff_actor,
) -> None:
    one, two = make_source(staff_actor), make_source(staff_actor)
    mine = add_file(staff_actor, one, "mine.pdf")
    theirs = add_file(staff_actor, two, "theirs.pdf")
    with pytest.raises(ServiceError, match="Not attachments of this message"):
        sources_service.set_attachment_roles(staff_actor, one.pk, [mine.pk, theirs.pk])
    assert Attachment.objects.get(pk=mine.pk).role == "attachment"
    assert Attachment.objects.get(pk=theirs.pk).role == "attachment"


def test_set_attachment_roles_is_human_only(staff_actor, system_actor) -> None:
    source = make_source(staff_actor)
    letter = add_file(staff_actor, source)
    with pytest.raises(NotAllowed):
        sources_service.set_attachment_roles(system_actor, source.pk, [letter.pk])


# ---- add_document ------------------------------------------------------------------------------


def _upload(name, data=b"%PDF-1.4 fictional", content_type="application/pdf"):
    return sources_service.IncomingFile(name=name, chunks=[data], content_type=content_type)


def test_add_document_makes_one_source_its_attachments_and_one_event(
    media, staff_actor, staff_user, reserved
) -> None:
    result = sources_service.add_document(
        staff_actor,
        reserved.pk,
        source_type="scanned_mail",
        received_at=RECEIVED_AT,
        files=[_upload("scan-1.pdf", b"first"), _upload("scan-2.pdf", b"second")],
        display_body="Letter from the records unit.",
    )
    source, attachments, event = result.source, result.attachments, result.event

    assert source.source_type == "scanned_mail" and source.external_ref is None
    # The person who added it has looked at it.
    assert source.reviewed_at is not None and source.reviewed_by_id == staff_user.pk
    assert source.flagged_for_review is False

    assert event.request_id == reserved.pk and event.source_id == source.pk
    assert (event.event_type_id, event.channel, event.direction) == (
        "correspondence",
        "mail",
        "inbound",
    )
    assert event.occurred_at == RECEIVED_AT and event.recorded_by_user_id == staff_user.pk
    assert event.display_body == "Letter from the records unit."
    assert Event.objects.filter(source_id=source.pk).count() == 1

    assert [a.filename for a in attachments] == ["scan-1.pdf", "scan-2.pdf"]
    assert [a.size_bytes for a in attachments] == [5, 6]
    assert all(a.role == "attachment" and a.mime_type == "application/pdf" for a in attachments)
    # The file is in storage, under the source, named by its hash, and reads back byte for byte.
    first = attachments[0]
    assert first.storage_url.startswith(f"attachments/{source.pk}/{first.checksum_sha256[:16]}-")
    with storage.open_stored(first.storage_url) as stored:
        assert stored.read() == b"first"
    # The untouched original is a manifest of what was uploaded.
    manifest = json.loads(storage.open_stored(source.raw_storage_url).read())
    assert manifest["source_type"] == "scanned_mail"
    assert [f["filename"] for f in manifest["files"]] == ["scan-1.pdf", "scan-2.pdf"]
    assert manifest["files"][0]["sha256"] == first.checksum_sha256
    assert source.raw_storage_url.endswith(f"{source.pk}.json")


@pytest.mark.parametrize(
    ("source_type", "channel"),
    [
        ("scanned_mail", "mail"),
        ("portal_capture", "portal"),
        ("imported_eml", "email"),
        ("postmark_email", "email"),
    ],
)
def test_the_channel_comes_from_what_the_document_is(
    media, staff_actor, reserved, source_type, channel
) -> None:
    result = sources_service.add_document(
        staff_actor,
        reserved.pk,
        source_type=source_type,
        received_at=RECEIVED_AT,
        files=[_upload("a.pdf")],
    )
    assert result.event.channel == channel
    # No body given: the event says what was added, so the timeline is never blank.
    assert result.event.display_body == "Added: a.pdf"


def test_a_document_of_type_other_must_say_how_it_arrived(media, staff_actor, reserved) -> None:
    with pytest.raises(ServiceError, match="needs a channel"):
        sources_service.add_document(
            staff_actor,
            reserved.pk,
            source_type="other",
            received_at=RECEIVED_AT,
            files=[_upload("a.pdf")],
        )
    ok = sources_service.add_document(
        staff_actor,
        reserved.pk,
        source_type="other",
        received_at=RECEIVED_AT,
        files=[_upload("a.pdf")],
        channel="in_person",
    )
    assert ok.event.channel == "in_person"


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\evil.exe", "evil.exe"),
        ("/absolute/path/report.pdf", "report.pdf"),
        ("..", "file"),
        (".hidden.pdf", "hidden.pdf"),
        (". .htaccess", "htaccess"),
        (" . .bashrc", "bashrc"),
        ("report\x00.pdf", "report.pdf"),
        ("a‮fdp.exe", "afdp.exe"),
        ("bad<name>:with|chars?.pdf", "bad_name__with_chars_.pdf"),
        ("", "file"),
        ("   ", "file"),
    ],
)
def test_uploaded_file_names_are_sanitised_before_they_reach_disk_or_the_database(
    media, staff_actor, reserved, given, kept
) -> None:
    result = sources_service.add_document(
        staff_actor,
        reserved.pk,
        source_type="scanned_mail",
        received_at=RECEIVED_AT,
        files=[_upload(given)],
    )
    (attachment,) = result.attachments
    assert attachment.filename == kept
    assert attachment.storage_url.endswith(f"-{kept}")
    # Whatever the name was, the file is inside the media directory and nowhere else.
    assert (media / attachment.storage_url).is_file()
    assert not (media.parent / "passwd").exists()


def test_a_very_long_name_is_cut_but_keeps_its_extension() -> None:
    long = "x" * 400 + ".pdf"
    cut = storage.safe_filename(long)
    assert cut.endswith(".pdf") and len(cut.encode()) == storage.MAX_NAME_BYTES
    assert storage.safe_filename(None) == "file"
    assert storage.safe_filename("Re: report (final).pdf") == "Re_ report (final).pdf"


def test_a_file_over_the_size_limit_is_refused_and_nothing_is_left_behind(
    media, staff_actor, reserved
) -> None:
    from portal.core.models import Setting

    Setting.objects.update_or_create(
        key="records.upload_max_file_bytes", defaults={"value": 10, "description": "test"}
    )
    with pytest.raises(ServiceError, match="larger than the size limit"):
        sources_service.add_document(
            staff_actor,
            reserved.pk,
            source_type="scanned_mail",
            received_at=RECEIVED_AT,
            files=[_upload("fits.pdf", b"tiny"), _upload("huge.pdf", b"x" * 11)],
        )
    assert not Source.objects.exists() and not Attachment.objects.exists()
    assert not Event.objects.filter(request_id=reserved.pk, event_type_id="correspondence").exists()
    # The first file WAS stored before the second was refused; it was taken out again.
    assert [p for p in media.rglob("*") if p.is_file()] == []


def test_too_many_files_is_refused(media, staff_actor, reserved) -> None:
    from portal.core.models import Setting

    Setting.objects.update_or_create(
        key="records.upload_max_files", defaults={"value": 2, "description": "test"}
    )
    with pytest.raises(ServiceError, match="Too many files"):
        sources_service.add_document(
            staff_actor,
            reserved.pk,
            source_type="scanned_mail",
            received_at=RECEIVED_AT,
            files=[_upload("a.pdf"), _upload("b.pdf"), _upload("c.pdf")],
        )
    assert [p for p in media.rglob("*") if p.is_file()] == []


def test_add_document_needs_something_a_person_a_zone_a_known_type_and_a_request(
    media, staff_actor, system_actor, reserved
) -> None:
    import uuid

    kwargs = {
        "source_type": "scanned_mail",
        "received_at": RECEIVED_AT,
        "files": [_upload("a.pdf")],
    }
    with pytest.raises(ServiceError, match="at least one file"):
        sources_service.add_document(
            staff_actor, reserved.pk, **{**kwargs, "files": [], "display_body": "  "}
        )
    with pytest.raises(NotAllowed):
        sources_service.add_document(system_actor, reserved.pk, **kwargs)
    with pytest.raises(ServiceError, match="time zone"):
        sources_service.add_document(
            staff_actor, reserved.pk, **{**kwargs, "received_at": datetime(2026, 3, 2, 17, 30)}
        )
    with pytest.raises(ServiceError, match="Unknown source type"):
        sources_service.add_document(
            staff_actor, reserved.pk, **{**kwargs, "source_type": "carrier_pigeon"}
        )
    with pytest.raises(NotFound):
        sources_service.add_document(staff_actor, uuid.uuid4(), **kwargs)
    assert not Source.objects.exists()
    assert [p for p in media.rglob("*") if p.is_file()] == []


def test_a_document_with_only_text_is_a_source_with_no_files(media, staff_actor, reserved) -> None:
    result = sources_service.add_document(
        staff_actor,
        reserved.pk,
        source_type="portal_capture",
        received_at=RECEIVED_AT,
        files=[],
        display_body="The portal now says 'in review'.",
    )
    assert result.attachments == [] and result.event.channel == "portal"
    assert result.event.display_body == "The portal now says 'in review'."


def test_a_failure_after_the_files_are_stored_removes_them_and_rolls_everything_back(
    media, staff_actor, reserved, monkeypatch
) -> None:
    def refuse(*args, **kwargs):
        raise ServiceError("the ledger said no")

    monkeypatch.setattr(events_service, "record_event", refuse)
    with pytest.raises(ServiceError, match="the ledger said no"):
        sources_service.add_document(
            staff_actor,
            reserved.pk,
            source_type="scanned_mail",
            received_at=RECEIVED_AT,
            files=[_upload("a.pdf")],
        )
    assert not Source.objects.exists() and not Attachment.objects.exists()
    assert [p for p in media.rglob("*") if p.is_file()] == []
    assert StatusChange.objects.filter(request_id=reserved.pk).count() == 1  # only the reservation


def test_a_stored_name_that_climbs_out_of_the_media_directory_is_not_found(media) -> None:
    (media.parent / "outside.txt").write_text("secret")
    for name in ("../outside.txt", "/etc/passwd", "", "attachments/missing.pdf"):
        with pytest.raises(FileNotFoundError):
            storage.open_stored(name)
