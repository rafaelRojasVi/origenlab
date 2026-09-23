-- Slice 2 — declarative invariants of the commercial case: crm.opportunity_organization (#34),
-- crm.opportunity_interest (#35) and crm.opportunity_evidence (#36).
--
-- docs/DOMAIN.md §3.6 (the commercial case), §7.1; docs/ARCHITECTURE.md §3.1 (one writer);
-- docs/WORKFLOWS.md §1.1. Exercised as the owner, which crosses RLS by ownership and holds every
-- privilege, so only the constraints and the structural triggers speak. A rule that the owner
-- cannot break is a rule no runtime role can break either.
--
-- SQLSTATEs: 23502 not null, 23505 unique, 23514 check, 23P01 exclusion, P0001 trigger guard.
--
-- The agreement between `crm.opportunity.organization_id` and the current confirmed requesting
-- institution is a DEFERRED constraint trigger — `set_requesting_institution` writes both rows in
-- one transaction and neither order may be refused. It is therefore exercised by appending
-- `set constraints all immediate` to the statement under test, which forces the pending check at
-- that point instead of at a commit this test never performs.
begin;
create extension if not exists pgtap with schema extensions;
-- Fixture (rolled back): the owner may call pgTAP for the duration of this transaction.
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(69);

-- ── fixtures ───────────────────────────────────────────────────────────────────────────────
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('10000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-0000000000f1', 'case.admin@example.test', 'Case Admin', 'admin', 'active'),
  ('10000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-0000000000f2', 'case.sales@example.test', 'Case Sales', 'sales', 'active');

insert into crm.organization (id, kind, name, confirmation) values
  ('10000000-0000-4000-8000-0000000000a1', 'university', 'Universidad Ejemplo', 'confirmed'),
  ('10000000-0000-4000-8000-0000000000a2', 'company', 'Distribuidora Ejemplo', 'confirmed'),
  ('10000000-0000-4000-8000-0000000000a3', 'company', 'Otra Institución', 'confirmed');

-- The distributor is a registered supplier and manufacturer, today and with no end date.
insert into crm.organization_relationship (organization_id, role, valid_from) values
  ('10000000-0000-4000-8000-0000000000a2', 'supplier', '2024-01-01'),
  ('10000000-0000-4000-8000-0000000000a2', 'manufacturer', '2024-01-01');

insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('10000000-0000-4000-8000-0000000000b1', 'Caso sin solicitante', 'lead', '10000000-0000-4000-8000-000000000001'),
  ('10000000-0000-4000-8000-0000000000b2', 'Caso con solicitante', 'lead', '10000000-0000-4000-8000-000000000001'),
  ('10000000-0000-4000-8000-0000000000b3', 'Caso de concordancia', 'lead', '10000000-0000-4000-8000-000000000001'),
  ('10000000-0000-4000-8000-0000000000b4', 'Caso de excepción proveedor', 'lead', '10000000-0000-4000-8000-000000000001');

insert into catalog.product (id, manufacturer_organization_id, model_number) values
  ('10000000-0000-4000-8000-0000000000c1', '10000000-0000-4000-8000-0000000000a2', 'UP200Ht');

insert into evidence.source_record (id, kind, dedupe_key, payload) values
  ('10000000-0000-4000-8000-0000000000d1', 'workbook_import', 'case-fixture-1', '{}'::jsonb);
insert into evidence.assertion (id, source_record_id, kind, value_norm) values
  ('10000000-0000-4000-8000-0000000000d2', '10000000-0000-4000-8000-0000000000d1', 'organization_name', 'universidad ejemplo');
insert into comms.mailbox (id, address_norm) values
  ('10000000-0000-4000-8000-0000000000e1', 'buzon@example.test');
insert into comms.message (id, mailbox_id, provider_message_id, direction, internal_date) values
  ('10000000-0000-4000-8000-0000000000e2', '10000000-0000-4000-8000-0000000000e1', 'msg-case-1', 'inbound', now());
insert into procurement.notice (id, codigo_externo, head) values
  ('10000000-0000-4000-8000-0000000000f9', 'ID-CASE-1', '{}'::jsonb);

-- ── #34 crm.opportunity_organization — what one institution is to one case ──────────────────

-- The machine proposes `mentioned`, and nothing else. Honest silence is representable; a guess
-- with a role attached is not.
select lives_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, origin_source_record_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a2', 'mentioned', '2026-01-01', 'machine_proposed', '10000000-0000-4000-8000-0000000000d1') $$,
  'opportunity_organization: the machine may propose `mentioned`');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a3', 'manufacturer', '2026-01-01', 'machine_proposed') $$,
  '23514', null, 'opportunity_organization: the machine may not propose any role but `mentioned`');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a1', 'requesting_institution', '2026-01-01', 'machine_proposed') $$,
  '23514', null, 'opportunity_organization: a machine-proposed requesting institution is unrepresentable');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a1', 'requesting_institution', '2026-01-01', 'confirmed') $$,
  '23514', null, 'opportunity_organization: a requesting institution names the operator who confirmed it');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a3', 'mentioned', '2026-01-01', 'machine_proposed', '10000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'opportunity_organization: a machine-proposed row has no confirmer');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a3', 'beneficiary', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'opportunity_organization: role is a closed vocabulary');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a3', 'funder', '2026-01-01', 'guessed', '10000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'opportunity_organization: confirmation ∈ {machine_proposed, confirmed}');
