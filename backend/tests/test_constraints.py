"""schema.sql's own rules, exercised with raw SQL (no service layer exists yet; P2 adds one).

Every test runs as portal_app (the `app_role` marker) to prove the constraints hold under the
grants, not just under the migrating owner role. `_apply_app_role_marker` is autouse and its
`SET LOCAL ROLE portal_app` takes effect BEFORE the `staff_user`/`agency` fixtures run (confirmed
with `pytest --setup-plan` and a live `select current_user` probe inside `staff_user`), so those
fixtures' INSERTs -- into core.users and records.agencies respectively -- execute as portal_app,
not as the owner. That is fine only because portal_app holds INSERT on core.users
(portal/db/hooks.py:38-46, granted post_migrate) and unrestricted INSERT on every records table
(records_db.0002); test_portal_app_can_insert_core_users below pins the first grant so a future
tightening fails at the grant, not with a confusing `permission denied for table users` inside
fixture setup.

Deliberately NOT tested here: two status_changes rows sharing the exact same `effective_at`. The
schema's cache-recompute trigger orders by `effective_at desc` with no tiebreaker, so which row
"wins" is unspecified today (see notes, "Finding worth knowing before touching
status_changes"). The fix is P2's `next_effective_at` (strictly increasing timestamps), not a P0
test asserting the current tie behaviour.

Uses a fixed synthetic future date (_SYNTHETIC_FUTURE_DUE_DATE), not `timedelta(days=N)`, even
though this file is exempt from test_no_day_counts.py's own scan (SCAN_ROOT is backend/portal;
this file lives in backend/tests). notes convention #5 is worded wider than that scan
-- "No statutory day count in `backend/`" -- so this is a deliberate narrowing of the enforced
rule to what the scanner actually walks, not an oversight; a harness file staying obviously
clean of the pattern it polices is worth more than the letter of the exemption.

`full_production_requires_human` (below) only pins the negative case: `production_received`
with `reason='event'` is refused. The constraint body is
`status <> 'production_received' or reason = 'manual_override'` -- it does NOT require a user
id, so the database alone does not enforce non-negotiable #4 ("production_received only via
override_status by a human actor"); it only enforces that the *reason* is 'manual_override'.
The real guard is P2's `_append_status_change` chokepoint, which is expected to require an
actor; the database is a partial second line, not the enforcement point. A positive-control
test below (test_production_received_accepted_with_manual_override) pins the legitimate human
path so a future tightening that accidentally blocks it fails here in P0, not as a P2 mystery.
"""

import uuid
from datetime import date, timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

# An arbitrary synthetic sentinel, not a statutory count -- deadlines.due_at is just `date not
# null` in schema.sql, no constraint ties it to "now". A fixed date, not `timezone.now() +
# timedelta(days=N)`, on purpose: the idiom is copyable and this file sits twenty lines from
# the harness (test_no_day_counts.py) that bans exactly that shape elsewhere in backend/.
_SYNTHETIC_FUTURE_DUE_DATE = date(2099, 1, 1)


def _insert_request(cur, *, agency_id, requester_user_id, status="pending_filing") -> uuid.UUID:
    tracking_number = f"REQ-TEST-{uuid.uuid4().hex[:8]}"
    cur.execute(
        """
        insert into records.requests
            (tracking_number, origin, requester_user_id, agency_id, subject, request_text,
             current_status)
        values (%s, 'filed', %s, %s, 'Test subject', 'Test request text', %s)
        returning id
        """,
        [tracking_number, requester_user_id, agency_id, status],
    )
    return cur.fetchone()[0]


def _insert_event(cur, *, request_id) -> uuid.UUID:
    cur.execute(
        """
        insert into records.events
            (request_id, occurred_at, event_type, channel, direction, display_body)
        values (%s, now(), 'note', 'system', 'internal', 'test event')
        returning id
        """,
        [request_id],
    )
    return cur.fetchone()[0]


@pytest.mark.app_role
def test_portal_app_can_insert_core_users(db) -> None:
    """Pins the grant every test in this module silently depends on (see module docstring):
    the `staff_user` fixture's create_user() call runs AFTER the role switch, as portal_app,
    so portal_app must hold INSERT on core.users (portal/db/hooks.py:38-46). notes
    lists this grant as "written but NOT yet exercised" -- this is the exercise. Without it,
    tightening the grant fails all five constraint tests inside fixture setup instead of here.
    """
    with connection.cursor() as cur:
        cur.execute("select has_table_privilege('portal_app', 'core.users', 'INSERT')")
        assert cur.fetchone()[0] is True


