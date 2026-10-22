"""What records_db 0006, 0007, 0008, 0009 and 0010 did, asserted against the live database.

Placement (19 tables), model-versus-column drift and the column grants live in their own files and
were updated alongside these migrations; this file covers the rest: the rows, the constraints that
replaced the three CHECKs, the two new constraints, the shape of `source_notes` and
`request_access`, and -- the one thing BUILD_PLAN says about the reverse of the non-additive
migration -- that the reverses fail loudly instead of rewriting the ledger.

The constraint tests run as portal_app (the `app_role` marker) so they prove the rules hold under
the grants, not only under the owner. The reverse-migration test runs as the owner, because only
the owner may alter tables, inside the test's own transaction: Postgres DDL is transactional, so
everything it unapplies is rolled back when the test ends.
"""

import uuid
from datetime import date

import pytest
from django.db import DatabaseError, IntegrityError, ProgrammingError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

_SYNTHETIC_DUE = date(2099, 1, 1)


def _insert_request(cur, *, agency_id, requester_user_id) -> uuid.UUID:
    cur.execute(
        """
        insert into records.requests
            (tracking_number, origin, requester_user_id, agency_id, subject, request_text,
             current_status)
        values (%s, 'filed', %s, %s, 'Test subject', 'Test request text', 'pending_filing')
        returning id
        """,
        [f"REQ-TEST-{uuid.uuid4().hex[:8]}", requester_user_id, agency_id],
    )
    return cur.fetchone()[0]


def _insert_deadline(cur, *, request_id, kind) -> uuid.UUID:
    cur.execute(
        """
        insert into records.deadlines (request_id, deadline_type, basis, due_at, status)
        values (%s, %s, 'agency_commitment', %s, 'active')
        returning id
        """,
        [request_id, kind, _SYNTHETIC_DUE],
    )
    return cur.fetchone()[0]


def _constraints(table: str) -> dict[str, tuple[str, str]]:
    """name -> (contype, definition) for every CHECK and foreign key on one records table."""
    with connection.cursor() as cur:
        cur.execute(
            """
            select conname, contype, pg_get_constraintdef(oid)
            from pg_constraint
            where conrelid = %s::regclass and contype in ('c', 'f')
            """,
            [f"records.{table}"],
        )
        return {name: (kind, definition) for name, kind, definition in cur.fetchall()}


# ---- 0007: the kinds are rows, the three CHECKs are gone ---------------------------------------


def test_deadline_definitions_holds_the_five_kinds(db) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "select code, label, sort_order, overdue_status "
            "from records.deadline_definitions order by sort_order"
        )
        rows = cur.fetchall()
    assert rows == [
        ("statutory_determination", "Determination", 10, "determination_overdue"),
        ("production", "Production", 20, "production_overdue"),
        ("fee_response", "Fee response", 30, None),
        ("challenge_response", "Challenge response", 35, None),  # records_db.0015 (P14)
        ("other", "Other", 40, None),
    ]


def test_deadline_type_is_a_foreign_key_on_both_tables_and_no_longer_a_check(db) -> None:
    for table in ("deadlines", "deadline_followups"):
        found = _constraints(table)
        checks_on_kind = {
            name
            for name, (kind, definition) in found.items()
            if kind == "c" and "deadline_type" in definition
        }
        assert checks_on_kind == set(), (table, checks_on_kind)
        fks = [d for (k, d) in found.values() if k == "f" and "deadline_definitions" in d]
        assert len(fks) == 1 and "(deadline_type)" in fks[0], (table, fks)


def test_events_channel_check_admits_in_person_and_nothing_else_new(db) -> None:
    definition = _constraints("events")["events_channel_check"][1]
    for channel in ("email", "portal", "mail", "call", "system", "in_person"):
        assert f"'{channel}'" in definition, channel
    assert definition.count("::text") == 6, definition


@pytest.mark.app_role
def test_a_deadline_of_an_unknown_kind_is_refused_by_the_foreign_key(
    db, staff_user, agency
) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        with pytest.raises(IntegrityError, match="deadlines_deadline_type_fkey"):
            with transaction.atomic():
                _insert_deadline(cur, request_id=request_id, kind="not_a_kind")
        with pytest.raises(IntegrityError, match="deadline_followups_deadline_type_fkey"):
            with transaction.atomic():
                cur.execute(
                    "insert into records.deadline_followups (request_id, deadline_type) "
                    "values (%s, 'not_a_kind')",
                    [request_id],
                )


