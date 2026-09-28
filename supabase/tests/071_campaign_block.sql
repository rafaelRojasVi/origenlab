-- Slice 5 — campaign safety blocks, proven in the database.
--
-- Companion to `20260928100000_slice5_campaign_block.sql`: the seeded September wave-2 incident
-- hold, the block row's shape and lifecycle (admin-only, write-once, lifted exactly once, never
-- deleted, never expiring), the runtime roles' reach, the campaign-level refusals, and their
-- enforcement on every write that moves a campaign towards a send — while nothing a block touches
-- is ever rewritten.
--
-- Exercised as the owner unless stated, so constraints and triggers speak. SQLSTATEs: 23514 check,
-- 23505 unique, P0001 trigger guard, 42501 privilege / not an admin.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
grant origenlab_api    to session_user with set true, inherit false;
grant origenlab_worker to session_user with set true, inherit false;

create function pg_temp.run_as(p_role text, p_sql text) returns text
language plpgsql as $$
begin
  execute format('set role %I', p_role);
  execute p_sql;
  reset role;
  return 'ok';
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$$;

set role origenlab_owner;
select plan(58);

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('71000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-0000000000f1', 'block.admin@example.test', 'Block Admin', 'admin', 'active'),
  ('71000000-0000-4000-8000-000000000002', '71000000-0000-4000-8000-0000000000f2', 'block.sales@example.test', 'Block Sales', 'sales', 'active'),
  ('71000000-0000-4000-8000-000000000003', '71000000-0000-4000-8000-0000000000f3', 'block.viewer@example.test', 'Block Viewer', 'viewer', 'active'),
  ('71000000-0000-4000-8000-000000000004', '71000000-0000-4000-8000-0000000000f4', 'block.former@example.test', 'Former Admin', 'admin', 'disabled');
insert into comms.mailbox (id, address_norm) values ('71000000-0000-4000-8000-000000000100', 'ventas-block@example.test');
-- 200: a draft ready to freeze. 201: frozen with two recipients. 202: an untouched draft.
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days, subject, body_html) values
  ('71000000-0000-4000-8000-000000000200', 'Borrador bloqueable', '71000000-0000-4000-8000-000000000100', 50, 90, 'A', '<p>B</p>'),
  ('71000000-0000-4000-8000-000000000202', 'Otro borrador', '71000000-0000-4000-8000-000000000100', 50, 90, 'A', '<p>B</p>');
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days, subject, body_html, body_text, content_sha256,
  content_frozen_at, audience_criteria, audience_criteria_version, audience_frozen_at, audience_policy_version, audience_sha256) values
  ('71000000-0000-4000-8000-000000000201', 'Congelada', '71000000-0000-4000-8000-000000000100', 50, 90, 'A', '<p>B</p>', 'B', repeat('a', 64),
   now(), '{"version": 1}'::jsonb, 1, now(), 'marketing-audience/2026-09-27.v1', repeat('b', 64));
insert into outbound.campaign_recipient (id, campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
  frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version)
values
  ('71000000-0000-4000-8000-000000000601', '71000000-0000-4000-8000-000000000201', 'uno@lab.example', 'snapshotted', '{}', now(),
   'included', '{}', '{}', 'sin_informacion', '[]', 1, repeat('a', 64), 'marketing-audience/2026-09-27.v1'),
  ('71000000-0000-4000-8000-000000000602', '71000000-0000-4000-8000-000000000201', 'dos@lab.example', 'snapshotted', '{}', now(),
   'included', '{}', '{}', 'sin_informacion', '[]', 1, repeat('a', 64), 'marketing-audience/2026-09-27.v1');
update outbound.campaign set status = 'audience_frozen' where id = '71000000-0000-4000-8000-000000000201';

create temporary view frozen_fingerprint as
  select md5(string_agg(row(r.*)::text, '|' order by r.id)) as fp from outbound.campaign_recipient r
   where r.campaign_id = '71000000-0000-4000-8000-000000000201';
create temporary table fp_before as select fp from frozen_fingerprint;

