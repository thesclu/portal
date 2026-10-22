"""Row-level security for privileged requests (BUILD_PLAN P17, DECISIONS 22): P9's rule again,
in Postgres, so an application bug cannot leak a privileged request to a person who is not on
its list.

The rule lives in one SECURITY DEFINER function, `records.request_visible_cols`, owned by the
owner role so it may read `requests` and `request_access` without the policies applying to
itself (a policy that read `request_access` under its own policy would recurse). It reads three
per-transaction settings the application sets:

  app.context   'web' for an API request (set by the session auth, inside the request's
                transaction), 'system' for the worker, management commands and the webhooks.
                Anything else, including unset, sees NO request row: the default is deny.
  app.user_id   the signed-in person's uuid, under 'web'.
  app.role      their records role ('staff', 'admin', 'requester'), under 'web'.
  app.scope     'index' while an administrator lists the privileged requests they are not on
                (tracking numbers only, `access.unlisted_privileged`); otherwise unset.

Policies, for `portal_app` only (the owner is unaffected, which is what migrations and the test
suite need): SELECT and UPDATE see a row when its request is visible; INSERT is allowed under
either context (the services decide what may be written; this is the second line for reading);
there is no DELETE policy, and no DELETE grant either. Every records table that hangs off a
request carries one; `sources`, `attachments` and `source_notes` borrow visibility from the
live events on them, as the Inbox does; unattached mail is staff's. The directory, the users
table and the vocabularies carry none.
"""

from django.db import migrations

CHILD_TABLES = (
    "access_requests",
    "attachment_items",
    "challenges",
    "deadline_followups",
    "deadlines",
    "events",
    "fees",
    "production_reviews",
    "request_access",
    "request_items",
    "status_changes",
    "tasks",
)
SOURCE_TABLES = ("attachments", "source_notes")

FUNCTIONS = """
CREATE OR REPLACE FUNCTION records.app_context() RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('app.context', true), '')
$$;

CREATE OR REPLACE FUNCTION records.request_visible_cols(
  p_id uuid, p_sensitivity text, p_requester_id uuid
) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = records, pg_temp AS $$
DECLARE
  ctx text := coalesce(current_setting('app.context', true), '');
  role text := coalesce(current_setting('app.role', true), '');
  uid uuid := nullif(coalesce(current_setting('app.user_id', true), ''), '')::uuid;
BEGIN
  IF ctx = 'system' THEN RETURN true; END IF;
  IF ctx <> 'web' OR uid IS NULL THEN RETURN false; END IF;
  IF role = 'requester' THEN RETURN p_requester_id = uid; END IF;
  IF role NOT IN ('staff', 'admin') THEN RETURN false; END IF;
  IF p_sensitivity IS DISTINCT FROM 'privileged' THEN RETURN true; END IF;
  IF p_requester_id = uid THEN RETURN true; END IF;
  IF role = 'admin' AND coalesce(current_setting('app.scope', true), '') = 'index' THEN
    RETURN true;
  END IF;
  RETURN EXISTS (
    SELECT 1 FROM records.request_access a
    WHERE a.request_id = p_id AND a.user_id = uid AND a.revoked_at IS NULL
  );
END
$$;

CREATE OR REPLACE FUNCTION records.request_visible(p_request_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = records, pg_temp AS $$
  SELECT coalesce(
    (SELECT records.request_visible_cols(r.id, r.sensitivity, r.requester_user_id)
       FROM records.requests r WHERE r.id = p_request_id),
    false)
$$;

CREATE OR REPLACE FUNCTION records.source_visible(p_source_id uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = records, pg_temp AS $$
DECLARE
  ctx text := coalesce(current_setting('app.context', true), '');
  role text := coalesce(current_setting('app.role', true), '');
  attached boolean;
BEGIN
  IF ctx = 'system' THEN RETURN true; END IF;
  IF ctx <> 'web' THEN RETURN false; END IF;
  SELECT EXISTS (
    SELECT 1 FROM records.events e WHERE e.source_id = p_source_id AND e.voided_at IS NULL
  ) INTO attached;
  IF NOT attached THEN RETURN role IN ('staff', 'admin'); END IF;
  RETURN EXISTS (
    SELECT 1 FROM records.events e
    WHERE e.source_id = p_source_id AND e.voided_at IS NULL
      AND records.request_visible(e.request_id)
  );
END
$$;

-- The one thing a member of staff may learn about a privileged request they cannot see: that
-- it exists, and its tracking number, so the refusal can name it and they can ask for access
-- (P9's 403, P13's ask). Nothing else; nobody else.
CREATE OR REPLACE FUNCTION records.sealed_tracking_number(p_request_id uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = records, pg_temp AS $$
  SELECT r.tracking_number FROM records.requests r
  WHERE r.id = p_request_id
    AND r.sensitivity = 'privileged'
    AND coalesce(current_setting('app.context', true), '') IN ('web', 'system')
    AND coalesce(current_setting('app.role', true), '') IN ('staff', 'admin')
       OR (r.id = p_request_id AND coalesce(current_setting('app.context', true), '') = 'system')
$$;

-- Whether a row the caller cannot see exists at all, for the handful of child tables the API
-- addresses by id: so the refusal stays the 403 P9 gives (naming nothing) rather than a 404
-- that would tell a 404-from-403 split apart. Staff only; the answer is one bit.
CREATE OR REPLACE FUNCTION records.hidden_row_exists(p_table text, p_id uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = records, pg_temp AS $$
DECLARE
  found boolean := false;
BEGIN
  IF coalesce(current_setting('app.role', true), '') NOT IN ('staff', 'admin') THEN
    RETURN false;
  END IF;
  CASE p_table
    WHEN 'sources' THEN SELECT EXISTS (SELECT 1 FROM records.sources WHERE id = p_id) INTO found;
    WHEN 'attachments' THEN
      SELECT EXISTS (SELECT 1 FROM records.attachments WHERE id = p_id) INTO found;
    WHEN 'source_notes' THEN
      SELECT EXISTS (SELECT 1 FROM records.source_notes WHERE id = p_id) INTO found;
    WHEN 'events' THEN SELECT EXISTS (SELECT 1 FROM records.events WHERE id = p_id) INTO found;
    WHEN 'deadlines' THEN
      SELECT EXISTS (SELECT 1 FROM records.deadlines WHERE id = p_id) INTO found;
    WHEN 'tasks' THEN SELECT EXISTS (SELECT 1 FROM records.tasks WHERE id = p_id) INTO found;
    WHEN 'fees' THEN SELECT EXISTS (SELECT 1 FROM records.fees WHERE id = p_id) INTO found;
    WHEN 'request_access' THEN
      SELECT EXISTS (SELECT 1 FROM records.request_access WHERE id = p_id) INTO found;
    WHEN 'request_items' THEN
      SELECT EXISTS (SELECT 1 FROM records.request_items WHERE id = p_id) INTO found;
    WHEN 'challenges' THEN
      SELECT EXISTS (SELECT 1 FROM records.challenges WHERE id = p_id) INTO found;
    WHEN 'production_reviews' THEN
      SELECT EXISTS (SELECT 1 FROM records.production_reviews WHERE id = p_id) INTO found;
    WHEN 'request_links' THEN
      SELECT EXISTS (SELECT 1 FROM records.request_links WHERE id = p_id) INTO found;
    WHEN 'access_requests' THEN
      SELECT EXISTS (SELECT 1 FROM records.access_requests WHERE id = p_id) INTO found;
    ELSE found := false;
  END CASE;
  RETURN found;
END
$$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT EXECUTE ON FUNCTION records.hidden_row_exists(text, uuid) TO portal_app;
    GRANT EXECUTE ON FUNCTION records.sealed_tracking_number(uuid) TO portal_app;
    GRANT EXECUTE ON FUNCTION records.app_context() TO portal_app;
    GRANT EXECUTE ON FUNCTION records.request_visible_cols(uuid, text, uuid) TO portal_app;
    GRANT EXECUTE ON FUNCTION records.request_visible(uuid) TO portal_app;
    GRANT EXECUTE ON FUNCTION records.source_visible(uuid) TO portal_app;
  END IF;
END
$$;
"""


