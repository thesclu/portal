-- The Drafting Schema — letter templates, drafts, and filing fan-out
-- Companion to records/schema.sql. Rationale lives in drafting-schema.md.
--
-- THIS FILE IS ADDITIVE AND SELF-CONTAINED.
--
-- It creates a new `drafting` schema and never touches `records`. That is
-- deliberate and load-bearing:
--
--   * records/schema.sql is pinned by sha256 in test_schema_sql_verbatim.py.
--     Nothing here edits it.
--   * A test pins the exact 16-table placement of the `records` schema.
--     Putting a drafting table in `records` would break it. Hence a third
--     schema alongside `records` and `core`.
--   * Every foreign key here points drafting -> records. Nothing in records
--     points back. Drafting produces requests; requests do not need to know
--     they came from a draft.
--
-- Two rules here differ from the records schema on purpose. Both are
-- explained in drafting-schema.md §"Two deliberate departures":
--
--   1. DELETE IS GRANTED on drafting tables. An unfiled draft is a scratch
--      document, not a record of anything. This is a carve-out from the
--      system's never-delete invariant and is meant to be visible as one.
--   2. Deletion is guarded by triggers, not by withholding the grant --
--      you may delete a draft nothing was ever filed from, and may not
--      delete one that produced a real request.
--
-- Requires: pgcrypto (already installed by records/schema.sql).

create schema if not exists drafting;


-- ============================================================
-- The house shell: letterhead, opening, closing
-- ============================================================
-- The org-wide furniture every letter is wrapped in. Distinct from a
-- subject-matter template, which supplies the items being requested.