-- valid_to >= valid_from, relaxed from `>` by 20260922200000: a reading opened and withdrawn
-- the same day is a true sentence, and until then it was unrepresentable — which made
-- §3.6.1's own correction procedure ("close valid_to and open the right row") impossible on
-- the day a row was created, which is when almost every correction happens. The zero-length
-- daterange overlaps nothing, so the exclusion constraint is unaffected either way.
select lives_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, valid_to, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a3', 'funder', '2026-01-01', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001') $$,
  'opportunity_organization: a reading may be opened and withdrawn on the same day');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, valid_to, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a3', 'purchasing_agent', '2026-01-01', '2025-12-31', 'confirmed', '10000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'opportunity_organization: valid_to >= valid_from');

-- Several concurrent roles for one institution on one case; the same role may not overlap itself.
select lives_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a2', 'supplier', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001') $$,
  'opportunity_organization: one institution may be supplier on a case where it is already mentioned');
select lives_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a2', 'manufacturer', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001') $$,
  'opportunity_organization: supplier and manufacturer coexist on one case');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a2', 'supplier', '2026-06-01', 'confirmed', '10000000-0000-4000-8000-000000000001') $$,
  '23P01', null, 'opportunity_organization: the same (case, institution, role) may not overlap itself in time');
select lives_ok($$
  update crm.opportunity_organization set valid_to = '2026-06-01'
   where opportunity_id = '10000000-0000-4000-8000-0000000000b1'
     and organization_id = '10000000-0000-4000-8000-0000000000a2' and role = 'supplier';
  insert into crm.opportunity_organization
    (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
    values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000a2', 'supplier', '2026-06-01', 'confirmed', '10000000-0000-4000-8000-000000000001');
$$, 'opportunity_organization: a role resumes once the previous period is closed ([) ranges)');
select throws_ok($$ delete from crm.opportunity_organization
  where opportunity_id = '10000000-0000-4000-8000-0000000000b1' and role = 'mentioned' $$,
  'P0001', null, 'opportunity_organization: rows are never deleted (a wrong decision is closed, not erased)');

-- One current requesting institution per case, written with the opportunity in one transaction.
select lives_ok($$
  insert into crm.opportunity_organization
    (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
    values ('10000000-0000-4000-8000-0000000000b2', '10000000-0000-4000-8000-0000000000a1', 'requesting_institution', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001');
  update crm.opportunity set organization_id = '10000000-0000-4000-8000-0000000000a1'
   where id = '10000000-0000-4000-8000-0000000000b2';
  set constraints all immediate;
  set constraints all deferred;
$$, 'opportunity_organization: an operator confirms the requesting institution and the case names it');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b2', '10000000-0000-4000-8000-0000000000a3', 'requesting_institution', '2026-02-01', 'confirmed', '10000000-0000-4000-8000-000000000001') $$,
  '23505', null, 'opportunity_organization: at most one current requesting institution per case');

-- The agreement with crm.opportunity.organization_id holds in both directions.
select throws_ok($$
  update crm.opportunity set organization_id = '10000000-0000-4000-8000-0000000000a1'
   where id = '10000000-0000-4000-8000-0000000000b3';
  set constraints all immediate;
$$, 'P0001', null, 'agreement: a case may not name a customer without a confirmed requesting institution');
select throws_ok($$
  insert into crm.opportunity_organization
    (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
    values ('10000000-0000-4000-8000-0000000000b3', '10000000-0000-4000-8000-0000000000a1', 'requesting_institution', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001');
  set constraints all immediate;
$$, 'P0001', null, 'agreement: a confirmed requesting institution must be the case customer');
select throws_ok($$
  insert into crm.opportunity_organization
    (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
    values ('10000000-0000-4000-8000-0000000000b3', '10000000-0000-4000-8000-0000000000a1', 'requesting_institution', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001');
  update crm.opportunity set organization_id = '10000000-0000-4000-8000-0000000000a3'
   where id = '10000000-0000-4000-8000-0000000000b3';
  set constraints all immediate;
$$, 'P0001', null, 'agreement: the case customer and the requesting institution are the same institution');
-- Since 20260922200000 a case is *opened* at `lead` and at no other stage, so the `qualified`
-- rules below are reached by moving a case rather than by inserting one already there — which
-- is also the only way a real case ever reaches them.
select throws_ok($$ insert into crm.opportunity (title, stage, owner_operator_id, organization_id)
  values ('Calificado de nacimiento', 'qualified', '10000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-0000000000a1') $$,
  'P0001', null, 'opportunity: a case is opened at `lead` and never at a later stage');
select throws_ok($$
  update crm.opportunity set organization_id = '10000000-0000-4000-8000-0000000000a1'
   where id = '10000000-0000-4000-8000-0000000000b3';
  update crm.opportunity set stage = 'qualifying' where id = '10000000-0000-4000-8000-0000000000b3';
  update crm.opportunity set stage = 'qualified' where id = '10000000-0000-4000-8000-0000000000b3';
  set constraints all immediate;
$$, 'P0001', null, 'agreement: a case cannot reach qualified without a confirmed requesting institution');
-- b1 has institutions on it (mentioned, supplier, manufacturer) and no requesting one, so its
-- organization_id is null: the state §3.6.5 says `lead` and `qualifying` may sit in forever.
select lives_ok($$ update crm.opportunity set stage = 'qualifying'
  where id = '10000000-0000-4000-8000-0000000000b1' $$,
  'opportunity: a case with no requesting institution may still be qualifying');
select throws_ok($$ update crm.opportunity set stage = 'qualified'
  where id = '10000000-0000-4000-8000-0000000000b1' $$,
  '23514', null, 'opportunity: the organization-required-from-qualified rule still refuses qualified');
-- ...and the two exits stay reachable, which before 20260922200000 they were not: the same
-- CHECK caught `lost` and `abandoned`, so a case that never found out who was asking could be
-- opened and then never closed.
select lives_ok($$ update crm.opportunity
  set stage = 'abandoned', closed_at = now(), close_reason = 'nunca respondieron'
  where id = '10000000-0000-4000-8000-0000000000b1' $$,
  'opportunity: a case that never found its requester can still be abandoned');
select throws_ok($$ update crm.opportunity
  set stage = 'lead', closed_at = null, close_reason = null
  where id = '10000000-0000-4000-8000-0000000000b1' $$,
  'P0001', null, 'opportunity: a terminal case is never revived');
select throws_ok($$ delete from crm.opportunity where id = '10000000-0000-4000-8000-0000000000b1' $$,
  'P0001', null, 'opportunity: a case is never deleted');
select lives_ok($$
  update crm.opportunity_organization set valid_to = '2026-03-01'
   where opportunity_id = '10000000-0000-4000-8000-0000000000b2' and role = 'requesting_institution';
  update crm.opportunity set organization_id = null where id = '10000000-0000-4000-8000-0000000000b2';
  set constraints all immediate;
  set constraints all deferred;
$$, 'agreement: closing the requesting institution and clearing the customer together is allowed');

-- The supplier exception: the only way a registered supplier becomes a requesting institution.
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b4', '10000000-0000-4000-8000-0000000000a2', 'requesting_institution', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001') $$,
  'P0001', null, 'supplier exception: a registered supplier or manufacturer is refused as requesting institution');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id, supplier_exception_reason)
  values ('10000000-0000-4000-8000-0000000000b4', '10000000-0000-4000-8000-0000000000a3', 'requesting_institution', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001', 'compra para su propio laboratorio') $$,
  '23514', null, 'supplier exception: the triple is all-or-none (a reason alone is not a record)');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id,
   supplier_exception_by_operator_id, supplier_exception_reason, supplier_exception_at)
  values ('10000000-0000-4000-8000-0000000000b4', '10000000-0000-4000-8000-0000000000a3', 'requesting_institution', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001',
          '10000000-0000-4000-8000-000000000001', '   ', now()) $$,
  '23514', null, 'supplier exception: the reason is not blank');
select throws_ok($$ insert into crm.opportunity_organization
  (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id,
   supplier_exception_by_operator_id, supplier_exception_reason, supplier_exception_at)
  values ('10000000-0000-4000-8000-0000000000b4', '10000000-0000-4000-8000-0000000000a3', 'supplier', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001',
          '10000000-0000-4000-8000-000000000001', 'razón', now()) $$,
  '23514', null, 'supplier exception: the triple belongs to a requesting institution and to no other role');
select lives_ok($$
  insert into crm.opportunity_organization
    (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id,
     supplier_exception_by_operator_id, supplier_exception_reason, supplier_exception_at)
    values ('10000000-0000-4000-8000-0000000000b4', '10000000-0000-4000-8000-0000000000a2', 'requesting_institution', '2026-01-01', 'confirmed', '10000000-0000-4000-8000-000000000001',
            '10000000-0000-4000-8000-000000000001', 'compra un equipo para su propio laboratorio', now());
  update crm.opportunity set organization_id = '10000000-0000-4000-8000-0000000000a2'
   where id = '10000000-0000-4000-8000-0000000000b4';
  set constraints all immediate;
  set constraints all deferred;
$$, 'supplier exception: with an operator, a motive and a date, the distributor may be the requesting institution');
select throws_ok($$ update crm.opportunity_organization set supplier_exception_reason = 'otra razón'
  where opportunity_id = '10000000-0000-4000-8000-0000000000b4' and role = 'requesting_institution' $$,
  'P0001', null, 'supplier exception: the motive can never be rewritten');
select throws_ok($$ update crm.opportunity_organization set supplier_exception_by_operator_id = '10000000-0000-4000-8000-000000000002'
  where opportunity_id = '10000000-0000-4000-8000-0000000000b4' and role = 'requesting_institution' $$,
  'P0001', null, 'supplier exception: the operator who granted it can never be rewritten');
select throws_ok($$ update crm.opportunity_organization
  set supplier_exception_by_operator_id = null, supplier_exception_reason = null, supplier_exception_at = null
  where opportunity_id = '10000000-0000-4000-8000-0000000000b4' and role = 'requesting_institution' $$,
  'P0001', null, 'supplier exception: the record can never be erased');
select is(
  (select count(*)::int from crm.organization_relationship
    where organization_id = '10000000-0000-4000-8000-0000000000a2'
      and role in ('supplier', 'manufacturer') and valid_to is null),
  2, 'supplier exception: both truths stay visible — the supplier and manufacturer relationships are untouched');
select is(
  (select count(*)::int from crm.organization_relationship
    where organization_id = '10000000-0000-4000-8000-0000000000a2' and role in ('customer', 'prospect')),
  0, 'supplier exception: it opens no customer and no prospect relationship');

-- ── #35 crm.opportunity_interest — what the case is seeking ─────────────────────────────────

select lives_ok($$ insert into crm.opportunity_interest
  (opportunity_id, model_text, confirmation, origin_source_record_id)
  values ('10000000-0000-4000-8000-0000000000b1', 'UP200Ht', 'machine_proposed', '10000000-0000-4000-8000-0000000000d1') $$,
  'opportunity_interest: a model string read off a message is enough');
select throws_ok($$ insert into crm.opportunity_interest (opportunity_id, description, confirmation)
  values ('10000000-0000-4000-8000-0000000000b1', 'algo', 'machine_proposed') $$,
  '23514', null, 'opportunity_interest: at least one of product, manufacturer or model text');
select throws_ok($$ insert into crm.opportunity_interest (opportunity_id, model_text, confirmation)
  values ('10000000-0000-4000-8000-0000000000b1', '   ', 'machine_proposed') $$,
  '23514', null, 'opportunity_interest: model text is not blank');
select lives_ok($$ insert into crm.opportunity_interest
  (opportunity_id, product_id, manufacturer_organization_id, quantity, quantity_unit, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000c1', '10000000-0000-4000-8000-0000000000a2', 2, 'unidad', 'confirmed', '10000000-0000-4000-8000-000000000001') $$,
  'opportunity_interest: a catalogue product and its own manufacturer agree');
select throws_ok($$ insert into crm.opportunity_interest
  (opportunity_id, product_id, manufacturer_organization_id, confirmation)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000c1', '10000000-0000-4000-8000-0000000000a3', 'machine_proposed') $$,
  'P0001', null, 'opportunity_interest: a product and a disagreeing manufacturer are refused');
select throws_ok($$ insert into crm.opportunity_interest (opportunity_id, model_text, quantity, confirmation)
  values ('10000000-0000-4000-8000-0000000000b1', 'X-1', 0, 'machine_proposed') $$,
  '23514', null, 'opportunity_interest: quantity > 0 when present');
select throws_ok($$ insert into crm.opportunity_interest (opportunity_id, model_text, confirmation, withdrawn_at)
  values ('10000000-0000-4000-8000-0000000000b1', 'X-2', 'machine_proposed', now()) $$,
  '23514', null, 'opportunity_interest: withdrawal is a reason-bearing pair');
select throws_ok($$ insert into crm.opportunity_interest (opportunity_id, model_text, confirmation, confirmed_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', 'X-3', 'machine_proposed', '10000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'opportunity_interest: a machine-proposed interest has no confirmer');
select lives_ok($$ update crm.opportunity_interest
  set withdrawn_at = now(), withdraw_reason = 'el caso dejó de tratarse de un homogeneizador'
  where opportunity_id = '10000000-0000-4000-8000-0000000000b1' and model_text = 'UP200Ht' $$,
  'opportunity_interest: an interest is withdrawn with its motive, not deleted');
select throws_ok($$ delete from crm.opportunity_interest
  where opportunity_id = '10000000-0000-4000-8000-0000000000b1' and model_text = 'UP200Ht' $$,
  'P0001', null, 'opportunity_interest: rows are never deleted');
select is(
  (select count(*)::int from information_schema.columns
    where table_schema = 'crm' and table_name = 'opportunity_interest'
      and (column_name ~ 'price|amount|currency|margin|total|cost' )),
  0, 'opportunity_interest: no money column — an interest is never a commitment and never a price');

-- ── #36 crm.opportunity_evidence — why the case believes what it believes ───────────────────

select lives_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, source_record_id, relation, linked_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000d1', 'origin', '10000000-0000-4000-8000-000000000001') $$,
  'opportunity_evidence: a source record is a typed subject');
select lives_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, message_id, relation, linked_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000e2', 'mentions', '10000000-0000-4000-8000-000000000001') $$,
  'opportunity_evidence: a message is a typed subject');
select lives_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, notice_id, relation, linked_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000f9', 'origin', '10000000-0000-4000-8000-000000000001') $$,
  'opportunity_evidence: a notice is a typed subject');
