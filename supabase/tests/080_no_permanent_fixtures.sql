-- Slice 0 — runs last: every fixture of the preceding files was rolled back. No probe object, no
-- SECURITY DEFINER function, no extra membership and no extra policy survives.
begin;
create extension if not exists pgtap with schema extensions;
select plan(7);

select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform') and p.prosecdef),
  0, 'no SECURITY DEFINER function remains');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform') and p.proname like '\_\_%'),
  0, 'no probe function remains');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform') and c.relname like '\_\_%'),
  0, 'no probe relation remains');
select is(
  (select count(*)::int from pg_type t join pg_namespace n on n.oid = t.typnamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform') and t.typname like '\_\_%'),
  0, 'no probe type remains');
select is(
  (select count(*)::int from pg_policies
    where schemaname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')),
  122, 'the policy set is intact (122)');
select is(
  (select count(*)::int from pg_auth_members am
    where am.roleid in ('origenlab_api'::regrole, 'origenlab_worker'::regrole, 'origenlab_migrator'::regrole)
      and (am.set_option or am.inherit_option)),
  0, 'no SET or INHERIT membership in a runtime role or the migrator remains');
select is(
  (select count(*)::int from crm.organization) + (select count(*)::int from outbound.contact_control) + (select count(*)::int from platform.operator),
  0, 'no fixture data remains');

select * from finish();
rollback;
