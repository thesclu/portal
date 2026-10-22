"""Non-negotiable #4 and the effective_at tiebreak, attacked rather than demonstrated.

The database CHECK named `full_production_requires_human` does not require a human -- its body is
`status <> 'production_received' or reason = 'manual_override'`, which a system actor satisfies by
passing that reason with no user id at all (test_constraints.py proves the row is accepted). So
these tests come at `production_received` from the attacker's side: a system actor trying every
public door in the service layer.
"""

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone
from portal.records.models import StatusChange
from portal.records.services import common
from portal.records.services import deadlines as deadlines_service
from portal.records.services import edits as edits_service
from portal.records.services import events as events_service
from portal.records.services import status as status_service
from portal.records.services.common import NotAllowed, ServiceError


class _FrozenClock:
    """Stands in for `django.utils.timezone` inside common.py, with a clock that does not move."""

    def __init__(self, instant):
        self._instant = instant

    def now(self):
        return self._instant


def _note_event(actor, request):
    return events_service.record_event(
        actor,
        request.pk,
        event_type="production",
        channel="email",
        direction="inbound",
        occurred_at=request.created_at,
        display_body="Records attached.",
    )


def test_system_actor_cannot_reach_production_received_by_any_service_door(
    reserved, system_actor
) -> None:
    event = _note_event(system_actor, reserved)
    with pytest.raises(NotAllowed):
        status_service.override_status(system_actor, reserved.pk, "production_received", "n")
    with pytest.raises(NotAllowed):
        events_service.record_event(
            system_actor,
            reserved.pk,
            event_type="production",
            channel="email",
            direction="inbound",
            occurred_at=reserved.created_at,
            display_body="Records attached.",
            status="production_received",
        )
    with pytest.raises(NotAllowed):
        status_service._append_status_change(
            reserved,
            "production_received",
            reason="manual_override",
            actor=system_actor,
            allow_system_manual=True,
        )
    with pytest.raises(NotAllowed):
        status_service._append_status_change(
            reserved,
            "production_received",
            reason="event",
            actor=system_actor,
            triggering_event=event,
        )
    assert not StatusChange.objects.filter(
        request_id=reserved.pk, status_id="production_received"
    ).exists()


def test_system_actor_cannot_classify_an_event_as_full_production(
    reserved, staff_actor, system_actor
) -> None:
    """edits.edit_with_audit is human-only anyway, but prove the chokepoint is what stops it."""
    event = _note_event(staff_actor, reserved)
    with pytest.raises(NotAllowed):
        edits_service.edit_with_audit(
            system_actor,
            "events",
            event.pk,
            "display_body",
            "Everything produced.",
            "classifying",
            status="production_received",
        )


def test_a_human_still_cannot_write_full_production_as_an_event(reserved, staff_actor) -> None:
    """reason must be manual_override too: a parser's confidence is never enough."""
    event = _note_event(staff_actor, reserved)
    with pytest.raises(NotAllowed):
        status_service._append_status_change(
            reserved,
            "production_received",
            reason="event",
            actor=staff_actor,
            triggering_event=event,
        )


def test_a_human_manual_override_is_the_one_door_that_opens(reserved, staff_actor) -> None:
    change = status_service.override_status(
        staff_actor, reserved.pk, "production_received", "checked every page"
    )
    assert change.status_id == "production_received"
    reserved.refresh_from_db()
    assert reserved.current_status_id == "production_received"


def test_unknown_status_is_a_service_error(reserved, staff_actor) -> None:
    with pytest.raises(ServiceError, match="Unknown status"):
        status_service.override_status(staff_actor, reserved.pk, "totally_done", "n")


def test_system_actor_cannot_manual_override_anything(reserved, system_actor) -> None:
    with pytest.raises(NotAllowed, match="requires a human actor"):
        status_service.override_status(system_actor, reserved.pk, "closed", "n")


def test_fifty_rapid_events_in_one_transaction_keep_the_cache_on_the_last_one(
    reserved, staff_actor, monkeypatch
) -> None:
    """The must-not-regress test for `next_effective_at`.

    The wall clock is frozen for the duration, which is the condition the original bug was found
    under: two status rows written in one transaction shared `now()`, and the recompute trigger
    orders by `effective_at desc` with no tiebreaker, so the cache was free to keep the OLDER row.
    Without freezing, Python's clock advances between inserts on its own and the test would pass
    against a `next_effective_at` that did nothing at all.
    """
    frozen = timezone.now()
    monkeypatch.setattr(common, "timezone", _FrozenClock(frozen))

    codes = ["acknowledged", "in_progress"] * 25
    with transaction.atomic():
        for code in codes:
            events_service.record_event(
                staff_actor,
                reserved.pk,
                event_type="correspondence",
                channel="email",
                direction="inbound",
                occurred_at=reserved.created_at,
                display_body="ping",
                status=code,
            )
    reserved.refresh_from_db()
    assert reserved.current_status_id == codes[-1]
    stamps = list(
        StatusChange.objects.filter(request_id=reserved.pk)
        .order_by("effective_at")
        .values_list("effective_at", flat=True)
    )
    assert len(stamps) == 51
    assert all(a < b for a, b in zip(stamps, stamps[1:], strict=False)), stamps
    # The reservation row predates the freeze, so the first frozen append lands exactly on
    # `frozen` and each of the other 49 adds one microsecond.
    assert stamps[1] == frozen
    assert stamps[-1] == frozen + timedelta(microseconds=49)


def test_the_cache_lands_on_the_new_row_even_after_a_forward_dated_one(
    reserved, staff_actor
) -> None:
    """A status row can legitimately carry an effective_at in the future -- a correction entered
    ahead of time, or simply a clock that ran ahead. The next append must still win."""
    ahead = timezone.now() + timedelta(hours=1)
    StatusChange.objects.filter(request_id=reserved.pk).update(effective_at=ahead)
    status_service.override_status(staff_actor, reserved.pk, "closed", "done with it")
    reserved.refresh_from_db()
    assert reserved.current_status_id == "closed"
    assert reserved.current_status_changed_at == ahead + timedelta(microseconds=1)


def test_mark_deadline_missed_leaves_a_terminal_request_alone(
    reserved, staff_actor, system_actor
) -> None:
    deadline = deadlines_service.open_deadline(
        staff_actor,
        reserved.pk,
        deadline_type="statutory_determination",
        due_at=reserved.created_at.date(),
        basis="statutory",
    )
    status_service.override_status(staff_actor, reserved.pk, "withdrawn", "requester withdrew")
    assert deadlines_service.mark_deadline_missed(system_actor, deadline.pk) is None
    reserved.refresh_from_db()
    assert reserved.current_status_id == "withdrawn"
