-- Slice 5 — campaign drafts, proven in the database.
--
-- Companion to `20260927120000_slice5_campaign_draft_authoring.sql`: the preheader column, the
-- rule that content moves only while the campaign is a draft, and the two draft events.
--
-- Exercised as the owner, so only constraints and triggers speak. SQLSTATEs: 23514 check,
-- P0001 trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(16);

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('50000000-0000-4000-8000-000000000001', '50000000-0000-4000-8000-0000000000f1', 'cd.sales@example.test', 'CD Sales', 'sales', 'active');
insert into comms.mailbox (id, address_norm) values
  ('50000000-0000-4000-8000-000000000100', 'ventas@example.test');
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days) values
  ('50000000-0000-4000-8000-000000000200', 'Borrador', '50000000-0000-4000-8000-000000000100', 50, 90);

-- ── preheader ─────────────────────────────────────────────────────────────────────────────────
select has_column('outbound', 'campaign', 'preheader', 'campaign has a preheader');
select throws_ok($$ update outbound.campaign set preheader = '   ' where id = '50000000-0000-4000-8000-000000000200' $$,
  '23514', null, 'a present preheader is not blank');
select throws_ok($$ update outbound.campaign set preheader = repeat('x', 256) where id = '50000000-0000-4000-8000-000000000200' $$,
  '23514', null, 'a preheader is at most 255 characters');

-- ── content moves only while draft ────────────────────────────────────────────────────────────
select lives_ok($$ update outbound.campaign set subject = 'Sonicadores Hielscher', preheader = 'UP200St en laboratorio',
  body_html = '<p>Hola</p>' where id = '50000000-0000-4000-8000-000000000200' $$,
  'a draft''s content can be edited');
select lives_ok($$ update outbound.campaign set status = 'cancelled' where id = '50000000-0000-4000-8000-000000000200' $$,
  'leaving draft is not a content edit');
select throws_ok($$ update outbound.campaign set subject = 'Otro asunto' where id = '50000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'the subject is frozen once the campaign is not a draft');
select throws_ok($$ update outbound.campaign set preheader = 'Otro' where id = '50000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'the preheader is frozen once the campaign is not a draft');
select throws_ok($$ update outbound.campaign set body_html = '<p>Otro</p>' where id = '50000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'the HTML body is frozen once the campaign is not a draft');
select lives_ok($$ update outbound.campaign set updated_at = now() where id = '50000000-0000-4000-8000-000000000200' $$,
  'a non-content column of a non-draft campaign still updates');

-- ── the guard itself ──────────────────────────────────────────────────────────────────────────
select is(
  (select p.prosecdef from pg_proc p where p.oid = 'outbound.campaign_content_draft_only()'::regprocedure),
  false, 'the campaign-content guard is SECURITY INVOKER');
select is(
  (select array_to_string(p.proconfig, '|') from pg_proc p where p.oid = 'outbound.campaign_content_draft_only()'::regprocedure),
  'search_path=pg_catalog', 'the campaign-content guard pins search_path = pg_catalog and nothing else');
select is(
  (select pg_get_userbyid(p.proowner) from pg_proc p where p.oid = 'outbound.campaign_content_draft_only()'::regprocedure),
  'origenlab_owner', 'the campaign-content guard is owned by origenlab_owner');
select is(
  (select count(*)::int from unnest(array['anon', 'authenticated', 'service_role', 'authenticator']) r(rolname)
    where has_function_privilege(r.rolname, 'outbound.campaign_content_draft_only()'::regprocedure, 'EXECUTE')),
  0, 'no Data-API-facing role effectively holds EXECUTE on the guard');
select results_eq(
  $$ select c.oid::regclass::text collate "default", t.tgname::text collate "default",
            t.tgenabled::text collate "default",
            (t.tgtype & 1) <> 0,              -- ROW
            (t.tgtype & 2) <> 0,              -- BEFORE
            (t.tgtype & 16) <> 0              -- UPDATE
       from pg_trigger t join pg_class c on c.oid = t.tgrelid
      where t.tgfoid = 'outbound.campaign_content_draft_only()'::regprocedure and not t.tgisinternal $$,
  $$ values ('outbound.campaign', 'campaign_content_draft_only', 'O', true, true, true) $$,
  'the guard fires from exactly one enabled BEFORE UPDATE row trigger on outbound.campaign');

-- ── audit vocabulary ──────────────────────────────────────────────────────────────────────────
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('campaign', '50000000-0000-4000-8000-000000000200', 1, 'campaign.draft_created', 1, '{}'::jsonb, 'operator', '50000000-0000-4000-8000-000000000001'),
         ('campaign', '50000000-0000-4000-8000-000000000200', 2, 'campaign.draft_content_saved', 1, '{}'::jsonb, 'operator', '50000000-0000-4000-8000-000000000001') $$,
  'the two draft events are in the closed vocabulary');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('campaign', '50000000-0000-4000-8000-000000000200', 3, 'campaign.draft_sent', 1, '{}'::jsonb, 'operator', '50000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'the vocabulary stays closed');

select * from finish();
rollback;