-- ── the September wave-2 incident hold is a durable fact ──────────────────────────────────────
select results_eq(
  $$ select scope, legacy_campaign_key, reference, placed_by_kind, placed_by_operator_id is null, lifted_at is null, version
       from outbound.campaign_block $$,
  $$ values ('legacy_campaign'::text, 'septiembre18-2026-wave2'::text, 'incident_hold_september_2026'::text, 'migrator'::text, true, true, 1) $$,
  'the migration records exactly one block: the September wave-2 incident hold, active, placed by the migration');
select ok((select reason ~ '2026-09-21' and reason ~ '279' and reason !~ '@' from outbound.campaign_block),
  'the hold says when and how much was contained, and names no address');

-- ── shape ─────────────────────────────────────────────────────────────────────────────────────
select hasnt_column('outbound', 'campaign_block', 'expires_at', 'a block has no expiry column');
select hasnt_column('outbound', 'campaign_block', 'until_at', 'a block has no until_at: nothing silently expires one');
select throws_ok($$ insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind, placed_by_operator_id)
  values ('all_campaigns', '71000000-0000-4000-8000-000000000200', 'x', 'operator', '71000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'an all-campaigns block names no campaign');
select throws_ok($$ insert into outbound.campaign_block (scope, reason, placed_by_kind, placed_by_operator_id)
  values ('campaign', 'x', 'operator', '71000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'a campaign block names its campaign');
select throws_ok($$ insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind, placed_by_operator_id)
  values ('campaign', '71000000-0000-4000-8000-000000000200', E' \n\t', 'operator', '71000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'a whitespace-only reason is refused');
select throws_ok($$ insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind)
  values ('campaign', '71000000-0000-4000-8000-000000000200', 'x', 'operator') $$,
  '23514', null, 'an operator block names its operator');
select throws_ok($$ insert into outbound.campaign_block (scope, legacy_campaign_key, reason, placed_by_kind)
  values ('legacy_campaign', 'Not A Key!', 'x', 'migrator') $$,
  '23514', null, 'a legacy campaign key has the V1 key shape');

-- ── only an active admin places a block ───────────────────────────────────────────────────────
select throws_ok($$ insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind, placed_by_operator_id)
  values ('campaign', '71000000-0000-4000-8000-000000000200', 'x', 'operator', '71000000-0000-4000-8000-000000000002') $$,
  '42501', null, 'sales cannot place a block');
select throws_ok($$ insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind, placed_by_operator_id)
  values ('campaign', '71000000-0000-4000-8000-000000000200', 'x', 'operator', '71000000-0000-4000-8000-000000000003') $$,
  '42501', null, 'a viewer cannot place a block');
select throws_ok($$ insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind, placed_by_operator_id)
  values ('campaign', '71000000-0000-4000-8000-000000000200', 'x', 'operator', '71000000-0000-4000-8000-000000000004') $$,
  '42501', null, 'a disabled admin cannot place a block');
select throws_ok($$ insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind, placed_by_operator_id, lifted_at, lifted_by_operator_id, lift_reason, version)
  values ('campaign', '71000000-0000-4000-8000-000000000200', 'x', 'operator', '71000000-0000-4000-8000-000000000001',
          now(), '71000000-0000-4000-8000-000000000001', 'y', 2) $$,
  'P0001', null, 'a block is never inserted already lifted');
select lives_ok($$ insert into outbound.campaign_block (id, scope, campaign_id, reason, placed_by_kind, placed_by_operator_id, placed_at)
  values ('71000000-0000-4000-8000-000000000700', 'campaign', '71000000-0000-4000-8000-000000000200',
          'Revisión de contenido pendiente', 'operator', '71000000-0000-4000-8000-000000000001', timestamptz '2020-01-01') $$,
  'an active admin places a campaign block');
select is((select placed_at from outbound.campaign_block where id = '71000000-0000-4000-8000-000000000700'), now(),
  'the database is the clock: a supplied placed_at is replaced by now()');
select throws_ok($$ insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind, placed_by_operator_id)
  values ('campaign', '71000000-0000-4000-8000-000000000200', 'otra vez', 'operator', '71000000-0000-4000-8000-000000000001') $$,
  '23505', null, 'a blocked campaign takes no second active block');

