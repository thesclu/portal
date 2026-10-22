"""A person reads their own asks for access (P20g; the gap P20f recorded in HANDOFF).

Under records_db.0017 an ask is a child of its request, so `access_requests_read` shows it only
to people who can see that request. The person who asks is, by definition, not one of them: their
own ask vanished for them, and `GET /records/access-requests/mine` was always empty, so the
no-access page could never say "you asked on …".

This adds one more SELECT policy, for `portal_app` under the web context only: the rows whose
asker (`user_id`) is the signed-in person (`app.user_id`). Postgres ORs permissive policies of
one command, so everyone who could read an ask still can, and the asker now reads their own.
It opens nothing else: the request row and every other child of it stay behind
`request_visible`, and the ask itself holds only what the asker wrote or was told (the request's
id, their reason, the decision and its note). No grant changes (SELECT was granted in 0014); no
table, no column; additive. The reverse drops the policy.
"""

from django.db import migrations

FORWARD = """
CREATE POLICY access_requests_read_own ON records.access_requests FOR SELECT TO portal_app
  USING (
    records.app_context() = 'web'
    AND user_id = nullif(coalesce(current_setting('app.user_id', true), ''), '')::uuid
  );
"""

BACKWARD = """
DROP POLICY IF EXISTS access_requests_read_own ON records.access_requests;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0018_agency_logins")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
