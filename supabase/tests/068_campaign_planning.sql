-- Slice 5 — campaign planning, proven in the database.
--
-- Companion to `20260927220000_slice5_campaign_planning.sql`: the planned day and instant, the
-- planning compare-and-set token, the guard that keeps planning on unsent campaigns and apart
-- from status changes, and the two planning events.
--
-- Exercised as the owner, so only constraints and triggers speak. SQLSTATEs: 23514 check,
-- P0001 trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(26);

insert into comms.mailbox (id, address_norm) values
  ('68000000-0000-4000-8000-000000000100', 'ventas@example.test');
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days) values
  ('68000000-0000-4000-8000-000000000200', 'Borrador planificado', '68000000-0000-4000-8000-000000000100', 50, 90),
  ('68000000-0000-4000-8000-000000000201', 'Para congelar', '68000000-0000-4000-8000-000000000100', 50, 90);
insert into outbound.campaign (id, name, status, mailbox_id, max_sends, recontact_interval_days) values
  ('68000000-0000-4000-8000-000000000202', 'Histórica', 'archived', '68000000-0000-4000-8000-000000000100', 1000, 90);

-- ── columns ───────────────────────────────────────────────────────────────────────────────────
select has_column('outbound', 'campaign', 'planned_for_date', 'campaign has a planned day');
select has_column('outbound', 'campaign', 'planned_for_at', 'campaign has a planned instant');
select col_not_null('outbound', 'campaign', 'planning_version', 'planning_version is NOT NULL');
select col_default_is('outbound', 'campaign', 'planning_version', '0', 'planning_version starts at 0');
select col_type_is('outbound', 'campaign', 'planned_for_date', 'date', 'the planned day is a date, not an instant');
select col_type_is('outbound', 'campaign', 'planned_for_at', 'timestamp with time zone', 'the planned instant is a timestamptz (UTC)');

-- ── a campaign is born unplanned ──────────────────────────────────────────────────────────────
select throws_ok($$ insert into outbound.campaign (name, mailbox_id, max_sends, recontact_interval_days, planned_for_date)
  values ('x', '68000000-0000-4000-8000-000000000100', 1, 1, date '2026-10-14') $$,
  'P0001', null, 'a new campaign carries no planned day');
select throws_ok($$ insert into outbound.campaign (name, mailbox_id, max_sends, recontact_interval_days, planning_version)
  values ('x', '68000000-0000-4000-8000-000000000100', 1, 1, 3) $$,
  'P0001', null, 'a new campaign starts at planning_version 0');

-- ── shape ─────────────────────────────────────────────────────────────────────────────────────
select throws_ok($$ update outbound.campaign set planned_for_at = timestamptz '2026-10-14 15:00+00'
  where id = '68000000-0000-4000-8000-000000000200' $$,
  '23514', null, 'a planned time needs a planned day');
select throws_ok($$ update outbound.campaign set planning_version = -1
  where id = '68000000-0000-4000-8000-000000000200' $$,
  '23514', null, 'planning_version is never negative');

-- ── a draft is planned, re-planned and cleared ────────────────────────────────────────────────
select lives_ok($$ update outbound.campaign set planned_for_date = date '2026-10-14', planning_version = 1
  where id = '68000000-0000-4000-8000-000000000200' $$,
  'a draft takes a planned day');
-- 2026-10-15 02:00 UTC is 2026-10-14 23:00 in Santiago (UTC-3 in October).
select lives_ok($$ update outbound.campaign set planned_for_at = timestamptz '2026-10-15 02:00+00', planning_version = 2
  where id = '68000000-0000-4000-8000-000000000200' $$,
  'a time that falls on the planned day in America/Santiago is accepted, even on the next UTC day');
