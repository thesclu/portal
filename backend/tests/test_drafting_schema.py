"""What the drafting schema grants, and what its two delete guards refuse.

Deliberate departure #1 from the records schema: DELETE is granted here. An unfiled draft is a
scratch document and deleting it destroys nothing. That carve-out is only safe because two
BEFORE DELETE triggers bound it, so the grant and the guards are tested together -- a passing
grant test on its own would be evidence of a hole, not of a feature.

Raw SQL throughout, deliberately: these assertions are about the database, so going through the
ORM would only add a layer that could mask a missing grant or an unfired trigger.
"""

import uuid

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from portal.records.actors import Actor
from portal.records.services import requests as requests_service

DRAFTING_TABLES = [
    "letter_shells",
    "shared_blocks",
    "letter_templates",
    "letter_template_items",
    "letter_template_citations",
    "letter_template_blocks",
    "letter_drafts",
    "letter_draft_items",
    "letter_draft_citations",
    "letter_recipients",
]

RESTRICT_VIOLATION = "23001"


def _sqlstate(exc: BaseException) -> str | None:
    """psycopg's own error hangs off the Django wrapper as __cause__ and carries the SQLSTATE."""
    return getattr(exc.__cause__, "sqlstate", None)


def _insert_draft(subject: str = "Scratch") -> uuid.UUID:
    with connection.cursor() as cur:
        cur.execute(
            "insert into drafting.letter_drafts (subject) values (%s) returning id", [subject]
        )
        return cur.fetchone()[0]


def _insert_recipient(draft_id, agency_id, request_id=None) -> uuid.UUID:
    status = "filed" if request_id is not None else "pending"
    filed_at = "now()" if request_id is not None else "null"
    with connection.cursor() as cur:
        cur.execute(
            f"""insert into drafting.letter_recipients
                (draft_id, agency_id, channel, status, request_id, filed_at)
                values (%s, %s, 'email', %s, %s, {filed_at}) returning id""",
            [draft_id, agency_id, status, request_id],
        )
        return cur.fetchone()[0]


@pytest.mark.app_role
def test_portal_app_may_read_write_and_delete_every_drafting_table_but_revisions(db) -> None:
    with connection.cursor() as cur:
        for table in DRAFTING_TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "select has_table_privilege('portal_app', %s, %s)",
                    [f"drafting.{table}", privilege],
                )
                assert cur.fetchone()[0] is True, (table, privilege)


@pytest.mark.app_role
def test_revisions_are_insert_only_at_the_grant_level(db) -> None:
    """The one drafting table that behaves like the records ledger: write-once history."""
    expected = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False}
    with connection.cursor() as cur:
        for privilege, allowed in expected.items():
            cur.execute(
                "select has_table_privilege('portal_app', %s, %s)",
                ["drafting.letter_template_revisions", privilege],
            )
            assert cur.fetchone()[0] is allowed, privilege


@pytest.mark.app_role
def test_the_fork_events_store_is_insert_only_at_the_grant_level(db) -> None:
    """P21c (drafting_db.0006): a webhook is recorded once and never rewritten or removed."""
    expected = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False}
    with connection.cursor() as cur:
        for privilege, allowed in expected.items():
            cur.execute(
                "select has_table_privilege('portal_app', %s, %s)",
                ["drafting.fork_events", privilege],
            )
            assert cur.fetchone()[0] is allowed, privilege


@pytest.mark.app_role
def test_portal_app_really_can_delete_an_unfiled_draft(db) -> None:
    """has_table_privilege only reads the catalogue. Issue the DELETE for real."""
    draft_id = _insert_draft()
    with connection.cursor() as cur:
        cur.execute("delete from drafting.letter_drafts where id = %s", [draft_id])
        cur.execute("select count(*) from drafting.letter_drafts where id = %s", [draft_id])
        assert cur.fetchone()[0] == 0


@pytest.mark.app_role
def test_portal_app_cannot_update_or_delete_a_revision_row(db) -> None:
    from django.db import ProgrammingError

    for statement in (
        "update drafting.letter_template_revisions set revision = 1",
        "delete from drafting.letter_template_revisions",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"):
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(statement)


@pytest.fixture
def filed_request(db, staff_user, agency):
    """A real reserved request, so a recipient row can legally claim status='filed'."""
    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Filed already",
        request_text="Body.",
    )


