-- Slice 9 — audit fixes (20261122100000): the Finanzas aggregates and events, uuid aggregate ids,
-- version columns, a CLP order's pesos, generic partner columns, a voided revision gives back the
-- quote it superseded, the note repoint is a merge's only, and the worker's model-spend features.
-- Fictitious fixtures only; everything is rolled back.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
grant origenlab_api to session_user with set true, inherit false;
grant origenlab_worker to session_user with set true, inherit false;
select plan(44);

create function pg_temp.run_as(p_role text, p_sql text) returns text
language plpgsql as $f$
begin
  execute format('set role %I', p_role);
  execute p_sql;
  reset role;
  return 'ok';
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$f$;

-- ── domain events: the four Finanzas aggregates and their six events ─────────────────────────
set role origenlab_owner;
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('expense', '11700000-0000-4000-8000-000000000001', 1, 'expense.created', 1, '{}'::jsonb, 'migrator'),
         ('expense', '11700000-0000-4000-8000-000000000001', 2, 'expense.updated', 1, '{}'::jsonb, 'migrator')$$,
  'expense.created / expense.updated are accepted on aggregate kind expense');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('supplier_order', '11700000-0000-4000-8000-000000000002', 1, 'supplier_order.created', 1, '{}'::jsonb, 'migrator'),
         ('supplier_order', '11700000-0000-4000-8000-000000000002', 2, 'supplier_order.updated', 1, '{}'::jsonb, 'migrator')$$,
  'supplier_order.created / supplier_order.updated are accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('monthly_target', '11700000-0000-4000-8000-000000000003', 1, 'monthly_target.set', 1, '{}'::jsonb, 'migrator')$$,
  'monthly_target.set is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('partner_rule', '11700000-0000-4000-8000-000000000004', 1, 'partner_rule.set', 1, '{}'::jsonb, 'migrator')$$,
  'partner_rule.set is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('quote_revision', '11700000-0000-4000-8000-000000000005', 1, 'quote_revision.supersession_cleared', 1, '{}'::jsonb, 'migrator')$$,
  'quote_revision.supersession_cleared is accepted');
select throws_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('expense', '11700000-0000-4000-8000-000000000006', 1, 'partner_rule.set', 1, '{}'::jsonb, 'migrator')$$,
  '23514', null, 'an event of another family is refused on aggregate kind expense');
select ok(not crm.domain_event_is_valid('monthly_target', 'expense.created', 1::smallint, '{}'::jsonb),
  'the validator keeps families apart');
reset role;

-- ── uuid aggregate ids ───────────────────────────────────────────────────────────────────────
select col_type_is('crm', 'monthly_target', 'aggregate_id', 'uuid', 'crm.monthly_target.aggregate_id is a uuid');
select col_not_null('crm', 'monthly_target', 'aggregate_id', 'monthly_target.aggregate_id is not null');
select col_has_default('crm', 'monthly_target', 'aggregate_id', 'monthly_target.aggregate_id has a default');
select col_type_is('crm', 'partner_rule', 'aggregate_id', 'uuid', 'crm.partner_rule.aggregate_id is a uuid');
select col_not_null('crm', 'partner_rule', 'aggregate_id', 'partner_rule.aggregate_id is not null');
select col_is_unique('crm', 'partner_rule', 'aggregate_id', 'partner_rule.aggregate_id is unique');
select col_is_unique('crm', 'monthly_target', 'aggregate_id', 'monthly_target.aggregate_id is unique');

-- ── version columns ──────────────────────────────────────────────────────────────────────────
select col_type_is('crm', 'expense', 'version', 'integer', 'crm.expense.version is an integer');
select col_default_is('crm', 'expense', 'version', '1', 'crm.expense.version starts at 1');
select col_type_is('crm', 'supplier_order', 'version', 'integer', 'crm.supplier_order.version is an integer');
select col_default_is('crm', 'supplier_order', 'version', '1', 'crm.supplier_order.version starts at 1');

-- ── fixtures ─────────────────────────────────────────────────────────────────────────────────
set role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('11700000-0000-4000-8000-0000000000a1', gen_random_uuid(), 'op117@example.test', 'Operadora Ficticia',
          'admin', 'active');
