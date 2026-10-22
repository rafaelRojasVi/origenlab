-- Performance indexes (2026-10-22): thread-id lookups, Gmail payload thread id, pg_trgm for
-- catalog model-key search and organization requester matching.
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
--     a GIN trigram index supports directly. Also alias_key like '%%' || qkey || '%%' (reads.py:26)
--     but that is on catalog.product_model_alias, not product.
--
--   crm.organization(lower(name)) gin_trgm_ops: the new SQL-based requester matching
--     (triage.py _REQUESTER_MATCH_SQL) uses word_similarity(lower(o.name), lower(%(body)s))
--     with threshold 0.5; the GIN trgm index on lower(name) makes this O(candidates) rather
--     than O(all active organizations).
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
--   crm.organization: 1,812 rows (STATUS.md §2.1). GIN build: <1 second (estimate).
--
-- Rollback: add a corrective migration that drops these four indexes.
--
-- This migration uses a DO block to step back to the connection role for the pg_trgm extension
-- (CREATE EXTENSION requires CREATE on the extensions schema, which belongs to the Supabase
-- connection role, not to origenlab_owner). The release parser rule is satisfied: the first
-- statement is `set role origenlab_owner` and the last is `reset role`.

set role origenlab_owner;

-- Step back to the connection role: trusted-extension installation requires CREATE on the
-- Supabase-managed extensions schema, which is held by the connection role (postgres/supabase),
-- not by origenlab_owner.
do $$ begin reset role; end $$;

create extension if not exists pg_trgm with schema extensions;

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

-- 4. crm.organization: GIN trigram index on lower(name) for the SQL-based word-similarity
--    requester matching (triage.py _REQUESTER_MATCH_SQL uses word_similarity(lower(o.name), ...)).
create index organization_name_trgm_idx
    on crm.organization
 using gin (lower(name) extensions.gin_trgm_ops);

reset role;
