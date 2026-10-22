"""The seam: a draft becomes requests, one per recipient.

The concurrency test lives in test_recipient_lock.py, which needs `transaction=True` and cannot
share these fixtures.
"""

import pytest
from django.db import connection
from portal.drafting.models import (
    LetterDraftItem,
    LetterRecipient,
    LetterTemplateRevision,
)
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import filing as drafting_filing
from portal.drafting.services import library as library_service
from portal.drafting.services.common import PROSE_ITEM_NUMBER
from portal.records.models import Deadline, Request, RequestItem
from portal.records.services.common import ServiceError


def test_a_wave_files_every_pending_recipient_and_shares_one_revision(
    db, draft, two_recipients, staff_actor
) -> None:
    result = drafting_filing.file_draft(staff_actor, draft.pk)

    assert result.filed_count == 2
    assert LetterTemplateRevision.objects.count() == 1, "one wave is one revision, not one each"
    rows = LetterRecipient.objects.filter(draft_id=draft.pk)
    assert {r.status for r in rows} == {"filed"}
    assert {r.template_revision_id for r in rows} == {result.revision_id}
    assert all(r.request_id is not None and r.filed_at is not None for r in rows)
    assert Request.objects.filter(current_status="filed").count() == 2
    assert Deadline.objects.filter(status="active").exists(), "the statutory clocks started"


def test_the_letter_that_went_out_carries_this_agency_name(
    db, draft, two_recipients, staff_actor
) -> None:
    drafting_filing.file_draft(staff_actor, draft.pk)
    texts = dict(
        Request.objects.select_related("agency").values_list("agency__name", "request_text")
    )
    for agency_name, text in texts.items():
        assert agency_name in text, (agency_name, text[:200])
        assert "{{AGENCY_FULL}}" not in text


def test_a_second_wave_from_an_unedited_template_mints_no_new_revision(
    db, draft, recipient, template, staff_actor, second_agency, staff_user
) -> None:
    first = drafting_filing.file_draft(staff_actor, draft.pk)
    later = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    drafts_service.add_recipient(later.pk, agency_id=second_agency.pk, channel="email")
    second = drafting_filing.file_draft(staff_actor, later.pk)

    assert second.revision_id == first.revision_id
    assert second.revision == 1
    assert LetterTemplateRevision.objects.count() == 1


def test_editing_the_template_between_waves_mints_the_next_revision(
    db, draft, recipient, template, staff_actor, second_agency, staff_user
) -> None:
    drafting_filing.file_draft(staff_actor, draft.pk)
    library_service.add_template_item(
        template.pk, item_number="11", body="Newly added ask", sort_order=10
    )
    later = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    drafts_service.add_recipient(later.pk, agency_id=second_agency.pk, channel="email")
    second = drafting_filing.file_draft(staff_actor, later.pk)

    assert second.revision == 2
    assert LetterTemplateRevision.objects.count() == 2
    labels = [
        s["label"] for s in LetterTemplateRevision.objects.get(revision=2).content["sections"]
    ]
    assert "11" in labels and "11" not in [
        s["label"] for s in LetterTemplateRevision.objects.get(revision=1).content["sections"]
    ]


def test_held_and_skipped_recipients_stay_behind(db, draft, agency, second_agency, staff_actor):
    held = drafts_service.add_recipient(
        draft.pk, agency_id=agency.pk, channel="mail", hold_reason="no verified email"
    )
    pending = drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk, channel="email")

    result = drafting_filing.file_draft(staff_actor, draft.pk)

    assert result.filed_count == 1
    by_id = {o.recipient_id: o for o in result.outcomes}
    assert by_id[held.pk].filed is False and by_id[held.pk].reason == "already held"
    assert by_id[pending.pk].filed is True
    assert LetterRecipient.objects.get(pk=held.pk).status == "held"


def test_a_held_recipient_files_later_alone_with_the_revision_its_letter_was_started_from(
    db, draft, agency, second_agency, template, staff_actor
) -> None:
    """Reversed by DECISIONS, "Design v2": until P10 a recipient filed later took whatever
    revision was current THEN, so a template fixed in between stamped an old letter with the
    fixed revision. It now points at the template as it stood when the letter was started, and an
    edit to the template afterwards mints nothing on its own."""
    held = drafts_service.add_recipient(
        draft.pk, agency_id=agency.pk, channel="mail", hold_reason="no verified email"
    )
    drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk, channel="email")
    first = drafting_filing.file_draft(staff_actor, draft.pk)

    library_service.add_template_item(
        template.pk, item_number="11", body="Later ask", sort_order=10
    )
    drafts_service.update_recipient(held.pk, status="pending", hold_reason=None)
    second = drafting_filing.file_draft(staff_actor, draft.pk, recipient_ids=[held.pk])

    assert second.filed_count == 1
    assert second.revision == first.revision == 1
    assert LetterRecipient.objects.get(pk=held.pk).template_revision_id == first.revision_id
    assert LetterTemplateRevision.objects.count() == 1, (
        "editing wrote no history; filing minted none"
    )