select throws_ok($$ update outbound.campaign set planned_for_at = timestamptz '2026-10-15 04:00+00', planning_version = 3
  where id = '68000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'a time that falls on another day in America/Santiago is refused');
select lives_ok($$ update outbound.campaign set planned_for_date = null, planned_for_at = null, planning_version = 3
  where id = '68000000-0000-4000-8000-000000000200' $$,
  'a draft''s planning can be cleared');

-- ── planning never rides on a status change ───────────────────────────────────────────────────
select throws_ok($$ update outbound.campaign set planned_for_date = date '2026-10-20', planning_version = 4, status = 'cancelled'
  where id = '68000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'planning and a status change are never one statement');
select is((select status from outbound.campaign where id = '68000000-0000-4000-8000-000000000200'),
  'draft', 'the refused statement left the campaign a draft');

-- ── an audience-frozen campaign may still be planned ──────────────────────────────────────────
update outbound.campaign
   set status = 'audience_frozen', subject = 'Asunto', body_text = 'Texto', body_html = '<p>Texto</p>',
       content_sha256 = repeat('a', 64), content_frozen_at = now(),
       audience_criteria = '{"v": 1}'::jsonb, audience_criteria_version = 1, audience_frozen_at = now(),
       audience_policy_version = 'marketing-audience/2026-09-27.v1', audience_sha256 = repeat('b', 64)
 where id = '68000000-0000-4000-8000-000000000201';
select lives_ok($$ update outbound.campaign set planned_for_date = date '2026-11-02', planning_version = 1
  where id = '68000000-0000-4000-8000-000000000201' $$,
  'an audience-frozen campaign takes a planned day');
select is((select (status, content_sha256) from outbound.campaign where id = '68000000-0000-4000-8000-000000000201')::text,
  '(audience_frozen,' || repeat('a', 64) || ')', 'planning left the frozen status and content fingerprint as they were');

-- ── a sent, archived or cancelled campaign is never planned ───────────────────────────────────
select throws_ok($$ update outbound.campaign set planned_for_date = date '2026-10-14', planning_version = 1
  where id = '68000000-0000-4000-8000-000000000202' $$,
  'P0001', null, 'an archived (historical) campaign cannot be planned');
update outbound.campaign set status = 'cancelled' where id = '68000000-0000-4000-8000-000000000200';
select throws_ok($$ update outbound.campaign set planned_for_date = date '2026-10-14', planning_version = 5
  where id = '68000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'a cancelled campaign cannot be planned');

-- ── the audit vocabulary ──────────────────────────────────────────────────────────────────────
select ok(
  (select pg_get_constraintdef(oid) from pg_constraint where conname = 'domain_event_type_check'
     and conrelid = 'crm.domain_event'::regclass) like '%campaign.planning_set%campaign.planning_cleared%',
  'crm.domain_event accepts campaign.planning_set and campaign.planning_cleared');

-- ── nothing reads the planned date ────────────────────────────────────────────────────────────
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and p.prosrc ilike '%planned_for%'
      and p.proname not in ('campaign_planning_guard', 'campaign_planning_absent_at_insert')),
  0, 'no function other than the two guards mentions the planned date: planning schedules nothing');

-- ── the guards themselves ─────────────────────────────────────────────────────────────────────
select is(
  (select count(*)::int from pg_proc p
    where p.oid in ('outbound.campaign_planning_guard()'::regprocedure, 'outbound.campaign_planning_absent_at_insert()'::regprocedure)
      and not p.prosecdef and p.proconfig = array['search_path=pg_catalog']
      and pg_get_userbyid(p.proowner) = 'origenlab_owner'),
  2, 'both planning guards are SECURITY INVOKER, pin search_path = pg_catalog and are owned by origenlab_owner');
select is(
  (select count(*)::int from unnest(array['anon', 'authenticated', 'service_role', 'authenticator']) r(rolname),
          unnest(array['outbound.campaign_planning_guard()'::regprocedure, 'outbound.campaign_planning_absent_at_insert()'::regprocedure]) f(fn)
    where has_function_privilege(r.rolname, f.fn, 'EXECUTE')),
  0, 'no Data-API-facing role effectively holds EXECUTE on either planning guard');
select results_eq(
  $$ select t.tgname::text collate "default", t.tgenabled::text collate "default",
            (t.tgtype & 1) <> 0, (t.tgtype & 2) <> 0, (t.tgtype & 4) <> 0, (t.tgtype & 8) <> 0, (t.tgtype & 16) <> 0
       from pg_trigger t
      where t.tgrelid = 'outbound.campaign'::regclass and t.tgname like 'campaign_planning%' and not t.tgisinternal
      order by 1 $$,
  $$ values ('campaign_planning_absent_at_insert', 'O', true, true, true, false, false),
            ('campaign_planning_guard', 'O', true, true, false, false, true) $$,
  'one BEFORE INSERT and one BEFORE UPDATE row trigger, both enabled, guard outbound.campaign planning');
select is(
  (select count(*)::int from information_schema.column_privileges
    where table_schema = 'outbound' and table_name = 'campaign'
      and column_name in ('planned_for_date', 'planned_for_at', 'planning_version')
      and grantee in ('anon', 'authenticated', 'service_role', 'authenticator')),
  0, 'no Data-API-facing role holds a privilege on the planning columns');

select * from finish();
rollback;
