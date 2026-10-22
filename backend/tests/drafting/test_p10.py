"""P10: reserve then file, revisions on save and at draft creation, one order for blocks and
items, the placeholders the design's letter uses, and recipient defaults.

The races are in test_recipient_lock.py. The earlier tests in test_filing.py still describe the
wave; where DECISIONS reversed what they asserted, they were changed there and say so.
"""

from datetime import UTC, datetime

import pytest
from django.test import Client
from portal.drafting import render
from portal.drafting.models import LetterRecipient, LetterTemplateRevision
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import filing as drafting_filing
from portal.drafting.services import library as library_service
from portal.records.actors import Actor
from portal.records.models import Event, Request, RequestItem, StatusChange
from portal.records.services import access as access_service
from portal.records.services import directory
from portal.records.services import filing as records_filing
from portal.records.services.common import InvalidTransition, NotAllowed, ServiceError

from tests.drafting.conftest import sendable_agency
from tests.drafting.test_api import DRAFTING_PREFIX, patch_json, post_json

WHEN = datetime(2026, 2, 10, 18, 0, tzinfo=UTC)

# Every placeholder the design's letter uses, one per line, so a test can read any of them back.
LETTER_SHELL = dict(
    name="Design v2 letter",
    letterhead="{{FILING_DATE}}\nVIA {{CHANNEL_METHOD}}",
    opening_body=(
        "{{RECIPIENT_OFFICE}}\n{{AGENCY_FULL}} ({{AGENCY_ABBR}})\n{{AGENCY_STREETADDRESS}}\n"
        "{{AGENCY_CITYSTATEZIP}}\nSent to: {{ADDRESS}}"
    ),
    closing_body="Thank you.",
)


@pytest.fixture
def full_shell(db):
    return library_service.create_shell(**LETTER_SHELL)


@pytest.fixture
def full_draft(db, full_shell, staff_user):
    made = drafts_service.create_draft(
        subject="Everything", shell_id=full_shell.pk, created_by_user_id=staff_user.pk
    )
    drafts_service.add_draft_item(made.pk, item_number="1", body="The ask", provision="carve-out")
    return made


def _values(agency, **kw):
    return drafting_filing.agency_values(agency, when=WHEN, **kw)


# ---- reserve, then file -------------------------------------------------------------------------


def test_reserving_freezes_the_letter_into_a_pending_request_and_the_recipient_stays_pending(
    db, draft, recipient, staff_actor
) -> None:
    before = drafting_filing.preview(recipient.pk)
    reserved = drafting_filing.reserve_recipient(staff_actor, recipient.pk)

    assert reserved.status == "pending" and reserved.filed_at is None
    request = Request.objects.get(pk=reserved.request_id)
    assert request.current_status_id == "pending_filing" and request.filed_at is None
    assert request.tracking_number and request.inbound_alias_email
    assert request.request_text == before.text
    assert reserved.template_revision_id == draft.source_revision_id is not None
    assert not Event.objects.filter(request_id=request.pk, event_type_id="submission").exists()
    # Once reserved, the preview IS the reserved letter, whatever is edited afterwards.
    drafts_service.update_draft(draft.pk, subject="Changed after reserving")
    assert drafting_filing.preview(recipient.pk).text == before.text


def test_reserving_twice_or_reserving_what_is_not_pending_is_refused(
    db, draft, agency, second_agency, staff_actor
) -> None:
    one = drafts_service.add_recipient(draft.pk, agency_id=agency.pk)
    held = drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk, hold_reason="wait")
    drafting_filing.reserve_recipient(staff_actor, one.pk)
    with pytest.raises(InvalidTransition, match="already has a reserved request"):
        drafting_filing.reserve_recipient(staff_actor, one.pk)
    with pytest.raises(InvalidTransition, match="is held"):
        drafting_filing.reserve_recipient(staff_actor, held.pk)
    assert Request.objects.count() == 1


def test_reservation_is_refused_naming_every_blank_and_nothing_is_minted(
    db, full_draft, bare_agency, staff_actor
) -> None:
    """New in P10. The preview used to report unresolved placeholders and filing went ahead."""
    recipient = drafts_service.add_recipient(
        full_draft.pk, agency_id=bare_agency.pk, channel="mail"
    )
    drafts_service.update_recipient(recipient.pk, status="pending", hold_reason=None)
    with pytest.raises(ServiceError) as refused:
        drafting_filing.reserve_recipient(staff_actor, recipient.pk)
    message = str(refused.value)
    for name in ("ADDRESS", "AGENCY_STREETADDRESS", "AGENCY_CITYSTATEZIP", "RECIPIENT_OFFICE"):
        assert name in message, message
    assert "Bare Agency" in message
    assert Request.objects.count() == 0
    assert LetterRecipient.objects.get(pk=recipient.pk).request_id is None
    # The wave reports it per recipient and reserves nothing either.
    wave = drafting_filing.file_draft(staff_actor, full_draft.pk)
    assert wave.filed_count == 0 and "AGENCY_STREETADDRESS" in wave.outcomes[0].reason
    assert Request.objects.count() == 0


