"""Production review, the file-to-item join, and request links (BUILD_PLAN P12). Four new tables;
additive, nothing in schema.sql moves.

  records.production_reviews        one review of one production: who, when, of which event, the
                                    note, and which review it supersedes. A later review supersedes;
                                    nothing is edited.
  records.production_review_items   one row per request item in a review: the disposition
                                    (produced | partly | withheld | not_yet), the exemption the
                                    agency cited and where, a note.
  records.attachment_items          a produced file tied to a request and, optionally, one of its
                                    items. NULL item = "whole request". Voided, never deleted.
  records.request_links             one request named as a duplicate of, or a follow-on to,
                                    another, with a reason. "Same campaign" is dropped with
                                    campaigns. Voided, never deleted.

Grants (BUILD_PLAN rule 4): SELECT and INSERT everywhere; UPDATE on the void columns of the two
tables that can be voided; never DELETE, never UPDATE on a review or its rows, which is what
"append-only" means at the database. The reverse drops the tables and fails loudly while any row
exists, as 0007, 0009 and 0010 do; the records placement pin becomes 23 in the same commit.
"""

from django.db import migrations

FORWARD = """
CREATE TABLE records.production_reviews (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id           uuid NOT NULL REFERENCES records.requests (id),
  event_id             uuid NOT NULL REFERENCES records.events (id),
  reviewed_by_user_id  uuid NOT NULL REFERENCES records.users (id),
  reviewed_at          timestamptz NOT NULL DEFAULT now(),
  note                 text,
  outcome              text NOT NULL CHECK (outcome IN ('partial_production', 'production_received')),
  status_change_id     uuid REFERENCES records.status_changes (id),
  supersedes_review_id uuid REFERENCES records.production_reviews (id)
);
CREATE INDEX production_reviews_request_idx ON records.production_reviews (request_id, reviewed_at);

CREATE TABLE records.production_review_items (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  review_id        uuid NOT NULL REFERENCES records.production_reviews (id),
  request_item_id  uuid NOT NULL REFERENCES records.request_items (id),
  disposition      text NOT NULL CHECK (disposition IN ('produced', 'partly', 'withheld', 'not_yet')),
  exemption_cited  text,
  cited_where      text,
  note             text,
  UNIQUE (review_id, request_item_id)
);

CREATE TABLE records.attachment_items (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attachment_id    uuid NOT NULL REFERENCES records.attachments (id),
  request_id       uuid NOT NULL REFERENCES records.requests (id),
  request_item_id  uuid REFERENCES records.request_items (id),
  review_id        uuid REFERENCES records.production_reviews (id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  voided_at        timestamptz,
  void_reason      text
);
CREATE INDEX attachment_items_request_idx ON records.attachment_items (request_id);
CREATE INDEX attachment_items_attachment_idx ON records.attachment_items (attachment_id);

CREATE TABLE records.request_links (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  from_request_id     uuid NOT NULL REFERENCES records.requests (id),
  to_request_id       uuid NOT NULL REFERENCES records.requests (id),
  kind                text NOT NULL CHECK (kind IN ('duplicate', 'follow_on')),
  reason              text NOT NULL,
  created_by_user_id  uuid NOT NULL REFERENCES records.users (id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  voided_at           timestamptz,
  void_reason         text,
  CHECK (from_request_id <> to_request_id)
);
CREATE UNIQUE INDEX request_links_active_uniq
  ON records.request_links (from_request_id, to_request_id, kind) WHERE voided_at IS NULL;
CREATE INDEX request_links_to_idx ON records.request_links (to_request_id);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT SELECT, INSERT ON records.production_reviews TO portal_app;
    GRANT SELECT, INSERT ON records.production_review_items TO portal_app;
    GRANT SELECT, INSERT ON records.attachment_items TO portal_app;
    GRANT UPDATE (voided_at, void_reason) ON records.attachment_items TO portal_app;
    GRANT SELECT, INSERT ON records.request_links TO portal_app;
    GRANT UPDATE (voided_at, void_reason) ON records.request_links TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT (SELECT count(*) FROM records.production_reviews)
       + (SELECT count(*) FROM records.attachment_items)
       + (SELECT count(*) FROM records.request_links) INTO blocked;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0012: % row(s) exist across production_reviews, attachment_items '
      'and request_links. Ledger rows are never deleted to make a reverse succeed.',
      blocked;
  END IF;
END
$$;

DROP TABLE records.request_links;
DROP TABLE records.attachment_items;
DROP TABLE records.production_review_items;
DROP TABLE records.production_reviews;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0011_source_ingested_at")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
