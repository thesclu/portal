"""The ordinary services: the library, and what draft creation copies.

The tests that carry weight here are the independence ones. If a draft edit could reach the
template, or a block edit could reach a draft, the design's central rule would be broken and
nothing else in the system would notice.
"""

import pytest
from portal.drafting.models import (
    LetterDraft,
    LetterDraftCitation,
    LetterDraftItem,
    LetterRecipient,
    LetterShell,
    LetterTemplateItem,
    SharedBlock,
)
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import library as library_service
from portal.drafting.services.common import PROSE_ITEM_NUMBER, letter_of_draft
from portal.records.services.common import InvalidTransition, NotFound, ServiceError

# ---- the library -----------------------------------------------------------


def test_promoting_a_second_shell_demotes_the_first(db, shell) -> None:
    """letter_shells_one_default is a unique partial index: without the demotion this is a 23505."""
    other = library_service.create_shell(
        name="Second", letterhead="L", opening_body="O", closing_body="C"
    )
    library_service.update_shell(other.pk, is_default=True)
    assert LetterShell.objects.filter(is_default=True).count() == 1
    assert LetterShell.objects.get(pk=other.pk).is_default is True


def test_an_unknown_field_is_refused_rather_than_silently_dropped(db, template) -> None:
    with pytest.raises(ServiceError, match="not editable"):
        library_service.update_template(template.pk, created_at="whenever")


def test_a_missing_row_is_a_not_found(db) -> None:
    import uuid

    with pytest.raises(NotFound):
        library_service.update_template(uuid.uuid4(), name="x")


def test_a_template_nothing_was_filed_from_is_deletable(db, template) -> None:
    library_service.delete_template(template.pk)
    assert library_service.list_templates() == []


def test_archiving_hides_a_template_without_erasing_it(db, template) -> None:
    library_service.archive_template(template.pk)
    assert library_service.list_templates() == []
    assert [t.pk for t in library_service.list_templates(include_archived=True)] == [template.pk]
    library_service.archive_template(template.pk, archived=False)
    assert [t.pk for t in library_service.list_templates()] == [template.pk]


def test_setting_the_block_list_replaces_it_and_the_order_given_is_the_order(db, template) -> None:
    second = library_service.create_block(name="Fee waiver", body="We request a fee waiver.")
    first = library_service.create_block(name="Format", body="Native format, please.")
    links = library_service.set_template_blocks(template.pk, [first.pk, second.pk])
    assert [link.shared_block_id for link in links] == [first.pk, second.pk]
    # Left unplaced, blocks sit just below zero, ahead of every item (P10 review, F9).
    assert [link.sort_order for link in links] == [-2, -1]


def test_a_template_cannot_pull_in_the_same_block_twice(db, template, block) -> None:
    with pytest.raises(ServiceError, match="at most once"):
        library_service.set_template_blocks(template.pk, [block.pk, block.pk])


def test_deleting_a_shell_a_template_still_points_at_is_refused_with_its_own_sentence(
    db, template, shell
) -> None:
    """F4: 23503 here (letter_templates.shell_id, no ON DELETE action) used to be routed through
    `refusing_to_delete_what_was_filed_from`, whose message is wrong twice for a shell -- nothing
    was filed, and there is no `archive_shell` door to send the caller to."""
    with pytest.raises(InvalidTransition) as excinfo:
        library_service.delete_shell(shell.pk)
    message = str(excinfo.value)
    assert "still used by a template or a draft" in message
    assert "filed" not in message.lower()
    assert "archive" not in message.lower()
    assert LetterShell.objects.filter(pk=shell.pk).exists()


def test_deleting_a_shared_block_a_template_still_pulls_in_is_refused_with_its_own_sentence(
    db, template, block
) -> None:
    """Same mechanism, same wrong-message bug, for letter_template_blocks.shared_block_id."""
    with pytest.raises(InvalidTransition) as excinfo:
        library_service.delete_block(block.pk)
    message = str(excinfo.value)
    assert "still used by a template or a draft" in message
    assert "filed" not in message.lower()
    assert "archive" not in message.lower()
    assert SharedBlock.objects.filter(pk=block.pk).exists()


