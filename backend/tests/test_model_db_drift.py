"""Every records model is an unmanaged view (models/_base.py); nothing keeps it in sync with the
database automatically. This is the tripwire: if schema.sql or an additive migration changes a
column and nobody updates the model, this test catches the drift.
"""

from django.db import connection
from portal.records.models import LEDGER_MODELS, Deadline, Request


def _db_table_name(model: type) -> str:
    # Meta.db_table is written as `records"."<table>` so Django quotes it as "records"."<table>".
    schema, _, table = model._meta.db_table.partition('"."')
    assert schema == "records", f"{model.__name__}.Meta.db_table is not in the records schema"
    return table


def _db_columns(table: str) -> set[str]:
    with connection.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'records' and table_name = %s",
            [table],
        )
        return {row[0] for row in cur.fetchall()}


def test_every_ledger_model_matches_its_table_columns(db) -> None:
    # An empty LEDGER_MODELS would make the loop below vacuously pass. Pin the known count
    # (records/models/__init__.py is hand-maintained) so a model nobody adds to the list stays
    # visible instead of silently uncovered. 17 since records_db.0007 added deadline_definitions,
    # 18 since records_db.0009 added source_notes, 19 since records_db.0010 added request_access;
    # 28 since records_db.0023 added task_notes (P23b).
    assert len(LEDGER_MODELS) == 28, f"expected 28 ledger models, found {len(LEDGER_MODELS)}"
    mismatches = {}
    for model in LEDGER_MODELS:
        table = _db_table_name(model)
        model_columns = {f.column for f in model._meta.concrete_fields}
        db_columns = _db_columns(table)
        if model_columns != db_columns:
            mismatches[model.__name__] = {
                "only_on_model": sorted(model_columns - db_columns),
                "only_in_db": sorted(db_columns - model_columns),
            }
    assert not mismatches, mismatches


def test_requests_model_declares_sensitivity(db) -> None:
    columns = {f.column for f in Request._meta.concrete_fields}
    assert "sensitivity" in columns
    # The model-only check above passes even if the column were dropped from the database
    # (or never landed there) as long as the model still declares the field. Query the
    # database directly too, so this test proves the additive migration actually landed.
    assert "sensitivity" in _db_columns(_db_table_name(Request))


def test_deadlines_model_declares_rule_ref(db) -> None:
    columns = {f.column for f in Deadline._meta.concrete_fields}
    assert "rule_ref" in columns
    assert "rule_ref" in _db_columns(_db_table_name(Deadline))