reset role;

-- ── a CLP order states its pesos ─────────────────────────────────────────────────────────────
select alike(pg_temp.run_as('origenlab_api', $$ insert into crm.supplier_order
    (supplier_name, ordered_on, amount_original, currency, created_by_operator_id)
  values ('Proveedor Ficticio Ltda', '2026-11-01', 100000, 'CLP', '11700000-0000-4000-8000-0000000000a1') $$),
  '23514%', 'a CLP order without amount_clp is refused');
select alike(pg_temp.run_as('origenlab_api', $$ insert into crm.supplier_order
    (supplier_name, ordered_on, amount_original, currency, amount_clp, created_by_operator_id)
  values ('Proveedor Ficticio Ltda', '2026-11-01', 100000, 'CLP', 0, '11700000-0000-4000-8000-0000000000a1') $$),
  '23514%', 'a CLP order of zero pesos is refused');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.supplier_order
    (supplier_name, ordered_on, amount_original, currency, amount_clp, created_by_operator_id)
  values ('Proveedor Ficticio Ltda', '2026-11-01', 100000, 'CLP', 100000, '11700000-0000-4000-8000-0000000000a1') $$),
  'ok', 'a CLP order with its pesos is accepted');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.supplier_order
    (supplier_name, ordered_on, amount_original, currency, created_by_operator_id)
  values ('Fabricante Ficticio GmbH', '2026-11-01', 900, 'EUR', '11700000-0000-4000-8000-0000000000a1') $$),
  'ok', 'a foreign-currency order may leave its pesos empty');

-- ── generic partner names ────────────────────────────────────────────────────────────────────
select columns_are('crm', 'partner_rule', array[
  'id', 'valid_from', 'rule_name', 'arriendo_clp', 'partner_b_rate', 'partner_a_salary', 'arriendo_pct_empresa',
  'split_pct', 'notes', 'set_by_operator_id', 'created_at', 'aggregate_id'],
  'crm.partner_rule carries generic partner columns only');
select col_hasnt_default('crm', 'partner_rule', 'arriendo_clp', 'the rent has no schema default');
select col_default_is('crm', 'partner_rule', 'partner_a_salary', '0', 'partner_a_salary defaults to 0');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.expense (month, category, amount_clp, paid_by)
  values ('2026-11-01', 'otros', 1000, 'partner_a') $$), 'ok', 'paid_by partner_a is accepted');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.expense (month, category, amount_clp, paid_by)
  values ('2026-11-01', 'otros', 1000, 'partner_b') $$), 'ok', 'paid_by partner_b is accepted');
select alike(pg_temp.run_as('origenlab_api', $$ insert into crm.expense (month, category, amount_clp, paid_by)
  values ('2026-11-01', 'otros', 1000, 'socio_ficticio') $$), '23514%', 'any other payer is refused');
select alike(pg_temp.run_as('origenlab_api', $$ insert into crm.partner_rule (valid_from, rule_name, split_pct)
  values ('2027-03-01', 'actual_50_50', 0.5) $$), '23502%', 'a rule without its rent is refused');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.partner_rule
    (valid_from, rule_name, arriendo_clp, partner_a_salary, partner_b_rate, split_pct)
  values ('2027-03-01', 'opcion_d', 300000, 0, 0.0150, 0.5) $$), 'ok', 'a rule with generic columns is accepted');
select alike(pg_temp.run_as('origenlab_api', $$ insert into crm.partner_rule
    (valid_from, rule_name, arriendo_clp, partner_a_salary, split_pct)
  values ('2027-03-02', 'opcion_c', 300000, -1, 0.5) $$), '23514%', 'a negative partner_a_salary is refused');

-- ── a voided revision gives the client's quote back ──────────────────────────────────────────
set role origenlab_owner;
insert into crm.organization (id, kind, name, confirmation)
  values ('11700000-0000-4000-8000-000000000101', 'company', 'Instituto Ficticio 117', 'confirmed');