# ---- draft creation --------------------------------------------------------


def test_a_draft_from_a_template_copies_items_citations_and_resolved_blocks(db, draft) -> None:
    items = LetterDraftItem.objects.filter(draft_id=draft.pk)
    assert items.filter(item_number=PROSE_ITEM_NUMBER).count() == 1, "the shared block, resolved"
    asks = {i.item_number: i for i in items.exclude(item_number=PROSE_ITEM_NUMBER)}
    assert set(asks) == {"2", "2a", "10"}
    assert asks["2a"].parent_item_id == asks["2"].pk, "parent remapped to the new draft ids"
    assert LetterDraftCitation.objects.filter(draft_id=draft.pk).count() == 1
    assert draft.source_template_id is not None and draft.source_request_id is None


def test_editing_the_draft_never_reaches_the_template(db, draft, template) -> None:
    """The rule the whole schema is shaped around. There is deliberately no column joining a
    draft item back to the template item it came from, so this cannot even be attempted."""
    item = LetterDraftItem.objects.get(draft_id=draft.pk, item_number="2")
    drafts_service.update_draft_item(item.pk, body="Rewritten on the way out the door")
    assert LetterTemplateItem.objects.get(template_id=template.pk, item_number="2").body == (
        "Widget camera footage"
    )
    assert {f.name for f in LetterDraftItem._meta.concrete_fields}.isdisjoint(
        {"template_item", "source_item", "template_item_id"}
    )


def test_editing_a_shared_block_never_reaches_a_draft_already_started(db, draft, block) -> None:
    library_service.update_block(block.pk, body="COMPLETELY DIFFERENT PROSE")
    prose = LetterDraftItem.objects.get(draft_id=draft.pk, item_number=PROSE_ITEM_NUMBER)
    assert prose.body == "'Record' means any writing, however stored."


def test_a_draft_from_a_past_request_copies_its_items_and_subject(
    db, reserved_with_items, staff_user
) -> None:
    draft = drafts_service.create_draft(
        from_request_id=reserved_with_items.pk, created_by_user_id=staff_user.pk
    )
    assert draft.source_request_id == reserved_with_items.pk
    assert draft.source_template_id is None
    assert draft.subject == reserved_with_items.subject
    numbers = list(
        LetterDraftItem.objects.filter(draft_id=draft.pk)
        .order_by("sort_order")
        .values_list("item_number", flat=True)
    )
    assert numbers == ["1", "2"]


@pytest.mark.parametrize("sensitivity", ["public", "internal", "privileged"])
def test_a_draft_from_a_past_request_inherits_its_sensitivity(
    db, staff_actor, staff_user, agency, sensitivity
) -> None:
    """F2: `sensitivity or source.sensitivity` was dead code because `create_draft`'s parameter
    defaulted to the non-empty `"internal"`, so `source.sensitivity` was never reached -- a draft
    started from a `privileged` request came back `internal`, silently. Proved for all three
    values, not just the one that used to be masked."""
    from portal.records.services import requests as requests_service

    source = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject=f"A {sensitivity} request",
        request_text="Whatever went out.",
        items=[requests_service.NewItem("1", "The only ask")],
        sensitivity=sensitivity,
    )

    draft = drafts_service.create_draft(from_request_id=source.pk, created_by_user_id=staff_user.pk)

    assert draft.sensitivity == sensitivity


def test_an_explicit_sensitivity_overrides_the_source_requests(
    db, staff_actor, staff_user, agency
) -> None:
    from portal.records.services import requests as requests_service

    source = requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="A privileged request",
        request_text="Whatever went out.",
        items=[requests_service.NewItem("1", "The only ask")],
        sensitivity="privileged",
    )

    draft = drafts_service.create_draft(
        from_request_id=source.pk, sensitivity="public", created_by_user_id=staff_user.pk
    )

    assert draft.sensitivity == "public"


