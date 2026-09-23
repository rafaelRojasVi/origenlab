-- Slice 3 — what the commercial-case commands are allowed to say, proven in the database.
--
-- docs/DOMAIN.md §3.6, §3.6.5; docs/WORKFLOWS.md §1.1; docs/DATA.md §1.1.
-- Companion to 062, which proves the three case tables' own shape. This file proves the three
-- things `20260922200000_slice3_commercial_case_commands.sql` added: the audit vocabulary the
-- commands write, the stage machine, and the corrected organization rule.
--
-- Exercised as the owner, which crosses RLS by ownership and holds every privilege, so only the
-- constraints and the structural triggers speak. A rule that the owner cannot break is a rule
-- no runtime role can break either — and the stage machine in particular is a rule the command
-- boundary states too, so proving it here is proving that the code is not the only thing
-- holding it.
--
-- SQLSTATEs: 23514 check, P0001 trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(47);

-- ── fixtures ───────────────────────────────────────────────────────────────────────────────

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('20000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-0000000000f1', 'cmd.admin@example.test', 'Cmd Admin', 'admin', 'active');

insert into crm.organization (id, kind, name, confirmation) values
  ('20000000-0000-4000-8000-0000000000a1', 'university', 'Universidad de Comandos', 'confirmed');

insert into evidence.source_record (id, kind, dedupe_key, payload) values
  ('20000000-0000-4000-8000-0000000000d1', 'gmail_message', 'cmd-fixture-1', '{}'::jsonb);

-- One case per stage-machine row, so each transition is tried on a case of its own and a
-- refusal in one does not hide a refusal in the next.
insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('20000000-0000-4000-8000-0000000000b1', 'Caso del vocabulario', 'lead', '20000000-0000-4000-8000-000000000001'),
  ('20000000-0000-4000-8000-0000000000b2', 'Caso de las etapas', 'lead', '20000000-0000-4000-8000-000000000001'),
  ('20000000-0000-4000-8000-0000000000b3', 'Caso terminal', 'lead', '20000000-0000-4000-8000-000000000001'),
  ('20000000-0000-4000-8000-0000000000b4', 'Caso sin solicitante', 'lead', '20000000-0000-4000-8000-000000000001');

insert into crm.opportunity_organization (id, opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id) values
  ('20000000-0000-4000-8000-0000000000c1', '20000000-0000-4000-8000-0000000000b1', '20000000-0000-4000-8000-0000000000a1', 'mentioned', current_date, 'confirmed', '20000000-0000-4000-8000-000000000001');
insert into crm.opportunity_interest (id, opportunity_id, model_text, confirmation, confirmed_by_operator_id) values
  ('20000000-0000-4000-8000-0000000000c2', '20000000-0000-4000-8000-0000000000b1', 'UP200Ht', 'confirmed', '20000000-0000-4000-8000-000000000001');
insert into crm.opportunity_evidence (id, opportunity_id, source_record_id, relation, linked_by_operator_id) values
  ('20000000-0000-4000-8000-0000000000c3', '20000000-0000-4000-8000-0000000000b1', '20000000-0000-4000-8000-0000000000d1', 'origin', '20000000-0000-4000-8000-000000000001');

-- ── 1. The audit vocabulary the commands write ─────────────────────────────────────────────
--
-- Five event types and three aggregate kinds, each proven by writing one. An event type
-- nothing can emit is a promise rather than a contract — and an event type a command emits
-- that the CHECK refuses is a command that cannot finish, which is how this list is kept
-- honest in both directions.

select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_organization', '20000000-0000-4000-8000-0000000000c1', 1, 'case_organization.added', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  'domain_event: case_organization.added');
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_organization', '20000000-0000-4000-8000-0000000000c1', 2, 'case_organization.role_confirmed', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  'domain_event: case_organization.role_confirmed');
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_organization', '20000000-0000-4000-8000-0000000000c1', 3, 'case_organization.ended', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  'domain_event: case_organization.ended');
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_interest', '20000000-0000-4000-8000-0000000000c2', 1, 'case_interest.added', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  'domain_event: case_interest.added');
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_evidence', '20000000-0000-4000-8000-0000000000c3', 1, 'case_evidence.linked', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  'domain_event: case_evidence.linked');

-- The family must match the aggregate kind. A case event filed under the opportunity is the
-- mistake this validator exists to make impossible.
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '20000000-0000-4000-8000-0000000000b1', 1, 'case_organization.added', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'domain_event: a case_organization event may not be filed under an opportunity');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_interest', '20000000-0000-4000-8000-0000000000c2', 2, 'case_evidence.linked', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'domain_event: an interest does not emit evidence events');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_organization', '20000000-0000-4000-8000-0000000000c1', 4, 'case_organization.invented', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'domain_event: the event vocabulary stays closed');

