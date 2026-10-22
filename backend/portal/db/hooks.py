"""The schemas have to exist before Django creates its first table.

Every connection carries search_path=core,records,public, so an unqualified
CREATE TABLE lands in `core`. That only works if `core` exists when the
migration recorder creates django_migrations, which happens before any
migration runs. This receiver is connected to pre_migrate in core.apps and is
idempotent, so it costs nothing on later runs and makes a fresh test database
work exactly like the dev one.
"""

from typing import Any

from django.db import connections

SCHEMAS = ("core", "records")


def ensure_schemas(sender: Any = None, using: str = "default", **kwargs: Any) -> None:
    with connections[using].cursor() as cursor:
        for schema in SCHEMAS:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def grant_core_to_app_role(sender: Any = None, using: str = "default", **kwargs: Any) -> None:
    """The app role gets ordinary DML on everything in `core`.

    `core` holds Django's operational tables (sessions, auth, jobs, settings),
    where a normal application role is the right level of privilege. The
    records schema is the opposite: its grants are spelled out column by column
    in records_db.0002 and never widened here. Connected to post_migrate so new
    tables from any app are covered; the ALTER DEFAULT PRIVILEGES line covers
    tables created later in the same session too.
    """
    with connections[using].cursor() as cursor:
        cursor.execute("select 1 from pg_roles where rolname = 'portal_app'")
        if cursor.fetchone() is None:
            return
        cursor.execute("GRANT USAGE ON SCHEMA core TO portal_app")
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO portal_app"
        )
        cursor.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO portal_app")
        cursor.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA core "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO portal_app"
        )
        cursor.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT USAGE, SELECT ON SEQUENCES TO portal_app"
        )