insert into crm.opportunity (id, title, stage, owner_operator_id, organization_id)
  values ('11700000-0000-4000-8000-000000000151', 'Caso ficticio 117', 'lead', '11700000-0000-4000-8000-0000000000a1',
          '11700000-0000-4000-8000-000000000101');
insert into crm.quote (id, opportunity_id, quote_number)
  values ('11700000-0000-4000-8000-000000000170', '11700000-0000-4000-8000-000000000151', 'Q-FICT-11701'),
         ('11700000-0000-4000-8000-000000000171', '11700000-0000-4000-8000-000000000151', 'Q-FICT-11702');
-- Quote 170: authored rev 1 approved, superseded by authored rev 2 (approved), and rev 3 approved
-- superseding nothing yet.
insert into crm.quote_revision (id, quote_id, revision_no, status, quote_currency, price_decimals,
  subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at,
  party_snapshot, party_snapshot_version, approved_at, approved_by_operator_id)
values
  ('11700000-0000-4000-8000-000000000181', '11700000-0000-4000-8000-000000000170', 1, 'approved', 'CLP', 0,
   100, 0, 100, 19, 119, now(), '{"sold_to": {}}', 1, now(), '11700000-0000-4000-8000-0000000000a1'),
  ('11700000-0000-4000-8000-000000000182', '11700000-0000-4000-8000-000000000170', 2, 'approved', 'CLP', 0,
   200, 0, 200, 38, 238, now(), '{"sold_to": {}}', 1, now(), '11700000-0000-4000-8000-0000000000a1');
update crm.quote_revision set superseded_by_revision_no = 2, superseded_at = now(), version = version + 1
 where id = '11700000-0000-4000-8000-000000000181';
-- Quote 171: a historical rev 1 (sent) superseded by an authored rev 2 (approved).
insert into evidence.source_record (id, kind, dedupe_key, payload, review_status)
  values ('11700000-0000-4000-8000-000000000190', 'gmail_message', 'gmail_message:fict117', '{}'::jsonb, 'pending');
insert into crm.quote_revision (id, quote_id, revision_no, status, origin, pdf_sha256, sent_at, origin_source_record_id)
  values ('11700000-0000-4000-8000-000000000191', '11700000-0000-4000-8000-000000000171', 1, 'sent',
          'historical_import', repeat('ab', 32), now(), '11700000-0000-4000-8000-000000000190');
insert into crm.quote_revision (id, quote_id, revision_no, status, quote_currency, price_decimals,
  subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at,
  party_snapshot, party_snapshot_version, approved_at, approved_by_operator_id)
values ('11700000-0000-4000-8000-000000000192', '11700000-0000-4000-8000-000000000171', 2, 'approved', 'CLP', 0,
   300, 0, 300, 57, 357, now(), '{"sold_to": {}}', 1, now(), '11700000-0000-4000-8000-0000000000a1');
update crm.quote_revision set superseded_by_revision_no = 2, superseded_at = now()
 where id = '11700000-0000-4000-8000-000000000191';
reset role;

select alike(pg_temp.run_as('origenlab_api', $$ update crm.quote_revision
     set superseded_by_revision_no = null, superseded_at = null, version = version + 1
   where id = '11700000-0000-4000-8000-000000000181' $$),
  '23514%', 'an authored supersession is not cleared while its superseding revision stands');
select alike(pg_temp.run_as('origenlab_api', $$ update crm.quote_revision
     set superseded_by_revision_no = null, superseded_at = null
   where id = '11700000-0000-4000-8000-000000000191' $$),
  'P0001%', 'a historical supersession is not cleared while its superseding revision stands');
select is(pg_temp.run_as('origenlab_api', $$ update crm.quote_revision set status = 'void', version = version + 1
   where id in ('11700000-0000-4000-8000-000000000182', '11700000-0000-4000-8000-000000000192') $$),
  'ok', 'the superseding revisions are voided');
select is(pg_temp.run_as('origenlab_api', $$ update crm.quote_revision
     set superseded_by_revision_no = null, superseded_at = null, version = version + 1
   where id = '11700000-0000-4000-8000-000000000181' $$),
  'ok', 'an authored supersession is cleared once its superseding revision is void');