@pytest.mark.app_role
def test_a_new_kind_opens_and_feeds_the_next_deadline_cache(db, staff_user, agency) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        for kind in ("fee_response", "other"):
            _insert_deadline(cur, request_id=request_id, kind=kind)
        cur.execute("select next_deadline_at from records.requests where id = %s", [request_id])
        assert cur.fetchone()[0] == _SYNTHETIC_DUE


@pytest.mark.app_role
def test_events_accept_in_person_and_still_refuse_an_unknown_channel(
    db, staff_user, agency
) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        cur.execute(
            """
            insert into records.events
                (request_id, occurred_at, event_type, channel, direction, display_body)
            values (%s, now(), 'note', 'in_person', 'internal', 'met at the records counter')
            """,
            [request_id],
        )
        with pytest.raises(IntegrityError, match="events_channel_check"):
            with transaction.atomic():
                cur.execute(
                    """
                    insert into records.events
                        (request_id, occurred_at, event_type, channel, direction, display_body)
                    values (%s, now(), 'note', 'carrier_pigeon', 'internal', 'x')
                    """,
                    [request_id],
                )


# ---- 0008: data rows ---------------------------------------------------------------------------


def test_v2_status_and_event_rows_exist_and_no_status_is_terminal(db) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "select code, label, sort_order, is_terminal from records.status_definitions "
            "where code in ('clarification_requested', 'fee_estimate_received') order by code"
        )
        statuses = cur.fetchall()
        cur.execute(
            "select code, label, sort_order from records.event_definitions "
            "where code in ('fee_payment', 'narrowing') order by code"
        )
        events = cur.fetchall()
    assert statuses == [
        ("clarification_requested", "Clarification requested", 25, False),
        ("fee_estimate_received", "Fee estimate received", 36, False),
    ]
    assert events == [("fee_payment", "Fee payment", 39), ("narrowing", "Narrowing", 72)]


# ---- 0006: the two new constraints -------------------------------------------------------------


@pytest.mark.app_role
def test_a_task_was_created_by_a_person_or_a_process_never_both(db, staff_user, agency) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        cur.execute(
            "insert into records.tasks (request_id, task_type, description, created_by_system) "
            "values (%s, 'mail_letter', 'by a process', 'test_sweep')",
            [request_id],
        )
        cur.execute(
            "insert into records.tasks (request_id, task_type, description) "
            "values (%s, 'mail_letter', 'a task that predates creator tracking')",
            [request_id],
        )
        with pytest.raises(IntegrityError, match="tasks_creator_not_both"):
            with transaction.atomic():
                cur.execute(
                    "insert into records.tasks "
                    "(request_id, task_type, description, created_by_user_id, created_by_system) "
                    "values (%s, 'mail_letter', 'both', %s, 'test_sweep')",
                    [request_id, staff_user.id],
                )


@pytest.mark.app_role
def test_an_agency_has_at_most_one_primary_contact(db, agency) -> None:
    with connection.cursor() as cur:
        for name, primary in (("First", True), ("Second", False)):
            cur.execute(
                "insert into records.agency_contacts (agency_id, name, is_primary) "
                "values (%s, %s, %s)",
                [agency.id, name, primary],
            )
        with pytest.raises(IntegrityError, match="agency_contacts_one_primary_idx"):
            with transaction.atomic():
                cur.execute(
                    "insert into records.agency_contacts (agency_id, name, is_primary) "
                    "values (%s, 'Third', true)",
                    [agency.id],
                )


# ---- the reverse of 0007 fails loudly ----------------------------------------------------------


def _migrate(target: tuple[str, str]) -> None:
    MigrationExecutor(connection).migrate([target])


def _constraint_names(table: str) -> set[str]:
    return set(_constraints(table))


