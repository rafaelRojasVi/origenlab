-- Slice 7 — email → cases corrections, proven in the database.
--
-- Companion to `20261005120000_slice7_mail_auto_case_corrections.sql`: the two event types an
-- undo writes, and the one declared correction the stage guard allows out of `won`/`lost` — back
-- to the stage the audit stream records, never anywhere else, never without the declaration.
--
-- Exercised as the owner (only constraints and triggers speak), then the runtime role for the
-- one write an undo performs as `origenlab_api`.
--
-- SQLSTATEs: 23514 check, P0001 trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(14);

-- ── fixtures ───────────────────────────────────────────────────────────────────────────────

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('76000000-0000-4000-8000-000000000001', gen_random_uuid(), 'mail.rules@example.test', 'Mail Rules Admin', 'admin', 'active');

insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('76000000-0000-4000-8000-0000000000b1', 'Caso perdido por el sistema', 'lead', '76000000-0000-4000-8000-000000000001'),
  ('76000000-0000-4000-8000-0000000000b2', 'Caso abandonado', 'lead', '76000000-0000-4000-8000-000000000001'),
  ('76000000-0000-4000-8000-0000000000b3', 'Otro caso', 'lead', '76000000-0000-4000-8000-000000000001'),
  ('76000000-0000-4000-8000-0000000000b4', 'Caso cerrado por una persona', 'lead', '76000000-0000-4000-8000-000000000001');

update crm.opportunity set stage = 'qualifying' where id = '76000000-0000-4000-8000-0000000000b1';
update crm.opportunity set stage = 'lost', closed_at = now(), close_reason = 'otro proveedor'
 where id = '76000000-0000-4000-8000-0000000000b1';
insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind) values
  ('opportunity', '76000000-0000-4000-8000-0000000000b1', 1, 'opportunity.staged', 1,
   '{"from_stage": "lead", "to_stage": "qualifying"}'::jsonb, 'worker'),
  ('opportunity', '76000000-0000-4000-8000-0000000000b1', 2, 'opportunity.staged', 1,
   '{"from_stage": "qualifying", "to_stage": "lost"}'::jsonb, 'worker');
update crm.opportunity set stage = 'abandoned', closed_at = now(), close_reason = 'discarded_by_correction'
 where id = '76000000-0000-4000-8000-0000000000b2';
insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind) values
  ('opportunity', '76000000-0000-4000-8000-0000000000b2', 1, 'opportunity.staged', 1,
   '{"from_stage": "lead", "to_stage": "abandoned"}'::jsonb, 'worker');

update crm.opportunity set stage = 'lost', closed_at = now(), close_reason = 'el cliente compró a otro'
 where id = '76000000-0000-4000-8000-0000000000b4';
insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id) values
  ('opportunity', '76000000-0000-4000-8000-0000000000b4', 1, 'opportunity.staged', 1,
   '{"from_stage": "lead", "to_stage": "lost"}'::jsonb, 'operator', '76000000-0000-4000-8000-000000000001');

-- ── 1. vocabulary ──────────────────────────────────────────────────────────────────────────

select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_evidence', gen_random_uuid(), 1, 'case_evidence.unlinked', 1, '{}'::jsonb, 'operator', '76000000-0000-4000-8000-000000000001') $$,
  'domain_event: case_evidence.unlinked');
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '76000000-0000-4000-8000-0000000000b3', 1, 'opportunity.stage_corrected', 1, '{}'::jsonb, 'operator', '76000000-0000-4000-8000-000000000001') $$,
  'domain_event: opportunity.stage_corrected');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '76000000-0000-4000-8000-0000000000b3', 2, 'case_evidence.unlinked', 1, '{}'::jsonb, 'operator', '76000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'domain_event: case_evidence.unlinked belongs to opportunity_evidence, not opportunity');
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('opportunity', '76000000-0000-4000-8000-0000000000b3', 3, 'opportunity.staged', 1, '{"system": {"rule_id": "R6"}}'::jsonb, 'worker') $$,
  'domain_event: a machine (worker) event carries no operator');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '76000000-0000-4000-8000-0000000000b3', 4, 'opportunity.staged', 1, '{}'::jsonb, 'worker', '76000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'domain_event: a worker event naming an operator is refused (actor shape)');

-- ── 2. the stage guard: never revived, except by a declared correction to where it came from ──

select throws_ok($$ update crm.opportunity set stage = 'qualifying', closed_at = null, close_reason = null where id = '76000000-0000-4000-8000-0000000000b1' $$,
  'P0001', null, 'stage: without the declaration a lost case is still never revived');

do $$ begin perform set_config('origenlab.case_stage_correction', '76000000-0000-4000-8000-0000000000b3', true); end $$;
select throws_ok($$ update crm.opportunity set stage = 'qualifying', closed_at = null, close_reason = null where id = '76000000-0000-4000-8000-0000000000b1' $$,
  'P0001', null, 'stage: a correction declared for another case does not unlock this one');

do $$ begin perform set_config('origenlab.case_stage_correction', '76000000-0000-4000-8000-0000000000b1', true); end $$;
select throws_ok($$ update crm.opportunity set stage = 'lead', closed_at = null, close_reason = null where id = '76000000-0000-4000-8000-0000000000b1' $$,
  'P0001', null, 'stage: a correction returns only to the recorded previous stage, not another');
select throws_ok($$ update crm.opportunity set stage = 'abandoned' where id = '76000000-0000-4000-8000-0000000000b1' $$,
  'P0001', null, 'stage: a correction never moves to another terminal stage');
select throws_ok($$ update crm.opportunity set stage = 'qualifying' where id = '76000000-0000-4000-8000-0000000000b1' $$,
  '23514', null, 'stage: a corrected case must clear closed_at (closed shape)');
select lives_ok($$ update crm.opportunity set stage = 'qualifying', closed_at = null, close_reason = null where id = '76000000-0000-4000-8000-0000000000b1' $$,
  'stage: a declared correction returns lost → qualifying, the stage it came from');

do $$ begin perform set_config('origenlab.case_stage_correction', '76000000-0000-4000-8000-0000000000b2', true); end $$;
select throws_ok($$ update crm.opportunity set stage = 'lead', closed_at = null, close_reason = null where id = '76000000-0000-4000-8000-0000000000b2' $$,
  'P0001', null, 'stage: abandoned is never corrected — it is what an undo uses to discard a case');
do $$ begin perform set_config('origenlab.case_stage_correction', '76000000-0000-4000-8000-0000000000b4', true); end $$;
select throws_ok($$ update crm.opportunity set stage = 'lead', closed_at = null, close_reason = null where id = '76000000-0000-4000-8000-0000000000b4' $$,
  'P0001', null, 'stage: a case a person closed is never revived, even with the declaration');
do $$ begin perform set_config('origenlab.case_stage_correction', '', true); end $$;

select is((select stage from crm.opportunity where id = '76000000-0000-4000-8000-0000000000b1'), 'qualifying',
  'stage: the corrected case is open again');

select * from finish();
rollback;
