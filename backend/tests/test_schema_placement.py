"""Pin where each schema file (plus the additive migrations) actually landed, table by table.

The records count is load-bearing beyond bookkeeping: the drafting schema exists as a third
schema rather than as ten more records tables precisely so that this name set moves only on
purpose. It has moved three times: from 16 to 17 when records_db.0007 added `deadline_definitions`
(BUILD_PLAN P8a), from 17 to 18 when records_db.0009 added `source_notes` (BUILD_PLAN P8b), and from
18 to 19 when records_db.0010 added `request_access` (BUILD_PLAN P9); 19 to 23 when records_db.0012
added the production review tables and request links (BUILD_PLAN P12); 23 to 24 when
records_db.0014 added `access_requests` (BUILD_PLAN P13); 24 to 25 when records_db.0015 added
`challenges` (BUILD_PLAN P14); 27 to 28 when records_db.0023 added `task_notes` (P23b).
BUILD_PLAN rule 4 says every new records table moves it again in the same commit that creates the
table. schema.sql itself still defines exactly the original 16.
"""

from django.db import connection

EXPECTED_RECORDS_TABLES = {
    "agencies",
    "agency_contacts",
    "users",
    "status_definitions",
    "event_definitions",
    "requests",
    "request_items",
    "sources",
    "attachments",
    "events",
    "deadlines",
    "deadline_followups",
    "status_changes",
    "fees",
    "tasks",
    "audit_log",
    # records_db.0007: the kinds of deadline, as rows.
    "deadline_definitions",
    # records_db.0009: notes on a message, before it belongs to any request.
    "source_notes",
    # records_db.0010: who may see a privileged request. With the two above, the only tables not
    # from schema.sql.
    "request_access",
    "production_reviews",
    "production_review_items",
    "attachment_items",
    "request_links",
    "access_requests",
    "challenges",
    "agency_closures",
    "agency_logins",
    # records_db.0023 (P23b): notes on a task.
    "task_notes",
}

EXPECTED_DRAFTING_TABLES = {
    "letter_shells",
    "shared_blocks",
    "letter_templates",
    "letter_template_items",
    "letter_template_citations",
    "letter_template_blocks",
    "letter_template_revisions",
    "letter_drafts",
    "letter_draft_items",
    "letter_draft_citations",
    "letter_recipients",
    # P19h
    "shared_block_citations",
    "letter_shell_blocks",
    # P21c: the drafting tool's webhooks, once each
    "fork_events",
}

EXPECTED_MAIL_TABLES = {
    "messages",
    "message_recipients",
    "message_attachments",
    "delivery_events",
    "saved_replies",
}

EXPECTED_CORE_TABLES = {"users", "settings", "app_roles", "django_migrations", "procrastinate_jobs"}

SECURITY_DEFINER_FUNCTIONS = {
    "recompute_request_current_status",
    "recompute_request_next_deadline",
    "next_tracking_number",
    # records_db.0017 (P17): the visibility rule the policies call, owned by the owner so it
    # may read requests and request_access without the policies applying to itself.
    "request_visible_cols",
    "request_visible",
    "source_visible",
    "sealed_tracking_number",
    "hidden_row_exists",
}


def test_records_schema_has_exactly_twenty_eight_tables(db) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = 'records'"
        )
        tables = {row[0] for row in cur.fetchall()}
    assert tables == EXPECTED_RECORDS_TABLES
    assert len(EXPECTED_RECORDS_TABLES) == 28
    assert {"deadline_definitions", "source_notes", "request_access"} <= tables


def test_drafting_schema_has_exactly_eleven_tables(db) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = 'drafting'"
        )
        tables = {row[0] for row in cur.fetchall()}
    assert tables == EXPECTED_DRAFTING_TABLES
    assert len(EXPECTED_DRAFTING_TABLES) == 14


def test_mail_schema_has_exactly_five_tables(db) -> None:
    """The fourth schema (BUILD_PLAN P11), pinned like drafting: a mail table in `records` would
    move the records count, and nothing moves it by accident."""
    with connection.cursor() as cur:
        cur.execute("select table_name from information_schema.tables where table_schema = 'mail'")
        tables = {row[0] for row in cur.fetchall()}
    assert tables == EXPECTED_MAIL_TABLES
    assert len(EXPECTED_MAIL_TABLES) == 5


def test_core_schema_holds_the_named_tables(db) -> None:
    with connection.cursor() as cur:
        cur.execute("select table_name from information_schema.tables where table_schema = 'core'")
        tables = {row[0] for row in cur.fetchall()}
    assert EXPECTED_CORE_TABLES <= tables


def test_recompute_and_tracking_functions_are_security_definer(db) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """
            select p.proname, p.prosecdef
            from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'records' and p.proname = any(%s)
            """,
            [list(SECURITY_DEFINER_FUNCTIONS)],
        )
        rows = dict(cur.fetchall())
    assert rows.keys() == SECURITY_DEFINER_FUNCTIONS
    assert all(rows.values()), rows


def test_sync_trigger_functions_are_not_security_definer(db) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """
            select p.proname, p.prosecdef
            from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'records' and p.proname like 'sync\\_%'
            """
        )
        rows = dict(cur.fetchall())
    assert rows, "expected at least one sync_* function in records"
    assert not any(rows.values()), rows


def test_no_records_claimed_status_definition(db) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "select sort_order, is_terminal from records.status_definitions where code = %s",
            ["no_records_claimed"],
        )
        row = cur.fetchone()
    assert row is not None, "no_records_claimed status_definitions row is missing"
    sort_order, is_terminal = row
    assert sort_order == 42
    assert is_terminal is False