def test_a_mail_recipient_with_no_street_address_reports_it_unresolved(
    db, full_draft, staff_actor
) -> None:
    no_street = directory.create_agency(
        staff_actor,
        name="No Street Agency",
        jurisdiction="Testland",
        records_law="TESTLAW",
        recipient_office="Records Unit",
        mailing_city_state_zip="Testville, TL 00000",
        filing_method="mail",
    )
    recipient = drafts_service.add_recipient(full_draft.pk, agency_id=no_street.pk)
    assert (recipient.channel, recipient.status) == ("mail", "pending")
    unresolved = drafting_filing.preview(recipient.pk).unresolved
    # Half an address is not one: the street is missing, so ADDRESS as a whole is too.
    assert unresolved == ("ADDRESS", "AGENCY_STREETADDRESS")
    with pytest.raises(ServiceError, match="AGENCY_STREETADDRESS"):
        drafting_filing.reserve_recipient(staff_actor, recipient.pk)


def test_a_letter_reserved_in_drafting_and_filed_from_its_requests_own_page_ends_filed(
    db, draft, recipient, staff_user
) -> None:
    """Filing has one door, and `records` knows nothing of `drafting`: the recipient is stamped by
    the callback this app registers, inside `file_request`'s transaction."""
    client = Client()
    client.force_login(staff_user)
    reserved = post_json(client, f"{DRAFTING_PREFIX}/recipients/{recipient.pk}/reserve", {})
    assert reserved.status_code == 200, reserved.content
    body = reserved.json()
    assert (body["status"], body["request_status"]) == ("pending", "pending_filing")
    assert body["tracking_number"].startswith("REQ-") and body["request_filed_at"] is None

    filed = post_json(
        client,
        f"/api/records/requests/{body['request_id']}/file",
        {"filed_at": WHEN.isoformat(), "channel": "portal"},
    )
    assert filed.status_code == 200, filed.content

    row = LetterRecipient.objects.get(pk=recipient.pk)
    assert row.status == "filed" and row.filed_at == WHEN
    shown = client.get(f"{DRAFTING_PREFIX}/drafts/{draft.pk}").json()["recipients"][0]
    assert (shown["status"], shown["request_status"]) == ("filed", "filed")
    # And the drafting door now refuses, because the request is no longer pending.
    again = post_json(client, f"{DRAFTING_PREFIX}/recipients/{recipient.pk}/file", {})
    assert again.status_code == 409


def test_filing_a_request_that_came_from_no_draft_stamps_nothing(
    db, reserved_with_items, staff_actor
):
    records_filing.file_request(staff_actor, reserved_with_items.pk, filed_at=WHEN, channel="email")
    assert not LetterRecipient.objects.exists()


def test_the_callback_is_registered_once_however_often_ready_runs(db) -> None:
    from django.apps import apps

    registered = records_filing._after_filed
    assert registered.count(drafting_filing.stamp_recipient_filed) == 1
    apps.get_app_config("drafting").ready()
    assert registered.count(drafting_filing.stamp_recipient_filed) == 1


def test_filing_one_recipient_reserves_it_first_and_records_the_agencys_reference(
    db, draft, recipient, staff_actor
) -> None:
    filed = drafting_filing.file_recipient(
        staff_actor,
        recipient.pk,
        filed_at=WHEN,
        channel="portal",
        agency_reference_number=" PRR-9 ",
    )
    assert filed.status == "filed" and filed.filed_at == WHEN
    request = Request.objects.get(pk=filed.request_id)
    assert request.agency_reference_number == "PRR-9"
    submission = Event.objects.get(request_id=request.pk, event_type_id="submission")
    assert submission.channel == "portal"
    with pytest.raises(InvalidTransition, match="already been filed"):
        drafting_filing.file_recipient(staff_actor, recipient.pk)


def test_a_held_or_skipped_recipient_is_not_filed(db, draft, agency, staff_actor) -> None:
    held = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, hold_reason="wait")
    with pytest.raises(InvalidTransition, match="is held"):
        drafting_filing.file_recipient(staff_actor, held.pk)
    assert Request.objects.count() == 0


def test_the_items_provision_reaches_the_request_and_starts_its_own_clock(
    db, template, agency, staff_user, staff_actor
) -> None:
    """`provision` travels template item -> draft item -> request item, so `file_request` computes
    the same deadlines whichever door files the letter."""
    item = template.items.get(item_number="2")
    library_service.update_template_item(item.pk, provision="footage")
    made = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    assert made.items.get(item_number="2").provision == "footage"
    recipient = drafts_service.add_recipient(made.pk, agency_id=agency.pk)
    reserved = drafting_filing.reserve_recipient(staff_actor, recipient.pk)
    carried = dict(
        RequestItem.objects.filter(request_id=reserved.request_id).values_list(
            "item_number", "provision"
        )
    )
    assert carried["2"] == "footage" and carried["10"] is None


# ---- skip ---------------------------------------------------------------------------------------


def test_skipping_before_reservation_touches_nothing_in_records(db, recipient, staff_actor) -> None:
    skipped = drafting_filing.skip_recipient(staff_actor, recipient.pk)
    assert skipped.status == "skipped" and skipped.request_id is None
    assert Request.objects.count() == 0
    # Not yet reserved, so it can be brought back.
    assert drafts_service.update_recipient(recipient.pk, status="pending").status == "pending"


