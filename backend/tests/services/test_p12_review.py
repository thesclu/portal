"""P12: production review, item by item, and the two write paths behind it.

A review in which every item is produced is the person declaring the request fully produced, and
goes through the override door (a human, `manual_override`, a note). Anything else is a partial
production by the event reviewed. Nothing else here can reach `production_received`.
"""

import pytest
from portal.records.actors import Actor
from portal.records.models import Attachment, AttachmentItem, ProductionReview, StatusChange
from portal.records.services import links as links_service
from portal.records.services import requests as requests_service
from portal.records.services import review as review_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError
from portal.records.services.review import FileLink, ItemReview

from tests.p8b_helpers import add_file, attach, make_source


@pytest.fixture
def actor(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def with_items(db, actor, staff_user, agency):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Two items",
        request_text="Body.",
        items=[
            requests_service.NewItem("1", "Contracts", sort_order=1),
            requests_service.NewItem("2", "Policies", sort_order=2),
        ],
    )


@pytest.fixture
def production(actor, with_items):
    """An inbound production with two files, opened by a review_production task."""
    source = make_source(actor)
    first = add_file(actor, source, "contract.pdf")
    second = add_file(actor, source, "policy.pdf")
    event = attach(actor, with_items, source, event_type="production")
    task = tasks_service.create_task(
        actor, with_items.pk, "review_production", "Review the records the agency produced."
    )
    return event, [first, second], task


def _items(request):
    return {i.item_number: i for i in request.items.all()}


def test_a_partial_review_appends_partial_production_by_the_event(
    actor, with_items, production
) -> None:
    event, (contract, policy), task = production
    items = _items(with_items)
    review = review_service.review_production(
        actor,
        with_items.pk,
        event_id=event.pk,
        rows=[
            ItemReview(items["1"].pk, "produced"),
            ItemReview(items["2"].pk, "withheld", exemption_cited="cited", cited_where="p. 1"),
        ],
        files=[FileLink(contract.pk, items["1"].pk), FileLink(policy.pk, None)],
        note="First look.",
    )
    with_items.refresh_from_db()
    assert with_items.current_status_id == "partial_production"
    change = StatusChange.objects.get(pk=review.status_change_id)
    assert (change.reason, change.triggering_event_id) == ("event", event.pk)
    assert review.outcome == "partial_production" and review.supersedes_id is None
    rows = {r.request_item_id: r for r in review.rows.all()}
    assert rows[items["2"].pk].exemption_cited == "cited"
    # The files are records now, tied to the request and (one of them) to an item.
    assert {a.role for a in Attachment.objects.filter(pk__in=[contract.pk, policy.pk])} == {
        "produced_record"
    }
    links = {
        link.attachment_id: link.request_item_id
        for link in review_service.file_links_of(with_items.pk)
    }
    assert links == {contract.pk: items["1"].pk, policy.pk: None}
    # The task that asked for the review is done.
    task.refresh_from_db()
    assert task.completed_at is not None
    assert (
        review_service.current_dispositions(with_items.pk)[items["2"].pk].disposition == "withheld"
    )


def test_every_item_produced_is_the_person_declaring_full_production(
    actor, with_items, production
) -> None:
    event, _, _ = production
    items = _items(with_items)
    review = review_service.review_production(
        actor,
        with_items.pk,
        event_id=event.pk,
        rows=[ItemReview(items["1"].pk, "produced"), ItemReview(items["2"].pk, "produced")],
        note="Checked every page.",
    )
    with_items.refresh_from_db()
    assert with_items.current_status_id == "production_received"
    change = StatusChange.objects.get(pk=review.status_change_id)
    assert (change.reason, change.triggering_event_id) == ("manual_override", None)
    assert "Checked every page" in (change.note or "")
    assert review.outcome == "production_received"