def test_an_unknown_recipient_id_is_refused_rather_than_silently_dropped(
    db, draft, recipient, staff_actor
) -> None:
    """F4: `LetterRecipient.objects.filter(pk__in=recipient_ids)` drops an id that names no row
    of this draft with no error -- a typo, or an id from the wrong draft. Filing nine of ten
    because the tenth id was wrong is the exact silent-partial-result shape this module exists to
    refuse."""
    import uuid

    bogus = uuid.uuid4()
    with pytest.raises(ServiceError, match=str(bogus)):
        drafting_filing.file_draft(staff_actor, draft.pk, recipient_ids=[recipient.pk, bogus])
    assert LetterRecipient.objects.get(pk=recipient.pk).status == "pending"
    assert Request.objects.count() == 0


def test_the_draft_order_survives_into_the_request_items(db, draft, recipient, staff_actor) -> None:
    """records_db.0005's whole point: '10' must not lead '2' in the filed request."""
    drafting_filing.file_draft(staff_actor, draft.pk)
    request = Request.objects.get()
    numbers = list(
        RequestItem.objects.filter(request_id=request.pk)
        .order_by("sort_order", "item_number")
        .values_list("item_number", flat=True)
    )
    assert numbers == ["2", "2a", "10"]


def test_parents_are_translated_from_uuid_links_to_batch_item_numbers(
    db, draft, recipient, staff_actor
) -> None:
    drafting_filing.file_draft(staff_actor, draft.pk)
    items = {i.item_number: i for i in RequestItem.objects.all()}
    assert items["2a"].parent_item_id == items["2"].pk
    assert items["2"].parent_item_id is None


def test_resolved_prose_does_not_become_a_request_item(db, draft, recipient, staff_actor) -> None:
    assert LetterDraftItem.objects.filter(draft_id=draft.pk, item_number=PROSE_ITEM_NUMBER).exists()
    drafting_filing.file_draft(staff_actor, draft.pk)
    assert not RequestItem.objects.filter(item_number=PROSE_ITEM_NUMBER).exists()
    assert RequestItem.objects.count() == 3


def test_two_items_sharing_a_number_are_refused_before_anything_is_minted(
    db, draft, two_recipients, staff_actor
) -> None:
    """Nothing enforces unique (draft_id, item_number), so `reserve_request` would silently pick
    one of them as the parent. Refuse first -- and refuse for the whole draft, before the first
    recipient, because a duplicate number is a property of the draft and filing eight of nine
    before noticing would be the worst outcome available."""
    drafts_service.add_draft_item(draft.pk, item_number="2", body="A second item numbered 2")
    with pytest.raises(ServiceError, match="both numbered"):
        drafting_filing.file_draft(staff_actor, draft.pk)
    assert Request.objects.count() == 0
    # The one revision is the one minted when the draft was started; the refused filing made none.
    assert LetterTemplateRevision.objects.count() == 1


def test_an_item_that_is_its_own_parent_is_refused_rather_than_dropped(
    db, draft, recipient, staff_actor
) -> None:
    orphan = drafts_service.add_draft_item(draft.pk, item_number="99", body="Loops")
    LetterDraftItem.objects.filter(pk=orphan.pk).update(parent_item_id=orphan.pk)
    with pytest.raises(ServiceError, match="loops back"):
        drafting_filing.file_draft(staff_actor, draft.pk)


def test_a_draft_with_no_author_and_no_requester_is_refused(db, template, agency, staff_actor):
    anonymous = drafts_service.create_draft(from_template_id=template.pk)
    drafts_service.add_recipient(anonymous.pk, agency_id=agency.pk, channel="email")
    with pytest.raises(ServiceError, match="nobody to file it on behalf of"):
        drafting_filing.file_draft(staff_actor, anonymous.pk)


def test_a_draft_with_no_template_files_without_a_revision(db, agency, staff_user, staff_actor):
    scratch = drafts_service.create_draft(subject="From scratch", created_by_user_id=staff_user.pk)
    drafts_service.add_draft_item(scratch.pk, item_number="1", body="The only ask")
    drafts_service.add_recipient(scratch.pk, agency_id=agency.pk, channel="email")

    result = drafting_filing.file_draft(staff_actor, scratch.pk)

    assert result.filed_count == 1
    assert result.revision_id is None
    assert LetterTemplateRevision.objects.count() == 0
    assert LetterRecipient.objects.get(draft_id=scratch.pk).template_revision_id is None


def test_a_second_call_on_an_already_filed_recipient_mints_nothing(
    db, draft, recipient, staff_actor
) -> None:
    """The sequential half of the double-click. The concurrent half is test_recipient_lock.py."""
    drafting_filing.file_draft(staff_actor, draft.pk)
    again = drafting_filing.file_draft(staff_actor, draft.pk)

    assert again.filed_count == 0
    assert again.outcomes[0].reason == "already filed"
    assert Request.objects.count() == 1