def test_skipping_a_reserved_recipient_withdraws_its_request_and_needs_a_note(
    db, recipient, staff_actor, staff_user
) -> None:
    reserved = drafting_filing.reserve_recipient(staff_actor, recipient.pk)
    with pytest.raises(ServiceError, match="needs a note"):
        drafting_filing.skip_recipient(staff_actor, recipient.pk, "  ")
    skipped = drafting_filing.skip_recipient(staff_actor, recipient.pk, "Agency merged.")

    assert skipped.status == "skipped" and skipped.request_id == reserved.request_id
    request = Request.objects.get(pk=reserved.request_id)
    assert request.current_status_id == "withdrawn"
    change = StatusChange.objects.filter(request_id=request.pk).order_by("-effective_at").first()
    assert (change.status_id, change.reason, change.note) == (
        "withdrawn",
        "manual_override",
        "Agency merged.",
    )
    # It cannot be un-skipped or filed, and skipping it again is a no-op, not a second withdrawal.
    with pytest.raises(ServiceError, match="already reserved"):
        drafts_service.update_recipient(recipient.pk, status="pending")
    with pytest.raises(InvalidTransition, match="request withdrawn"):
        drafting_filing.file_recipient(staff_actor, recipient.pk)
    assert drafting_filing.skip_recipient(staff_actor, recipient.pk, "Again.").status == "skipped"
    assert StatusChange.objects.filter(request_id=request.pk, status_id="withdrawn").count() == 1


def test_a_system_actor_cannot_skip_and_a_filed_recipient_cannot_be_skipped(
    db, draft, recipient, staff_actor
) -> None:
    with pytest.raises(NotAllowed):
        drafting_filing.skip_recipient(Actor.for_system("a_job"), recipient.pk, "x")
    drafting_filing.file_recipient(staff_actor, recipient.pk)
    with pytest.raises(InvalidTransition, match="already out the door"):
        drafting_filing.skip_recipient(staff_actor, recipient.pk, "Too late.")


def test_a_reserved_recipient_is_frozen_including_how_its_letter_says_it_was_sent(
    db, full_draft, agency, staff_actor
) -> None:
    """Review F7 and F2. The channel is wording too ("VIA EMAIL", the address), and a hold the
    request's own page cannot see is not a hold."""
    recipient = drafts_service.add_recipient(full_draft.pk, agency_id=agency.pk)
    reserved = drafting_filing.reserve_recipient(staff_actor, recipient.pk)
    for field in (
        {"placeholder_overrides": {"X": "y"}},
        {"agency_contact_id": None},
        {"channel": "mail"},
    ):
        with pytest.raises(ServiceError, match="wording is fixed"):
            drafts_service.update_recipient(recipient.pk, **field)
    for status in ("skipped", "held"):
        with pytest.raises(ServiceError, match="already reserved"):
            drafts_service.update_recipient(recipient.pk, status=status, hold_reason="Wait.")
    with pytest.raises(ServiceError, match="reserved to go by email"):
        drafting_filing.file_recipient(staff_actor, recipient.pk, channel="mail")
    filed = drafting_filing.file_recipient(staff_actor, recipient.pk, channel="email")
    text = Request.objects.get(pk=reserved.request_id).request_text
    submission = Event.objects.get(request_id=reserved.request_id, event_type_id="submission")
    assert filed.channel == submission.channel == "email" and "VIA EMAIL" in text


def test_a_channel_named_when_filing_an_unreserved_letter_is_the_one_it_is_written_for(
    db, full_draft, agency, staff_actor
) -> None:
    recipient = drafts_service.add_recipient(full_draft.pk, agency_id=agency.pk)
    filed = drafting_filing.file_recipient(staff_actor, recipient.pk, channel="mail")
    text = Request.objects.get(pk=filed.request_id).request_text
    assert filed.channel == "mail" and "VIA U.S. MAIL" in text and "1 Test Street" in text


# ---- revisions: on save, and when a letter is started -------------------------------------------


def test_a_draft_points_at_the_template_as_it_stood_when_it_was_started(
    db, template, staff_user, agency, staff_actor
) -> None:
    """The false negative the P3b gate described, closed: fix a template, and a letter started
    before the fix must not end up stamped with the fixed revision."""
    library_service.add_template_item(
        template.pk, item_number="11", body="Added before", sort_order=11
    )
    started = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    revision = LetterTemplateRevision.objects.get(pk=started.source_revision_id)
    as_started = render.snapshot(drafting_filing_letter(template))
    assert revision.content == as_started
    assert revision.summary == library_service.STARTED_SUMMARY
    assert revision.created_by_user_id == staff_user.pk

    # Edit the template afterwards: no history is written, and nothing about the draft moves.
    library_service.add_template_item(
        template.pk, item_number="12", body="Added after", sort_order=12
    )
    assert LetterTemplateRevision.objects.count() == 1
    started.refresh_from_db()
    assert started.source_revision_id == revision.pk
    assert LetterTemplateRevision.objects.get(pk=revision.pk).content == as_started

    # Filing mints nothing and stamps the revision the letter was started from.
    recipient = drafts_service.add_recipient(started.pk, agency_id=agency.pk)
    filed = drafting_filing.file_recipient(staff_actor, recipient.pk)
    assert filed.template_revision_id == revision.pk
    assert LetterTemplateRevision.objects.count() == 1
    labels = [s["label"] for s in revision.content["sections"]]
    assert "11" in labels and "12" not in labels


