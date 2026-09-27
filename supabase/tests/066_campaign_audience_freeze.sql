-- Slice 5 — audience freeze, proven in the database.
--
-- Companion to `20260927180000_slice5_campaign_audience_freeze.sql`: the freeze facts are
-- write-once, a campaign never returns to draft, a snapshot row is inserted only while its
-- campaign is a draft, names the content and policy its campaign froze with, keeps relevance
-- apart from permission, and is never rewritten or deleted. Legacy rows without snapshot facts
-- are untouched.
--
-- Exercised as the owner, so only constraints and triggers speak. SQLSTATEs: 23514 check,
-- 23503 foreign key, P0001 trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(27);

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('66000000-0000-4000-8000-000000000001', '66000000-0000-4000-8000-0000000000f1', 'af.sales@example.test', 'AF Sales', 'sales', 'active');
insert into comms.mailbox (id, address_norm) values
  ('66000000-0000-4000-8000-000000000100', 'ventas@example.test');
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days, subject, body_html) values
  ('66000000-0000-4000-8000-000000000200', 'Sonicadores', '66000000-0000-4000-8000-000000000100', 50, 90, 'Asunto', '<p>Hola</p>'),
  ('66000000-0000-4000-8000-000000000201', 'Legado', '66000000-0000-4000-8000-000000000100', 50, 90, null, null);

-- ── the freeze, in the order the command performs it ─────────────────────────────────────────
select lives_ok($$ update outbound.campaign set body_text = 'Hola', content_sha256 = repeat('a', 64), content_frozen_at = now(),
  audience_criteria = '{"version": 1}'::jsonb, audience_criteria_version = 1, audience_frozen_at = now(),
  audience_policy_version = 'marketing-audience/2026-09-27.v1', audience_sha256 = repeat('b', 64)
  where id = '66000000-0000-4000-8000-000000000200' $$,
  'the freeze facts are written while the campaign is still a draft');
select lives_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons,
  frozen_at, frozen_inclusion, frozen_reasons, frozen_notes, relevance, interest_evidence, evidence_observed_at,
  campaign_version, content_sha256, policy_version)
  values ('66000000-0000-4000-8000-000000000200', 'ana@lab.example', 'snapshotted', '{}', now(), 'included', '{}', '{}',
          'evidenced', '[{"brand_id": "hielscher"}]'::jsonb, now(), 3, repeat('a', 64), 'marketing-audience/2026-09-27.v1'),
         ('66000000-0000-4000-8000-000000000200', 'bea@lab.example', 'excluded', '{prior_contact}', now(), 'excluded', '{prior_contact}', '{}',
          'sin_informacion', '[]'::jsonb, null, 3, repeat('a', 64), 'marketing-audience/2026-09-27.v1') $$,
  'an included and an excluded snapshot row are inserted while the campaign is a draft');
select lives_ok($$ update outbound.campaign set status = 'audience_frozen' where id = '66000000-0000-4000-8000-000000000200' $$,
  'the campaign moves to audience_frozen');

-- ── shape of a snapshot row ──────────────────────────────────────────────────────────────────
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion, frozen_reasons)
  values ('66000000-0000-4000-8000-000000000201', 'x@lab.example', 'snapshotted', '{}', now(), 'included', '{}') $$,
  '23514', null, 'snapshot facts are all present or all absent');
select throws_ok($$ update outbound.campaign set audience_policy_version = 'x/1' where id = '66000000-0000-4000-8000-000000000201' $$,
  '23514', null, 'a policy version needs an audience fingerprint and criteria');

-- A second draft to exercise the row constraints without the frozen campaign's guard.
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days, subject, body_text, content_sha256, content_frozen_at,
  audience_criteria, audience_criteria_version, audience_frozen_at, audience_policy_version, audience_sha256) values
  ('66000000-0000-4000-8000-000000000202', 'Otra', '66000000-0000-4000-8000-000000000100', 50, 90, 'A', 'B', repeat('c', 64), now(),
   '{"version": 1}'::jsonb, 1, now(), 'marketing-audience/2026-09-27.v1', repeat('d', 64));