def test_a_later_review_supersedes_and_the_newest_dispositions_stand(
    actor, with_items, production
) -> None:
    event, _, _ = production
    items = _items(with_items)
    first = review_service.review_production(
        actor,
        with_items.pk,
        event_id=event.pk,
        rows=[ItemReview(items["1"].pk, "not_yet"), ItemReview(items["2"].pk, "not_yet")],
    )
    second = review_service.review_production(
        actor,
        with_items.pk,
        event_id=event.pk,
        rows=[ItemReview(items["1"].pk, "produced"), ItemReview(items["2"].pk, "partly")],
    )
    assert second.supersedes_id == first.pk
    assert [r.pk for r in review_service.reviews_of(with_items.pk)] == [second.pk, first.pk]
    standing = review_service.current_dispositions(with_items.pk)
    assert standing[items["1"].pk].disposition == "produced"
    assert ProductionReview.objects.count() == 2  # nothing edited, nothing deleted


def test_what_a_review_refuses(actor, with_items, production) -> None:
    event, (contract, _), _ = production
    items = _items(with_items)
    whole = [ItemReview(items["1"].pk, "produced"), ItemReview(items["2"].pk, "produced")]
    with pytest.raises(NotAllowed):
        review_service.review_production(
            Actor(system="job"), with_items.pk, event_id=event.pk, rows=whole
        )
    with pytest.raises(ServiceError, match="Every item needs a disposition"):
        review_service.review_production(actor, with_items.pk, event_id=event.pk, rows=[whole[0]])
    with pytest.raises(ServiceError, match="Unknown disposition"):
        review_service.review_production(
            actor,
            with_items.pk,
            event_id=event.pk,
            rows=[whole[0], ItemReview(items["2"].pk, "lost")],
        )
    other = make_source(actor)
    stranger = add_file(actor, other, "elsewhere.pdf")
    with pytest.raises(ServiceError, match="not one of this production's files"):
        review_service.review_production(
            actor, with_items.pk, event_id=event.pk, rows=whole, files=[FileLink(stranger.pk)]
        )
    # Only a production is reviewed; a plain correspondence is recorded as one first.
    letter = attach(actor, with_items, make_source(actor), event_type="correspondence")
    with pytest.raises(InvalidTransition, match="Only a production"):
        review_service.review_production(actor, with_items.pk, event_id=letter.pk, rows=whole)
    with pytest.raises(NotFound):
        review_service.review_production(actor, with_items.pk, event_id=contract.pk, rows=whole)
    assert ProductionReview.objects.count() == 0 and AttachmentItem.objects.count() == 0


def test_links_are_directional_reasoned_voidable_and_never_doubled(
    actor, with_items, reserved
) -> None:
    link = links_service.link_requests(
        actor, with_items.pk, reserved.pk, kind="duplicate", reason="Filed twice."
    )
    assert [row.pk for row in links_service.links_of(reserved.pk)] == [link.pk]
    with pytest.raises(ServiceError, match="already exists"):
        links_service.link_requests(
            actor, with_items.pk, reserved.pk, kind="duplicate", reason="again"
        )
    with pytest.raises(ServiceError, match="itself"):
        links_service.link_requests(
            actor, with_items.pk, with_items.pk, kind="follow_on", reason="no"
        )
    with pytest.raises(ServiceError, match="needs a reason"):
        links_service.link_requests(actor, with_items.pk, reserved.pk, kind="follow_on", reason=" ")
    with pytest.raises(ServiceError, match="Unknown link kind"):
        links_service.link_requests(
            actor, with_items.pk, reserved.pk, kind="same_campaign", reason="x"
        )
    voided = links_service.void_link(actor, link.pk, "Not a duplicate after all.")
    assert voided.voided_at is not None and links_service.links_of(reserved.pk) == []
    # Voided is not deleted; the same link can be made again as a new row.
    again = links_service.link_requests(
        actor, with_items.pk, reserved.pk, kind="duplicate", reason="It is, though."
    )
    assert again.pk != link.pk
    with_items.refresh_from_db()
    assert with_items.current_status_id == "pending_filing"  # a link moves no status