def drafting_filing_letter(template):
    from portal.drafting.services.common import letter_of_template

    return letter_of_template(template)


def test_saving_records_who_and_what_and_an_unchanged_template_saves_nothing_new(
    db, template, staff_actor, staff_user
) -> None:
    with pytest.raises(ServiceError, match="what changed"):
        library_service.save_template(staff_actor, template.pk, " ")
    with pytest.raises(NotAllowed):
        library_service.save_template(Actor.for_system("a_job"), template.pk, "x")
    assert LetterTemplateRevision.objects.count() == 0

    first = library_service.save_template(staff_actor, template.pk, "  First version.  ")
    assert (first.revision, first.summary, first.created_by_user_id) == (
        1,
        "First version.",
        staff_user.pk,
    )
    again = library_service.save_template(staff_actor, template.pk, "Nothing changed.")
    assert again.pk == first.pk and LetterTemplateRevision.objects.count() == 1

    library_service.add_template_item(template.pk, item_number="11", body="New ask", sort_order=11)
    assert LetterTemplateRevision.objects.count() == 1, "editing writes no history"
    second = library_service.save_template(staff_actor, template.pk, "Added item 11.")
    assert second.revision == 2 and second.summary == "Added item 11."


def test_a_change_of_provision_alone_is_a_change_to_the_template(db, template, staff_actor) -> None:
    """A provision decides which deadlines a request gets, so it is part of what a template says
    even though it prints nowhere in the letter."""
    first = library_service.save_template(staff_actor, template.pk, "First.")
    library_service.update_template_item(
        template.items.get(item_number="2").pk, provision="footage"
    )
    second = library_service.save_template(staff_actor, template.pk, "Item 2 has its own clock.")
    assert second.revision == first.revision + 1
    item = next(s for s in second.content["sections"] if s["label"] == "2")
    assert item["provision"] == "footage"
    assert second.content["version"] == render.SNAPSHOT_VERSION


def test_the_revisions_door_shows_author_summary_and_how_many_letters_were_filed(
    db, template, agency, second_agency, staff_user
) -> None:
    client = Client()
    client.force_login(staff_user)
    base = f"{DRAFTING_PREFIX}/templates/{template.pk}"
    assert client.get(base).json()["saved"] is False
    assert post_json(client, f"{base}/save", {"summary": ""}).status_code == 422
    saved = post_json(client, f"{base}/save", {"summary": "First version."})
    assert saved.status_code == 200, saved.content
    assert client.get(base).json()["saved"] is True

    started = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    for target in (agency, second_agency):
        drafts_service.add_recipient(started.pk, agency_id=target.pk)
    one, two = LetterRecipient.objects.filter(draft_id=started.pk).order_by("created_at")
    drafting_filing.file_recipient(Actor.human(staff_user), one.pk)
    drafting_filing.reserve_recipient(Actor.human(staff_user), two.pk)  # reserved is not filed

    (row,) = client.get(f"{base}/revisions").json()
    assert (row["revision"], row["summary"], row["created_by_name"]) == (
        1,
        "First version.",
        staff_user.name,
    )
    assert row["filed_count"] == 1
    # The lists carry the same counts, so "used" needs no call per row.
    (listed,) = [
        t for t in client.get(f"{DRAFTING_PREFIX}/templates").json() if t["id"] == str(template.pk)
    ]
    assert listed["filed_count"] == 1
    assert [b["used_in"] for b in client.get(f"{DRAFTING_PREFIX}/blocks").json()] == [1]
    patch_json(
        client, f"{DRAFTING_PREFIX}/template-items/{template.items.first().pk}", {"body": "x"}
    )
    assert client.get(base).json()["saved"] is False


# ---- one order for blocks and items -------------------------------------------------------------


def test_a_block_placed_after_the_last_item_renders_after_it_and_survives_the_snapshot(
    db, template, block, staff_actor, staff_user
) -> None:
    """Until P10 every block came ahead of every item. The fixture's items sit at 0, 1, 2, 9."""
    middle = library_service.create_block(name="Between", body="BETWEEN-ITEMS")
    library_service.set_template_blocks(template.pk, [middle.pk, block.pk], sort_orders=[2, 50])

    def order(letter):
        return [(s.kind, s.label) for s in letter.sections() if s.kind in ("block", "item")]

    from portal.drafting.services.common import letter_of_draft, letter_of_template

    kinds = order(letter_of_template(template))
    assert kinds == [
        ("item", "2"),
        ("block", "Between"),
        ("item", "2a"),
        ("item", "10"),
        ("block", "Record definition"),
    ]

    revision = library_service.save_template(staff_actor, template.pk, "Blocks placed.")
    frozen = [(s["kind"], s["label"]) for s in revision.content["sections"]]
    assert [f for f in frozen if f[0] in ("block", "item")] == kinds
    stored = LetterTemplateRevision.objects.get(pk=revision.pk).content
    assert stored == render.snapshot(letter_of_template(template)), "survives the jsonb round trip"

    # A draft started from it keeps the same places, and so does the letter that goes out.
    started = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    text = render.render_text(letter_of_draft(started))
    assert text.index("Widget disposal manifests") < text.index("'Record' means")
    assert text.index("BETWEEN-ITEMS") < text.index("Body-worn footage only")
    assert text.index("Widget camera footage") < text.index("BETWEEN-ITEMS")


