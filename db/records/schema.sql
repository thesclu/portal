-- The Request Ledger — public records request tracking data model
-- California / CPRA-focused, built to extend
--
-- Postgres. Paste top to bottom into a migration — the blocks are in
-- dependency order. Full rationale for every table and field lives in
-- records-schema.md alongside this file; this is just the SQL.

create extension if not exists "pgcrypto"; -- for gen_random_uuid()

-- gen_random_uuid() is UUIDv4 — fully random, so every insert lands in a
-- random spot in each primary key's index. Fine at this scale; if events
-- or status_changes reach the tens of millions of rows, switch these PKs
-- to UUIDv7 (time-ordered — new rows insert at the end of the index
-- instead of scattering it). Postgres 18 has uuidv7() built in. Nothing
-- else here has to change either way.

-- ============================================================
-- agencies, agency_contacts, users
-- ============================================================

create table agencies (
  id                     uuid primary key default gen_random_uuid(),
  name                   text not null,
  jurisdiction           text not null,
  records_law            text,
  portal_automation      text check (portal_automation in ('none','filing_only','filing_and_sync')),
  default_request_email  text,
  portal_url             text,
  notes                  text,
  created_at             timestamptz not null default now()
);

create table agency_contacts (
  id          uuid primary key default gen_random_uuid(),
  agency_id   uuid not null references agencies(id),
  name        text not null,
  email       text,
  phone       text,
  title       text,
  notes       text,
  created_at  timestamptz not null default now()
);

create table users (
  id          uuid primary key default gen_random_uuid(),
  role        text not null check (role in ('staff','requester','admin')),
  name        text not null,
  email       text,
  created_at  timestamptz not null default now()
);

-- ============================================================
-- status_definitions, event_definitions
-- ============================================================

create table status_definitions (
  code        text primary key,
  label       text not null,
  sort_order  int not null,
  is_terminal boolean not null default false
);

insert into status_definitions (code, label, sort_order, is_terminal) values
  ('pending_filing',        'Pending filing',           5, false),
  ('filed',                 'Filed',                   10, false),
  ('acknowledged',          'Acknowledged',            20, false),
  ('in_progress',           'In progress',             30, false),
  ('determination_issued',  'Determination issued',    40, false),
  ('determination_overdue', 'Determination overdue',   45, false),
  ('new_production',        'New production',          50, false),
  ('production_unreviewed', 'Production unreviewed',   55, false),
  ('partial_production',    'Partial production',      60, false),
  ('production_received',   'Production received',     70, false),
  ('production_overdue',    'Production overdue',      75, false),
  ('appealed',              'Appealed',                80, false),
  ('denied',                'Denied',                  90, true),
  ('closed',                'Closed',                 100, true),
  ('withdrawn',             'Withdrawn',              110, true);
-- adding a new one later is an insert, not a migration

create table event_definitions (
  code        text primary key,
  label       text not null,
  sort_order  int not null
);

insert into event_definitions (code, label, sort_order) values
  ('submission',       'Submission',        10),
  ('correspondence',   'Correspondence',    20),
  ('acknowledgment',   'Acknowledgment',    30),
  ('extension_notice', 'Extension notice',  35),
  ('fee_notice',       'Fee notice',        38),
  ('determination',    'Determination',     40),
  ('production',       'Production',        50),
  ('follow_up',        'Follow-up',         60),
  ('clarification',    'Clarification',     70),
  ('appeal',           'Appeal',            80),
  ('note',             'Note',              90);

-- ============================================================
-- requests, request_items
-- ============================================================

create table requests (
  id                        uuid primary key default gen_random_uuid(),
  tracking_number           text not null unique,
  agency_reference_number   text,
  origin                    text not null check (origin in ('filed','imported')),
  requester_user_id         uuid not null references users(id),
  agency_id                 uuid not null references agencies(id),
  inbound_alias_email       text unique,
  subject                   text not null,
  request_text              text not null,
  auto_followups_enabled    boolean not null default true,
  current_status            text not null references status_definitions(code),
  current_status_changed_at timestamptz,
  next_deadline_at          date,
  created_at                timestamptz not null default now(), -- reservation time
  filed_at                  timestamptz,                         -- null until actually sent
  updated_at                timestamptz not null default now()
);

