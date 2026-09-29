-- Slice 0/2 — inventory proofs: seven schemas, exactly the reviewed 41 tables, ownership, RLS
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

-- Exactly the reviewed 41 application tables (DOMAIN.md §7, §7.1, §7.2, §7.3).
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
    'outbound.contact_control', 'outbound.campaign_reply',
    'evidence.source_record', 'evidence.assertion',
    'catalog.product', 'catalog.supplier_product',
    'procurement.notice',
    'platform.operator', 'platform.command_receipt',
    'crm.address', 'crm.opportunity_participant',
    'crm.opportunity_organization', 'crm.opportunity_interest', 'crm.opportunity_evidence',
    'outbound.campaign_block',
    'platform.auth_principal', 'platform.operator_profile', 'platform.auth_event',
    'platform.auth_session'
  ],
  'exactly the reviewed 41 application tables exist');

select results_eq(
  $$ select n.nspname::text collate "default", count(*)::int
       from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where c.relkind = 'r'
        and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      group by 1 order by 1 $$,
  $$ values ('catalog', 2), ('comms', 4), ('crm', 19), ('evidence', 2), ('outbound', 7), ('platform', 6), ('procurement', 1) $$,
  'counts by schema: crm 19, comms 4, outbound 7, evidence 2, catalog 2, procurement 1, platform 6');

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

-- The closed SECURITY DEFINER list (ARCHITECTURE.md §6.2) was empty in Slice 0; slice 5 builds its
-- first entry, outbound.add_contact_control (W10 unsubscribe); slice 1 its second and third,
-- platform.begin_pin_attempt and platform.finish_pin_attempt (the PIN attempt, 20260929100000,
-- which replaced 20260928194000's platform.record_pin_attempt). Every other function is INVOKER.
select is(
  (select array_agg(n.nspname || '.' || p.proname order by 1)::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and p.prosecdef),
  '{outbound.add_contact_control,platform.begin_pin_attempt,platform.finish_pin_attempt}', 'the only SECURITY DEFINER functions are the closed-list outbound.add_contact_control, platform.begin_pin_attempt and platform.finish_pin_attempt');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and not coalesce(p.proconfig @> array['search_path=pg_catalog'], false)),
  0, 'every application function pins search_path = pg_catalog');
select set_eq(
  $$ select n.nspname || '.' || p.proname
       from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform') $$,
  array['platform.reject_mutation', 'crm.organization_reject_parent_cycle', 'crm.domain_event_is_valid',
        'crm.opportunity_organization_supplier_exception_required',
        'crm.opportunity_organization_exception_immutable',
        'crm.opportunity_requesting_institution_agrees',
        'crm.opportunity_interest_manufacturer_agrees',
        'crm.opportunity_evidence_link_immutable',
        'crm.opportunity_stage_guard',
        'crm.quote_revision_historical_guard',
        'outbound.campaign_content_draft_only',
        'outbound.campaign_freeze_facts_write_once',
        'outbound.campaign_recipient_snapshot_guard',
        'outbound.campaign_planning_guard',
        'outbound.campaign_planning_absent_at_insert',
        'outbound.unsubscribe_permanent',
        'outbound.add_contact_control',
        'outbound.marketing_contact_refusals',
        'outbound.archived_campaign_immutable',
        'outbound.campaign_block_guard',
        'outbound.campaign_hold_refusals',
        'outbound.campaign_hold_guard',
        'platform.operator_security_version',
        'platform.auth_principal_security_version',
        'platform.operator_profile_security_version',
        'platform.auth_session_guard',
        'platform.auth_event_actor_guard', 'platform.hmac_sha256',
        'platform.begin_pin_attempt', 'platform.finish_pin_attempt'],
  'exactly the three Slice 0 helper functions, the five commercial-case guards, the stage guard, the historical-revision guard, the campaign-content guard, the two audience-freeze guards, the two campaign-planning guards, the three W10 unsubscribe functions, the three campaign-block functions, the archived-campaign guard, the three sign-in version guards, the session guard, the audit-actor guard, the HMAC helper and the two PIN-attempt definers exist');

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

-- RLS enabled on all 41 tables and never forced (the owner crosses it by ownership).
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r'
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and c.relrowsecurity),
  41, 'RLS is enabled on all 41 tables');
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
  41, 'every table is commented with its DOMAIN.md §7 inventory number');

select * from finish();
rollback;