@pytest.mark.app_role
def test_production_received_requires_manual_override(db, staff_user, agency) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        event_id = _insert_event(cur, request_id=request_id)
        with pytest.raises(IntegrityError, match="full_production_requires_human"):
            with transaction.atomic():
                cur.execute(
                    """
                    insert into records.status_changes
                        (request_id, status, reason, triggering_event_id)
                    values (%s, 'production_received', 'event', %s)
                    """,
                    [request_id, event_id],
                )


@pytest.mark.app_role
def test_production_received_accepted_with_manual_override(db, staff_user, agency) -> None:
    """The positive case for full_production_requires_human: only the refusal was pinned
    before (see module docstring). Without this, a future tightening that blocks the
    legitimate human path would pass P0 and only surface later as a P2 mystery.
    """
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        cur.execute(
            """
            insert into records.status_changes (request_id, status, reason)
            values (%s, 'production_received', 'manual_override')
            """,
            [request_id],
        )
        cur.execute("select current_status from records.requests where id = %s", [request_id])
        assert cur.fetchone()[0] == "production_received"


@pytest.mark.app_role
def test_event_reason_requires_triggering_event(db, staff_user, agency) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        with pytest.raises(IntegrityError, match="status_change_trigger_matches_reason"):
            with transaction.atomic():
                cur.execute(
                    """
                    insert into records.status_changes (request_id, status, reason)
                    values (%s, 'acknowledged', 'event')
                    """,
                    [request_id],
                )


@pytest.mark.app_role
def test_event_recorder_cannot_be_both_user_and_system(db, staff_user, agency) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        with pytest.raises(IntegrityError, match="events_recorder_not_both"):
            with transaction.atomic():
                cur.execute(
                    """
                    insert into records.events
                        (request_id, occurred_at, event_type, channel, direction,
                         recorded_by_user_id, recorded_by_system, display_body)
                    values (%s, now(), 'note', 'system', 'internal', %s, 'test_system', 'x')
                    """,
                    [request_id, staff_user.id],
                )


@pytest.mark.app_role
def test_current_status_moves_to_the_later_effective_at(db, staff_user, agency) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        first_at = timezone.now()
        second_at = first_at + timedelta(seconds=1)
        cur.execute(
            """
            insert into records.status_changes (request_id, status, reason, effective_at)
            values (%s, 'filed', 'manual_override', %s)
            """,
            [request_id, first_at],
        )
        cur.execute(
            """
            insert into records.status_changes (request_id, status, reason, effective_at)
            values (%s, 'acknowledged', 'manual_override', %s)
            """,
            [request_id, second_at],
        )
        cur.execute("select current_status from records.requests where id = %s", [request_id])
        assert cur.fetchone()[0] == "acknowledged"


@pytest.mark.app_role
def test_current_status_pins_on_effective_at_not_insertion_order(db, staff_user, agency) -> None:
    """The forward-order test above cannot tell "order by effective_at desc" apart from "last
    row inserted wins" -- both give the same answer when the later-effective row is also
    inserted last. Insert the LATER-effective row FIRST and the EARLIER one SECOND: only a
    trigger that genuinely orders by effective_at (not insertion/ctid order) lands on the
    later status here.
    """
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        later_at = timezone.now()
        earlier_at = later_at - timedelta(seconds=1)
        cur.execute(
            """
            insert into records.status_changes (request_id, status, reason, effective_at)
            values (%s, 'acknowledged', 'manual_override', %s)
            """,
            [request_id, later_at],
        )
        cur.execute(
            """
            insert into records.status_changes (request_id, status, reason, effective_at)
            values (%s, 'filed', 'manual_override', %s)
            """,
            [request_id, earlier_at],
        )
        cur.execute("select current_status from records.requests where id = %s", [request_id])
        assert cur.fetchone()[0] == "acknowledged"


@pytest.mark.app_role
def test_active_deadline_sets_and_satisfied_clears_next_deadline_at(db, staff_user, agency) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        due_at = _SYNTHETIC_FUTURE_DUE_DATE
        cur.execute(
            """
            insert into records.deadlines
                (request_id, deadline_type, basis, due_at, status)
            values (%s, 'statutory_determination', 'statutory', %s, 'active')
            returning id
            """,
            [request_id, due_at],
        )
        deadline_id = cur.fetchone()[0]
        cur.execute("select next_deadline_at from records.requests where id = %s", [request_id])
        assert cur.fetchone()[0] == due_at

        cur.execute(
            "update records.deadlines set status = 'satisfied' where id = %s", [deadline_id]
        )
        cur.execute("select next_deadline_at from records.requests where id = %s", [request_id])
        assert cur.fetchone()[0] is None
