-- Performance indexes (2026-10-22): thread-id lookups, Gmail payload thread id, and pg_trgm
-- for catalog model-key search.
--
-- Evidence for each index:
--   comms.message(provider_thread_id): CASE_CARDS_SQL (triage.py) joins
--     comms.message m ON m.provider_thread_id = t.thread_id; _LINKED_CASES and _TENDER_THREAD join
--     evidence.source_record sr on sr.payload->>'gmail_thread_id' = %s then look up messages on
--     the same thread; _THREAD_ATTACHMENTS filters m.provider_thread_id = %s. The existing
--     message_thread_idx leads with mailbox_id (apps/api routes always have a mailbox) but the
--     worker triage queries join on thread id alone (triage.py:210, 166, 177, 229).
--     api v2/triage_review.py:148 also joins m2.provider_thread_id = m.provider_thread_id.
--
--   evidence.source_record((payload->>'gmail_thread_id')): CASE_CARDS_SQL (triage.py:184,189)
--     and _LINKED_CASES (triage.py:166) and _TENDER_THREAD (triage.py:177) each filter on
--     sr.payload ->> 'gmail_thread_id' = %s or IS NOT NULL. Every gmail_message source record
--     stores the thread id in its payload under that exact key.
--
--   catalog.product(model_key) gin_trgm_ops: catalog/reads.py _SEARCH_WHERE uses
--     p.model_key like '%%' || %(qkey)s || '%%' (reads.py:24) -- a leading-wildcard LIKE that
--     a GIN trigram index supports directly.
--
-- Production lock estimate (row counts from docs/STATUS.md §2.1, 2026-09-30 snapshot):
--   comms.message: not in the count table; the worker captures ~10 messages per 10-minute cron
--     run since 2026-10-05 (~2,600 messages). CREATE INDEX with no CONCURRENTLY takes a brief
--     ShareLock; at this row count: <1 second (estimate).
--   evidence.source_record: ~35 rows (data-bearing, STATUS.md §2.1 clean-room); in production
--     the Gmail sync has been running since 2026-10-05 with a 10-min cron, so roughly
--     ~4,000 rows (estimate). <1 second (estimate).
--   catalog.product: ~10,000 rows (STATUS.md §2.7.61 loaded catalog). GIN build: ~1-5 seconds
--     (estimate). ShareLock for that duration.
--
-- Rollback: add a corrective migration that drops these three indexes.
--
-- Prerequisite (run once as postgres in the Supabase SQL editor — DONE 2026-10-22):
--   create extension if not exists pg_trgm with schema extensions;
--   grant usage on schema extensions to origenlab_owner;
--
-- This migration uses a DO block to step back to the connection role for the pg_trgm extension
-- and the USAGE grant. On a fresh cluster the DO block installs them; on production they are
-- already present and both branches are skipped. The release parser rule is satisfied: the first
-- statement is `set role origenlab_owner` and the last is `reset role`.

set role origenlab_owner;

-- Step back to connection role for the prerequisite check-and-install.
-- In production both checks pass (prerequisites applied 2026-10-22) and neither branch fires.
-- On a fresh cluster (CI, local) the connection is a superuser and both succeed.
do $$
declare
  prereq text :=
    'create extension if not exists pg_trgm with schema extensions; '
    'grant usage on schema extensions to origenlab_owner;';
begin
  reset role;  -- back to connection role (postgres / origenlab_migrator)

  -- (a) install pg_trgm if absent
  if not exists (select 1 from pg_extension where extname = 'pg_trgm') then
    begin
      execute 'create extension pg_trgm with schema extensions';
    exception when insufficient_privilege then
      raise exception
        'pg_trgm is absent and this session cannot install it. '
        'Run once as postgres in the Supabase SQL editor before merging: %', prereq;
    end;
  end if;

  -- (b) ensure origenlab_owner has USAGE on the extensions schema
  if not has_schema_privilege('origenlab_owner', 'extensions', 'USAGE') then
    begin
      execute 'grant usage on schema extensions to origenlab_owner';
    exception when insufficient_privilege then
      raise exception
        'origenlab_owner lacks USAGE on the extensions schema and this session cannot grant it. '
        'Run once as postgres in the Supabase SQL editor before merging: %', prereq;
    end;
  end if;
end $$;

set role origenlab_owner;

-- 1. comms.message: partial B-tree on provider_thread_id alone (for the triage queries that
--    join on thread id without a mailbox filter). The existing message_thread_idx leads with
--    mailbox_id and is not used by those queries.
create index message_provider_thread_idx
    on comms.message (provider_thread_id)
 where provider_thread_id is not null;

-- 2. evidence.source_record: expression index on the Gmail thread id stored in the JSONB
--    payload. Used by CASE_CARDS_SQL, _LINKED_CASES and _TENDER_THREAD which all filter on
--    sr.payload ->> 'gmail_thread_id'.
create index source_record_gmail_thread_idx
    on evidence.source_record ((payload ->> 'gmail_thread_id'))
 where payload ->> 'gmail_thread_id' is not null;

-- 3. catalog.product: GIN trigram index on model_key for the leading-wildcard LIKE search in
--    catalog/reads.py _SEARCH_WHERE (model_key like '%%' || qkey || '%%').
--    extensions.gin_trgm_ops is schema-qualified: hosted Supabase sets extensions in
--    search_path automatically; the test disposable database does not, so the explicit
--    schema reference is needed for portability.
create index product_model_key_trgm_idx
    on catalog.product
 using gin (model_key extensions.gin_trgm_ops);

reset role;
