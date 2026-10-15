-- Slice 7 — Cotizador PR A (20261016100000): authored quote lines carry what the engine needs, a
-- revision carries its printed conditions and pricing choices, lines change only while their
-- revision is an authored draft, and a revision holds one exchange rate per currency.
-- Synthetic fixtures only. Run as the owner (crosses RLS by ownership; only the constraints, the
-- guard trigger and the vocabularies speak), plus one API-role proof for the new DELETE grant.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
grant origenlab_api to session_user with set true, inherit false;
set role origenlab_owner;
select plan(15);

-- Fixture (rolled back): an operator, an organization, a case, a quote, one authored draft
-- revision and one approved revision.
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
values ('97000000-0000-4000-8000-0000000000a1', '97000000-0000-4000-8000-0000000000f1', 'admin97@example.test', 'Admin', 'admin', 'active');
insert into crm.organization (id, kind, name, confirmation)
values ('97000000-0000-4000-8000-000000000001', 'company', 'Cotizador Org', 'confirmed');
insert into crm.opportunity (id, title, stage, owner_operator_id, organization_id)
values ('97000000-0000-4000-8000-000000000051', 'Authoring case', 'lead', '97000000-0000-4000-8000-0000000000a1', '97000000-0000-4000-8000-000000000001');
update crm.opportunity set stage = 'qualifying' where id = '97000000-0000-4000-8000-000000000051';
update crm.opportunity set stage = 'qualified' where id = '97000000-0000-4000-8000-000000000051';
update crm.opportunity set stage = 'quoting' where id = '97000000-0000-4000-8000-000000000051';
insert into crm.quote (id, opportunity_id, quote_number)
values ('97000000-0000-4000-8000-000000000070', '97000000-0000-4000-8000-000000000051', 'Q-2026-9701');
-- Revision 1 is approved (immutable); revision 2 is the authored draft.
insert into crm.quote_revision (id, quote_id, revision_no, status, quote_currency, price_decimals,
  subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at,
  party_snapshot, party_snapshot_version, approved_at, approved_by_operator_id)
values ('97000000-0000-4000-8000-000000000081', '97000000-0000-4000-8000-000000000070', 1, 'approved', 'CLP', 0,
  100, 0, 100, 19, 119, now(), '{"sold_to": {}}', 1, now(), '97000000-0000-4000-8000-0000000000a1');
insert into crm.quote_revision (id, quote_id, revision_no, status, quote_currency, price_decimals)
values ('97000000-0000-4000-8000-000000000082', '97000000-0000-4000-8000-000000000070', 2, 'draft', 'CLP', 0);

select has_column('crm', 'quote_line', 'item_type', 'quote_line.item_type exists');
select has_column('crm', 'quote_line', 'cost_observation_id', 'quote_line.cost_observation_id exists');
select has_column('crm', 'quote_revision', 'principal_rule', 'quote_revision.principal_rule exists');
select has_column('crm', 'quote_revision', 'fx_override_reasons', 'quote_revision.fx_override_reasons exists');

select lives_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, item_type)
  values ('97000000-0000-4000-8000-000000000082', 1, 'item', 'Equipo de prueba', 1, 'equipment') $$,
  'a line goes into an authored draft');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity)
  values ('97000000-0000-4000-8000-000000000081', 1, 'item', 'Equipo de prueba', 1) $$,
  '23514', null, 'no line goes into an approved revision');
select lives_ok($$ delete from crm.quote_line where quote_revision_id = '97000000-0000-4000-8000-000000000082' $$,
  'draft lines can be deleted');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, item_type)
  values ('97000000-0000-4000-8000-000000000082', 1, 'item', 'X', 1, 'gadget') $$,
  '23514', null, 'item_type is a closed vocabulary');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, expense_method)
  values ('97000000-0000-4000-8000-000000000082', 2, 'item', 'X', 1, 'manual') $$,
  '23514', null, 'expense_method only on logistics/fee lines');

-- One rate per currency in a revision.
select lives_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, cost_currency, unit_cost, fx_rate, fx_as_of, fx_source)
  values ('97000000-0000-4000-8000-000000000082', 3, 'item', 'A', 1, 'EUR', 100, 1000, '2026-01-02', 'bcentral') $$,
  'first EUR line');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, cost_currency, unit_cost, fx_rate, fx_as_of, fx_source)
  values ('97000000-0000-4000-8000-000000000082', 4, 'item', 'B', 1, 'EUR', 100, 1001, '2026-01-02', 'bcentral') $$,
  '23514', null, 'a second EUR rate is refused');

select throws_ok($$ insert into catalog.cost_parameter (key, value_numeric, reason)
  values ('quote_number_seedx', 1, 'x') $$, '23514', null, 'unknown cost-parameter key still refused');
select lives_ok($$ insert into catalog.cost_parameter (key, value_json, reason)
  values ('quote_default_warranty', to_jsonb('[GARANTÍA]'::text), 'prueba') $$,
  'a text default is stored as json');
select ok(crm.domain_event_is_valid('quote_revision', 'quote_revision.draft_saved', 1::smallint, '{}'::jsonb),
  'draft_saved is a quote_revision event');

-- The API role may delete a draft's lines (grant + RLS policy), and the row is really gone.
create function pg_temp.api_deletes(p_revision uuid, p_line_no int) returns text
language plpgsql as $f$
declare n bigint;
begin
  set local role origenlab_api;
  delete from crm.quote_line where quote_revision_id = p_revision and line_no = p_line_no;
  get diagnostics n = row_count;
  reset role;
  set local role origenlab_owner;
  return 'deleted ' || n;
exception when others then
  reset role;
  set local role origenlab_owner;
  return sqlstate || ': ' || sqlerrm;
end
$f$;
select is(pg_temp.api_deletes('97000000-0000-4000-8000-000000000082', 3), 'deleted 1',
  'the API role deletes a line of its draft');

select * from finish();
rollback;