select lives_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, assertion_id, relation, linked_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000d2', 'supports_requesting_institution', '10000000-0000-4000-8000-000000000001') $$,
  'opportunity_evidence: an assertion is a typed subject');
select throws_ok($$ insert into crm.opportunity_evidence (opportunity_id, relation, linked_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', 'mentions', '10000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'opportunity_evidence: exactly one typed subject — none is refused');
select throws_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, source_record_id, message_id, relation, linked_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000d1', '10000000-0000-4000-8000-0000000000e2', 'mentions', '10000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'opportunity_evidence: exactly one typed subject — two are refused');
select throws_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, source_record_id, relation, linked_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000d1', 'proves', '10000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'opportunity_evidence: relation is a closed vocabulary');
select lives_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, source_record_id, relation, linked_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000d1', 'contradicts', '10000000-0000-4000-8000-000000000001') $$,
  'opportunity_evidence: `contradicts` is first-class — the reading against a case is durable');
select throws_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, source_record_id, relation, linked_by_operator_id)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000d1', 'origin', '10000000-0000-4000-8000-000000000002') $$,
  '23505', null, 'opportunity_evidence: one link per (case, subject, relation) while linked');
select throws_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, source_record_id, relation, linked_by_operator_id, unlinked_at)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000d1', 'supports_interest', '10000000-0000-4000-8000-000000000001', now()) $$,
  '23514', null, 'opportunity_evidence: unlinking is a reason-bearing pair');