def test_block_positions_must_match_the_blocks_and_default_to_the_order_given(
    db, template, block
) -> None:
    other = library_service.create_block(name="Other", body="other")
    with pytest.raises(ServiceError, match="one position for each block"):
        library_service.set_template_blocks(template.pk, [block.pk, other.pk], sort_orders=[1])
    rows = library_service.set_template_blocks(template.pk, [other.pk, block.pk])
    assert [(r.shared_block_id, r.sort_order) for r in rows] == [(other.pk, -2), (block.pk, -1)]


def test_the_letters_furniture_still_brackets_the_body() -> None:
    letter = render.letter_from_parts(
        subject="S",
        shell=render.Shell(letterhead="LH", opening_body="OPEN", closing_body="CLOSE"),
        items=[render.Item("1", "ask", sort_order=5)],
        citations=[render.Citation("1", "cite", sort_order=0)],
        blocks=[render.Block("late", "late prose", sort_order=99)],
    )
    assert [s.kind for s in letter.sections()] == [
        "letterhead",
        "subject",
        "opening",
        "item",
        "block",
        "citation",
        "closing",
    ]


# ---- the placeholders the design's letter uses ---------------------------------------------------


def test_every_placeholder_resolves_from_the_agency_the_channel_and_the_date(
    db, agency, staff_actor
) -> None:
    values = _values(agency, channel="email")
    assert values == {
        "AGENCY_FULL": "Test Agency",
        "AGENCY_ABBR": "TA",
        "RECIPIENT_OFFICE": "Records Unit",
        "ADDRESS": "records@test-agency.example.org",
        "AGENCY_STREETADDRESS": "1 Test Street",
        "AGENCY_CITYSTATEZIP": "Testville, TL 00000",
        "CHANNEL_METHOD": "EMAIL",
        # 18:00 UTC is 10:00 the same civil date in TESTLAW's zone.
        "FILING_DATE": "February 10, 2026",
    }
    assert set(values) == set(render.PLACEHOLDER_NAMES)


def test_the_address_and_the_channel_line_follow_the_channel(db, agency, staff_actor) -> None:
    by_post = _values(agency, channel="mail")
    assert by_post["ADDRESS"] == "1 Test Street\nTestville, TL 00000"
    assert by_post["CHANNEL_METHOD"] == "U.S. MAIL"

    form = sendable_agency(
        staff_actor,
        "Web Form Agency",
        portal_url="https://forms.example.org/prr",
    )
    directory.update_agency(
        staff_actor, form.pk, "Files by web form.", filing_method="portal", portal_kind="Web form"
    )
    form.refresh_from_db()
    by_form = _values(form, channel="portal")
    assert (by_form["ADDRESS"], by_form["CHANNEL_METHOD"]) == (
        "https://forms.example.org/prr",
        "WEB FORM",
    )
    # A portal whose kind is not on record resolves to nothing, and so is reported, not invented.
    directory.update_agency(staff_actor, form.pk, "Kind unknown.", portal_kind=None)
    form.refresh_from_db()
    assert "CHANNEL_METHOD" not in _values(form, channel="portal")
    # A phone call has no channel line of its own either.
    assert "CHANNEL_METHOD" not in _values(agency, channel="call")


def test_the_recipients_own_contact_and_overrides_win(db, full_draft, agency, staff_actor) -> None:
    contact = directory.create_contact(staff_actor, agency.pk, name="Custodian of Records")
    recipient = drafts_service.add_recipient(
        full_draft.pk,
        agency_id=agency.pk,
        agency_contact_id=contact.pk,
        placeholder_overrides={"AGENCY_ABBR": "T.A."},
    )
    values = drafting_filing.placeholders_for(recipient, agency, when=WHEN)
    assert values["RECIPIENT_OFFICE"] == "Custodian of Records"
    assert values["AGENCY_ABBR"] == "T.A."


def test_the_date_is_the_agencys_own_and_is_not_moved_by_filing_later(
    db, full_draft, agency, bare_agency, staff_actor
) -> None:
    # 03:00 UTC on the 11th is still the 10th where the agency is.
    late = datetime(2026, 2, 11, 3, 0, tzinfo=UTC)
    assert drafting_filing.agency_values(agency, channel="email", when=late)["FILING_DATE"] == (
        "February 10, 2026"
    )
    # No calendar mapped for the agency's law: the UTC date, not a crash.
    unmapped = directory.create_agency(staff_actor, name="Unmapped", jurisdiction="Testland")
    assert drafting_filing.agency_values(unmapped, channel="email", when=late)["FILING_DATE"] == (
        "February 11, 2026"
    )

    recipient = drafts_service.add_recipient(full_draft.pk, agency_id=agency.pk)
    reserved = drafting_filing.reserve_recipient(staff_actor, recipient.pk)
    dated = Request.objects.get(pk=reserved.request_id).request_text
    assert "VIA EMAIL" in dated and "{{" not in dated
    drafting_filing.file_recipient(
        staff_actor, recipient.pk, filed_at=datetime(2031, 6, 1, 18, 0, tzinfo=UTC)
    )
    assert Request.objects.get(pk=reserved.request_id).request_text == dated


