"""Every drafting model is an unmanaged view over a table drafting-schema.sql defines; nothing
keeps it in sync automatically. Same tripwire as test_model_db_drift.py does for records.

Two extra assertions this one carries that the records version does not need:
- that none of these models inherits LedgerModel, which would make `delete()` raise and turn the
  schema's deliberate DELETE grant into dead SQL (deliberate departure #1);
- that no drafting table's columns are reachable from the records `search_path`, which is why
  every db_table here has to be schema-qualified.
"""

from typing import Any

from django.db import connection
from portal.drafting.models import DRAFTING_MODELS
from portal.records.models import LedgerModel


def _db_table_name(model: Any) -> str:
    schema, _, table = model._meta.db_table.partition('"."')
    assert schema == "drafting", f"{model.__name__}.Meta.db_table is not in the drafting schema"
    return table


def _db_columns(table: str) -> set[str]:
    with connection.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'drafting' and table_name = %s",
            [table],
        )
        return {row[0] for row in cur.fetchall()}


def test_every_drafting_model_matches_its_table_columns(db) -> None:
    assert len(DRAFTING_MODELS) == 14, f"expected 14 drafting models, found {len(DRAFTING_MODELS)}"
    mismatches = {}
    for model in DRAFTING_MODELS:
        table = _db_table_name(model)
        model_columns = {f.column for f in model._meta.concrete_fields if f.column}
        db_columns = _db_columns(table)
        if model_columns != db_columns:
            mismatches[model.__name__] = {
                "only_on_model": sorted(model_columns - db_columns),
                "only_in_db": sorted(db_columns - model_columns),
            }
    assert not mismatches, mismatches


def test_the_models_cover_every_table_in_the_drafting_schema() -> None:
    """The column comparison above is per model, so a table with no model at all would not show
    up in it. This is the other half."""
    modelled = {_db_table_name(m) for m in DRAFTING_MODELS}
    assert len(modelled) == 14, sorted(modelled)  # P19h: two tables; P21c: fork_events


def test_no_drafting_model_inherits_the_ledger_base(db) -> None:
    offenders = [m.__name__ for m in DRAFTING_MODELS if issubclass(m, LedgerModel)]
    assert offenders == [], (
        f"{offenders} inherit LedgerModel, whose delete() raises unconditionally -- that would "
        "make the drafting schema's DELETE grant unreachable from Python."
    )


def test_drafting_models_are_unmanaged(db) -> None:
    unmanaged = [m.__name__ for m in DRAFTING_MODELS if m._meta.managed]
    assert unmanaged == [], f"{unmanaged} are managed; their DDL belongs to drafting_db.0001"
