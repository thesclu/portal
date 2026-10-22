"""What the application role may do in the records schema, column by column.

portal_app is the role the web server and the worker connect as. It may read
everything and insert into every table, because inserting is how the ledger
grows. It may UPDATE only the columns the data model calls mutable, and never
the cache columns on requests (current_status, current_status_changed_at,
next_deadline_at), which only schema.sql's own triggers move. It may not
DELETE anywhere: corrections are voids and supersessions. Migration 0003 makes
the recompute functions SECURITY DEFINER so the triggers still work under
these grants.

The role is created by db/init/00_roles.sql (cluster-level). If it is missing
this migration is a no-op, so a database can still be migrated on a machine
that has not run the bootstrap; test_privileges.py will then fail loudly.
"""

from django.db import migrations

GRANTS = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    RAISE NOTICE 'portal_app role missing: skipping records grants';
    RETURN;
  END IF;

  GRANT USAGE ON SCHEMA records TO portal_app;
  GRANT SELECT ON ALL TABLES IN SCHEMA records TO portal_app;
  GRANT INSERT ON ALL TABLES IN SCHEMA records TO portal_app;
  REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA records FROM portal_app;
  REVOKE ALL ON SEQUENCE records.request_tracking_seq FROM portal_app;

  GRANT UPDATE (name, jurisdiction, records_law, portal_automation, default_request_email,
                portal_url, notes)
    ON records.agencies TO portal_app;
  GRANT UPDATE (name, email, phone, title, notes)
    ON records.agency_contacts TO portal_app;
  GRANT UPDATE (role, name, email)
    ON records.users TO portal_app;
  GRANT UPDATE (label, sort_order, is_terminal)
    ON records.status_definitions TO portal_app;
  GRANT UPDATE (label, sort_order)
    ON records.event_definitions TO portal_app;
  GRANT UPDATE (agency_reference_number, subject, request_text, auto_followups_enabled,
                filed_at, inbound_alias_email, updated_at)
    ON records.requests TO portal_app;
  GRANT UPDATE (item_number, parent_item_id, description)
    ON records.request_items TO portal_app;
  GRANT UPDATE (flagged_for_review, flag_note, reviewed_at, reviewed_by_user_id)
    ON records.sources TO portal_app;
  GRANT UPDATE (role)
    ON records.attachments TO portal_app;
  GRANT UPDATE (event_type, occurred_at, display_body, actor_user_id, actor_agency_contact_id,
                related_deadline_id, voided_at, void_reason)
    ON records.events TO portal_app;
  GRANT UPDATE (due_at, status, legal_basis, provision, satisfied_by_event_id,
                superseded_by_deadline_id, flagged_for_review, flag_note)
    ON records.deadlines TO portal_app;
  GRANT UPDATE (last_sent_at, followup_count)
    ON records.deadline_followups TO portal_app;
  GRANT UPDATE (voided_at)
    ON records.status_changes TO portal_app;
  GRANT UPDATE (status, amount_cents, waiver_reason, due_at, paid_at, source_event_id)
    ON records.fees TO portal_app;
  GRANT UPDATE (description, due_at, completed_at, completed_by_user_id)
    ON records.tasks TO portal_app;
  -- audit_log: insert only.
END
$$;
"""

REVOKE_ALL = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA records FROM portal_app;
    REVOKE USAGE ON SCHEMA records FROM portal_app;
  END IF;
END
$$;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0001_schema_sql")]
    operations = [migrations.RunSQL(sql=[GRANTS], reverse_sql=[REVOKE_ALL])]