select throws_ok($$ insert into crm.opportunity_evidence
  (opportunity_id, source_record_id, relation)
  values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000d1', 'supports_interest') $$,
  '23502', null, 'opportunity_evidence: only a named operator links evidence to a case');
select lives_ok($$
  update crm.opportunity_evidence set unlinked_at = now(), unlink_reason = 'no era este caso'
   where opportunity_id = '10000000-0000-4000-8000-0000000000b1'
     and source_record_id = '10000000-0000-4000-8000-0000000000d1' and relation = 'origin';
  insert into crm.opportunity_evidence
    (opportunity_id, source_record_id, relation, linked_by_operator_id)
    values ('10000000-0000-4000-8000-0000000000b1', '10000000-0000-4000-8000-0000000000d1', 'origin', '10000000-0000-4000-8000-000000000001');
$$, 'opportunity_evidence: the same subject may be linked again once the earlier link is closed');
select throws_ok($$ update crm.opportunity_evidence set relation = 'mentions'
  where opportunity_id = '10000000-0000-4000-8000-0000000000b1' and relation = 'contradicts' $$,
  'P0001', null, 'opportunity_evidence: append-only — the relation of a link is never rewritten');
select throws_ok($$ update crm.opportunity_evidence set source_record_id = null, message_id = '10000000-0000-4000-8000-0000000000e2'
  where opportunity_id = '10000000-0000-4000-8000-0000000000b1' and relation = 'contradicts' $$,
  'P0001', null, 'opportunity_evidence: append-only — the subject of a link is never rewritten');
