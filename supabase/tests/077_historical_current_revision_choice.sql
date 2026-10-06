-- Slice 7 — choosing the current historical revision, proven in the database.
--
-- Companion to `20261006120000_slice7_historical_current_revision_choice.sql`: a historical
-- revision may be superseded by a lower-numbered revision of its quote, only by a current one,
-- never by itself, and a supersession chain cannot close into a cycle. A V2-authored revision
-- keeps the forward rule.
--
-- Exercised as the owner, so only constraints and triggers speak; the runtime role's grant for the
-- write is asserted, and exercised end to end by apps/api's database tests. SQLSTATEs: 23514
-- check, P0001 trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(10);

-- ── fixtures ───────────────────────────────────────────────────────────────────────────────

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('77000000-0000-4000-8000-000000000001', gen_random_uuid(), 'revision.choice@example.test', 'Revision Choice', 'sales', 'active');
insert into evidence.source_record (id, kind, dedupe_key, payload) values
  ('77000000-0000-4000-8000-0000000000d1', 'gmail_message', 'cr-fixture-1', '{}'::jsonb);
insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('77000000-0000-4000-8000-0000000000b1', 'Caso con revisiones', 'lead', '77000000-0000-4000-8000-000000000001');
insert into crm.quote (id, opportunity_id, quote_number, number_origin) values
  ('77000000-0000-4000-8000-0000000000c1', '77000000-0000-4000-8000-0000000000b1', '07701-26', 'printed_historical'),
  ('77000000-0000-4000-8000-0000000000c2', '77000000-0000-4000-8000-0000000000b1', '07702-26', 'printed_historical');
-- Quote c1: three current historical revisions; quote c2: one historical, one void.
insert into crm.quote_revision (id, quote_id, revision_no, status, origin, origin_source_record_id, pdf_sha256, sent_at) values
  ('77000000-0000-4000-8000-0000000000e1', '77000000-0000-4000-8000-0000000000c1', 1, 'sent', 'historical_import',
   '77000000-0000-4000-8000-0000000000d1', repeat('1', 64), now()),
  ('77000000-0000-4000-8000-0000000000e2', '77000000-0000-4000-8000-0000000000c1', 2, 'sent', 'historical_import',
   '77000000-0000-4000-8000-0000000000d1', repeat('2', 64), now()),
  ('77000000-0000-4000-8000-0000000000e3', '77000000-0000-4000-8000-0000000000c1', 3, 'sent', 'historical_import',
   '77000000-0000-4000-8000-0000000000d1', repeat('3', 64), now()),
  ('77000000-0000-4000-8000-0000000000f1', '77000000-0000-4000-8000-0000000000c2', 1, 'sent', 'historical_import',
   '77000000-0000-4000-8000-0000000000d1', repeat('4', 64), now()),
  ('77000000-0000-4000-8000-0000000000f2', '77000000-0000-4000-8000-0000000000c2', 2, 'sent', 'historical_import',
   '77000000-0000-4000-8000-0000000000d1', repeat('5', 64), now());
update crm.quote_revision set status = 'void' where id = '77000000-0000-4000-8000-0000000000f2';

-- ── the relaxed rule ───────────────────────────────────────────────────────────────────────

select throws_ok($$ update crm.quote_revision set superseded_by_revision_no = 2, superseded_at = now()
  where id = '77000000-0000-4000-8000-0000000000e2' $$,
  '23514', null, 'a historical revision is never superseded by itself');
select lives_ok($$ update crm.quote_revision set superseded_by_revision_no = 1, superseded_at = now()
  where id = '77000000-0000-4000-8000-0000000000e3' $$,
  'a historical revision may be superseded by a lower-numbered revision of its quote');
select throws_ok($$ update crm.quote_revision set superseded_by_revision_no = 3, superseded_at = now()
  where id = '77000000-0000-4000-8000-0000000000e2' $$,
  'P0001', null, 'a superseded revision supersedes nothing');
select throws_ok($$ update crm.quote_revision set superseded_by_revision_no = 3, superseded_at = now()
  where id = '77000000-0000-4000-8000-0000000000e1' $$,
  'P0001', null, 'no cycle: the revision that supersedes 3 cannot be superseded by 3');
select throws_ok($$ update crm.quote_revision set superseded_by_revision_no = 2, superseded_at = now()
  where id = '77000000-0000-4000-8000-0000000000f1' $$,
  'P0001', null, 'a void revision supersedes nothing');

-- ── authored revisions keep the forward rule ──────────────────────────────────────────────

select is(
  (select pg_get_constraintdef(oid) from pg_constraint
    where conrelid = 'crm.quote_revision'::regclass and conname = 'quote_revision_supersession_forward'),
  'CHECK (((superseded_by_revision_no IS NULL) OR (superseded_by_revision_no > revision_no) OR ((origin = ''historical_import''::text) AND (superseded_by_revision_no <> revision_no))))',
  'only a historical_import revision is exempt from the forward rule');
select is(
  (select pg_get_userbyid(p.proowner) || ' ' || p.prosecdef::text || ' ' || array_to_string(p.proconfig, '|')
     from pg_proc p where p.oid = 'crm.quote_revision_historical_guard()'::regprocedure),
  'origenlab_owner false search_path=pg_catalog',
  'the replaced guard keeps its owner, SECURITY INVOKER and pinned search_path');

-- ── the runtime role's write ─────────────────────────────────────────────────────────────

select ok(
  has_column_privilege('origenlab_api', 'crm.quote_revision', 'superseded_by_revision_no', 'UPDATE')
  and has_column_privilege('origenlab_api', 'crm.quote_revision', 'superseded_at', 'UPDATE'),
  'origenlab_api may record the supersession the operator''s choice writes');
select lives_ok($$ update crm.quote_revision set superseded_by_revision_no = 1, superseded_at = now(),
    version = version + 1, updated_at = now()
  where id = '77000000-0000-4000-8000-0000000000e2' $$,
  'the chosen revision supersedes the other current one');
select is(
  (select count(*)::int from crm.quote_revision
    where quote_id = '77000000-0000-4000-8000-0000000000c1'
      and status <> 'void' and superseded_by_revision_no is null),
  1, 'the quote is left with exactly one current revision');

select * from finish();
rollback;
