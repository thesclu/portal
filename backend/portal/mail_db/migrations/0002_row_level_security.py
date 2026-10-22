"""Row-level security on mail (P17): a message is visible when its request is
(`records.request_visible`, records_db.0017); recipients, attachments and delivery events
follow their message. Same shape as the records policies; `portal_app` only; no DELETE policy
(the schema's own trigger bounds deletes to drafts, and the grant stays)."""

from django.db import migrations

CHILDREN = ("message_recipients", "message_attachments", "delivery_events")


def _policies(table: str, using: str) -> str:
    return f"""
ALTER TABLE mail.{table} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {table}_read ON mail.{table} FOR SELECT TO portal_app USING ({using});
CREATE POLICY {table}_update ON mail.{table} FOR UPDATE TO portal_app
  USING ({using}) WITH CHECK (true);
CREATE POLICY {table}_insert ON mail.{table} FOR INSERT TO portal_app
  WITH CHECK (records.app_context() IN ('web', 'system'));
CREATE POLICY {table}_delete ON mail.{table} FOR DELETE TO portal_app USING ({using});
"""


def _drop(table: str) -> str:
    return f"""
DROP POLICY IF EXISTS {table}_read ON mail.{table};
DROP POLICY IF EXISTS {table}_update ON mail.{table};
DROP POLICY IF EXISTS {table}_insert ON mail.{table};
DROP POLICY IF EXISTS {table}_delete ON mail.{table};
ALTER TABLE mail.{table} DISABLE ROW LEVEL SECURITY;
"""


FORWARD = _policies("messages", "records.request_visible(request_id)") + "".join(
    _policies(t, "EXISTS (SELECT 1 FROM mail.messages m WHERE m.id = message_id)")
    for t in CHILDREN
)
BACKWARD = "".join(_drop(t) for t in ("messages", *CHILDREN))


class Migration(migrations.Migration):
    dependencies = [("mail_db", "0001_mail_schema_sql"), ("records_db", "0017_row_level_security")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