def test_preview_as_reads_the_draft_for_an_agency_that_is_not_a_recipient_yet(
    db, full_draft, agency, bare_agency, staff_user
) -> None:
    client = Client()
    client.force_login(staff_user)
    base = f"{DRAFTING_PREFIX}/drafts/{full_draft.pk}/preview"
    shown = client.get(f"{base}?agency_id={agency.pk}")
    assert shown.status_code == 200, shown.content
    body = shown.json()
    assert body["recipient_id"] is None and body["unresolved"] == []
    assert "VIA EMAIL" in body["text"] and "Test Agency (TA)" in body["text"]
    by_post = client.get(f"{base}?agency_id={agency.pk}&channel=mail").json()
    assert "VIA U.S. MAIL" in by_post["text"]
    blank = client.get(f"{base}?agency_id={bare_agency.pk}").json()
    assert "CHANNEL_METHOD" in blank["unresolved"] and "RECIPIENT_OFFICE" in blank["unresolved"]
    assert not LetterRecipient.objects.exists()
    assert client.get(f"{base}?agency_id={full_draft.pk}").status_code == 404


# ---- recipients arrive ready, or held with the reason --------------------------------------------


def test_a_new_recipients_channel_defaults_from_the_agency(db, draft, agency, staff_actor) -> None:
    row = drafts_service.add_recipient(draft.pk, agency_id=agency.pk)
    assert (row.channel, row.status, row.hold_reason) == ("email", "pending", None)


def test_a_recipient_that_cannot_go_out_arrives_held_and_says_why(
    db, draft, bare_agency, second_agency, staff_actor
) -> None:
    undecided = drafts_service.add_recipient(draft.pk, agency_id=bare_agency.pk)
    assert (undecided.status, undecided.hold_reason) == ("held", drafts_service.NO_FILING_METHOD)

    unverified = directory.create_agency(
        staff_actor,
        name="Unverified Agency",
        jurisdiction="Testland",
        default_request_email="records@unverified.example.org",
        filing_method="email",
    )
    by_email = drafts_service.add_recipient(draft.pk, agency_id=unverified.pk)
    assert (by_email.status, by_email.hold_reason) == ("held", drafts_service.NO_VERIFIED_EMAIL)

    # The caller's own reason wins, and a channel that needs no address is not held for one.
    own = drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk, hold_reason="Wait.")
    assert own.hold_reason == "Wait."
    post = sendable_agency(staff_actor, "Postal Agency")
    assert (
        drafts_service.add_recipient(draft.pk, agency_id=post.pk, channel="mail").status
        == "pending"
    )


def test_recipients_through_the_api_need_no_channel(db, draft, agency, staff_user) -> None:
    client = Client()
    client.force_login(staff_user)
    made = post_json(
        client, f"{DRAFTING_PREFIX}/drafts/{draft.pk}/recipients", {"agency_id": str(agency.pk)}
    )
    assert made.status_code == 201, made.content
    assert made.json()["channel"] == "email" and made.json()["request_status"] is None
    skipped = post_json(client, f"{DRAFTING_PREFIX}/recipients/{made.json()['id']}/skip", {})
    assert skipped.status_code == 200 and skipped.json()["status"] == "skipped"


# ---- P9 still holds across the new doors ---------------------------------------------------------


def test_reserving_a_privileged_letter_puts_the_person_reserving_on_its_requests_list(
    db, template, agency, staff_user, staff_actor, colleague_user
) -> None:
    sealed = drafts_service.create_draft(
        from_template_id=template.pk, sensitivity="privileged", created_by_user_id=staff_user.pk
    )
    recipient = drafts_service.add_recipient(sealed.pk, agency_id=agency.pk)
    reserved = drafting_filing.reserve_recipient(staff_actor, recipient.pk)
    request = Request.objects.get(pk=reserved.request_id)
    assert request.sensitivity == "privileged"
    assert [row.user_id for row in access_service.active_access(request.pk)] == [staff_user.pk]
    outsider = Client()
    outsider.force_login(colleague_user)
    for path in ("reserve", "file", "skip"):
        refused = post_json(outsider, f"{DRAFTING_PREFIX}/recipients/{recipient.pk}/{path}", {})
        assert refused.status_code == 403 and refused.json()["code"] == "privileged"


# ---- found by the second-pass review (notes/portal/2026-10-05_p10_review_reviewer.md) ----------


def _state(recipient):
    row = LetterRecipient.objects.get(pk=recipient.pk)
    request = Request.objects.get(pk=row.request_id) if row.request_id else None
    return row.status, request.current_status_id if request else None


