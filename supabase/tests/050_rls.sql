-- Slice 0 — row level security (docs/ARCHITECTURE.md §6.1 points 1, 2, 5). RLS is enabled and
-- not forced on all 32 tables; the policy set is exactly one named role gate per granted
-- (table, role, verb); and RLS is demonstrably live: with its policy removed, a table becomes
-- unreachable for the runtime role — deny-by-default — without any error.
begin;
create extension if not exists pgtap with schema extensions;
select plan(15);

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
  return sqlstate || ': ' || sqlerrm;
end
$$;
create function pg_temp.query_as(p_role text, p_sql text) returns text
language plpgsql as $$
declare v text;
begin
  execute format('set role %I', p_role);
  execute p_sql into v;
  reset role;
  return v;
exception when others then
  return sqlstate || ': ' || sqlerrm;
end
$$;

create temp table expected_policies (schema_name text, table_name text, role_name text, cmd text);
insert into expected_policies values
    ('crm', 'organization', 'origenlab_api', 'SELECT'),
    ('crm', 'organization', 'origenlab_api', 'INSERT'),
    ('crm', 'organization', 'origenlab_api', 'UPDATE'),
    ('crm', 'organization', 'origenlab_worker', 'SELECT'),
    ('crm', 'organization_domain', 'origenlab_api', 'SELECT'),
    ('crm', 'organization_domain', 'origenlab_api', 'INSERT'),
    ('crm', 'organization_domain', 'origenlab_api', 'UPDATE'),
    ('crm', 'organization_domain', 'origenlab_worker', 'SELECT'),
    ('crm', 'organization_relationship', 'origenlab_api', 'SELECT'),
    ('crm', 'organization_relationship', 'origenlab_api', 'INSERT'),
    ('crm', 'organization_relationship', 'origenlab_api', 'UPDATE'),
    ('crm', 'organization_relationship', 'origenlab_worker', 'SELECT'),
    ('crm', 'external_identifier', 'origenlab_api', 'SELECT'),
    ('crm', 'external_identifier', 'origenlab_api', 'INSERT'),
    ('crm', 'external_identifier', 'origenlab_api', 'UPDATE'),
    ('crm', 'external_identifier', 'origenlab_worker', 'SELECT'),
    ('crm', 'person', 'origenlab_api', 'SELECT'),
    ('crm', 'person', 'origenlab_api', 'INSERT'),
    ('crm', 'person', 'origenlab_api', 'UPDATE'),
    ('crm', 'person', 'origenlab_worker', 'SELECT'),
    ('crm', 'affiliation', 'origenlab_api', 'SELECT'),
    ('crm', 'affiliation', 'origenlab_api', 'INSERT'),
    ('crm', 'affiliation', 'origenlab_api', 'UPDATE'),
    ('crm', 'affiliation', 'origenlab_worker', 'SELECT'),
    ('crm', 'contact_point', 'origenlab_api', 'SELECT'),
    ('crm', 'contact_point', 'origenlab_api', 'INSERT'),
    ('crm', 'contact_point', 'origenlab_api', 'UPDATE'),
    ('crm', 'contact_point', 'origenlab_worker', 'SELECT'),
    ('crm', 'address', 'origenlab_api', 'SELECT'),
    ('crm', 'address', 'origenlab_api', 'INSERT'),
    ('crm', 'address', 'origenlab_api', 'UPDATE'),
    ('crm', 'address', 'origenlab_worker', 'SELECT'),
    ('crm', 'opportunity', 'origenlab_api', 'SELECT'),
    ('crm', 'opportunity', 'origenlab_api', 'INSERT'),
    ('crm', 'opportunity', 'origenlab_api', 'UPDATE'),
    ('crm', 'opportunity', 'origenlab_worker', 'SELECT'),
    ('crm', 'opportunity_participant', 'origenlab_api', 'SELECT'),
    ('crm', 'opportunity_participant', 'origenlab_api', 'INSERT'),
    ('crm', 'opportunity_participant', 'origenlab_api', 'UPDATE'),
    ('crm', 'opportunity_participant', 'origenlab_worker', 'SELECT'),
    ('crm', 'task', 'origenlab_api', 'SELECT'),
    ('crm', 'task', 'origenlab_api', 'INSERT'),
    ('crm', 'task', 'origenlab_api', 'UPDATE'),
    ('crm', 'task', 'origenlab_worker', 'SELECT'),
    ('crm', 'activity', 'origenlab_api', 'SELECT'),
    ('crm', 'activity', 'origenlab_api', 'INSERT'),
    ('crm', 'activity', 'origenlab_worker', 'SELECT'),
    ('crm', 'domain_event', 'origenlab_api', 'SELECT'),
    ('crm', 'domain_event', 'origenlab_api', 'INSERT'),
    ('crm', 'domain_event', 'origenlab_worker', 'SELECT'),
    ('crm', 'quote', 'origenlab_api', 'SELECT'),
    ('crm', 'quote', 'origenlab_api', 'INSERT'),
    ('crm', 'quote', 'origenlab_api', 'UPDATE'),
    ('crm', 'quote', 'origenlab_worker', 'SELECT'),
    ('crm', 'quote_revision', 'origenlab_api', 'SELECT'),
    ('crm', 'quote_revision', 'origenlab_api', 'INSERT'),
    ('crm', 'quote_revision', 'origenlab_api', 'UPDATE'),
    ('crm', 'quote_revision', 'origenlab_worker', 'SELECT'),
    ('crm', 'quote_line', 'origenlab_api', 'SELECT'),
    ('crm', 'quote_line', 'origenlab_api', 'INSERT'),
    ('crm', 'quote_line', 'origenlab_api', 'UPDATE'),
    ('crm', 'quote_line', 'origenlab_worker', 'SELECT'),
    ('comms', 'mailbox', 'origenlab_api', 'SELECT'),
    ('comms', 'mailbox', 'origenlab_worker', 'SELECT'),
    ('comms', 'mailbox', 'origenlab_worker', 'INSERT'),
    ('comms', 'mailbox', 'origenlab_worker', 'UPDATE'),
    ('comms', 'message', 'origenlab_api', 'SELECT'),
    ('comms', 'message', 'origenlab_worker', 'SELECT'),
    ('comms', 'message', 'origenlab_worker', 'INSERT'),
    ('comms', 'message', 'origenlab_worker', 'UPDATE'),
    ('comms', 'message_participant', 'origenlab_api', 'SELECT'),
    ('comms', 'message_participant', 'origenlab_api', 'UPDATE'),
    ('comms', 'message_participant', 'origenlab_worker', 'SELECT'),
    ('comms', 'message_participant', 'origenlab_worker', 'INSERT'),
    ('comms', 'attachment', 'origenlab_api', 'SELECT'),
    ('comms', 'attachment', 'origenlab_worker', 'SELECT'),
    ('comms', 'attachment', 'origenlab_worker', 'INSERT'),
    ('comms', 'attachment', 'origenlab_worker', 'UPDATE'),
    ('outbound', 'send_control', 'origenlab_api', 'SELECT'),
    ('outbound', 'send_control', 'origenlab_worker', 'SELECT'),
    ('outbound', 'campaign', 'origenlab_api', 'SELECT'),
    ('outbound', 'campaign', 'origenlab_api', 'INSERT'),
    ('outbound', 'campaign', 'origenlab_api', 'UPDATE'),
    ('outbound', 'campaign', 'origenlab_worker', 'SELECT'),
    ('outbound', 'campaign_recipient', 'origenlab_api', 'SELECT'),
    ('outbound', 'campaign_recipient', 'origenlab_api', 'INSERT'),
    ('outbound', 'campaign_recipient', 'origenlab_api', 'UPDATE'),
    ('outbound', 'campaign_recipient', 'origenlab_worker', 'SELECT'),
    ('outbound', 'send_attempt', 'origenlab_api', 'SELECT'),
    ('outbound', 'send_attempt', 'origenlab_worker', 'SELECT'),
    ('outbound', 'contact_control', 'origenlab_api', 'SELECT'),
    ('outbound', 'contact_control', 'origenlab_worker', 'SELECT'),
    ('evidence', 'source_record', 'origenlab_api', 'SELECT'),
    ('evidence', 'source_record', 'origenlab_api', 'UPDATE'),
    ('evidence', 'source_record', 'origenlab_worker', 'SELECT'),
    ('evidence', 'source_record', 'origenlab_worker', 'INSERT'),
    ('evidence', 'source_record', 'origenlab_worker', 'UPDATE'),
    ('evidence', 'assertion', 'origenlab_api', 'SELECT'),
    ('evidence', 'assertion', 'origenlab_api', 'UPDATE'),
    ('evidence', 'assertion', 'origenlab_worker', 'SELECT'),
    ('evidence', 'assertion', 'origenlab_worker', 'INSERT'),
    ('catalog', 'product', 'origenlab_api', 'SELECT'),
    ('catalog', 'product', 'origenlab_api', 'INSERT'),
    ('catalog', 'product', 'origenlab_api', 'UPDATE'),
    ('catalog', 'product', 'origenlab_worker', 'SELECT'),
    ('catalog', 'supplier_product', 'origenlab_api', 'SELECT'),
    ('catalog', 'supplier_product', 'origenlab_api', 'INSERT'),
    ('catalog', 'supplier_product', 'origenlab_worker', 'SELECT'),
    ('catalog', 'supplier_product', 'origenlab_worker', 'INSERT'),
    ('procurement', 'notice', 'origenlab_api', 'SELECT'),
    ('procurement', 'notice', 'origenlab_api', 'UPDATE'),
    ('procurement', 'notice', 'origenlab_worker', 'SELECT'),
    ('procurement', 'notice', 'origenlab_worker', 'INSERT'),
    ('procurement', 'notice', 'origenlab_worker', 'UPDATE'),
    ('platform', 'operator', 'origenlab_api', 'SELECT'),
    ('platform', 'operator', 'origenlab_api', 'INSERT'),
    ('platform', 'operator', 'origenlab_api', 'UPDATE'),
    ('platform', 'operator', 'origenlab_worker', 'SELECT'),
    ('platform', 'command_receipt', 'origenlab_api', 'SELECT'),
    ('platform', 'command_receipt', 'origenlab_api', 'INSERT'),
    ('platform', 'command_receipt', 'origenlab_api', 'UPDATE'),
    ('platform', 'command_receipt', 'origenlab_worker', 'SELECT');