def test_reversing_0007_refuses_while_rows_of_a_new_kind_exist_and_restores_the_checks_after(
    db, staff_user, agency
) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        other_id = _insert_deadline(cur, request_id=request_id, kind="other")
        cur.execute(
            """
            insert into records.events
                (request_id, occurred_at, event_type, channel, direction, display_body)
            values (%s, now(), 'note', 'in_person', 'internal', 'met at the counter')
            returning id
            """,
            [request_id],
        )
        visit_id = cur.fetchone()[0]

    # 0008 reverses first (nothing points at its rows), then 0007 must refuse, by name, and its
    # savepoint rolls back so the new constraints are all still standing.
    with pytest.raises(DatabaseError, match="cannot reverse records_db.0007"):
        _migrate(("records_db", "0006_v2_columns"))
    assert "deadlines_deadline_type_fkey" in _constraint_names("deadlines")
    assert "deadlines_deadline_type_check" not in _constraint_names("deadlines")
    assert "'in_person'" in _constraints("events")["events_channel_check"][1]

    # Remove the blockers (as the owner; portal_app can delete nothing) and the reverse completes.
    with connection.cursor() as cur:
        cur.execute("delete from records.deadlines where id = %s", [other_id])
        cur.execute("delete from records.events where id = %s", [visit_id])
    _migrate(("records_db", "0006_v2_columns"))

    assert "deadlines_deadline_type_check" in _constraint_names("deadlines")
    assert "deadlines_deadline_type_fkey" not in _constraint_names("deadlines")
    assert "deadline_followups_deadline_type_check" in _constraint_names("deadline_followups")
    assert "'in_person'" not in _constraints("events")["events_channel_check"][1]
    with connection.cursor() as cur:
        cur.execute("select to_regclass('records.deadline_definitions')")
        assert cur.fetchone()[0] is None
        with pytest.raises(IntegrityError, match="deadlines_deadline_type_check"):
            with transaction.atomic():
                _insert_deadline(cur, request_id=request_id, kind="other")


# ---- 0009: notes on a message -----------------------------------------------------------------


def _insert_source(cur) -> uuid.UUID:
    cur.execute(
        "insert into records.sources (source_type, raw_storage_url) "
        "values ('other', 'test://source') returning id"
    )
    return cur.fetchone()[0]


def test_source_notes_has_the_columns_and_the_index_the_spec_names(db) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """
            select column_name, data_type, is_nullable, column_default is not null
            from information_schema.columns
            where table_schema = 'records' and table_name = 'source_notes'
            order by ordinal_position
            """
        )
        columns = cur.fetchall()
        cur.execute(
            "select indexdef from pg_indexes "
            "where schemaname = 'records' and tablename = 'source_notes' and indexname <> %s",
            ["source_notes_pkey"],
        )
        indexes = [row[0] for row in cur.fetchall()]
    assert columns == [
        ("id", "uuid", "NO", True),
        ("source_id", "uuid", "NO", False),
        ("author_user_id", "uuid", "NO", False),
        ("body", "text", "NO", False),
        ("voided_at", "timestamp with time zone", "YES", False),
        ("void_reason", "text", "YES", False),
        ("created_at", "timestamp with time zone", "NO", True),
    ]
    assert len(indexes) == 1 and "(source_id, created_at)" in indexes[0], indexes


@pytest.mark.app_role
def test_a_note_needs_a_source_and_an_author_that_exist(db, staff_user) -> None:
    with connection.cursor() as cur:
        source_id = _insert_source(cur)
        cur.execute(
            "insert into records.source_notes (source_id, author_user_id, body) "
            "values (%s, %s, 'call them first')",
            [source_id, staff_user.id],
        )
        with pytest.raises(IntegrityError, match="source_notes_source_id_fkey"):
            with transaction.atomic():
                cur.execute(
                    "insert into records.source_notes (source_id, author_user_id, body) "
                    "values (%s, %s, 'x')",
                    [uuid.uuid4(), staff_user.id],
                )
        with pytest.raises(IntegrityError, match="source_notes_author_user_id_fkey"):
            with transaction.atomic():
                cur.execute(
                    "insert into records.source_notes (source_id, author_user_id, body) "
                    "values (%s, %s, 'x')",
                    [source_id, uuid.uuid4()],
                )