-- What the commands do NOT emit is absent on purpose: the columns for withdrawing an interest
-- and unlinking evidence exist, and until a command writes them their event types stay out.
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_interest', '20000000-0000-4000-8000-0000000000c2', 3, 'case_interest.withdrawn', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'domain_event: no command withdraws an interest, so no event type says one did');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_evidence', '20000000-0000-4000-8000-0000000000c3', 2, 'case_evidence.unlinked', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'domain_event: no command unlinks evidence, so no event type says one did');

-- The older vocabulary survived this migration's rewrite of the CHECK. Regression: the list is
-- retyped in full every time it grows, and the one added on 2026-09-22 is easy to drop.
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('source_record', '20000000-0000-4000-8000-0000000000d1', 1, 'source_record.review_noted', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  'domain_event: source_record.review_noted survived the vocabulary extension');
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '20000000-0000-4000-8000-0000000000b1', 2, 'opportunity.organization_set', 1, '{}'::jsonb, 'operator', '20000000-0000-4000-8000-000000000001') $$,
  'domain_event: the case reuses opportunity.* rather than inventing a case.* family');
select is(
  (select count(*)::int from pg_constraint
    where conname = 'domain_event_type_check'
      and pg_get_constraintdef(oid) ~ 'case\.'),
  0, 'domain_event: there is no `case.*` family — the case is an opportunity');

-- ── 2. The stage machine ───────────────────────────────────────────────────────────────────
--
-- WORKFLOWS.md §1.1 was a table in a document and a Slice 2 note that said "command trigger".
-- It is a trigger now. Every pair below is a row of that table, and the ones that are absent
-- from it are the ones that raise.

select throws_ok($$ insert into crm.opportunity (title, stage, owner_operator_id) values ('x', 'qualifying', '20000000-0000-4000-8000-000000000001') $$,
  'P0001', null, 'stage: a case is opened at lead, not at qualifying');
select throws_ok($$ insert into crm.opportunity (title, stage, owner_operator_id, closed_at, close_reason) values ('x', 'abandoned', '20000000-0000-4000-8000-000000000001', now(), 'nace muerto') $$,
  'P0001', null, 'stage: a case is not opened already abandoned');

-- lead
select throws_ok($$ update crm.opportunity set stage = 'qualified' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'P0001', null, 'stage: lead → qualified is not a move');
select throws_ok($$ update crm.opportunity set stage = 'quoting' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'P0001', null, 'stage: lead → quoting is not a move');
select throws_ok($$ update crm.opportunity set stage = 'negotiating' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'P0001', null, 'stage: lead → negotiating is not a move');
select throws_ok($$ update crm.opportunity set stage = 'won', closed_at = now() where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'P0001', null, 'stage: lead → won is not a move');
select lives_ok($$ update crm.opportunity set stage = 'qualifying' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'stage: lead → qualifying');
-- qualifying → lead, and back
select lives_ok($$ update crm.opportunity set stage = 'lead' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'stage: qualifying → lead (a case may step back)');
select lives_ok($$ update crm.opportunity set stage = 'qualifying' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'stage: lead → qualifying again');
-- qualifying → qualified needs the institution, which is §3.6.5, not the stage table
select lives_ok($$
  insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, confirmation, confirmed_by_operator_id)
    values ('20000000-0000-4000-8000-0000000000b2', '20000000-0000-4000-8000-0000000000a1', 'requesting_institution', current_date, 'confirmed', '20000000-0000-4000-8000-000000000001');
  update crm.opportunity set organization_id = '20000000-0000-4000-8000-0000000000a1' where id = '20000000-0000-4000-8000-0000000000b2';
  update crm.opportunity set stage = 'qualified' where id = '20000000-0000-4000-8000-0000000000b2';
  set constraints all immediate;
  set constraints all deferred;
$$, 'stage: qualifying → qualified, with a human-confirmed requesting institution');
select lives_ok($$ update crm.opportunity set stage = 'quoting' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'stage: qualified → quoting');
select lives_ok($$ update crm.opportunity set stage = 'negotiating' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'stage: quoting → negotiating');
select lives_ok($$ update crm.opportunity set stage = 'quoting' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'stage: negotiating → quoting (a case may step back)');
select throws_ok($$ update crm.opportunity set stage = 'lead' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'P0001', null, 'stage: quoting → lead is not a move — stepping back is one step');
select lives_ok($$ update crm.opportunity set stage = 'qualified' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'stage: quoting → qualified (a case may step back)');
select lives_ok($$ update crm.opportunity set stage = 'qualifying' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'stage: qualified → qualifying');

-- Terminal, from anywhere, and never revived.
select lives_ok($$ update crm.opportunity set stage = 'lost', closed_at = now(), close_reason = 'compraron a otro' where id = '20000000-0000-4000-8000-0000000000b3' $$,
  'stage: lead → lost, with a motive');
select throws_ok($$ update crm.opportunity set stage = 'lead', closed_at = null, close_reason = null where id = '20000000-0000-4000-8000-0000000000b3' $$,
  'P0001', null, 'stage: a lost case is never revived');