def test_deleting_a_draft_that_has_a_filed_recipient_is_refused(db, agency, filed_request) -> None:
    draft_id = _insert_draft("Has a filed recipient")
    _insert_recipient(draft_id, agency.pk, filed_request.pk)

    with pytest.raises(IntegrityError) as caught:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("delete from drafting.letter_drafts where id = %s", [draft_id])
    assert _sqlstate(caught.value) == RESTRICT_VIOLATION
    assert "cannot be deleted" in str(caught.value)

    with connection.cursor() as cur:
        cur.execute("select count(*) from drafting.letter_drafts where id = %s", [draft_id])
        assert cur.fetchone()[0] == 1


def test_deleting_a_filed_recipient_directly_is_refused(db, agency, filed_request) -> None:
    """The guard that matters most: letter_recipients is the only place the
    request -> draft -> revision link lives, so deleting one silently destroys it."""
    draft_id = _insert_draft("Direct recipient delete")
    recipient_id = _insert_recipient(draft_id, agency.pk, filed_request.pk)

    with pytest.raises(IntegrityError) as caught:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("delete from drafting.letter_recipients where id = %s", [recipient_id])
    assert _sqlstate(caught.value) == RESTRICT_VIOLATION

    with connection.cursor() as cur:
        cur.execute("select count(*) from drafting.letter_recipients where id = %s", [recipient_id])
        assert cur.fetchone()[0] == 1


def test_deleting_an_unfiled_draft_cascades_through_the_recipient_guard(db, agency) -> None:
    """The two guards compose: an unfiled recipient passes its own guard, so the cascade
    completes rather than the draft-level refusal firing on a draft that filed nothing."""
    draft_id = _insert_draft("Nothing filed")
    recipient_id = _insert_recipient(draft_id, agency.pk, None)

    with connection.cursor() as cur:
        cur.execute("delete from drafting.letter_drafts where id = %s", [draft_id])
        cur.execute("select count(*) from drafting.letter_recipients where id = %s", [recipient_id])
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from drafting.letter_drafts where id = %s", [draft_id])
        assert cur.fetchone()[0] == 0


def test_a_template_that_has_been_filed_from_cannot_be_deleted(db) -> None:
    """ON DELETE RESTRICT from letter_template_revisions -- a different SQLSTATE (23503) from the
    two hand-written triggers, and the reason anything catching a deletion refusal must catch
    both."""
    with connection.cursor() as cur:
        cur.execute("insert into drafting.letter_templates (name) values ('T') returning id")
        template_id = cur.fetchone()[0]
        cur.execute(
            "insert into drafting.letter_template_revisions (template_id, revision, content) "
            "values (%s, 1, '{}'::jsonb)",
            [template_id],
        )

    with pytest.raises(IntegrityError) as caught:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("delete from drafting.letter_templates where id = %s", [template_id])
    assert _sqlstate(caught.value) == "23503"


def test_the_two_triggers_are_what_refuses_not_something_else(db, agency, filed_request) -> None:
    """Negative control for the two tests above. DELETE really is granted on these tables, so a
    refusal could in principle come from a missing privilege, a foreign key, or the trigger. Drop
    both triggers and the identical deletes succeed -- which pins the refusal on the triggers and
    proves the two assertions above are not passing for an unrelated reason. Postgres DDL is
    transactional and pytest-django rolls this test back, so the triggers come straight back.
    """
    draft_id = _insert_draft("Trigger control")
    recipient_id = _insert_recipient(draft_id, agency.pk, filed_request.pk)

    with connection.cursor() as cur:
        cur.execute(
            "drop trigger trg_block_delete_of_filed_recipient on drafting.letter_recipients"
        )
        cur.execute("drop trigger trg_block_delete_of_filed_draft on drafting.letter_drafts")
        cur.execute("delete from drafting.letter_recipients where id = %s", [recipient_id])
        assert cur.rowcount == 1
        cur.execute("delete from drafting.letter_drafts where id = %s", [draft_id])
        assert cur.rowcount == 1


# ---- drafting_db.0002: reserve before filing, revision authorship, provisions ------------------


def _reserved_request(staff_user, agency):
    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Reserved, not sent",
        request_text="Body.",
    )