select is((select count(*)::int from expected_policies), 122, 'the matrix implies 122 policies');

-- Posture.
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and not c.relrowsecurity),
  0, 'RLS is enabled on every application table');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and c.relforcerowsecurity),
  0, 'RLS is not forced: origenlab_owner crosses it by ownership, the only application-owned exemption');

-- Exactly one named policy per granted (table, role, verb); nothing for any other role.
select set_eq(
  $$ select schemaname::text collate "default", tablename::text collate "default", unnest(roles)::text collate "default", cmd::text collate "default" from pg_policies
      where schemaname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform') $$,
  $$ select schema_name, table_name, role_name, cmd from expected_policies $$,
  'the policy set is exactly one policy per granted (table, role, verb) and nothing else');
select is(
  (select count(*)::int from pg_policies p
    where schemaname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and p.policyname <> p.roles[1] || '_' || lower(p.cmd)),
  0, 'every policy is named <role>_<verb>');
select is(
  (select count(*)::int from pg_policies p
    where schemaname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and (p.permissive <> 'PERMISSIVE' or array_length(p.roles, 1) <> 1)),
  0, 'every policy is permissive and names exactly one role');
select is(
  (select count(*)::int from pg_policies p
    where schemaname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and ((p.cmd in ('SELECT', 'UPDATE', 'DELETE') and p.qual is distinct from 'true')
        or (p.cmd in ('INSERT', 'UPDATE') and p.with_check is distinct from 'true'))),
  0, 'every policy is a pure role gate: USING (true) / WITH CHECK (true)');
