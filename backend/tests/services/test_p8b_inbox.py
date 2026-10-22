"""The Inbox's three buckets: who is in each, how a message moves between them, and that the counts
for the navigation are the very rows the list shows."""

from datetime import UTC, datetime, timedelta

import pytest
from portal.records.models import Source
from portal.records.services import directory
from portal.records.services import events as events_service
from portal.records.services import inbox as inbox_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services.common import ServiceError

from tests.p8b_helpers import add_file, attach, make_source

pytestmark = pytest.mark.usefixtures("as_each_role")


def _ids(page) -> set:
    return {row.source.pk for row in page.rows}


def _in(staff_user, bucket, source, **kw) -> bool:
    return source.pk in _ids(inbox_service.inbox(staff_user.pk, bucket, **kw))


def _membership(staff_user, source) -> set[str]:
    return {b for b in inbox_service.BUCKETS if _in(staff_user, b, source)}


# ---- who is in each bucket ---------------------------------------------------------------------


def test_unplaced_mail_is_in_all_and_only_in_flagged_when_it_is_flagged(
    staff_actor, staff_user
) -> None:
    plain = make_source(staff_actor)
    flagged = make_source(staff_actor, flag=("no_matching_request", "no alias"))
    # Mail with no event and no flag is inbound mail in flight: it is in All and nowhere else.
    assert _membership(staff_user, plain) == {"all"}
    assert _membership(staff_user, flagged) == {"flagged", "all"}


def test_mail_on_a_request_is_new_until_somebody_reviews_it(
    staff_actor, staff_user, reserved
) -> None:
    source = make_source(staff_actor)
    attach(staff_actor, reserved, source)
    assert _membership(staff_user, source) == {"new", "all"}


def test_review_moves_a_message_from_new_to_all_and_unreview_moves_it_back(
    staff_actor, staff_user, reserved
) -> None:
    source = make_source(staff_actor)
    attach(staff_actor, reserved, source)
    assert _membership(staff_user, source) == {"new", "all"}

    sources_service.mark_source_reviewed(staff_actor, source.pk)
    assert _membership(staff_user, source) == {"all"}

    sources_service.mark_source_unreviewed(staff_actor, source.pk)
    assert _membership(staff_user, source) == {"new", "all"}


def test_review_moves_a_flagged_message_out_of_flagged_and_unreview_brings_it_back(
    staff_actor, staff_user
) -> None:
    source = make_source(staff_actor, flag=("other", "odd"))
    assert _membership(staff_user, source) == {"flagged", "all"}
    sources_service.mark_source_reviewed(staff_actor, source.pk)
    assert _membership(staff_user, source) == {"all"}
    # Nothing is attached, so unreviewing it puts it back in front of somebody.
    sources_service.mark_source_unreviewed(staff_actor, source.pk)
    assert _membership(staff_user, source) == {"flagged", "all"}


def test_a_flagged_message_on_a_request_is_flagged_and_not_new(
    staff_actor, staff_user, reserved
) -> None:
    """The auto-matched event that also may cover other requests: it needs a decision first."""
    source = make_source(staff_actor)
    attach(staff_actor, reserved, source)
    sources_service.flag_source(
        staff_actor, source.pk, "Names two other requests.", "may_cover_others"
    )
    assert _membership(staff_user, source) == {"flagged", "all"}
    sources_service.mark_source_reviewed(staff_actor, source.pk)
    assert _membership(staff_user, source) == {"all"}


def test_flagged_means_flagged_and_not_reviewed_even_for_a_row_that_is_somehow_both(
    staff_actor, staff_user
) -> None:
    """The services never leave a message flagged AND reviewed, but the definition is "flagged and
    not reviewed" and the query must say so rather than lean on that invariant."""
    source = make_source(staff_actor, flag=("other", "x"))
    Source.objects.filter(pk=source.pk).update(
        reviewed_at=datetime(2026, 3, 3, tzinfo=UTC), reviewed_by_id=staff_user.pk
    )
    assert _membership(staff_user, source) == {"all"}
    # The badge says the same as the list: a second, looser count of "flagged" would say 1.
    assert inbox_service.bucket_counts(staff_user.pk)["flagged"] == 0


def test_a_resolved_message_is_in_all_only(staff_actor, staff_user) -> None:
    source = make_source(staff_actor, flag=("other", "x"))
    sources_service.resolve_without_attaching(staff_actor, source.pk, "duplicate", "n")
    assert _membership(staff_user, source) == {"all"}