def test_a_reserved_letter_withdrawn_from_its_requests_page_becomes_skipped_not_stuck(
    db, draft, agency, second_agency, staff_actor
) -> None:
    """F1. Only filing is announced across the seam. Withdrawn from the request's own page, the
    recipient sat `pending` for good: not fileable, not skippable, retried by every wave."""
    from portal.records.services import status as status_service

    one = drafts_service.add_recipient(draft.pk, agency_id=agency.pk)
    two = drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk)
    reserved = drafting_filing.reserve_recipient(staff_actor, one.pk)
    status_service.override_status(staff_actor, reserved.request_id, "withdrawn", "Not needed.")
    assert _state(one) == ("pending", "withdrawn"), "the lag the reconcile step exists for"

    # Any action brings it into line. Skipping needs no note and withdraws nothing a second time.
    assert drafting_filing.skip_recipient(staff_actor, one.pk).status == "skipped"
    assert _state(one) == ("skipped", "withdrawn")
    assert (
        StatusChange.objects.filter(request_id=reserved.request_id, status_id="withdrawn").count()
        == 1
    )
    with pytest.raises(InvalidTransition, match="request withdrawn"):
        drafting_filing.file_recipient(staff_actor, one.pk)

    # A wave reports it as skipped, files the other, and does not keep retrying it.
    wave = drafting_filing.file_draft(staff_actor, draft.pk)
    by_id = {o.recipient_id: o for o in wave.outcomes}
    assert by_id[one.pk].reason == "already skipped" and by_id[two.pk].filed is True


def test_a_wave_alone_reconciles_a_letter_withdrawn_or_moved_on_from_its_requests_page(
    db, draft, agency, second_agency, staff_actor
) -> None:
    from portal.records.services import status as status_service

    one = drafts_service.add_recipient(draft.pk, agency_id=agency.pk)
    two = drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk)
    first = drafting_filing.reserve_recipient(staff_actor, one.pk)
    second = drafting_filing.reserve_recipient(staff_actor, two.pk)
    status_service.override_status(staff_actor, first.request_id, "withdrawn", "Not needed.")
    # Overridden straight to a later status with no filing: the letter is evidently out.
    status_service.override_status(staff_actor, second.request_id, "acknowledged", "They replied.")

    wave = drafting_filing.file_draft(staff_actor, draft.pk)
    assert wave.filed_count == 0
    assert _state(one) == ("skipped", "withdrawn")
    assert _state(two) == ("filed", "acknowledged")
    assert LetterRecipient.objects.get(pk=two.pk).filed_at is not None
    with pytest.raises(InvalidTransition, match="already been filed"):
        drafting_filing.file_recipient(staff_actor, two.pk)


def test_a_recipient_cannot_leave_hold_into_a_letter_that_cannot_go_out(
    db, draft, bare_agency, staff_actor
) -> None:
    """F3. The stand-in channel of an agency with no filing method was acted on the moment someone
    set the status back: a submission "by email" to an agency with no address."""
    held = drafts_service.add_recipient(draft.pk, agency_id=bare_agency.pk)
    with pytest.raises(ServiceError, match="no filing method"):
        drafts_service.update_recipient(held.pk, status="pending")
    with pytest.raises(ServiceError, match="no verified address"):
        drafts_service.update_recipient(held.pk, status="pending", channel="email")
    assert LetterRecipient.objects.get(pk=held.pk).status == "held"
    released = drafts_service.update_recipient(held.pk, status="pending", channel="mail")
    assert (released.status, released.channel, released.hold_reason) == ("pending", "mail", None)
    # An empty reason is no reason: it does not get an agency past the default holds.
    other = directory.create_agency(staff_actor, name="Other Bare", jurisdiction="Testland")
    assert (
        drafts_service.add_recipient(draft.pk, agency_id=other.pk, hold_reason=" ").status == "held"
    )


def test_the_requests_subject_and_items_carry_no_placeholders_either(
    db, agency, staff_user, staff_actor
) -> None:
    """F6. The docket and the request page show these, not the letter text."""
    made = drafts_service.create_draft(
        subject="Records request to {{AGENCY_FULL}}", created_by_user_id=staff_user.pk
    )
    drafts_service.add_draft_item(
        made.pk, item_number="1", body="All records held by {{AGENCY_ABBR}}"
    )
    recipient = drafts_service.add_recipient(made.pk, agency_id=agency.pk)
    reserved = drafting_filing.reserve_recipient(staff_actor, recipient.pk)
    request = Request.objects.get(pk=reserved.request_id)
    item = RequestItem.objects.get(request_id=request.pk)
    assert request.subject == "Records request to Test Agency"
    assert item.description == "All records held by TA"
    assert "{{" not in request.request_text


def test_save_keeps_the_savers_name_and_summary_when_a_letter_was_started_first(
    db, template, staff_actor, staff_user, colleague_user
) -> None:
    """F8. The colleague's letter froze the edit under the fixed summary; Save then returned that
    row and the saver's line on what changed was thrown away, for good."""
    drafts_service.create_draft(from_template_id=template.pk, created_by_user_id=colleague_user.pk)
    started = LetterTemplateRevision.objects.get()
    saved = library_service.save_template(staff_actor, template.pk, "Tightened item 2.")
    assert saved.pk != started.pk and saved.revision == started.revision + 1
    assert (saved.summary, saved.created_by_user_id) == ("Tightened item 2.", staff_user.pk)
    assert saved.content == started.content
    # A second Save of the same state has something saved to return, and adds nothing.
    assert library_service.save_template(staff_actor, template.pk, "Again.").pk == saved.pk
    # And a letter started now points at the saved revision.
    later = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    assert later.source_revision_id == saved.pk


