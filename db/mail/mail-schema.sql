-- The Mail Schema — messages the portal sends, their delivery, and saved replies
-- Companion to records/schema.sql and drafting/drafting-schema.sql (BUILD_PLAN P11).
--
-- THIS FILE IS ADDITIVE AND SELF-CONTAINED. It creates a new `mail` schema and
-- never touches `records` or `drafting`. Every foreign key here points
-- mail -> records or mail -> drafting; nothing points back.
--
-- Why a schema of its own: a message is not an events row. A draft changes,
-- has no occurrence time, and is deleted when discarded, and `records` grants
-- no DELETE. So, as drafting does, this schema GRANTS DELETE and bounds it with
-- a trigger: a draft may be deleted, a message that was queued or sent may not.
-- When the provider accepts a message its sent copy becomes a records.sources
-- row and an outbound records.events row through the records services; that is
-- the seam, and it is one-way.
--
-- Requires: pgcrypto (installed by records/schema.sql).

create schema if not exists mail;


-- ============================================================
-- Messages
-- ============================================================
-- One outbound email. `kind` says what it is for; `status` is its lifecycle:
--   draft    being written; deletable
--   queued   Send was pressed; waits `send_after` (the undo window) for the job
--   sending  the job has it
--   sent     the provider accepted it; `source_id` is its copy in records
--   failed   the provider refused it, or sending is switched off; `error` says
-- `from_address` is always the request's own alias (DECISIONS 14), so a reply
-- lands on the request. `letter_recipient_id` is set on a filing: when the
-- message is sent, that recipient's request is filed with the sent copy.
-- `attach_letter` attaches the request's frozen letter as a PDF at send time.

create table mail.messages (
  id                     uuid primary key default gen_random_uuid(),
  request_id             uuid not null references records.requests(id),
  letter_recipient_id    uuid references drafting.letter_recipients(id),
  in_reply_to_source_id  uuid references records.sources(id),
  kind                   text not null
                           check (kind in ('new','reply','forward','filing','followup')),
  status                 text not null default 'draft'
                           check (status in ('draft','queued','sending','sent','failed')),
  from_address           text not null,
  subject                text not null default '',
  body_text              text not null default '',
  attach_letter          boolean not null default false,
  created_by_user_id     uuid references records.users(id),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  queued_at              timestamptz,
  send_after             timestamptz,
  sent_at                timestamptz,
  -- sent through the provider's test token: accepted, never delivered
  test_send              boolean not null default false,
  provider_message_id    text,
  source_id              uuid references records.sources(id),
  error                  text,
  constraint messages_sent_has_source check (status <> 'sent' or source_id is not null),
  constraint messages_sent_has_time   check (status <> 'sent' or sent_at is not null)
);
create index messages_request_idx on mail.messages (request_id, created_at);
create index messages_status_idx  on mail.messages (status, send_after);
create unique index messages_provider_id_uniq
  on mail.messages (provider_message_id) where provider_message_id is not null;


-- ============================================================
-- Recipients: to, cc, bcc, each with its own delivery state
-- ============================================================

create table mail.message_recipients (
  id                  uuid primary key default gen_random_uuid(),
  message_id          uuid not null references mail.messages(id) on delete cascade,
  kind                text not null check (kind in ('to','cc','bcc')),
  address             text not null,
  name                text,
  delivery_state      text not null default 'pending'
                        check (delivery_state in ('pending','delivered','bounced','complained')),
  delivered_at        timestamptz,
  bounced_at          timestamptz,
  bounce_description  text,
  unique (message_id, kind, address)
);


-- ============================================================
-- Attachments: a join to the request's own files, never a copy
-- ============================================================

create table mail.message_attachments (
  message_id     uuid not null references mail.messages(id) on delete cascade,
  attachment_id  uuid not null references records.attachments(id),
  primary key (message_id, attachment_id)
);


-- ============================================================
-- Delivery events: what the provider told us, append-only
-- ============================================================
-- Keyed on the provider's own id, so a webhook delivered twice is a no-op.

create table mail.delivery_events (
  id                 uuid primary key default gen_random_uuid(),
  message_id         uuid not null references mail.messages(id),
  recipient_id       uuid references mail.message_recipients(id),
  provider_event_id  text not null unique,
  record_type        text not null,
  payload            jsonb not null,
  occurred_at        timestamptz not null,
  created_at         timestamptz not null default now()
);
create index delivery_events_message_idx on mail.delivery_events (message_id, occurred_at);


-- ============================================================
-- Saved replies
-- ============================================================

create table mail.saved_replies (
  id                  uuid primary key default gen_random_uuid(),
  name                text not null,
  body                text not null,
  created_by_user_id  uuid references records.users(id),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  archived_at         timestamptz
);


-- ============================================================
-- The delete guard
-- ============================================================
-- A draft is scratch and may go. Anything that was ever queued is part of
-- what happened and stays: Postgres refuses the delete, whatever the grant.

create or replace function mail.block_delete_of_non_draft() returns trigger as $$
begin
  if old.status <> 'draft' then
    raise exception 'message % is % and cannot be deleted; only a draft can be', old.id, old.status
      using errcode = 'restrict_violation';
  end if;
  return old;
end;
$$ language plpgsql;

create trigger trg_block_delete_of_non_draft
  before delete on mail.messages
  for each row execute function mail.block_delete_of_non_draft();


-- ============================================================
-- Grants
-- ============================================================

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'portal_app') then
    grant usage on schema mail to portal_app;
    grant select, insert, update, delete on
      mail.messages, mail.message_recipients, mail.message_attachments, mail.saved_replies
      to portal_app;
    grant select, insert on mail.delivery_events to portal_app;
  end if;
end
$$;