select is(pg_temp.run_as('origenlab_api', $$ update crm.quote_revision
     set superseded_by_revision_no = null, superseded_at = null
   where id = '11700000-0000-4000-8000-000000000191' $$),
  'ok', 'a historical supersession is cleared once its superseding revision is void');
select alike(pg_temp.run_as('origenlab_api', $$ update crm.quote_revision set grand_total = 1
   where id = '11700000-0000-4000-8000-000000000181' $$),
  '23514%', 'the given-back revision is still frozen');

-- ── crm.note: the repoint exception is a merge's ─────────────────────────────────────────────
set role origenlab_owner;
insert into crm.person (id, display_name, confirmation, version) values
  ('11700000-0000-4000-8000-000000000201', 'Persona Ficticia Uno', 'confirmed', 1),
  ('11700000-0000-4000-8000-000000000202', 'Persona Ficticia Dos', 'confirmed', 1),
  ('11700000-0000-4000-8000-000000000203', 'Persona Ficticia Tres', 'confirmed', 1);
update crm.person set merged_into_person_id = '11700000-0000-4000-8000-000000000202', version = version + 1
 where id = '11700000-0000-4000-8000-000000000201';
insert into crm.note (id, subject_kind, subject_id, body, author_operator_id, version) values
  ('11700000-0000-4000-8000-000000000301', 'person', '11700000-0000-4000-8000-000000000201', 'Nota ficticia',
   '11700000-0000-4000-8000-0000000000a1', 1),
  ('11700000-0000-4000-8000-000000000302', 'person', '11700000-0000-4000-8000-000000000201', 'Otra nota ficticia',
   '11700000-0000-4000-8000-0000000000a1', 1),
  ('11700000-0000-4000-8000-000000000303', 'opportunity', '11700000-0000-4000-8000-000000000151', 'Nota del caso',
   '11700000-0000-4000-8000-0000000000a1', 1);
reset role;

select alike(pg_temp.run_as('origenlab_api', $$ update crm.note set subject_id = '11700000-0000-4000-8000-000000000203'
   where id = '11700000-0000-4000-8000-000000000301' $$),
  'P0001%', 'a note is not repointed to a person its subject was not merged into');
select alike(pg_temp.run_as('origenlab_api', $$ update crm.note set subject_id = '11700000-0000-4000-8000-000000000202',
     archived_at = now(), archived_by_operator_id = '11700000-0000-4000-8000-0000000000a1', archive_reason = 'x'
   where id = '11700000-0000-4000-8000-000000000301' $$),
  'P0001%', 'a repoint that also writes the archive columns is refused');
select alike(pg_temp.run_as('origenlab_api', $$ update crm.note set subject_id = '11700000-0000-4000-8000-000000000202'
   where id = '11700000-0000-4000-8000-000000000303' $$),
  'P0001%', 'a case note is never repointed');
select is(pg_temp.run_as('origenlab_api', $$ update crm.note set subject_id = '11700000-0000-4000-8000-000000000202'
   where id = '11700000-0000-4000-8000-000000000301' $$),
  'ok', 'a note moves to the person its subject was merged into');
select is(pg_temp.run_as('origenlab_api', $$ update crm.note set subject_id = '11700000-0000-4000-8000-000000000201'
   where id = '11700000-0000-4000-8000-000000000301' $$),
  'ok', 'the undo moves it back while the merge still stands');
select is((select version from crm.note where id = '11700000-0000-4000-8000-000000000301'), 1,
  'a repoint leaves the note''s version alone');

-- ── platform.model_usage: the worker's features ──────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ insert into platform.model_usage
    (feature, model, step, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, usd_cost)
  values ('triage', 'modelo-ejemplo', 'read', 1, 1, 0, 0, 0),
         ('case_pick', 'modelo-ejemplo', 'read', 1, 1, 0, 0, 0),
         ('subject_memory', 'modelo-ejemplo', 'read', 1, 1, 0, 0, 0),
         ('quote_read', 'modelo-ejemplo', 'read', 1, 1, 0, 0, 0) $$),
  'ok', 'the worker records spend under triage, case_pick, subject_memory and quote_read');

select * from finish();
rollback;