@pytest.mark.app_role
def test_a_note_can_be_voided_but_not_rewritten_or_deleted(db, staff_user) -> None:
    with connection.cursor() as cur:
        source_id = _insert_source(cur)
        cur.execute(
            "insert into records.source_notes (source_id, author_user_id, body) "
            "values (%s, %s, 'original wording') returning id",
            [source_id, staff_user.id],
        )
        (note_id,) = cur.fetchone()
        cur.execute(
            "update records.source_notes set voided_at = now(), void_reason = 'wrong message' "
            "where id = %s",
            [note_id],
        )
        with pytest.raises(ProgrammingError, match="permission denied"):
            with transaction.atomic():
                cur.execute(
                    "update records.source_notes set body = 'new wording' where id = %s", [note_id]
                )
        with pytest.raises(ProgrammingError, match="permission denied"):
            with transaction.atomic():
                cur.execute("delete from records.source_notes where id = %s", [note_id])
        cur.execute("select body, void_reason from records.source_notes where id = %s", [note_id])
        assert cur.fetchone() == ("original wording", "wrong message")


def test_reversing_0009_refuses_while_a_note_exists_and_drops_the_table_after(
    db, staff_user
) -> None:
    with connection.cursor() as cur:
        source_id = _insert_source(cur)
        cur.execute(
            "insert into records.source_notes (source_id, author_user_id, body) "
            "values (%s, %s, 'kept') returning id",
            [source_id, staff_user.id],
        )
        (note_id,) = cur.fetchone()

    with pytest.raises(DatabaseError, match="cannot reverse records_db.0009"):
        _migrate(("records_db", "0008_v2_rows"))
    with connection.cursor() as cur:
        cur.execute("select to_regclass('records.source_notes')")
        assert cur.fetchone()[0] is not None
        cur.execute("select count(*) from records.source_notes")
        assert cur.fetchone()[0] == 1

    # The blocker removed (as the owner; portal_app can delete nothing), the reverse completes.
    with connection.cursor() as cur:
        cur.execute("delete from records.source_notes where id = %s", [note_id])
    _migrate(("records_db", "0008_v2_rows"))
    with connection.cursor() as cur:
        cur.execute("select to_regclass('records.source_notes')")
        assert cur.fetchone()[0] is None


# ---- 0010: who may see a privileged request -----------------------------------------------------


def _grant(cur, *, request_id, user_id, granted_by=None) -> uuid.UUID:
    cur.execute(
        "insert into records.request_access (request_id, user_id, granted_by_user_id) "
        "values (%s, %s, %s) returning id",
        [request_id, user_id, granted_by],
    )
    return cur.fetchone()[0]


def test_request_access_has_the_columns_and_the_index_the_spec_names(db) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """
            select column_name, data_type, is_nullable, column_default is not null
            from information_schema.columns
            where table_schema = 'records' and table_name = 'request_access'
            order by ordinal_position
            """
        )
        columns = cur.fetchall()
        cur.execute(
            "select indexname, indexdef from pg_indexes "
            "where schemaname = 'records' and tablename = 'request_access' and indexname <> %s",
            ["request_access_pkey"],
        )
        indexes = dict(cur.fetchall())
    assert columns == [
        ("id", "uuid", "NO", True),
        ("request_id", "uuid", "NO", False),
        ("user_id", "uuid", "NO", False),
        ("granted_by_user_id", "uuid", "YES", False),
        ("note", "text", "YES", False),
        ("granted_at", "timestamp with time zone", "NO", True),
        ("revoked_at", "timestamp with time zone", "YES", False),
        ("revoked_by_user_id", "uuid", "YES", False),
    ]
    assert set(indexes) == {"request_access_active_uniq"}
    definition = indexes["request_access_active_uniq"]
    assert "UNIQUE" in definition and "(request_id, user_id)" in definition
    assert "WHERE (revoked_at IS NULL)" in definition