create table drafting.letter_shells (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  letterhead    text not null,
  opening_body  text not null,
  closing_body  text not null,
  is_default    boolean not null default false,
  archived_at   timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- at most one default shell at a time
create unique index letter_shells_one_default
  on drafting.letter_shells (is_default)
  where is_default and archived_at is null;


-- ============================================================
-- Shared blocks: reusable prose used across templates
-- ============================================================
-- e.g. the "Record/writing definition" block that appears in every CPRA
-- letter. Edited in one place, pulled into many templates.

create table drafting.shared_blocks (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  body         text not null,
  archived_at  timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);


-- ============================================================
-- Templates: the reusable, deliberately-authored letter
-- ============================================================
-- A template is edited for its own sake, in its own destination. Editing a
-- draft on the way to filing NEVER writes back here.

create table drafting.letter_templates (
  id             uuid primary key default gen_random_uuid(),
  name           text not null,
  jurisdiction   text,             -- 'California'
  legal_basis    text,             -- 'CPRA' / 'PRA' / 'PRL' / 'FOIA'
  shell_id       uuid references drafting.letter_shells(id),
  notes          text,
  archived_at    timestamptz,      -- retirement: hidden from the picker, still reusable
  created_by_user_id uuid references records.users(id),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index on drafting.letter_templates (archived_at);

create table drafting.letter_template_items (
  id              uuid primary key default gen_random_uuid(),
  template_id     uuid not null references drafting.letter_templates(id) on delete cascade,
  item_number     text not null,   -- '3', '4a' -- as it appears in the letter
  parent_item_id  uuid references drafting.letter_template_items(id) on delete cascade,
  body            text not null,
  sort_order      int not null default 0
);

create index on drafting.letter_template_items (template_id, sort_order);

create table drafting.letter_template_citations (
  id            uuid primary key default gen_random_uuid(),
  template_id   uuid not null references drafting.letter_templates(id) on delete cascade,
  item_id       uuid references drafting.letter_template_items(id) on delete cascade,
  marker        text not null,     -- the footnote marker used in the body
  citation_text text not null,     -- 'Gov. Code § 7920.000 et seq.'
  sort_order    int not null default 0
);

create index on drafting.letter_template_citations (template_id, sort_order);

create table drafting.letter_template_blocks (
  template_id      uuid not null references drafting.letter_templates(id) on delete cascade,
  shared_block_id  uuid not null references drafting.shared_blocks(id),
  sort_order       int not null default 0,
  primary key (template_id, shared_block_id)
);


-- ============================================================
-- Template revisions: written lazily, only when something is filed
-- ============================================================
-- Edit a template all afternoon and nothing is written here. The first time
-- a filing uses a given template state, that state is frozen as one row and
-- every recipient in the wave points at it.
--
-- This exists because filing-time edits are local to the draft, which means
-- a filed letter is the template PLUS that drafter's edits MINUS the
-- placeholders -- contaminated as evidence of what the template said. The
-- snapshot is the only clean record of that.
--
-- `content` holds the whole frozen structure: shell text, items, citations,
-- and resolved shared blocks, so the row is self-contained and readable
-- without joining anything that may since have changed.

create table drafting.letter_template_revisions (
  id           uuid primary key default gen_random_uuid(),
  template_id  uuid not null references drafting.letter_templates(id) on delete restrict,
  revision     int not null,
  content      jsonb not null,
  created_at   timestamptz not null default now(),
  unique (template_id, revision)
);

-- ON DELETE RESTRICT above is the whole retirement rule: a template that has
-- ever been filed from cannot be deleted, only archived. Postgres refuses it;
-- no application check required.

create index on drafting.letter_template_revisions (template_id, revision desc);


-- ============================================================
-- Drafts: a working letter aimed at specific agencies
-- ============================================================
-- Freely editable, freely deletable, and completely divorced from the
-- template once created. Started from a template, from a past request, or
-- from nothing.

create table drafting.letter_drafts (
  id                 uuid primary key default gen_random_uuid(),
  subject            text not null,
  shell_id           uuid references drafting.letter_shells(id),

  -- provenance: where this draft was started from, if anywhere
  source_template_id uuid references drafting.letter_templates(id) on delete set null,
  source_request_id  uuid references records.requests(id),

  -- passed through to reserve_request when recipients are created
  sensitivity        text not null default 'internal'
                       check (sensitivity in ('public', 'internal', 'privileged')),

  created_by_user_id uuid references records.users(id),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),

  -- a draft starts from at most one place
  constraint letter_drafts_one_source check (
    source_template_id is null or source_request_id is null
  )
);

create index on drafting.letter_drafts (source_template_id);

create table drafting.letter_draft_items (
  id              uuid primary key default gen_random_uuid(),
  draft_id        uuid not null references drafting.letter_drafts(id) on delete cascade,
  item_number     text not null,
  parent_item_id  uuid references drafting.letter_draft_items(id) on delete cascade,
  body            text not null,
  sort_order      int not null default 0
);

create index on drafting.letter_draft_items (draft_id, sort_order);

create table drafting.letter_draft_citations (
  id            uuid primary key default gen_random_uuid(),
  draft_id      uuid not null references drafting.letter_drafts(id) on delete cascade,
  item_id       uuid references drafting.letter_draft_items(id) on delete cascade,
  marker        text not null,
  citation_text text not null,
  sort_order    int not null default 0
);

create index on drafting.letter_draft_citations (draft_id, sort_order);


-- ============================================================
-- Recipients: one row per agency this draft is going to
-- ============================================================
-- This is the seam. A recipient is a draft aimed at one agency; when it is
-- actually filed it acquires a request_id and everything after that lives in
-- the records schema.

create table drafting.letter_recipients (
  id                    uuid primary key default gen_random_uuid(),
  draft_id              uuid not null references drafting.letter_drafts(id) on delete cascade,
  agency_id             uuid not null references records.agencies(id),
  agency_contact_id     uuid references records.agency_contacts(id),

  channel               text not null
                          check (channel in ('email', 'portal', 'mail', 'call')),

  status                text not null default 'pending'
                          check (status in ('pending', 'held', 'filed', 'skipped')),
  hold_reason           text,       -- 'no verified email', 'no tested portal adapter', ...

  -- per-agency placeholder overrides; the defaults resolve from the agency
  -- directory at render time and are not stored
  placeholder_overrides jsonb,

  -- set at filing. the seam into records.
  request_id            uuid references records.requests(id),
  template_revision_id  uuid references drafting.letter_template_revisions(id),
  filed_at              timestamptz,

  created_at            timestamptz not null default now(),

  unique (draft_id, agency_id),

  -- a filed recipient has produced a request, and vice versa
  constraint letter_recipients_filed_has_request check (
    (status = 'filed') = (request_id is not null)
  ),
  constraint letter_recipients_filed_has_time check (
    (status = 'filed') = (filed_at is not null)
  ),
  constraint letter_recipients_held_has_reason check (
    status <> 'held' or hold_reason is not null
  )
);

create index on drafting.letter_recipients (draft_id);
create index on drafting.letter_recipients (agency_id);
create index on drafting.letter_recipients (request_id);
create index on drafting.letter_recipients (template_revision_id);

-- one request comes from at most one recipient
create unique index letter_recipients_request_uniq
  on drafting.letter_recipients (request_id)
  where request_id is not null;


-- ============================================================
-- The delete guards
-- ============================================================
-- Drafts and recipients are deletable -- that is the point -- but only while
-- nothing has been filed from them. Once a recipient has produced a request,
-- it is the record of a real letter and stops being scratch.
--
-- TWO guards, because there are two ways to reach a filed recipient:
--
--   * deleting the draft, which would cascade to its recipients; and
--   * deleting the recipient row directly, which the DELETE grant permits.
--
-- The second is the one that matters most. letter_recipients is the ONLY
-- place the request -> draft -> revision link lives, deliberately: records
-- has no back-pointer. Deleting a filed recipient would therefore destroy,
-- silently and permanently, the answers to "which template produced this
-- request" and "which revision did it inherit" -- the two questions the
-- revision mechanism exists to answer.
--
-- Both are plain BEFORE DELETE guards. They touch no cached column and
-- recompute nothing, so they do not inherit the lock_request concurrency
-- problem that the records triggers have. (That is true of DELETION only --
-- filing a recipient is a check-then-act on `status` and does need the row
-- locked; see drafting-schema.md.)
--
-- They compose: deleting an unfiled draft cascades to recipients that all
-- have a null request_id, so the recipient guard passes and the cascade
-- completes; deleting a filed draft is refused by the draft guard before any
-- cascade begins.

create or replace function drafting.block_delete_of_filed_draft() returns trigger as $$
begin
  if exists (
    select 1 from drafting.letter_recipients
    where draft_id = old.id and request_id is not null
  ) then
    raise exception
      'draft % has filed recipients and cannot be deleted', old.id
      using errcode = 'restrict_violation';
  end if;
  return old;
end;
$$ language plpgsql;

create trigger trg_block_delete_of_filed_draft
  before delete on drafting.letter_drafts
  for each row execute function drafting.block_delete_of_filed_draft();


create or replace function drafting.block_delete_of_filed_recipient() returns trigger as $$
begin
  if old.request_id is not null then
    raise exception
      'recipient % produced request % and cannot be deleted', old.id, old.request_id
      using errcode = 'restrict_violation';
  end if;
  return old;
end;
$$ language plpgsql;

create trigger trg_block_delete_of_filed_recipient
  before delete on drafting.letter_recipients
  for each row execute function drafting.block_delete_of_filed_recipient();


-- ============================================================
-- Grants
-- ============================================================
-- DELETE is granted here and nowhere else in the system. See the header.

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'portal_app') then
    grant usage on schema drafting to portal_app;

    grant select, insert, update, delete on
      drafting.letter_shells,
      drafting.shared_blocks,
      drafting.letter_templates,
      drafting.letter_template_items,
      drafting.letter_template_citations,
      drafting.letter_template_blocks,
      drafting.letter_drafts,
      drafting.letter_draft_items,
      drafting.letter_draft_citations,
      drafting.letter_recipients
      to portal_app;

    -- revisions are write-once history: no UPDATE, no DELETE, ever
    grant select, insert on drafting.letter_template_revisions to portal_app;
  end if;
end
$$;