@pytest.mark.app_role
def test_a_recipient_may_hold_a_reserved_request_while_pending_but_not_be_filed_without_one(
    db, staff_user, agency
) -> None:
    request = _reserved_request(staff_user, agency)
    draft_id = _insert_draft()
    with connection.cursor() as cur:
        cur.execute(
            "insert into drafting.letter_recipients (draft_id, agency_id, channel, status, "
            "request_id) values (%s, %s, 'email', 'pending', %s) returning id",
            [draft_id, agency.pk, request.pk],
        )
        (recipient_id,) = cur.fetchone()
        # Filed still needs a request, and still needs a time (the unchanged constraint).
        with pytest.raises(IntegrityError, match="letter_recipients_filed_has_request"):
            with transaction.atomic():
                cur.execute(
                    "update drafting.letter_recipients set status = 'filed', request_id = null, "
                    "filed_at = now() where id = %s",
                    [recipient_id],
                )
        with pytest.raises(IntegrityError, match="letter_recipients_filed_has_time"):
            with transaction.atomic():
                cur.execute(
                    "update drafting.letter_recipients set status = 'filed' where id = %s",
                    [recipient_id],
                )
        # A reserved recipient is already undeletable: its request exists in records for good.
        with pytest.raises(IntegrityError) as refused:
            with transaction.atomic():
                cur.execute("delete from drafting.letter_recipients where id = %s", [recipient_id])
    assert _sqlstate(refused.value) == RESTRICT_VIOLATION


def test_0002_added_exactly_the_columns_the_brief_names(db) -> None:
    wanted = {
        ("letter_template_revisions", "created_by_user_id", "uuid"),
        ("letter_template_revisions", "summary", "text"),
        ("letter_drafts", "source_revision_id", "uuid"),
        ("letter_template_items", "provision", "text"),
        ("letter_draft_items", "provision", "text"),
    }
    with connection.cursor() as cur:
        cur.execute(
            "select table_name, column_name, data_type from information_schema.columns "
            "where table_schema = 'drafting' and is_nullable = 'YES' and column_name in "
            "('created_by_user_id', 'summary', 'source_revision_id', 'provision') "
            "and table_name in ('letter_template_revisions', 'letter_drafts', "
            "'letter_template_items', 'letter_draft_items')"
        )
        found = set(cur.fetchall())
    # letter_drafts.created_by_user_id is from the base file; everything else here is 0002's.
    assert found - {("letter_drafts", "created_by_user_id", "uuid")} == wanted


@pytest.mark.app_role
def test_a_revisions_author_and_summary_are_as_write_once_as_the_rest_of_it(db, staff_user) -> None:
    with connection.cursor() as cur:
        cur.execute("insert into drafting.letter_templates (name) values ('T') returning id")
        (template_id,) = cur.fetchone()
        cur.execute(
            "insert into drafting.letter_template_revisions (template_id, revision, content, "
            "created_by_user_id, summary) values (%s, 1, '{}', %s, 'first save') returning id",
            [template_id, staff_user.pk],
        )
        (revision_id,) = cur.fetchone()
        with pytest.raises(ProgrammingError, match="permission denied"):
            with transaction.atomic():
                cur.execute(
                    "update drafting.letter_template_revisions set summary = 'rewritten' "
                    "where id = %s",
                    [revision_id],
                )


def test_reversing_0002_refuses_while_anything_uses_it_and_restores_the_constraint_after(
    db, staff_user, agency
) -> None:
    from django.db import DatabaseError
    from django.db.migrations.executor import MigrationExecutor

    def reverse() -> None:
        MigrationExecutor(connection).migrate([("drafting_db", "0001_drafting_schema_sql")])

    request = _reserved_request(staff_user, agency)
    draft_id = _insert_draft()
    with connection.cursor() as cur:
        cur.execute(
            "insert into drafting.letter_recipients (draft_id, agency_id, channel, status, "
            "request_id) values (%s, %s, 'email', 'pending', %s) returning id",
            [draft_id, agency.pk, request.pk],
        )
        (recipient_id,) = cur.fetchone()
    with pytest.raises(DatabaseError, match="cannot reverse drafting_db.0002: 1 reserved"):
        reverse()

    with connection.cursor() as cur:
        cur.execute(
            "update drafting.letter_recipients set request_id = null where id = %s", [recipient_id]
        )
    reverse()
    with connection.cursor() as cur:
        cur.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'letter_recipients_filed_has_request'"
        )
        assert "= (request_id IS NOT NULL)" in cur.fetchone()[0]
        cur.execute(
            "select count(*) from information_schema.columns where table_schema = 'drafting' "
            "and column_name in ('provision', 'summary', 'source_revision_id')"
        )
        assert cur.fetchone()[0] == 0
