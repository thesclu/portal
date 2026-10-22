"""Who sees what, for six roles, in the row-level policies' functions (P20h; DECISIONS 22, 37a, 43).

Additive: `CREATE OR REPLACE FUNCTION` on four of 0017's functions, with the same signatures, so
every policy that calls them is unchanged. `records/services/access.py` states the same rule in
Python (`visible_q`, `_visible`, `source_visible_q`); the two must agree and `tests/test_rls.py`
holds them to it role by role.

`request_visible_cols`, under the web context:
  requester         their own requests (unchanged)
  admin             every request, privileged included (37a: "grant-only except for admins")
  staff,
  staff_attorney    every request that is not privileged; a privileged one when they are its
                    requester of record or hold an active grant (unchanged for staff)
  limited_attorney  only requests they hold an active grant on, or are the requester of record of
  intern            requests made by an intern (the requester of record's role is `intern`; see
                    DECISIONS 43 for why that is who "made" a request), plus requests they hold
                    a grant on or are the requester of record of; a privileged one only by grant or
                    as its requester of record
  anything else     nothing (a role added later sees nothing until this function names it)

`source_visible`: mail placed on nothing is triage, read by admin, staff and staff attorneys only
(37a "unmatched mail is for staff and admins"); placed mail follows its requests, as before.
`sealed_tracking_number` and `hidden_row_exists`, the one bit and the one tracking number a
refusal may name, answer the same three roles; for a limited-scope attorney or an intern a hidden
request is indistinguishable from one that does not exist.

The reverse puts back 0017's bodies exactly.
"""

from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION records.request_visible_cols(
  p_id uuid, p_sensitivity text, p_requester_id uuid
) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = records, pg_temp AS $$
DECLARE
  ctx text := coalesce(current_setting('app.context', true), '');
  role text := coalesce(current_setting('app.role', true), '');
  uid uuid := nullif(coalesce(current_setting('app.user_id', true), ''), '')::uuid;
  sealed boolean := p_sensitivity IS NOT DISTINCT FROM 'privileged';
BEGIN
  IF ctx = 'system' THEN RETURN true; END IF;
  IF ctx <> 'web' OR uid IS NULL THEN RETURN false; END IF;
  IF role = 'requester' THEN RETURN p_requester_id = uid; END IF;
  IF role = 'admin' THEN RETURN true; END IF;
  IF role NOT IN ('staff', 'staff_attorney', 'limited_attorney', 'intern') THEN RETURN false; END IF;
  IF NOT sealed AND role IN ('staff', 'staff_attorney') THEN RETURN true; END IF;
  IF p_requester_id = uid THEN RETURN true; END IF;
  IF EXISTS (
    SELECT 1 FROM records.request_access a
    WHERE a.request_id = p_id AND a.user_id = uid AND a.revoked_at IS NULL
  ) THEN
    RETURN true;
  END IF;
  IF NOT sealed AND role = 'intern' THEN
    RETURN EXISTS (SELECT 1 FROM records.users u WHERE u.id = p_requester_id AND u.role = 'intern');
  END IF;
  RETURN false;
END
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
  IF NOT attached THEN RETURN role IN ('staff', 'staff_attorney', 'admin'); END IF;
  RETURN EXISTS (
    SELECT 1 FROM records.events e
    WHERE e.source_id = p_source_id AND e.voided_at IS NULL
      AND records.request_visible(e.request_id)
  );
END
$$;

CREATE OR REPLACE FUNCTION records.sealed_tracking_number(p_request_id uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = records, pg_temp AS $$
  SELECT r.tracking_number FROM records.requests r
  WHERE r.id = p_request_id
    AND r.sensitivity = 'privileged'
    AND coalesce(current_setting('app.context', true), '') IN ('web', 'system')
    AND coalesce(current_setting('app.role', true), '') IN ('staff', 'staff_attorney', 'admin')
       OR (r.id = p_request_id AND coalesce(current_setting('app.context', true), '') = 'system')
$$;

CREATE OR REPLACE FUNCTION records.hidden_row_exists(p_table text, p_id uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = records, pg_temp AS $$
DECLARE
  found boolean := false;
BEGIN
  IF coalesce(current_setting('app.role', true), '') NOT IN ('staff', 'staff_attorney', 'admin')
  THEN
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
"""

BACKWARD = """
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

CREATE OR REPLACE FUNCTION records.sealed_tracking_number(p_request_id uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = records, pg_temp AS $$
  SELECT r.tracking_number FROM records.requests r
  WHERE r.id = p_request_id
    AND r.sensitivity = 'privileged'
    AND coalesce(current_setting('app.context', true), '') IN ('web', 'system')
    AND coalesce(current_setting('app.role', true), '') IN ('staff', 'admin')
       OR (r.id = p_request_id AND coalesce(current_setting('app.context', true), '') = 'system')
$$;

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
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0020_six_roles")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