-- ── the campaign-level refusals ───────────────────────────────────────────────────────────────
select is(outbound.campaign_hold_refusals('71000000-0000-4000-8000-000000000200'), array['campaign_blocked'],
  'a blocked campaign is refused as campaign_blocked');
select is(outbound.campaign_hold_refusals('71000000-0000-4000-8000-000000000202'), '{}'::text[],
  'another campaign is not affected by a campaign block');
select is(outbound.campaign_hold_refusals('71000000-0000-4000-8000-0000000009ff'), array['campaign_unknown'],
  'an unknown campaign is never reported as clear');

-- ── enforcement while blocked: freeze ─────────────────────────────────────────────────────────
select throws_ok($$ update outbound.campaign
    set body_text = 'B', content_sha256 = repeat('c', 64), content_frozen_at = now(),
        audience_criteria = '{"version": 1}'::jsonb, audience_criteria_version = 1, audience_frozen_at = now(),
        audience_policy_version = 'marketing-audience/2026-09-27.v1', audience_sha256 = repeat('d', 64)
  where id = '71000000-0000-4000-8000-000000000200' $$,
  'P0001', null, 'freezing the audience of a blocked campaign is refused');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('campaign', '71000000-0000-4000-8000-000000000200', 1, 'campaign.audience_frozen', 1, '{}', 'operator', '71000000-0000-4000-8000-000000000002') $$,
  'P0001', null, 'recording a freeze event for a blocked campaign is refused');
select lives_ok($$ update outbound.campaign set planned_for_date = (now() at time zone 'America/Santiago')::date + 7, planning_version = 1
  where id = '71000000-0000-4000-8000-000000000200' $$,
  'planning a blocked draft is still possible: planning schedules nothing');
select lives_ok($$ update outbound.campaign set subject = 'Asunto revisado' where id = '71000000-0000-4000-8000-000000000200' $$,
  'editing a blocked draft is still possible: a draft is not a step towards a send');

-- ── enforcement while blocked: dry run, approval, activation, reservation, dispatch ───────────
insert into outbound.campaign_block (id, scope, campaign_id, reason, placed_by_kind, placed_by_operator_id)
values ('71000000-0000-4000-8000-000000000701', 'campaign', '71000000-0000-4000-8000-000000000201',
        'Incidente en revisión', 'operator', '71000000-0000-4000-8000-000000000001');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('campaign', '71000000-0000-4000-8000-000000000201', 1, 'campaign.dry_run_recorded', 1, '{}', 'operator', '71000000-0000-4000-8000-000000000002') $$,
  'P0001', null, 'recording a dry run for a blocked campaign is refused');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('campaign', '71000000-0000-4000-8000-000000000201', 1, 'campaign.approved', 1, '{}', 'operator', '71000000-0000-4000-8000-000000000001') $$,
  'P0001', null, 'recording an approval for a blocked campaign is refused');
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('campaign', '71000000-0000-4000-8000-000000000201', 1, 'campaign.planning_set', 1, '{}', 'operator', '71000000-0000-4000-8000-000000000002') $$,
  'an event that is not a step towards a send is still recorded');
select throws_ok($$ update outbound.campaign
    set status = 'approved', approved_at = now(), approved_by_operator_id = '71000000-0000-4000-8000-000000000001', approved_override_count = 0
  where id = '71000000-0000-4000-8000-000000000201' $$,
  'P0001', null, 'approving a blocked campaign is refused');
select throws_ok($$ update outbound.campaign_recipient set state = 'reserved' where id = '71000000-0000-4000-8000-000000000601' $$,
  'P0001', null, 'reserving a recipient of a blocked campaign is refused');
select throws_ok($$ insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm)
  values ('marketing', '71000000-0000-4000-8000-000000000201', '71000000-0000-4000-8000-000000000601',
          '71000000-0000-4000-8000-000000000100', 'uno@lab.example') $$,
  'P0001', null, 'creating a marketing send attempt for a blocked campaign is refused');
select is(outbound.marketing_contact_refusals('71000000-0000-4000-8000-000000000601'), array['campaign_blocked'],
  'the send-time contract refuses a clean recipient of a blocked campaign');