select throws_ok($$ delete from crm.opportunity_evidence
  where opportunity_id = '10000000-0000-4000-8000-0000000000b1' and relation = 'contradicts' $$,
  'P0001', null, 'opportunity_evidence: rows are never deleted');

-- ── the separations these three tables must keep ────────────────────────────────────────────

select is(
  (select count(*)::int from pg_constraint fk
     join pg_class c on c.oid = fk.conrelid
     join pg_namespace n on n.oid = c.relnamespace
     join pg_class r on r.oid = fk.confrelid
     join pg_namespace rn on rn.oid = r.relnamespace
    where fk.contype = 'f' and n.nspname = 'crm'
      and c.relname in ('opportunity_organization', 'opportunity_interest', 'opportunity_evidence')
      and rn.nspname = 'outbound'),
  0, 'the case model holds no foreign key into outbound.* — marketing is separate in both directions');
select is(
  (select count(*)::int from pg_constraint fk
     join pg_class c on c.oid = fk.conrelid
     join pg_namespace n on n.oid = c.relnamespace
     join pg_class r on r.oid = fk.confrelid
     join pg_namespace rn on rn.oid = r.relnamespace
    where fk.contype = 'f' and rn.nspname = 'crm'
      and r.relname in ('opportunity_organization', 'opportunity_interest', 'opportunity_evidence')
      and n.nspname = 'outbound'),
  0, 'no outbound.* table points at the case model either');
select is(
  (select count(*)::int from information_schema.columns
    where table_schema = 'crm'
      and table_name in ('opportunity_organization', 'opportunity_interest', 'opportunity_evidence')
      and column_name ~ 'consent|opt_in|opt_out|subscription|audience|campaign'),
  0, 'the case model carries no consent, subscription, audience or campaign column');
select hasnt_table('crm', 'commercial_case', 'the case is crm.opportunity: no commercial_case table and no second lifecycle');
select throws_ok($$ insert into crm.quote (quote_number, created_by_operator_id)
  values ('Q-CASE-1', '10000000-0000-4000-8000-000000000001') $$,
  '23502', null, 'a quote still requires an opportunity');
select is(
  (select count(*)::int from crm.opportunity where stage not in
    ('lead', 'qualifying', 'qualified', 'quoting', 'negotiating', 'won', 'lost', 'abandoned')),
  0, 'the opportunity stage machine is unchanged — the case added no stage');

select * from finish();
rollback;