select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
  frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version)
  values ('66000000-0000-4000-8000-000000000202', 'c@lab.example', 'snapshotted', '{}', now(), 'included', '{}', '{}',
          'sin_informacion', '[]'::jsonb, 1, repeat('a', 64), 'marketing-audience/2026-09-27.v1') $$,
  '23503', null, 'a snapshot row cannot name content its campaign was not frozen with');
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
  frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version)
  values ('66000000-0000-4000-8000-000000000202', 'c@lab.example', 'snapshotted', '{}', now(), 'included', '{}', '{}',
          'low', '[]'::jsonb, 1, repeat('c', 64), 'marketing-audience/2026-09-27.v1') $$,
  '23514', null, 'relevance has no "low": evidenced or sin_informacion only');
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
  frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version)
  values ('66000000-0000-4000-8000-000000000202', 'c@lab.example', 'snapshotted', '{}', now(), 'included', '{}', '{}',
          'evidenced', '[]'::jsonb, 1, repeat('c', 64), 'marketing-audience/2026-09-27.v1') $$,
  '23514', null, 'evidenced relevance carries at least one piece of evidence');
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
  frozen_reasons, frozen_notes, relevance, interest_evidence, evidence_observed_at, campaign_version, content_sha256, policy_version)
  values ('66000000-0000-4000-8000-000000000202', 'c@lab.example', 'snapshotted', '{}', now(), 'included', '{}', '{}',
          'sin_informacion', '[]'::jsonb, now(), 1, repeat('c', 64), 'marketing-audience/2026-09-27.v1') $$,
  '23514', null, '«Sin información» carries no evidence date');
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
  frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version)
  values ('66000000-0000-4000-8000-000000000202', 'c@lab.example', 'excluded', '{}', now(), 'excluded', '{}', '{}',
          'sin_informacion', '[]'::jsonb, 1, repeat('c', 64), 'marketing-audience/2026-09-27.v1') $$,
  '23514', null, 'an excluded snapshot row carries at least one reason');
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
  frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version)
  values ('66000000-0000-4000-8000-000000000202', 'c@lab.example', 'excluded', '{low_interest}', now(), 'excluded', '{low_interest}', '{}',
          'sin_informacion', '[]'::jsonb, 1, repeat('c', 64), 'marketing-audience/2026-09-27.v1') $$,
  '23514', null, 'frozen reasons come from the closed vocabulary');
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
  frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version)
  values ('66000000-0000-4000-8000-000000000202', 'c@lab.example', 'reserved', '{}', now(), 'included', '{}', '{}',
          'sin_informacion', '[]'::jsonb, 1, repeat('c', 64), 'marketing-audience/2026-09-27.v1') $$,
  'P0001', null, 'a new snapshot row starts in the state it was frozen with');

-- ── inserted only while draft ────────────────────────────────────────────────────────────────
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
  frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version)
  values ('66000000-0000-4000-8000-000000000200', 'late@lab.example', 'snapshotted', '{}', now(), 'included', '{}', '{}',
          'sin_informacion', '[]'::jsonb, 3, repeat('a', 64), 'marketing-audience/2026-09-27.v1') $$,
  'P0001', null, 'no snapshot row joins a campaign that is already frozen');

-- ── a frozen campaign is never rewritten ─────────────────────────────────────────────────────
select throws_ok($$ update outbound.campaign set audience_criteria = '{"version": 1, "brand_id": "ika"}'::jsonb
  where id = '66000000-0000-4000-8000-000000000200' $$, 'P0001', null, 'frozen audience criteria are never rewritten');
select throws_ok($$ update outbound.campaign set audience_sha256 = repeat('e', 64) where id = '66000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'the audience fingerprint is never rewritten');
select throws_ok($$ update outbound.campaign set audience_policy_version = 'marketing-audience/next'
  where id = '66000000-0000-4000-8000-000000000200' $$, 'P0001', null, 'the policy version is never rewritten');
select throws_ok($$ update outbound.campaign set body_text = 'Otro' where id = '66000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'the frozen text body is never rewritten');
select throws_ok($$ update outbound.campaign set status = 'draft' where id = '66000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'a frozen campaign never returns to draft');
select lives_ok($$ update outbound.campaign set updated_at = now() where id = '66000000-0000-4000-8000-000000000200' $$,
  'a non-freeze column of a frozen campaign still updates');

-- ── a frozen recipient snapshot is never rewritten or deleted ────────────────────────────────
select throws_ok($$ update outbound.campaign_recipient set frozen_inclusion = 'excluded', frozen_reasons = '{manual_hold}'
  where address_norm = 'ana@lab.example' $$, 'P0001', null, 'the inclusion decision is never rewritten');
select throws_ok($$ update outbound.campaign_recipient set interest_evidence = '[]'::jsonb, relevance = 'sin_informacion'
  where address_norm = 'ana@lab.example' $$, 'P0001', null, 'the frozen evidence is never rewritten');
select throws_ok($$ update outbound.campaign_recipient set address_norm = 'otra@lab.example' where address_norm = 'ana@lab.example' $$,
  'P0001', null, 'the frozen address is never rewritten');
select throws_ok($$ delete from outbound.campaign_recipient where address_norm = 'bea@lab.example' $$,
  'P0001', null, 'a frozen snapshot row is never deleted');
select lives_ok($$ update outbound.campaign_recipient set updated_at = now() where address_norm = 'ana@lab.example' $$,
  'a live lifecycle column of a snapshot row still updates');

-- ── legacy rows without snapshot facts are untouched ─────────────────────────────────────────
update outbound.campaign set status = 'archived' where id = '66000000-0000-4000-8000-000000000201';
select lives_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state)
  values ('66000000-0000-4000-8000-000000000201', 'legacy@lab.example', 'snapshotted') $$,
  'a legacy recipient without snapshot facts is still insertable into an archived campaign');

-- ── audit vocabulary ─────────────────────────────────────────────────────────────────────────
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('campaign', '66000000-0000-4000-8000-000000000200', 1, 'campaign.audience_frozen', 1, '{}'::jsonb, 'operator', '66000000-0000-4000-8000-000000000001') $$,
  'campaign.audience_frozen is in the closed vocabulary');
select is(
  (select count(*)::int from pg_proc p
    where p.oid in ('outbound.campaign_freeze_facts_write_once()'::regprocedure, 'outbound.campaign_recipient_snapshot_guard()'::regprocedure)
      and not p.prosecdef and array_to_string(p.proconfig, '|') = 'search_path=pg_catalog'
      and pg_get_userbyid(p.proowner) = 'origenlab_owner'),
  2, 'both freeze guards are SECURITY INVOKER, pin search_path = pg_catalog and belong to origenlab_owner');

select * from finish();
rollback;