def test_preview_renders_exactly_what_filing_would_send(db, draft, agency, staff_actor) -> None:
    """A genuinely complete letter -- unlike the `recipient` fixture, this one supplies
    RECIPIENT_OFFICE explicitly (a real contact, or an override, is how a drafter avoids F3's
    invisible-blank bug), so `unresolved == ()` is asserting something true rather than the old
    silent-blank behaviour."""
    recipient = drafts_service.add_recipient(
        draft.pk,
        agency_id=agency.pk,
        channel="email",
        placeholder_overrides={"RECIPIENT_OFFICE": "Records Unit"},
    )
    previewed = drafting_filing.preview(recipient.pk)
    drafting_filing.file_draft(staff_actor, draft.pk)
    assert Request.objects.get().request_text == previewed.text
    assert previewed.unresolved == ()


def test_preview_reports_a_blank_default_as_unresolved_not_silently_filled(
    db, draft, bare_agency, staff_actor
) -> None:
    """F3's repro: an agency with no contact on this recipient, no recipient office and no
    default_request_email or portal_url (`bare_agency`) used to resolve RECIPIENT_OFFICE and
    ADDRESS to `""` and report the letter as fully resolved. `unresolved_placeholders` must catch
    both."""
    recipient = drafts_service.add_recipient(draft.pk, agency_id=bare_agency.pk, channel="email")
    previewed = drafting_filing.preview(recipient.pk)
    assert "RECIPIENT_OFFICE" in previewed.unresolved
    assert "{{RECIPIENT_OFFICE}}" in previewed.text


def test_one_failing_recipient_does_not_roll_back_the_others(
    db, draft, agency, second_agency, staff_actor, monkeypatch
) -> None:
    """The transaction boundary, proved by injecting a failure rather than by describing one.

    Nine agencies must not lose eight good filings because the ninth row is malformed, so each
    recipient files inside its own savepoint. There is no naturally malformed row to reach for
    here -- every vocabulary a recipient carries is a subset of what records accepts -- so the
    failure is injected at the seam itself, which is where a real one would surface.
    """
    good = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")
    doomed = drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk, channel="email")

    real_file_request = drafting_filing.records_filing.file_request

    def explode(actor, request_id, **kwargs):
        if Request.objects.get(pk=request_id).agency_id == second_agency.pk:
            raise ServiceError("injected failure at the seam")
        return real_file_request(actor, request_id, **kwargs)

    monkeypatch.setattr(drafting_filing.records_filing, "file_request", explode)

    result = drafting_filing.file_draft(staff_actor, draft.pk)

    assert result.filed_count == 1
    by_id = {o.recipient_id: o for o in result.outcomes}
    assert by_id[good.pk].filed is True
    assert by_id[doomed.pk].filed is False
    assert "injected failure" in (by_id[doomed.pk].reason or "")

    assert LetterRecipient.objects.get(pk=good.pk).status == "filed"
    assert LetterRecipient.objects.get(pk=doomed.pk).status == "pending"
    # The good filing survived the bad one's rollback, and the bad one left nothing behind.
    assert Request.objects.filter(agency_id=agency.pk).count() == 1
    assert Request.objects.filter(agency_id=second_agency.pk).count() == 0


def test_a_genuine_database_error_on_one_recipient_does_not_roll_back_the_others(
    db, draft, agency, second_agency, staff_actor, monkeypatch
) -> None:
    """F1: `_file_one` used to catch only `(ServiceError, IntegrityError)`. A real Postgres
    error that is NOT an IntegrityError -- the realistic case is `40P01` from two overlapping
    waves inverting `select_for_update` lock order -- escaped `_file_one`, escaped `file_draft`,
    and rolled back the whole HTTP transaction under `ATOMIC_REQUESTS`.

    This injects a genuine error Postgres itself raises (division by zero -> `DataError`, a
    `DatabaseError` subclass that is not `IntegrityError`), not a Python-constructed exception,
    so the fix is proven against the same shape of failure the widened `except DatabaseError`
    clause exists for.
    """
    good = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")
    doomed = drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk, channel="email")

    real_reserve = drafting_filing.requests_service.reserve_request

    def reserve_or_explode(actor, *, agency_id, **kwargs):
        if agency_id == second_agency.pk:
            with connection.cursor() as cur:
                cur.execute("SELECT 1/0")  # genuine Postgres DataError, sqlstate 22012
        return real_reserve(actor, agency_id=agency_id, **kwargs)

    monkeypatch.setattr(drafting_filing.requests_service, "reserve_request", reserve_or_explode)

    result = drafting_filing.file_draft(staff_actor, draft.pk)

    assert result.filed_count == 1
    by_id = {o.recipient_id: o for o in result.outcomes}
    assert by_id[good.pk].filed is True
    assert by_id[doomed.pk].filed is False
    # The fixed string, never Postgres's own DETAIL line -- P3's gate found that text leaking a
    # tracking number and an inbound alias in one response body.
    assert by_id[doomed.pk].reason == drafting_filing.CONSTRAINT_REASON
    assert "division" not in (by_id[doomed.pk].reason or "")

    assert LetterRecipient.objects.get(pk=good.pk).status == "filed"
    assert LetterRecipient.objects.get(pk=doomed.pk).status == "pending"
    assert Request.objects.filter(agency_id=agency.pk).count() == 1
    assert Request.objects.filter(agency_id=second_agency.pk).count() == 0
