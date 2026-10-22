"""What portal_app can and cannot do, asserted against the live grants (records_db.0002/0003).

Every test here runs as the portal_app role (the `app_role` marker in conftest.py).

RECORDS_TABLES is derived from information_schema.tables at runtime rather than hand-copied,
so it can never drift from the actual schema (test_schema_placement.py keeps the one literal
16-name list, as the assertion it exists to be, not a lookup a second file has to stay in sync
with by hand -- see F9: a table added there and forgotten here used to mean its DELETE
privilege was never probed).
"""

import pytest
from django.db import ProgrammingError, connection, transaction


def _records_tables() -> list[str]:
    with connection.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = 'records'"
        )
        return [row[0] for row in cur.fetchall()]


COLUMN_UPDATE_PRIVILEGE_CASES = [
    ("records.requests", "current_status", False),
    ("records.requests", "current_status_changed_at", False),
    ("records.requests", "next_deadline_at", False),
    ("records.requests", "subject", True),
    ("records.status_changes", "status", False),
    ("records.status_changes", "voided_at", True),
    # records_db.0005: portal_app can write the drafting fan-out's item order. F4 made this
    # reachable through edits.EDITABLE too -- an unreachable grant is a standing invitation.
    ("records.request_items", "sort_order", True),
    # records_db.0006: every new column is grantable for UPDATE except the two write-once creator
    # columns on tasks, which follow deadlines.rule_ref (set at insert, never rewritten).
    ("records.requests", "assigned_to_user_id", True),
    ("records.request_items", "provision", True),
    ("records.tasks", "assigned_to_user_id", True),
    ("records.tasks", "created_by_user_id", False),
    ("records.tasks", "created_by_system", False),
    ("records.deadlines", "rule_ref", False),
    ("records.attachments", "page_count", True),
    ("records.agencies", "short_name", True),
    ("records.agencies", "recipient_office", True),
    ("records.agencies", "mailing_street", True),
    ("records.agencies", "mailing_city_state_zip", True),
    ("records.agencies", "filing_method", True),
    ("records.agencies", "portal_kind", True),
    ("records.agencies", "request_email_verified_at", True),
    ("records.agency_contacts", "is_primary", True),
    ("records.sources", "flag_reason", True),
    ("records.sources", "assigned_to_user_id", True),
    ("records.sources", "resolution", True),
    ("records.sources", "resolution_note", True),
    # records_db.0011: the inbound job records that it has run. What a source IS stays fixed.
    ("records.sources", "ingested_at", True),
    ("records.sources", "raw_storage_url", False),
    ("records.sources", "raw_metadata", False),
    ("records.fees", "description", True),
    ("records.fees", "payment_method", True),
    # records_db.0007: the lookup table may be relabelled and reordered, never re-keyed, and the
    # status a lapse moves a request to is a decision, not an edit.
    ("records.deadline_definitions", "label", True),
    ("records.deadline_definitions", "sort_order", True),
    ("records.deadline_definitions", "code", False),
    ("records.deadline_definitions", "overdue_status", False),
    # records_db.0009: a note is voided, never edited or re-pointed. The text, the message it is
    # about and who wrote it are fixed at insert.
    ("records.source_notes", "voided_at", True),
    ("records.source_notes", "void_reason", True),
    ("records.source_notes", "body", False),
    ("records.source_notes", "source_id", False),
    ("records.source_notes", "author_user_id", False),
    ("records.source_notes", "created_at", False),
    # records_db.0010: access ends by being revoked. Who was granted, on which request, by whom
    # and when are fixed at insert, so a grant cannot be re-pointed at another person or request.
    ("records.request_access", "revoked_at", True),
    ("records.request_access", "revoked_by_user_id", True),
    ("records.request_access", "request_id", False),
    ("records.request_access", "user_id", False),
    ("records.request_access", "granted_by_user_id", False),
    ("records.request_access", "note", False),
    ("records.request_access", "granted_at", False),
]


@pytest.mark.app_role
def test_current_user_is_portal_app_without_rls_bypass(db) -> None:
    with connection.cursor() as cur:
        cur.execute("select current_user")
        assert cur.fetchone()[0] == "portal_app"
        cur.execute("select rolbypassrls from pg_roles where rolname = current_user")
        assert cur.fetchone()[0] is False


@pytest.mark.app_role
def test_no_delete_privilege_on_any_records_table(db) -> None:
    tables = _records_tables()
    assert tables, "information_schema.tables returned no records.* tables -- query is wrong"
    with connection.cursor() as cur:
        for table in tables:
            cur.execute(
                "select has_table_privilege('portal_app', %s, 'DELETE')",
                [f"records.{table}"],
            )
            (has_delete,) = cur.fetchone()
            assert has_delete is False, table


@pytest.mark.app_role
def test_column_update_privileges_match_the_grants(db) -> None:
    with connection.cursor() as cur:
        for table, column, expected in COLUMN_UPDATE_PRIVILEGE_CASES:
            cur.execute(
                "select has_column_privilege('portal_app', %s, %s, 'UPDATE')",
                [table, column],
            )
            (can_update,) = cur.fetchone()
            assert can_update is expected, (table, column)


