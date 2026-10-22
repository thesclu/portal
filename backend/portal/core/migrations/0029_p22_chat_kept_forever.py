"""P22: team chat is kept forever, and its one setting.

owner, 2026-10-16/17: every version of every message, deleted ones included, is kept, and only
the legal director (and the database) can read all chats. The app role holds ordinary DML on
everything in `core` (`portal.db.hooks.grant_core_to_app_role`, re-granted after every migrate),
so the rule is held by triggers rather than by grants:

  - a DELETE on any chat table is refused (a channel is archived, a membership is left, a
    message is deleted by a tombstone, never by removing a row);
  - an UPDATE of a message version is refused too: a version is what was written, as it was.

TRUNCATE (the test runner's flush) is not a row operation and is not affected.

`chat.attachment_max_bytes` (the largest file a person may attach, editable in Settings →
Organisation) is seeded from defaults.toml as core.0002 seeds, and `org.legal_director`'s
description now says what reads it.
"""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed

KEPT = (
    "chat_channels",
    "chat_memberships",
    "chat_messages",
    "chat_message_versions",
    "chat_attachments",
    "chat_mentions",
)

FUNCTION = """
CREATE OR REPLACE FUNCTION core.chat_kept_forever() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Team chat is kept forever: % on %.% is refused.',
    TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
    USING ERRCODE = 'restrict_violation';
END
$$;
"""

LEGAL_DIRECTOR = "org.legal_director"
LEGAL_DIRECTOR_BEFORE = (
    "The person who is the organisation's legal director (their user id; empty: nobody named). "
    "Nothing reads it yet; team chat will."
)
LEGAL_DIRECTOR_AFTER = (
    "The person who is the organisation's legal director (their user id; empty: nobody named). "
    "They alone may read every team chat conversation, edits and deleted messages included, for "
    "discovery; every such read is in the activity log."
)


def _triggers() -> list[str]:
    out = [FUNCTION]
    for table in KEPT:
        out.append(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON core.{table} "
            "FOR EACH ROW EXECUTE FUNCTION core.chat_kept_forever();"
        )
    out.append(
        "CREATE TRIGGER chat_message_versions_no_update BEFORE UPDATE ON "
        "core.chat_message_versions FOR EACH ROW EXECUTE FUNCTION core.chat_kept_forever();"
    )
    return out


def _drop_triggers() -> list[str]:
    out = [
        "DROP TRIGGER IF EXISTS chat_message_versions_no_update ON core.chat_message_versions;"
    ]
    for table in KEPT:
        out.append(f"DROP TRIGGER IF EXISTS {table}_no_delete ON core.{table};")
    out.append("DROP FUNCTION IF EXISTS core.chat_kept_forever();")
    return out


def forward(apps, schema_editor):
    seed_settings(apps, schema_editor)
    apps.get_model("core", "Setting").objects.filter(
        key=LEGAL_DIRECTOR, description=LEGAL_DIRECTOR_BEFORE
    ).update(description=LEGAL_DIRECTOR_AFTER)


def backward(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    Setting.objects.filter(key="chat.attachment_max_bytes").delete()
    Setting.objects.filter(key=LEGAL_DIRECTOR, description=LEGAL_DIRECTOR_AFTER).update(
        description=LEGAL_DIRECTOR_BEFORE
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0028_p22_team_chat")]
    operations = [
        migrations.RunSQL(sql=_triggers(), reverse_sql=_drop_triggers()),
        migrations.RunPython(forward, backward),
    ]
