"""records-schema.md's "A request's life, event by event", steps 1-7, in order.

Parametrised over the database role: the same walkthrough runs as the migration owner and as
portal_app, so the services are proved to work under the column-level grants rather than only
under privileges the application will never have.
"""

from datetime import date, timedelta

import pytest
from portal.records.models import Attachment, Deadline
from portal.records.services import deadlines as deadlines_service
from portal.records.services import edits as edits_service
from portal.records.services import events as events_service
from portal.records.services import filing as filing_service
from portal.records.services import sources as sources_service
from portal.records.services import status as status_service

from .conftest import FILED_AT


@pytest.fixture(params=["owner", "app_role"], autouse=True)
def role(request, db):
    if request.param == "app_role":
        request.getfixturevalue("app_role")


def test_the_documented_life_of_a_request(reserved, staff_actor, system_actor, rules, capsys):
    # 1. Reserved: the row, the number and the alias exist; nothing has been sent.
    assert reserved.current_status_id == "pending_filing"
    assert reserved.filed_at is None
    footage_item = reserved.items.get(item_number="2")

    # 2. Filed by email. One submission event, filed_at set, statutory clocks opened.
    source = sources_service.register_source(
        staff_actor,
        source_type="postmark_email",
        raw_storage_url="s3://test/outbound-1.eml",
        external_ref="pm-outbound-1",
        message_id="<out-1@example.org>",
    )
    result = filing_service.file_request(
        staff_actor,
        reserved.pk,
        filed_at=FILED_AT,
        channel="email",
        source_id=source.pk,
        item_provisions={footage_item.pk: ("widgets",)},
    )
    request = result.request
    assert request.current_status_id == "filed"
    assert request.filed_at == FILED_AT
    assert result.submission_event.event_type_id == "submission"
    # The "widgets" carve-out has a production rule but no determination rule, so that half of
    # the lookup is unresolved -- recorded as a task naming the provision, never silently dropped.
    assert [t.task_type for t in result.tasks] == ["enter_deadline_rules"]
    assert "widgets" in result.tasks[0].description
    assert str(footage_item.pk) in result.tasks[0].description
    by_type = {(d.deadline_type, d.request_item_id): d for d in result.deadlines}
    determination = by_type[("statutory_determination", None)]
    assert determination.due_at == date(2026, 2, 17)
    assert determination.rule_ref == "testlaw.determination.general.2020-01-01"
    assert by_type[("production", None)].due_at == date(2026, 3, 12)
    assert by_type[("production", footage_item.pk)].due_at == date(2026, 2, 26)
    assert request.next_deadline_at == date(2026, 2, 17)

    # 3. The agency acknowledges, and its own reference number is learned and audited.
    inbound = sources_service.register_source(
        staff_actor,
        source_type="postmark_email",
        raw_storage_url="s3://test/inbound-1.eml",
        external_ref="pm-inbound-1",
    )
    events_service.record_event(
        staff_actor,
        request.pk,
        event_type="acknowledgment",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + timedelta(days=1),
        display_body="We have your request; our reference is TA-2026-77.",
        source_id=inbound.pk,
        status="acknowledged",
    )
    edits_service.edit_with_audit(
        staff_actor,
        "requests",
        request.pk,
        "agency_reference_number",
        "TA-2026-77",
        "from their acknowledgment letter",
    )
    request.refresh_from_db()
    assert request.current_status_id == "acknowledged"
    assert request.agency_reference_number == "TA-2026-77"

    # 4. A determination letter names a production date that disagrees with the statute.
    determination_event = events_service.record_event(
        staff_actor,
        request.pk,
        event_type="determination",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + timedelta(days=3),
        display_body="Determination: records exist. We expect to produce by 2026-04-30.",
        status="determination_issued",
    )
    promised = deadlines_service.open_deadline(
        staff_actor,
        request.pk,
        deadline_type="production",
        due_at=date(2026, 4, 30),
        basis="agency_commitment",
        source_event_id=determination_event.pk,
    )
    assert promised.flagged_for_review
    assert str(by_type[("production", None)].pk) in promised.flag_note

    # 5. Records arrive. Automation may say new_production and no more than that.
    production_source = sources_service.register_source(
        staff_actor,
        source_type="postmark_email",
        raw_storage_url="s3://test/inbound-2.eml",
        external_ref="pm-inbound-2",
    )
    sources_service.add_attachment(
        staff_actor,
        production_source.pk,
        filename="widgets-jan.pdf",
        storage_url="s3://test/widgets-jan.pdf",
        role="produced_record",
    )
    production_event = events_service.record_event(
        system_actor,
        request.pk,
        event_type="production",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + timedelta(days=5),
        display_body="Attached: the January widget inspection reports.",
        source_id=production_source.pk,
        status="new_production",
    )
    assert Attachment.objects.filter(source_id=production_source.pk).count() == 1
    deadlines_service.satisfy_deadline(
        system_actor, by_type[("production", None)].pk, production_event.pk
    )

    # A person reviews it and only then is it fully produced.
    status_service.override_status(
        staff_actor, request.pk, "production_received", "reviewed every page against items 1-2"
    )
    request.refresh_from_db()
    assert request.current_status_id == "production_received"

    # 6. A determination clock lapses with nothing sent: the sweep marks it and says so.
    Deadline.objects.filter(pk=determination.pk).update(due_at=date(2026, 2, 1))
    change = deadlines_service.mark_deadline_missed(system_actor, determination.pk)
    assert change is not None
    assert change.status_id == "determination_overdue"
    assert change.triggering_deadline_id == determination.pk
    assert Deadline.objects.get(pk=determination.pk).status == "missed"

    # 7. The follow-up nudge is an event about a deadline, not one that creates a deadline.
    nudge = events_service.record_event(
        system_actor,
        request.pk,
        event_type="follow_up",
        channel="email",
        direction="outbound",
        occurred_at=FILED_AT + timedelta(days=12),
        display_body="Following up on our request.",
        related_deadline_id=determination.pk,
    )
    assert nudge.actor_user_id is None
    assert nudge.recorded_by_system == "test_sweep"
    assert nudge.related_deadline_id == determination.pk

    history = status_service.status_history(request.pk)
    assert [row.status_id for row in history] == [
        "pending_filing",
        "filed",
        "acknowledged",
        "determination_issued",
        "new_production",
        "production_received",
        "determination_overdue",
    ]
    with capsys.disabled():
        print(f"\n--- status history for {request.tracking_number} ---")
        for row in history:
            print(
                f"{row.effective_at.isoformat()}  {row.status_id:<22} "
                f"reason={row.reason:<16} voided={row.voided_at}  note={row.note}"
            )