def test_a_message_whose_only_event_was_voided_is_not_new(
    staff_actor, staff_user, reserved
) -> None:
    source = make_source(staff_actor)
    event = attach(staff_actor, reserved, source)
    events_service.void_event(staff_actor, event.pk, "wrong request")
    # Inbound still (it arrived), unreviewed and unflagged, but it lands on no request now.
    assert _membership(staff_user, source) == {"all"}


def test_a_message_we_sent_is_in_no_bucket(staff_actor, staff_user, reserved) -> None:
    """Only outbound events: a sent copy, not mail that came in. (Sent arrives with P11.)"""
    sent = make_source(staff_actor)
    attach(staff_actor, reserved, sent, event_type="follow_up", direction="outbound")
    assert _membership(staff_user, sent) == set()


def test_a_message_with_an_outbound_and_an_inbound_event_is_inbound(
    staff_actor, staff_user, reserved
) -> None:
    both = make_source(staff_actor)
    attach(staff_actor, reserved, both, direction="outbound", event_type="follow_up")
    attach(staff_actor, reserved, both)
    assert _membership(staff_user, both) == {"new", "all"}


# ---- the counts and the list are one query -----------------------------------------------------


def test_the_counts_are_the_rows_of_the_unfiltered_buckets(
    staff_actor, staff_user, reserved
) -> None:
    flagged = [make_source(staff_actor, flag=("other", f"f{i}")) for i in range(2)]
    fresh = [make_source(staff_actor) for _ in range(3)]
    for source in fresh:
        attach(staff_actor, reserved, source)
    sources_service.mark_source_reviewed(staff_actor, fresh[0].pk)
    make_source(staff_actor)  # unplaced, unflagged: All only

    page = inbox_service.inbox(staff_user.pk, "all")
    assert page.counts == {"flagged": 2, "new": 2, "all": 6}
    for bucket in inbox_service.BUCKETS:
        listed = inbox_service.inbox(staff_user.pk, bucket)
        assert page.counts[bucket] == len(listed.rows) == listed.total
    assert inbox_service.bucket_counts(staff_user.pk) == page.counts
    assert {s.pk for s in flagged} <= _ids(inbox_service.inbox(staff_user.pk, "flagged"))


def _a_mailbox_where_every_bucket_holds_something_different(staff_actor, staff_user, reserved):
    """One message for each clause of each bucket's definition, so that a count written any other
    way than the list is written lands on a different number for at least one bucket. Returns
    {what it is: (source, the buckets it is in)}, the buckets written out by hand from the
    definitions in `inbox.py`'s docstring and nothing the code under test says."""
    made: dict[str, tuple[Source, set[str]]] = {}

    def mail(name, buckets, *, flag=None):
        source = make_source(staff_actor, flag=flag)
        made[name] = (source, set(buckets))
        return source

    # Not placed on any request.
    mail("unplaced and flagged", {"flagged", "all"}, flag=("other", "x"))
    mail("unplaced, nothing else", {"all"})
    somehow_both = mail("flagged and reviewed", {"all"}, flag=("other", "y"))
    Source.objects.filter(pk=somehow_both.pk).update(
        reviewed_at=datetime(2026, 3, 3, tzinfo=UTC), reviewed_by_id=staff_user.pk
    )
    resolved = mail("resolved without attaching", {"all"}, flag=("other", "z"))
    sources_service.resolve_without_attaching(staff_actor, resolved.pk, "duplicate", "same letter")

    # On the request.
    attach(staff_actor, reserved, mail("new", {"new", "all"}))
    reviewed = mail("attached and reviewed", {"all"})
    attach(staff_actor, reserved, reviewed)
    sources_service.mark_source_reviewed(staff_actor, reviewed.pk)
    flagged = mail("attached and flagged", {"flagged", "all"})
    attach(staff_actor, reserved, flagged)
    sources_service.flag_source(staff_actor, flagged.pk, "Names two others.", "may_cover_others")

    # Voided, and with and without a second, live event.
    voided = mail("attached, then voided", {"all"})
    events_service.void_event(staff_actor, attach(staff_actor, reserved, voided).pk, "wrong one")
    half = mail("one event voided, one live", {"new", "all"})
    first = attach(staff_actor, reserved, half)
    attach(staff_actor, reserved, half)
    events_service.void_event(staff_actor, first.pk, "wrong one")

    # Direction.
    sent = mail("only outbound", set())
    attach(staff_actor, reserved, sent, event_type="follow_up", direction="outbound")
    both_ways = mail("outbound and inbound", {"new", "all"})
    attach(staff_actor, reserved, both_ways, event_type="follow_up", direction="outbound")
    attach(staff_actor, reserved, both_ways)
    return made