def test_unplaced_blocks_stay_ahead_of_every_item_and_tied_blocks_order_the_same_everywhere(
    db, staff_user
) -> None:
    """F9. Left unplaced, blocks took 0, 1, 2 and scattered between items that also start at 0;
    and two blocks at one position sorted by name in the revision and by text in the draft."""
    from portal.drafting.services.common import letter_of_draft, letter_of_template

    template = library_service.create_template(name="Order")
    for number in ("1", "2", "3"):
        library_service.add_template_item(
            template.pk, item_number=number, body=f"ask {number}", sort_order=int(number) - 1
        )
    zeta = library_service.create_block(name="Zeta", body="AAA zeta")
    alpha = library_service.create_block(name="Alpha", body="ZZZ alpha")
    library_service.set_template_blocks(template.pk, [zeta.pk, alpha.pk])
    kinds = [s.kind for s in letter_of_template(template).sections()]
    assert kinds == ["subject", "block", "block", "item", "item", "item"]

    library_service.set_template_blocks(template.pk, [zeta.pk, alpha.pk], sort_orders=[1, 1])
    started = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    in_revision = [
        s["body"]
        for s in LetterTemplateRevision.objects.get(pk=started.source_revision_id).content[
            "sections"
        ]
        if s["kind"] in ("block", "item")
    ]
    in_letter = [s.body for s in letter_of_draft(started).sections() if s.kind in ("block", "item")]
    assert in_letter == in_revision == ["ask 1", "AAA zeta", "ZZZ alpha", "ask 2", "ask 3"]


def test_a_draft_is_copied_from_its_revision_not_from_whatever_the_template_says_a_moment_later(
    db, template, staff_user, monkeypatch
) -> None:
    """F5. Minting locks the template row, but an item is edited without touching it, so a second
    read could differ from what was just frozen. An edit is slipped in right after the mint."""
    real = library_service.mint_or_reuse_revision

    def mint_then_edit(template_id, **kwargs):
        revision = real(template_id, **kwargs)
        library_service.update_template_item(
            template.items.get(item_number="2").pk, body="EDITED after the freeze"
        )
        return revision

    monkeypatch.setattr(library_service, "mint_or_reuse_revision", mint_then_edit)
    started = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    revision = LetterTemplateRevision.objects.get(pk=started.source_revision_id)
    frozen = {s["label"]: s["body"] for s in revision.content["sections"] if s["kind"] == "item"}
    copied = dict(started.items.exclude(item_number="").values_list("item_number", "body"))
    assert copied == frozen and copied["2"] == "Widget camera footage"
    # Parents and citations came across too.
    assert (
        started.items.get(item_number="2a").parent_item_id == started.items.get(item_number="2").pk
    )
    assert started.citations.count() == 1


@pytest.mark.parametrize(
    ("body", "overrides"),
    [
        ("unclosed {{ADDRESS and more", None),
        ("a stray }} here", None),
        ("To {{AGENCY_FULL}}", {"AGENCY_FULL": "   "}),
        ("To {{AGENCY_FULL}}", {"AGENCY_FULL": "half {{ open"}),
    ],
)
def test_a_letter_with_a_malformed_or_blank_placeholder_is_not_reserved(
    db, agency, staff_user, staff_actor, body, overrides
) -> None:
    """F10."""
    made = drafts_service.create_draft(subject="Braces", created_by_user_id=staff_user.pk)
    drafts_service.add_draft_item(made.pk, item_number="1", body=body)
    recipient = drafts_service.add_recipient(
        made.pk, agency_id=agency.pk, placeholder_overrides=overrides
    )
    assert drafting_filing.preview(recipient.pk).unresolved != ()
    with pytest.raises(ServiceError, match="still has blanks"):
        drafting_filing.reserve_recipient(staff_actor, recipient.pk)
    assert Request.objects.count() == 0


def test_an_override_must_be_text(db, draft, agency, recipient) -> None:
    with pytest.raises(ServiceError, match="must be text"):
        drafts_service.update_recipient(recipient.pk, placeholder_overrides={"AGENCY_FULL": ["a"]})
    with pytest.raises(ValueError, match="must be text"):
        render.agency_placeholders(agency_name="A", overrides={"AGENCY_FULL": 3})


def test_a_template_with_a_saved_version_says_why_it_cannot_be_deleted(
    db, template, staff_actor
) -> None:
    """F12. Revisions used to exist only because of a filing; the refusal still said so."""
    library_service.save_template(staff_actor, template.pk, "First.")
    with pytest.raises(InvalidTransition) as refused:
        library_service.delete_template(template.pk)
    assert "a version of it was saved" in str(refused.value)
    assert "has been filed from" not in str(refused.value)


def test_a_draft_from_before_drafts_recorded_their_revision_still_gets_one_at_reservation(
    db, draft, recipient, staff_actor
) -> None:
    """F13."""
    type(draft).objects.filter(pk=draft.pk).update(source_revision_id=None)
    reserved = drafting_filing.reserve_recipient(staff_actor, recipient.pk)
    assert reserved.template_revision_id is not None
    draft.refresh_from_db()
    assert draft.source_revision_id == reserved.template_revision_id