def _policies(table: str, using: str) -> str:
    return f"""
ALTER TABLE records.{table} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {table}_read ON records.{table} FOR SELECT TO portal_app USING ({using});
CREATE POLICY {table}_update ON records.{table} FOR UPDATE TO portal_app
  USING ({using}) WITH CHECK (true);
CREATE POLICY {table}_insert ON records.{table} FOR INSERT TO portal_app
  WITH CHECK (records.app_context() IN ('web', 'system'));
"""


def _drop(table: str) -> str:
    return f"""
DROP POLICY IF EXISTS {table}_read ON records.{table};
DROP POLICY IF EXISTS {table}_update ON records.{table};
DROP POLICY IF EXISTS {table}_insert ON records.{table};
ALTER TABLE records.{table} DISABLE ROW LEVEL SECURITY;
"""


FORWARD = (
    FUNCTIONS
    + _policies("requests", "records.request_visible_cols(id, sensitivity, requester_user_id)")
    + "".join(_policies(t, "records.request_visible(request_id)") for t in CHILD_TABLES)
    + _policies(
        "production_review_items",
        "EXISTS (SELECT 1 FROM records.production_reviews pr WHERE pr.id = review_id)",
    )
    + _policies(
        "request_links",
        "records.request_visible(from_request_id) AND records.request_visible(to_request_id)",
    )
    + _policies("sources", "records.source_visible(id)")
    + "".join(_policies(t, "records.source_visible(source_id)") for t in SOURCE_TABLES)
)

BACKWARD = (
    "".join(
        _drop(t)
        for t in (
            "requests",
            *CHILD_TABLES,
            "production_review_items",
            "request_links",
            "sources",
            *SOURCE_TABLES,
        )
    )
    + """
DROP FUNCTION IF EXISTS records.hidden_row_exists(text, uuid);
DROP FUNCTION IF EXISTS records.sealed_tracking_number(uuid);
DROP FUNCTION IF EXISTS records.source_visible(uuid);
DROP FUNCTION IF EXISTS records.request_visible(uuid);
DROP FUNCTION IF EXISTS records.request_visible_cols(uuid, text, uuid);
DROP FUNCTION IF EXISTS records.app_context();
"""
)


class Migration(migrations.Migration):
    dependencies = [("records_db", "0016_closures_and_merges")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