def test_every_bucket_count_is_the_number_of_rows_the_list_of_that_bucket_returns(
    staff_actor, staff_user, reserved
) -> None:
    """P8b review F9. The code builds the badge and the list from one function, but a second,
    slightly different count passed the whole suite, because the only counts test ran on a
    mailbox where review had cleared every flag and every bucket's loose version agreed with its
    strict one. Here the buckets differ, clause by clause, and the badge must equal the list."""
    made = _a_mailbox_where_every_bucket_holds_something_different(
        staff_actor, staff_user, reserved
    )

    for name, (source, buckets) in made.items():
        assert _membership(staff_user, source) == buckets, name

    counts = inbox_service.bucket_counts(staff_user.pk)
    for bucket in inbox_service.BUCKETS:
        listed = inbox_service.inbox(staff_user.pk, bucket)
        assert counts[bucket] == len(listed.rows) == listed.total == listed.counts[bucket], bucket
        assert counts[bucket] == sum(bucket in b for _, b in made.values()), bucket
    # The mailbox is only a test of the pin if the buckets are not the same size.
    assert counts == {"flagged": 2, "new": 3, "all": 10}


def test_the_counts_are_computed_by_the_function_that_builds_the_lists(
    staff_actor, staff_user, reserved, monkeypatch
) -> None:
    """Whatever `_bucket_sources` says a bucket holds is what the list shows and what the badge
    counts. Narrow it and BOTH must follow, which no second implementation of a count could do,
    whichever clause it got wrong and whichever bucket it was written for."""
    flagged = [make_source(staff_actor, flag=("other", f"f{i}")) for i in range(2)]
    fresh = [make_source(staff_actor) for _ in range(2)]
    for source in fresh:
        attach(staff_actor, reserved, source)
    assert inbox_service.bucket_counts(staff_user.pk) == {"flagged": 2, "new": 2, "all": 4}

    gone = {flagged[1].pk, fresh[1].pk}
    real = inbox_service._bucket_sources
    monkeypatch.setattr(
        inbox_service, "_bucket_sources", lambda bucket: real(bucket).exclude(pk__in=gone)
    )
    page = inbox_service.inbox(staff_user.pk, "all")
    assert _ids(page) == {flagged[0].pk, fresh[0].pk}
    assert page.counts == {"flagged": 1, "new": 1, "all": 2}
    assert inbox_service.bucket_counts(staff_user.pk) == page.counts


def test_counts_ignore_the_filters_and_the_search_but_the_total_does_not(
    staff_actor, staff_user, reserved
) -> None:
    for i in range(3):
        attach(staff_actor, reserved, make_source(staff_actor), body=f"message {i}")
    page = inbox_service.inbox(staff_user.pk, "new", q="message 1")
    assert page.total == 1 and len(page.rows) == 1
    assert page.counts["new"] == 3


def test_an_unknown_bucket_is_refused(staff_user) -> None:
    with pytest.raises(ServiceError, match="Unknown inbox bucket"):
        inbox_service.inbox(staff_user.pk, "sent")


# ---- the filters -------------------------------------------------------------------------------


def test_newest_first_and_paging(staff_actor, staff_user) -> None:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    made = [make_source(staff_actor, captured_at=base + timedelta(hours=h)) for h in (3, 1, 2)]
    full = inbox_service.inbox(staff_user.pk, "all")
    assert [r.source.pk for r in full.rows] == [made[0].pk, made[2].pk, made[1].pk]
    page = inbox_service.inbox(staff_user.pk, "all", limit=1, offset=1)
    assert [r.source.pk for r in page.rows] == [made[2].pk]
    assert page.total == 3


def test_filters_by_request_agency_and_assignee(
    staff_actor, staff_user, colleague_user, reserved, agency
) -> None:
    other_agency = directory.create_agency(
        staff_actor, name="Another Agency", jurisdiction="Testland", records_law="TESTLAW"
    )
    elsewhere = requests_service.reserve_request(
        staff_actor,
        agency_id=other_agency.pk,
        requester_user_id=staff_user.pk,
        subject="Elsewhere",
        request_text="Body.",
    )
    here, there, loose = (make_source(staff_actor) for _ in range(3))
    attach(staff_actor, reserved, here)
    attach(staff_actor, elsewhere, there)
    sources_service.assign_source(staff_actor, loose.pk, colleague_user.pk)

    def listed(**filters):
        return _ids(inbox_service.inbox(staff_user.pk, "all", **filters))

    assert listed(request_id=reserved.pk) == {here.pk}
    assert listed(agency_id=agency.pk) == {here.pk}
    assert listed(agency_id=other_agency.pk) == {there.pk}
    assert listed(assignee_id=colleague_user.pk) == {loose.pk}
    assert listed(assignee_id=staff_user.pk) == set()
    page = inbox_service.inbox(staff_user.pk, "all", assignee_id=colleague_user.pk)
    assert page.rows[0].assignee_name == "Colleague Person"