def test_a_draft_cannot_claim_two_sources(db, template, reserved_with_items) -> None:
    with pytest.raises(ServiceError, match="not both"):
        drafts_service.create_draft(
            from_template_id=template.pk, from_request_id=reserved_with_items.pk
        )


def test_a_draft_from_scratch_needs_a_subject(db) -> None:
    with pytest.raises(ServiceError, match="needs a subject"):
        drafts_service.create_draft()


def test_a_draft_from_scratch_takes_the_default_shell(db, shell) -> None:
    draft = drafts_service.create_draft(subject="Written here")
    assert draft.shell_id == shell.pk
    assert letter_of_draft(draft).shell is not None


# ---- recipients ------------------------------------------------------------


def test_an_agency_with_no_route_comes_in_held_with_its_reason(db, draft, agency) -> None:
    row = drafts_service.add_recipient(
        draft.pk, agency_id=agency.pk, channel="mail", hold_reason="no verified email"
    )
    assert row.status == LetterRecipient.HELD


def test_a_held_recipient_must_say_why(db, recipient) -> None:
    with pytest.raises(ServiceError, match="say why"):
        drafts_service.update_recipient(recipient.pk, status="held")


def test_clearing_the_reason_while_still_held_is_refused(db, draft, agency) -> None:
    """The UPDATE would write the null and hit letter_recipients_held_has_reason; catching it
    here means the caller gets a sentence instead of a constraint name."""
    row = drafts_service.add_recipient(
        draft.pk, agency_id=agency.pk, channel="mail", hold_reason="no verified email"
    )
    with pytest.raises(ServiceError, match="say why"):
        drafts_service.update_recipient(row.pk, hold_reason=None)


def test_nobody_can_set_a_recipient_to_filed_by_hand(db, recipient) -> None:
    with pytest.raises(ServiceError, match="mints its request"):
        drafts_service.update_recipient(recipient.pk, status="filed")


# ---- deletion --------------------------------------------------------------


def test_an_unfiled_draft_deletes_with_everything_under_it(db, draft, recipient) -> None:
    drafts_service.delete_draft(draft.pk)
    assert not LetterDraft.objects.filter(pk=draft.pk).exists()
    assert not LetterDraftItem.objects.filter(draft_id=draft.pk).exists()
    assert not LetterRecipient.objects.filter(pk=recipient.pk).exists()


def test_deleting_a_filed_draft_gives_a_sentence_not_a_constraint_name(
    db, draft, recipient, staff_actor
) -> None:
    from portal.drafting.services import filing as drafting_filing

    drafting_filing.file_draft(staff_actor, draft.pk)
    with pytest.raises(InvalidTransition, match="Archive it instead"):
        drafts_service.delete_draft(draft.pk)
    assert LetterDraft.objects.filter(pk=draft.pk).exists()


def test_deleting_a_template_filed_from_gives_the_same_sentence(
    db, draft, recipient, template, staff_actor
) -> None:
    """A different SQLSTATE from the test above -- 23503 from ON DELETE RESTRICT rather than
    23001 from a trigger -- and the caller must not be able to tell the difference."""
    from portal.drafting.services import filing as drafting_filing

    drafting_filing.file_draft(staff_actor, draft.pk)
    with pytest.raises(InvalidTransition, match="Archive it instead"):
        library_service.delete_template(template.pk)


def test_the_transaction_survives_a_refused_delete(db, draft, recipient, staff_actor) -> None:
    """Catching an IntegrityError without a savepoint leaves the connection unusable, and under
    ATOMIC_REQUESTS there is always an enclosing transaction. Prove the next query still runs."""
    from portal.drafting.services import filing as drafting_filing

    drafting_filing.file_draft(staff_actor, draft.pk)
    with pytest.raises(InvalidTransition):
        drafts_service.delete_draft(draft.pk)
    assert LetterDraftItem.objects.filter(draft_id=draft.pk).count() > 0