select is((select count(*)::int from outbound.send_attempt), 0, 'blocking enqueued nothing: no send attempt exists');
select is((select fp from frozen_fingerprint), (select fp from fp_before),
  'blocking rewrote no frozen recipient');
select is((select status from outbound.campaign where id = '71000000-0000-4000-8000-000000000201'), 'audience_frozen',
  'blocking changed no campaign status');

-- ── the block's lifecycle ─────────────────────────────────────────────────────────────────────
select throws_ok($$ delete from outbound.campaign_block where id = '71000000-0000-4000-8000-000000000701' $$,
  'P0001', null, 'a block is never deleted');
select throws_ok($$ update outbound.campaign_block set reason = 'otra razón' where id = '71000000-0000-4000-8000-000000000701' $$,
  'P0001', null, 'a block''s placement is write-once');
select throws_ok($$ update outbound.campaign_block
    set lifted_at = now(), lifted_by_operator_id = '71000000-0000-4000-8000-000000000001', lift_reason = 'resuelto'
  where id = '71000000-0000-4000-8000-000000000701' $$,
  'P0001', null, 'a lift moves the version with it');
select throws_ok($$ update outbound.campaign_block
    set lifted_at = now(), lifted_by_operator_id = '71000000-0000-4000-8000-000000000002', lift_reason = 'resuelto', version = 2
  where id = '71000000-0000-4000-8000-000000000701' $$,
  '42501', null, 'sales cannot lift a block');
select throws_ok($$ update outbound.campaign_block
    set lifted_at = now(), lifted_by_operator_id = '71000000-0000-4000-8000-000000000001', lift_reason = E'\n ', version = 2
  where id = '71000000-0000-4000-8000-000000000701' $$,
  '23514', null, 'a lift needs a real reason');
select lives_ok($$ update outbound.campaign_block
    set lifted_at = timestamptz '2020-01-01', lifted_by_operator_id = '71000000-0000-4000-8000-000000000001',
        lift_reason = 'Incidente resuelto', version = 2
  where id = '71000000-0000-4000-8000-000000000701' $$,
  'an active admin lifts a block, with a reason');
select is((select (lifted_at = now())::text || '/' || version from outbound.campaign_block where id = '71000000-0000-4000-8000-000000000701'),
  'true/2', 'the database is the clock for the lift too, and the version is 2');
select throws_ok($$ update outbound.campaign_block set lift_reason = 'otra' where id = '71000000-0000-4000-8000-000000000701' $$,
  'P0001', null, 'a lifted block is immutable');
select is(outbound.marketing_contact_refusals('71000000-0000-4000-8000-000000000601'), '{}'::text[],
  'once lifted, the campaign-level refusal is gone');

-- ── every campaign ────────────────────────────────────────────────────────────────────────────
insert into outbound.campaign_block (id, scope, reason, placed_by_kind, placed_by_operator_id)
values ('71000000-0000-4000-8000-000000000702', 'all_campaigns', 'Pausa general de envíos', 'operator', '71000000-0000-4000-8000-000000000001');
select is(outbound.campaign_hold_refusals('71000000-0000-4000-8000-000000000202'), array['all_campaigns_blocked'],
  'an all-campaigns block reaches a campaign with no block of its own');
select throws_ok($$ update outbound.campaign_recipient set state = 'reserved' where id = '71000000-0000-4000-8000-000000000602' $$,
  'P0001', null, 'an all-campaigns block refuses a reservation');
update outbound.campaign_block
   set lifted_at = now(), lifted_by_operator_id = '71000000-0000-4000-8000-000000000001', lift_reason = 'Fin de la pausa', version = 2
 where id = '71000000-0000-4000-8000-000000000702';

-- ── paused ────────────────────────────────────────────────────────────────────────────────────
update outbound.campaign set status = 'paused', approved_at = now(),
       approved_by_operator_id = '71000000-0000-4000-8000-000000000001', approved_override_count = 0
 where id = '71000000-0000-4000-8000-000000000201';
select is(outbound.campaign_hold_refusals('71000000-0000-4000-8000-000000000201'), array['campaign_paused'],
  'a paused campaign is refused as campaign_paused');
select throws_ok($$ update outbound.campaign_recipient set state = 'reserved' where id = '71000000-0000-4000-8000-000000000601' $$,
  'P0001', null, 'a paused campaign reserves no recipient');