create index on requests (agency_id);
create index on requests (current_status);
create index on requests (next_deadline_at);

create sequence request_tracking_seq;
create or replace function next_tracking_number() returns text as $$
  select 'REQ-' || to_char(now(), 'YYYY') || '-' ||
         lpad(nextval('request_tracking_seq')::text, 6, '0');
$$ language sql;

create table request_items (
  id              uuid primary key default gen_random_uuid(),
  request_id      uuid not null references requests(id),
  item_number     text not null,
  parent_item_id  uuid references request_items(id),
  description     text not null
);

create index on request_items (request_id);

-- ============================================================
-- sources, attachments
-- ============================================================

create table sources (
  id                  uuid primary key default gen_random_uuid(),
  source_type         text not null check (source_type in
                        ('postmark_email','imported_eml','portal_capture','scanned_mail','other')),
  external_ref        text, -- Postmark's own id
  message_id          text, -- the RFC 5322 Message-ID header, for threading
  raw_metadata        jsonb,
  raw_storage_url     text not null,
  flagged_for_review  boolean not null default false,
  flag_note           text,
  reviewed_at         timestamptz,
  reviewed_by_user_id uuid references users(id),
  captured_at         timestamptz not null default now(),
  created_at          timestamptz not null default now()
);

create table attachments (
  id               uuid primary key default gen_random_uuid(),
  source_id        uuid not null references sources(id),
  filename         text not null,
  mime_type        text,
  size_bytes       integer,
  storage_url      text not null,
  role             text not null default 'attachment'
                     check (role in ('attachment','produced_record','supporting_document')),
  checksum_sha256  text,
  created_at       timestamptz not null default now()
);

create index on attachments (source_id);

-- ============================================================
-- events
-- ============================================================

create table events (
  id                        uuid primary key default gen_random_uuid(),
  request_id                uuid not null references requests(id),
  occurred_at               timestamptz not null,
  logged_at                 timestamptz not null default now(),
  event_type                text not null references event_definitions(code),
  channel                   text not null check (channel in ('email','portal','mail','call','system')),
  direction                 text not null check (direction in ('outbound','inbound','internal')),
  actor_user_id             uuid references users(id),
  actor_agency_contact_id   uuid references agency_contacts(id),
  recorded_by_user_id       uuid references users(id),
  recorded_by_system        text, -- 'postmark_webhook', 'auto_followup_job', 'legacy_importer', ...
  display_body              text not null,
  source_id                 uuid references sources(id), -- not unique
  related_deadline_id       uuid, -- FK added below, after deadlines exists
  voided_at                 timestamptz,
  void_reason               text,
  created_at                timestamptz not null default now(),

  constraint events_recorder_not_both check (
    recorded_by_user_id is null or recorded_by_system is null
  )
);

create index on events (request_id, occurred_at);
create index on events (source_id);

-- ============================================================
-- deadlines, deadline_followups
-- ============================================================

create table deadlines (
  id                          uuid primary key default gen_random_uuid(),
  request_id                  uuid not null references requests(id),
  request_item_id             uuid references request_items(id),
  deadline_type               text not null check (deadline_type in ('statutory_determination','production')),
  legal_basis                 text,
  provision                   text,
  basis                       text not null check (basis in ('statutory','agency_commitment')),
  due_at                      date not null,
  status                      text not null default 'active'
                                check (status in ('active','satisfied','missed','superseded','waived')),
  source_event_id             uuid references events(id),
  satisfied_by_event_id       uuid references events(id),
  superseded_by_deadline_id   uuid references deadlines(id),
  flagged_for_review          boolean not null default false,
  flag_note                   text,
  created_at                  timestamptz not null default now()
);

alter table events add constraint events_related_deadline_fk
  foreign key (related_deadline_id) references deadlines(id);

create index on deadlines (status, due_at);       -- the overdue job's query
create index on deadlines (request_id, deadline_type);

create or replace function recompute_request_next_deadline(p_request_id uuid) returns void as $$
begin
  update requests
    set next_deadline_at = (
          select min(due_at) from deadlines
          where request_id = p_request_id and status = 'active'
        ),
        updated_at = now()
    where id = p_request_id;