select throws_ok($$ update crm.opportunity set stage = 'negotiating', closed_at = null, close_reason = null where id = '20000000-0000-4000-8000-0000000000b3' $$,
  'P0001', null, 'stage: a lost case is not reopened at a later stage either');
select throws_ok($$ update crm.opportunity set stage = 'abandoned' where id = '20000000-0000-4000-8000-0000000000b3' $$,
  'P0001', null, 'stage: one terminal stage does not become another');
select throws_ok($$ delete from crm.opportunity where id = '20000000-0000-4000-8000-0000000000b3' $$,
  'P0001', null, 'stage: a case is never deleted');

-- An update that changes something else is not a transition and is not refused as one.
select lives_ok($$ update crm.opportunity set title = 'Caso de las etapas, renombrado' where id = '20000000-0000-4000-8000-0000000000b2' $$,
  'stage: staying put is not a transition');

-- ── 3. `qualified` means an operator decided who is asking ─────────────────────────────────
--
-- Not a fourth guard. Three shipped rules compose into it, and this proves the composition
-- rather than restating it: the organization is required from `qualified`; `organization_id`
-- IS the current requesting institution (deferred trigger, both directions); and a
-- `requesting_institution` row must be `confirmed` with a named operator.

select throws_ok($$
  update crm.opportunity set stage = 'qualifying' where id = '20000000-0000-4000-8000-0000000000b4';
  update crm.opportunity set stage = 'qualified' where id = '20000000-0000-4000-8000-0000000000b4';
$$, '23514', null, 'qualified: refused without an organization');
select throws_ok($$
  update crm.opportunity set organization_id = '20000000-0000-4000-8000-0000000000a1' where id = '20000000-0000-4000-8000-0000000000b4';
  update crm.opportunity set stage = 'qualifying' where id = '20000000-0000-4000-8000-0000000000b4';
  update crm.opportunity set stage = 'qualified' where id = '20000000-0000-4000-8000-0000000000b4';
  set constraints all immediate;
$$, 'P0001', null, 'qualified: an organization without the matching requesting-institution row is refused');
select throws_ok($$
  insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, confirmation)
    values ('20000000-0000-4000-8000-0000000000b4', '20000000-0000-4000-8000-0000000000a1', 'requesting_institution', current_date, 'machine_proposed');
$$, '23514', null, 'qualified: the requesting-institution row a machine would write is unrepresentable');

-- ── 4. The two exits stay reachable ────────────────────────────────────────────────────────
--
-- The defect 20260922200000 corrected. `opportunity_organization_required_from_qualified` read
-- `stage IN ('lead','qualifying')`, which caught `lost` and `abandoned` too — and those are
-- reachable from `lead` itself. A case that never found out who was asking could be opened and
-- then never closed: the only reachable states were `lead` and `qualifying`, forever.

select lives_ok($$ update crm.opportunity set stage = 'abandoned', closed_at = now(), close_reason = 'nunca respondieron' where id = '20000000-0000-4000-8000-0000000000b4' $$,
  'exit: a case with no institution at all can be abandoned');
select is(
  (select organization_id from crm.opportunity where id = '20000000-0000-4000-8000-0000000000b4'),
  null::uuid, 'exit: it is abandoned without inventing an institution for it');
select matches(
  (select pg_get_constraintdef(oid) from pg_constraint where conname = 'opportunity_organization_required_from_qualified'),
  'lost',
  'exit: the corrected rule names the exits explicitly');
select lives_ok($$ insert into crm.opportunity (id, title, stage, owner_operator_id) values ('20000000-0000-4000-8000-0000000000b5', 'Caso que se pierde enseguida', 'lead', '20000000-0000-4000-8000-000000000001');
  update crm.opportunity set stage = 'lost', closed_at = now(), close_reason = 'era spam' where id = '20000000-0000-4000-8000-0000000000b5' $$,
  'exit: a case with no institution at all can be lost');

-- ── 5. Nothing else moved ──────────────────────────────────────────────────────────────────

select is(
  (select count(*)::int from information_schema.tables
    where table_schema in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and table_type = 'BASE TABLE'),
  36, 'the commands migration adds no table: the inventory is still 36');
select is(
  (select count(*)::int from pg_trigger t join pg_class c on c.oid = t.tgrelid
     join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'outbound' and not t.tgisinternal
      and t.tgname like '%case%'),
  0, 'no case trigger reaches outbound.*');
select is(
  (select count(*)::int from crm.opportunity_organization where confirmation = 'machine_proposed' and role <> 'mentioned'),
  0, 'the machine still proposes only `mentioned`');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'crm' and p.proname = 'opportunity_stage_guard' and p.prosecdef),
  0, 'the stage guard is SECURITY INVOKER, like every other function in these schemas');
select is(
  (select count(*)::int from information_schema.role_table_grants
    where grantee in ('origenlab_api', 'origenlab_worker') and privilege_type = 'DELETE'
      and table_schema = 'crm' and table_name like 'opportunity%'),
  0, 'no runtime role may delete a case, an institution on it, an interest or an evidence link');

select * from finish();
rollback;