select is(
  (select count(*)::int from pg_policies p
    where schemaname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and not (p.roles <@ array['origenlab_api', 'origenlab_worker']::name[])),
  0, 'no policy names PUBLIC, anon, authenticated, service_role or any other role');

-- RLS is live. Seed one organization as the owner, then remove the api SELECT policy (fixture,
-- rolled back) and show the table goes dark for api without an error.
select is(left(pg_temp.run_as('origenlab_owner', $$insert into crm.organization (id, kind, name, confirmation)
  values ('00000000-0000-4000-8000-00000000c001', 'company', 'RLS probe', 'confirmed')$$), 2), 'ok', 'fixture: one organization row');
select is(pg_temp.query_as('origenlab_api', 'select count(*)::text from crm.organization'), '1', 'api sees the row through origenlab_api_select');
select is(left(pg_temp.run_as('origenlab_owner', 'drop policy origenlab_api_select on crm.organization'), 2), 'ok', 'fixture: remove the api SELECT policy (rolled back)');
select is(pg_temp.query_as('origenlab_api', 'select count(*)::text from crm.organization'), '0', 'without a policy the table is unreachable for api: deny-by-default, no error');
select is(pg_temp.query_as('origenlab_worker', 'select count(*)::text from crm.organization'), '1', 'the worker policy is unaffected');

-- A table added later is deny-by-default until a policy is added deliberately.
select is(left(pg_temp.run_as('origenlab_owner', $$
  create table crm.__future_rls (id int primary key);
  alter table crm.__future_rls enable row level security;
  grant select on crm.__future_rls to origenlab_api;
  insert into crm.__future_rls values (1);
$$), 2), 'ok', 'fixture: a future table with RLS enabled, SELECT granted, one row, no policy');
select is(pg_temp.query_as('origenlab_api', 'select count(*)::text from crm.__future_rls'), '0', 'a granted table with no policy yields no rows to the runtime role');

select * from finish();
rollback;