def test_a_voided_event_does_not_place_a_message_for_the_filters(
    staff_actor, staff_user, reserved
) -> None:
    source = make_source(staff_actor)
    event = attach(staff_actor, reserved, source)
    events_service.void_event(staff_actor, event.pk, "wrong request")
    assert not _in(staff_user, "all", source, request_id=reserved.pk)


def test_search_finds_a_message_by_what_a_person_would_look_for(
    staff_actor, staff_user, reserved
) -> None:
    by_flag = make_source(staff_actor, flag=("other", "Probably the Lindgren matter"))
    by_file = make_source(staff_actor)
    add_file(staff_actor, by_file, "quarterly-audit-extract.xlsx")
    by_event = make_source(staff_actor)
    attach(staff_actor, reserved, by_event, body="We are producing the vendor agreement.")
    by_note = make_source(staff_actor)
    sources_service.add_source_note(staff_actor, by_note.pk, "Ask Maya about the siting map.")
    by_meta = make_source(
        staff_actor, metadata={"from": "l.alvarez@example.test", "subject": "Hello"}
    )
    plain = make_source(staff_actor)

    def found(q):
        return _ids(inbox_service.inbox(staff_user.pk, "all", q=q))

    assert found("lindgren") == {by_flag.pk}
    assert found("AUDIT-EXTRACT") == {by_file.pk}
    assert found("vendor agreement") == {by_event.pk}
    assert found(reserved.tracking_number) == {by_event.pk}
    assert found("Test Agency") == {by_event.pk}
    assert found("siting map") == {by_note.pk}
    assert found("alvarez@example") == {by_meta.pk}
    assert found("zzz-no-such-text") == set()
    # A blank search is no search.
    assert found("   ") == {by_flag.pk, by_file.pk, by_event.pk, by_note.pk, by_meta.pk, plain.pk}


def test_a_voided_note_is_not_searched(staff_actor, staff_user) -> None:
    source = make_source(staff_actor)
    note = sources_service.add_source_note(staff_actor, source.pk, "Ask the unicorn.")
    assert _ids(inbox_service.inbox(staff_user.pk, "all", q="unicorn")) == {source.pk}
    sources_service.void_source_note(staff_actor, note.pk, "typo")
    assert _ids(inbox_service.inbox(staff_user.pk, "all", q="unicorn")) == set()


def test_a_row_carries_its_files_its_events_its_notes_and_who_has_it(
    staff_actor, staff_user, colleague_user, reserved
) -> None:
    source = make_source(staff_actor)
    add_file(staff_actor, source, "b.pdf")
    add_file(staff_actor, source, "a.pdf")
    event = attach(staff_actor, reserved, source)
    sources_service.add_source_note(staff_actor, source.pk, "one")
    note = sources_service.add_source_note(staff_actor, source.pk, "two")
    sources_service.void_source_note(staff_actor, note.pk, "oops")
    sources_service.assign_source(staff_actor, source.pk, colleague_user.pk)

    (row,) = inbox_service.inbox(staff_user.pk, "all").rows
    assert [a.filename for a in row.attachments] == ["b.pdf", "a.pdf"]
    assert [e.pk for e in row.events] == [event.pk]
    assert row.events[0].request.tracking_number == reserved.tracking_number
    assert row.events[0].request.agency.name == "Test Agency"
    assert row.note_count == 1  # the voided one is not counted
    assert row.assignee_name == "Colleague Person"


def test_a_page_of_rows_costs_a_fixed_number_of_queries(
    staff_actor, staff_user, reserved, django_assert_max_num_queries
) -> None:
    for _ in range(12):
        source = make_source(staff_actor)
        add_file(staff_actor, source)
        attach(staff_actor, reserved, source)
    with django_assert_max_num_queries(12):
        page = inbox_service.inbox(staff_user.pk, "new")
    assert len(page.rows) == 12