select lives_ok($$ update outbound.campaign set status = 'active' where id = '71000000-0000-4000-8000-000000000201' $$,
  'resuming a paused campaign with no block is the status machine''s business, not a hold');

-- ── an attempt already in flight still records its outcome ────────────────────────────────────
select lives_ok($$ update outbound.campaign_recipient set state = 'reserved' where id = '71000000-0000-4000-8000-000000000601' $$,
  'with no hold, a recipient is reserved');
insert into outbound.send_attempt (id, purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm)
values ('71000000-0000-4000-8000-000000000800', 'marketing', '71000000-0000-4000-8000-000000000201',
        '71000000-0000-4000-8000-000000000601', '71000000-0000-4000-8000-000000000100', 'uno@lab.example');
update outbound.send_attempt
   set submission_state = 'dispatching', rfc822_message_id = '<block-test@example.test>',
       dispatch_started_at = now(), lease_expires_at = now() + interval '1 minute'
 where id = '71000000-0000-4000-8000-000000000800';
insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind, placed_by_operator_id)
values ('campaign', '71000000-0000-4000-8000-000000000201', 'Bloqueo durante un envío', 'operator', '71000000-0000-4000-8000-000000000001');
select lives_ok($$ update outbound.send_attempt
    set submission_state = 'accepted', delivery_state = 'pending', accepted_at = now()
  where id = '71000000-0000-4000-8000-000000000800' $$,
  'a block placed mid-dispatch never refuses recording that attempt''s outcome (WORKFLOWS.md §2.1)');
select lives_ok($$ update outbound.campaign set status = 'paused' where id = '71000000-0000-4000-8000-000000000201' $$,
  'pausing a blocked campaign is always possible');
select throws_ok($$ update outbound.campaign set status = 'active' where id = '71000000-0000-4000-8000-000000000201' $$,
  'P0001', null, 'a blocked campaign is not re-activated');

-- ── the runtime roles ─────────────────────────────────────────────────────────────────────────
reset role;
select is(left(pg_temp.run_as('origenlab_api', $$insert into outbound.campaign_block (scope, legacy_campaign_key, reason, placed_by_kind)
    values ('legacy_campaign', 'forged-hold', 'x', 'migrator')$$), 5), '42501',
  'the API cannot place a block without an operator');
select is(left(pg_temp.run_as('origenlab_api', $$update outbound.campaign_block set reason = 'x' where true$$), 5), '42501',
  'the API''s column grant does not reach a block''s placement');
select is(left(pg_temp.run_as('origenlab_api', $$delete from outbound.campaign_block where true$$), 5), '42501',
  'the API cannot delete a block');
select is(left(pg_temp.run_as('origenlab_worker', $$insert into outbound.campaign_block (scope, reason, placed_by_kind, placed_by_operator_id)
    values ('all_campaigns', 'x', 'operator', '71000000-0000-4000-8000-000000000001')$$), 5), '42501',
  'the worker cannot place a block');
select is(left(pg_temp.run_as('origenlab_worker', $$select count(*) from outbound.campaign_block$$), 2), 'ok',
  'the worker reads blocks (a future send path must see them)');
select is(left(pg_temp.run_as('origenlab_api',
    $$insert into outbound.campaign_block (scope, campaign_id, reason, placed_by_kind, placed_by_operator_id)
      values ('campaign', '71000000-0000-4000-8000-000000000202', 'x', 'operator', '71000000-0000-4000-8000-000000000002')$$), 5), '42501',
  'through the API, a sales operator is still refused by the database');

-- ── vocabulary ────────────────────────────────────────────────────────────────────────────────
set role origenlab_owner;
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('campaign_block', '71000000-0000-4000-8000-000000000700', 1, 'campaign_block.placed', 1, '{}', 'operator', '71000000-0000-4000-8000-000000000001'),
         ('campaign_block', '71000000-0000-4000-8000-000000000700', 2, 'campaign_block.lifted', 1, '{}', 'operator', '71000000-0000-4000-8000-000000000001') $$,
  'campaign_block.placed and campaign_block.lifted are audit events of the campaign_block aggregate');

select * from finish();
rollback;