end;
$$ language plpgsql;

create or replace function sync_request_next_deadline() returns trigger as $$
begin
  if (tg_op = 'DELETE') then
    perform recompute_request_next_deadline(old.request_id);
    return old;
  else
    perform recompute_request_next_deadline(new.request_id);
    return new;
  end if;
end;
$$ language plpgsql;

create trigger trg_sync_next_deadline
  after insert or update of status, due_at or delete on deadlines
  for each row execute function sync_request_next_deadline();

create table deadline_followups (
  id              uuid primary key default gen_random_uuid(),
  request_id      uuid not null references requests(id),
  deadline_type   text not null check (deadline_type in ('statutory_determination','production')),
  last_sent_at    timestamptz,
  followup_count  int not null default 0,
  unique (request_id, deadline_type)
);

-- ============================================================
-- status_changes
-- ============================================================

create table status_changes (
  id                      uuid primary key default gen_random_uuid(),
  request_id              uuid not null references requests(id),
  status                  text not null references status_definitions(code),
  effective_at            timestamptz not null default now(),
  triggering_event_id     uuid references events(id),
  triggering_deadline_id  uuid references deadlines(id),
  reason                  text not null check (reason in ('event','deadline_expired','manual_override')),
  note                    text,
  voided_at               timestamptz,
  created_at              timestamptz not null default now(),

  constraint status_change_trigger_matches_reason check (
    (reason = 'event'            and triggering_event_id is not null and triggering_deadline_id is null) or
    (reason = 'deadline_expired' and triggering_deadline_id is not null and triggering_event_id is null) or
    (reason = 'manual_override'  and triggering_event_id is null and triggering_deadline_id is null)
  ),

  -- the rule that matters most: nothing automated can ever mark a
  -- request as fully, completely done
  constraint full_production_requires_human check (
    status <> 'production_received' or reason = 'manual_override'
  )
);

create index on status_changes (request_id, effective_at);

create or replace function recompute_request_current_status(p_request_id uuid) returns void as $$
declare
  latest record;
begin
  select status, effective_at into latest
    from status_changes
    where request_id = p_request_id and voided_at is null
    order by effective_at desc
    limit 1;

  update requests
    set current_status = latest.status,
        current_status_changed_at = latest.effective_at,
        updated_at = now()
    where id = p_request_id;
end;
$$ language plpgsql;

create or replace function sync_request_current_status() returns trigger as $$
begin
  perform recompute_request_current_status(new.request_id);
  return new;
end;
$$ language plpgsql;

create trigger trg_sync_current_status
  after insert on status_changes
  for each row execute function sync_request_current_status();

create or replace function sync_after_void() returns trigger as $$
begin
  if new.voided_at is not null and old.voided_at is null then
    perform recompute_request_current_status(new.request_id);
  end if;
  return new;
end;
$$ language plpgsql;

create trigger trg_recompute_on_void
  after update of voided_at on status_changes
  for each row execute function sync_after_void();

-- ============================================================
-- fees, tasks, audit_log
-- ============================================================

create table fees (
  id              uuid primary key default gen_random_uuid(),
  request_id      uuid not null references requests(id),
  source_event_id uuid references events(id),
  amount_cents    integer,
  status          text not null check (status in
                    ('assessed','waived','waiting_on_payment','paid','disputed')),
  waiver_reason   text,
  due_at          date,
  paid_at         timestamptz,
  created_at      timestamptz not null default now()
);

create table tasks (
  id                    uuid primary key default gen_random_uuid(),
  request_id            uuid not null references requests(id),
  task_type             text not null,
  description           text not null,
  due_at                timestamptz,
  completed_at          timestamptz,
  completed_by_user_id  uuid references users(id),
  created_at            timestamptz not null default now()
);

create index on tasks (request_id) where completed_at is null;

create table audit_log (
  id                uuid primary key default gen_random_uuid(),
  entity_type       text not null,
  entity_id         uuid not null,
  field_name        text not null,
  old_value         text,
  new_value         text,
  edited_by_user_id uuid references users(id),
  note              text not null,
  edited_at         timestamptz not null default now()
);

create index on audit_log (entity_type, entity_id);
