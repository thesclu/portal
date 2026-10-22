"""The mail schema: models match tables, the grants are what the file says, and the one delete
guard holds. Same shape as the drafting tests, because the carve-out is the same one: DELETE is
granted, and a trigger bounds it to drafts."""

import uuid
from typing import Any

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from portal.mail.models import MAIL_MODELS
from portal.records.actors import Actor
from portal.records.models import LedgerModel
from portal.records.services import requests as requests_service

MAIL_TABLES = [
    "messages",
    "message_recipients",
    "message_attachments",
    "delivery_events",
    "saved_replies",
]


def _db_table_name(model: Any) -> str:
    schema, _, table = model._meta.db_table.partition('"."')
    assert schema == "mail", f"{model.__name__}.Meta.db_table is not in the mail schema"
    return table


def _db_columns(table: str) -> set[str]:
    with connection.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'mail' and table_name = %s",
            [table],
        )
        return {row[0] for row in cur.fetchall()}


def test_every_mail_model_matches_its_table_columns(db) -> None:
    assert len(MAIL_MODELS) == 5
    mismatches = {}
    for model in MAIL_MODELS:
        table = _db_table_name(model)
        model_columns = {f.column for f in model._meta.concrete_fields if f.column}
        db_columns = _db_columns(table)
        if model_columns != db_columns:
            mismatches[model.__name__] = {
                "only_on_model": sorted(model_columns - db_columns),
                "only_in_db": sorted(db_columns - model_columns),
            }
    assert not mismatches, mismatches
    assert {_db_table_name(m) for m in MAIL_MODELS} == set(MAIL_TABLES)


def test_mail_models_are_unmanaged_and_not_ledger_models(db) -> None:
    assert [m.__name__ for m in MAIL_MODELS if m._meta.managed] == []
    assert [m.__name__ for m in MAIL_MODELS if issubclass(m, LedgerModel)] == []


@pytest.mark.app_role
def test_portal_app_grants_on_the_mail_tables(db) -> None:
    """Everything but delivery events is read-write-delete; delivery events are append-only."""
    with connection.cursor() as cur:
        for table in MAIL_TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "select has_table_privilege('portal_app', %s, %s)", [f"mail.{table}", privilege]
                )
                allowed = cur.fetchone()[0]
                expected = table != "delivery_events" or privilege in ("SELECT", "INSERT")
                assert allowed is expected, (table, privilege)


@pytest.fixture
def a_request(db, staff_user, agency):
    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="For mail",
        request_text="Body.",
    )


def _insert_message(request_id, status: str) -> uuid.UUID:
    with connection.cursor() as cur:
        cur.execute(
            "insert into mail.messages (request_id, kind, status, from_address) "
            "values (%s, 'new', %s, 'a@requests.example.org') returning id",
            [request_id, status],
        )
        return cur.fetchone()[0]


def test_a_draft_can_be_deleted_and_anything_queued_cannot(db, a_request) -> None:
    draft = _insert_message(a_request.pk, "draft")
    with connection.cursor() as cur:
        cur.execute("delete from mail.messages where id = %s", [draft])
        cur.execute("select count(*) from mail.messages where id = %s", [draft])
        assert cur.fetchone()[0] == 0
    for status in ("queued", "sent", "failed"):
        row = _insert_message(a_request.pk, status) if status != "sent" else None
        if row is None:
            continue
        with pytest.raises(IntegrityError, match="cannot be deleted"):
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute("delete from mail.messages where id = %s", [row])


def test_a_sent_message_needs_its_source_and_time(db, a_request) -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _insert_message(a_request.pk, "sent")


@pytest.mark.app_role
def test_portal_app_cannot_touch_a_delivery_event_once_written(db, a_request) -> None:
    for statement in (
        "update mail.delivery_events set record_type = 'x'",
        "delete from mail.delivery_events",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"):
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(statement)