@pytest.mark.app_role
def test_raw_update_of_current_status_is_refused(db) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("UPDATE records.requests SET current_status = 'closed'")


@pytest.mark.app_role
def test_raw_delete_from_audit_log_is_refused(db) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("DELETE FROM records.audit_log")


@pytest.mark.app_role
def test_deadline_definitions_may_be_read_and_added_to_but_never_deleted_from(db) -> None:
    """0002's `ON ALL TABLES` grants covered only the tables that existed then, so a new table has
    no privileges at all until its own migration writes them (HANDOFF, 'most likely to get wrong'
    item 1). This pins what records_db.0007 wrote."""
    with connection.cursor() as cur:
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", False),  # table-level UPDATE; only (label, sort_order) are granted above
            ("DELETE", False),
            ("TRUNCATE", False),
        ):
            cur.execute(
                "select has_table_privilege('portal_app', 'records.deadline_definitions', %s)",
                [privilege],
            )
            (held,) = cur.fetchone()
            assert held is expected, privilege


@pytest.mark.app_role
def test_source_notes_may_be_read_and_added_to_but_never_deleted_from(db) -> None:
    """records_db.0009 wrote its own grants; 0002's `ON ALL TABLES` knew nothing of this table."""
    with connection.cursor() as cur:
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", False),  # table-level UPDATE; only (voided_at, void_reason) are granted
            ("DELETE", False),
            ("TRUNCATE", False),
        ):
            cur.execute(
                "select has_table_privilege('portal_app', 'records.source_notes', %s)",
                [privilege],
            )
            (held,) = cur.fetchone()
            assert held is expected, privilege


@pytest.mark.app_role
def test_request_access_may_be_read_and_added_to_but_never_deleted_from(db) -> None:
    """records_db.0010 wrote its own grants, as 0009 did."""
    with connection.cursor() as cur:
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", False),  # table-level UPDATE; only the two revoke columns are granted
            ("DELETE", False),
            ("TRUNCATE", False),
        ):
            cur.execute(
                "select has_table_privilege('portal_app', 'records.request_access', %s)",
                [privilege],
            )
            (held,) = cur.fetchone()
            assert held is expected, privilege


@pytest.mark.app_role
def test_raw_update_of_a_note_body_is_refused(db) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("UPDATE records.source_notes SET body = 'rewritten'")


@pytest.mark.app_role
def test_raw_update_of_a_write_once_task_column_is_refused(db) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("UPDATE records.tasks SET created_by_system = 'someone_else'")


# ---- P17: row-level security --------------------------------------------------------------------

RLS_TABLES = {
    "records": {
        "requests",
        "access_requests",
        "attachment_items",
        "challenges",
        "deadline_followups",
        "deadlines",
        "events",
        "fees",
        "production_reviews",
        "production_review_items",
        "request_access",
        "request_items",
        "request_links",
        "status_changes",
        "tasks",
        "sources",
        "attachments",
        "source_notes",
        # records_db.0023 (P23b): borrows the task's visibility.
        "task_notes",
    },
    "mail": {"messages", "message_recipients", "message_attachments", "delivery_events"},
}


def test_every_table_that_hangs_off_a_request_has_row_level_security(db) -> None:
    """records_db.0017 / mail_db.0002: the policies exist, are enabled, are for portal_app, and
    cover SELECT, UPDATE and INSERT (DELETE on records has no policy and no grant). The directory,
    the users table and the vocabularies carry none: they name no request."""
    with connection.cursor() as cur:
        cur.execute(
            "select schemaname, tablename, rowsecurity from pg_tables "
            "where schemaname in ('records', 'mail')"
        )
        enabled = {(s, t) for s, t, on in cur.fetchall() if on}
        cur.execute(
            "select schemaname, tablename, cmd, roles from pg_policies "
            "where schemaname in ('records', 'mail')"
        )
        policies = cur.fetchall()
    expected = {(schema, t) for schema, tables in RLS_TABLES.items() for t in tables}
    assert enabled == expected
    by_table: dict[tuple[str, str], set[str]] = {}
    for schema, table, cmd, roles in policies:
        assert roles == ["portal_app"] or roles == "{portal_app}", (schema, table, roles)
        by_table.setdefault((schema, table), set()).add(cmd)
    for key in expected:
        wanted = {"SELECT", "UPDATE", "INSERT"} | ({"DELETE"} if key[0] == "mail" else set())
        assert by_table.get(key) == wanted, (key, by_table.get(key))


@pytest.mark.app_role
def test_the_app_role_sees_no_request_row_without_a_context(db) -> None:
    """Default deny: with app.context unset, portal_app reads nothing from requests. The app_role
    fixture sets 'system'; this clears it for one statement and puts it back."""
    with connection.cursor() as cur:
        cur.execute("select current_setting('app.context', true)")
        assert cur.fetchone()[0] == "system"
        cur.execute("select set_config('app.context', '', true)")
        cur.execute("select records.request_visible_cols(gen_random_uuid(), 'public', null)")
        assert cur.fetchone()[0] is False
        cur.execute("select set_config('app.context', 'system', true)")
        cur.execute("select records.request_visible_cols(gen_random_uuid(), 'privileged', null)")
        assert cur.fetchone()[0] is True
