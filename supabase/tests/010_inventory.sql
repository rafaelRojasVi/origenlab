-- Slice 0 — inventory proofs: seven schemas, exactly the reviewed 32 tables, ownership, RLS
-- posture, no SECURITY DEFINER function, pinned search_path, `public` empty, forbidden columns
-- absent, send flags false. docs/DOMAIN.md §7; docs/ARCHITECTURE.md §3, §6.1, §6.2.
begin;
create extension if not exists pgtap with schema extensions;
select plan(29);

-- Seven private schemas, owned by origenlab_owner.
select has_schema('crm');
select has_schema('comms');
select has_schema('outbound');
select has_schema('evidence');
select has_schema('catalog');
select has_schema('procurement');
select has_schema('platform');
select is(
  (select count(*)::int from pg_namespace
    where nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and nspowner = 'origenlab_owner'::regrole),
  7, 'all seven application schemas are owned by origenlab_owner');

-- Exactly the reviewed 32 application tables (DOMAIN.md §7).
select set_eq(
  $$ select n.nspname || '.' || c.relname
       from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where c.relkind = 'r'
        and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform') $$,
  array[
    'crm.organization', 'crm.organization_domain', 'crm.organization_relationship', 'crm.external_identifier',
    'crm.person', 'crm.affiliation', 'crm.contact_point', 'crm.opportunity', 'crm.task', 'crm.activity',
    'crm.domain_event', 'crm.quote', 'crm.quote_revision', 'crm.quote_line',
    'comms.mailbox', 'comms.message', 'comms.message_participant', 'comms.attachment',
    'outbound.send_control', 'outbound.campaign', 'outbound.campaign_recipient', 'outbound.send_attempt',
    'outbound.contact_control',
    'evidence.source_record', 'evidence.assertion',
    'catalog.product', 'catalog.supplier_product',
    'procurement.notice',
    'platform.operator', 'platform.command_receipt',
    'crm.address', 'crm.opportunity_participant'
  ],
  'exactly the reviewed 32 application tables exist');

select results_eq(
  $$ select n.nspname::text collate "default", count(*)::int
       from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where c.relkind = 'r'
        and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      group by 1 order by 1 $$,
  $$ values ('catalog', 2), ('comms', 4), ('crm', 16), ('evidence', 2), ('outbound', 5), ('platform', 2), ('procurement', 1) $$,
  'counts by schema: crm 16, comms 4, outbound 5, evidence 2, catalog 2, procurement 1, platform 2');

-- No views, materialized views, partitions or foreign tables in Slice 0.
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('v', 'm', 'p', 'f')
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')),
  0, 'no view, materialized view, partitioned or foreign table exists yet');

-- Every relation and every function in the seven schemas is owned by origenlab_owner.
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and c.relowner <> 'origenlab_owner'::regrole),
  0, 'every relation (tables, indexes, sequences) is owned by origenlab_owner');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and p.proowner <> 'origenlab_owner'::regrole),
  0, 'every application function is owned by origenlab_owner');

-- The closed SECURITY DEFINER list is empty in Slice 0; every function is INVOKER with a pinned path.
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and p.prosecdef),
  0, 'no SECURITY DEFINER function exists (the closed list is empty until Slice 5)');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and not coalesce(p.proconfig @> array['search_path=pg_catalog'], false)),
  0, 'every application function pins search_path = pg_catalog');
select set_eq(
  $$ select n.nspname || '.' || p.proname
       from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform') $$,
  array['platform.reject_mutation', 'crm.organization_reject_parent_cycle', 'crm.domain_event_is_valid'],
  'exactly the three Slice 0 helper functions exist');

-- `public` holds nothing.
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'v', 'm', 'S', 'f', 'p')),
  0, 'public holds no relation');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public'),
  0, 'public holds no function');

-- Extension used by the exclusion constraints.
select has_extension('btree_gist');

-- Columns that must not exist (DOMAIN.md §2.5, §2.6, §2.8, §3.3).
select hasnt_column('crm', 'contact_point', 'consent_status', 'contact_point carries no consent column');
select hasnt_column('crm', 'opportunity', 'person_id', 'opportunity carries no person_id');
select hasnt_column('crm', 'opportunity', 'contact_point_id', 'opportunity carries no contact_point_id');
select hasnt_column('crm', 'external_identifier', 'entity_id', 'external_identifier has no polymorphic subject column');
select hasnt_column('crm', 'address', 'parent_type', 'address is bound by a typed FK, not a parent_type/parent_id pair');
select col_not_null('crm', 'address', 'organization_id', 'address.organization_id is NOT NULL');

-- RLS enabled on all 32 tables and never forced (the owner crosses it by ownership).
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r'
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and c.relrowsecurity),
  32, 'RLS is enabled on all 32 tables');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r'
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and c.relforcerowsecurity),
  0, 'RLS is not FORCEd on any table');

-- Both send flags false, single row id = 1.
select results_eq(
  $$ select id, marketing_enabled, transactional_enabled from outbound.send_control $$,
  $$ values (1, false, false) $$,
  'outbound.send_control holds exactly one row, id = 1, with both flags false');

-- Every table carries its inventory comment.
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r'
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and obj_description(c.oid, 'pg_class') like 'DOMAIN.md §7 #%'),
  32, 'every table is commented with its DOMAIN.md §7 inventory number');

select * from finish();
rollback;