@pytest.mark.app_role
def test_one_active_grant_per_person_per_request_and_any_number_of_revoked_ones(
    db, staff_user, colleague_user, agency
) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        first = _grant(cur, request_id=request_id, user_id=colleague_user.id)
        with pytest.raises(IntegrityError, match="request_access_active_uniq"):
            with transaction.atomic():
                _grant(cur, request_id=request_id, user_id=colleague_user.id)
        # A different person on the same request is fine.
        _grant(cur, request_id=request_id, user_id=staff_user.id)
        # Revoked, the first no longer blocks: sharing again is a new row and the old one stays.
        cur.execute(
            "update records.request_access set revoked_at = now(), revoked_by_user_id = %s "
            "where id = %s",
            [staff_user.id, first],
        )
        _grant(cur, request_id=request_id, user_id=colleague_user.id)
        cur.execute(
            "select count(*), count(*) filter (where revoked_at is null) "
            "from records.request_access where request_id = %s and user_id = %s",
            [request_id, colleague_user.id],
        )
        assert cur.fetchone() == (2, 1)


@pytest.mark.app_role
def test_a_grant_needs_a_request_and_people_that_exist(db, staff_user, agency) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        for values, constraint in (
            ([uuid.uuid4(), staff_user.id, None], "request_access_request_id_fkey"),
            ([request_id, uuid.uuid4(), None], "request_access_user_id_fkey"),
            ([request_id, staff_user.id, uuid.uuid4()], "request_access_granted_by_user_id_fkey"),
        ):
            with pytest.raises(IntegrityError, match=constraint):
                with transaction.atomic():
                    cur.execute(
                        "insert into records.request_access "
                        "(request_id, user_id, granted_by_user_id) values (%s, %s, %s)",
                        values,
                    )


@pytest.mark.app_role
def test_a_grant_can_be_revoked_but_not_repointed_or_deleted(
    db, staff_user, colleague_user, agency
) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        access_id = _grant(cur, request_id=request_id, user_id=colleague_user.id)
        for statement, params in (
            (
                "update records.request_access set user_id = %s where id = %s",
                [staff_user.id, access_id],
            ),
            ("update records.request_access set granted_at = now() where id = %s", [access_id]),
            ("delete from records.request_access where id = %s", [access_id]),
        ):
            with pytest.raises(ProgrammingError, match="permission denied"):
                with transaction.atomic():
                    cur.execute(statement, params)
        cur.execute("select user_id from records.request_access where id = %s", [access_id])
        assert cur.fetchone() == (colleague_user.id,)


def test_reversing_0010_refuses_while_an_access_row_exists_and_drops_the_table_after(
    db, staff_user, agency
) -> None:
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        access_id = _grant(cur, request_id=request_id, user_id=staff_user.id)
        # A revoked row blocks too: it is history, and history is not dropped to tidy a reverse.
        cur.execute(
            "update records.request_access set revoked_at = now() where id = %s", [access_id]
        )

    with pytest.raises(DatabaseError, match="cannot reverse records_db.0010"):
        _migrate(("records_db", "0009_source_notes"))
    with connection.cursor() as cur:
        cur.execute("select count(*) from records.request_access")
        assert cur.fetchone()[0] == 1

    # The blocker removed (as the owner; portal_app can delete nothing), the reverse completes.
    with connection.cursor() as cur:
        cur.execute("delete from records.request_access where id = %s", [access_id])
    _migrate(("records_db", "0009_source_notes"))
    with connection.cursor() as cur:
        cur.execute("select to_regclass('records.request_access')")
        assert cur.fetchone()[0] is None


def test_0010_refuses_to_apply_while_a_request_is_already_privileged(
    db, staff_user, agency
) -> None:
    """A request already privileged when the table arrives would have nobody on its list, and no
    screen would show that it exists. The migration says so instead of letting that happen."""
    _migrate(("records_db", "0009_source_notes"))
    with connection.cursor() as cur:
        request_id = _insert_request(cur, agency_id=agency.id, requester_user_id=staff_user.id)
        cur.execute(
            "update records.requests set sensitivity = 'privileged' where id = %s", [request_id]
        )
    with pytest.raises(DatabaseError, match="cannot apply records_db.0010"):
        with transaction.atomic():
            _migrate(("records_db", "0010_request_access"))
    with connection.cursor() as cur:
        cur.execute("select to_regclass('records.request_access')")
        assert cur.fetchone()[0] is None
        cur.execute(
            "update records.requests set sensitivity = 'internal' where id = %s", [request_id]
        )
    _migrate(("records_db", "0010_request_access"))
    with connection.cursor() as cur:
        cur.execute("select to_regclass('records.request_access')")
        assert cur.fetchone()[0] is not None
