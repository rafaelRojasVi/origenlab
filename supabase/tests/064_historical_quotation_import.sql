-- Slice 3 — historical quotations, proven in the database.
--
-- Companion to `20260925200000_slice3_historical_quotation_import.sql`: the two number origins
-- and their uniqueness (gate G1), the shape of a historical revision, its immutability and its
-- one rollback (sent → void), and the assertion / event vocabulary it needs.
--
-- Exercised as the owner, so only constraints and triggers speak. SQLSTATEs: 23505 unique,
-- 23514 check, P0001 trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(26);

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('40000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-0000000000f1', 'hq.admin@example.test', 'HQ Admin', 'admin', 'active');
insert into evidence.source_record (id, kind, dedupe_key, payload) values
  ('40000000-0000-4000-8000-0000000000d1', 'gmail_message', 'hq-fixture-1', '{}'::jsonb);
insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('40000000-0000-4000-8000-0000000000b1', 'Caso histórico uno', 'lead', '40000000-0000-4000-8000-000000000001'),
  ('40000000-0000-4000-8000-0000000000b2', 'Caso histórico dos', 'lead', '40000000-0000-4000-8000-000000000001');

-- ── numbers: G1 ─────────────────────────────────────────────────────────────────────────────

select lives_ok($$ insert into crm.quote (id, opportunity_id, quote_number, number_origin) values
  ('40000000-0000-4000-8000-0000000000c1', '40000000-0000-4000-8000-0000000000b1', '01230-26', 'printed_historical') $$,
  'a printed historical number is accepted');
select lives_ok($$ insert into crm.quote (id, opportunity_id, quote_number, number_origin) values
  ('40000000-0000-4000-8000-0000000000c2', '40000000-0000-4000-8000-0000000000b2', '01230-26', 'printed_historical') $$,
  'the same printed number on another case is accepted (G1: unique per case)');
select throws_ok($$ insert into crm.quote (opportunity_id, quote_number, number_origin) values
  ('40000000-0000-4000-8000-0000000000b1', '01230-26', 'printed_historical') $$,
  '23505', null, 'one printed number appears once per case');
select lives_ok($$ insert into crm.quote (opportunity_id, quote_number) values
  ('40000000-0000-4000-8000-0000000000b1', 'M0001') $$, 'a minted number is accepted');
select throws_ok($$ insert into crm.quote (opportunity_id, quote_number) values
  ('40000000-0000-4000-8000-0000000000b2', 'M0001') $$,
  '23505', null, 'a minted number stays globally unique');
select throws_ok($$ insert into crm.quote (opportunity_id, quote_number, number_origin) values
  ('40000000-0000-4000-8000-0000000000b2', 'M0002', 'guessed') $$,
  '23514', null, 'number_origin is a closed vocabulary');

-- ── a historical revision's shape ───────────────────────────────────────────────────────────

select throws_ok($$ insert into crm.quote_revision (quote_id, revision_no, status, origin, pdf_sha256, sent_at) values
  ('40000000-0000-4000-8000-0000000000c1', 1, 'sent', 'historical_import', repeat('a', 64), now()) $$,
  '23514', null, 'a historical revision needs its evidence record');
select throws_ok($$ insert into crm.quote_revision (quote_id, revision_no, status, origin, origin_source_record_id, sent_at) values
  ('40000000-0000-4000-8000-0000000000c1', 1, 'sent', 'historical_import', '40000000-0000-4000-8000-0000000000d1', now()) $$,
  '23514', null, 'a historical revision needs its document hash');
select throws_ok($$ insert into crm.quote_revision (quote_id, revision_no, status, origin, origin_source_record_id, pdf_sha256, sent_at) values
  ('40000000-0000-4000-8000-0000000000c1', 1, 'draft', 'historical_import', '40000000-0000-4000-8000-0000000000d1', repeat('a', 64), now()) $$,
  '23514', null, 'a historical revision is born sent');
select lives_ok($$ insert into crm.quote_revision (id, quote_id, revision_no, status, origin, origin_source_record_id, pdf_sha256, sent_at) values
  ('40000000-0000-4000-8000-0000000000e1', '40000000-0000-4000-8000-0000000000c1', 1, 'sent', 'historical_import',
   '40000000-0000-4000-8000-0000000000d1', repeat('a', 64), now()) $$,
  'a historical revision needs no totals, approval, snapshot or currency');
select throws_ok($$ insert into crm.quote_revision (quote_id, revision_no, status, origin, origin_source_record_id, pdf_sha256, sent_at) values
  ('40000000-0000-4000-8000-0000000000c1', 2, 'sent', 'historical_import', '40000000-0000-4000-8000-0000000000d1', repeat('a', 64), now()) $$,
  '23505', null, 'one document is one revision of a quote');
select throws_ok($$ insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals) values
  ('40000000-0000-4000-8000-0000000000c2', 1, 'draft', null, 0) $$,
  '23514', null, 'an authored revision still needs its currency');

-- ── immutability and the one rollback ───────────────────────────────────────────────────────

select throws_ok($$ update crm.quote_revision set pdf_sha256 = repeat('b', 64) where id = '40000000-0000-4000-8000-0000000000e1' $$,
  'P0001', null, 'a historical revision records a document and is immutable');
select throws_ok($$ delete from crm.quote_revision where id = '40000000-0000-4000-8000-0000000000e1' $$,
  'P0001', null, 'a historical revision is never deleted');
select lives_ok($$ update crm.quote_revision set status = 'void' where id = '40000000-0000-4000-8000-0000000000e1' $$,
  'sent → void is the append-only rollback');
select throws_ok($$ update crm.quote_revision set status = 'sent' where id = '40000000-0000-4000-8000-0000000000e1' $$,
  'P0001', null, 'a void revision is never revived');

-- ── the guard function itself: a declared post-Slice-0 delta ────────────────────────────────
--
-- The frozen Slice 0 audit baseline counts three functions; this migration is the one that makes
-- current head carry ten. The audit's declared-gap check (supabase/audit/
-- assert_declared_local_head_gap.py) accepts that count only because these proofs pin what the
-- tenth function is: invoker, pg_catalog-only search path, no EXECUTE for any Data-API-facing
-- role, and bound to exactly one row trigger.

select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'crm' and p.proname = 'quote_revision_historical_guard'
      and p.pronargs = 0 and p.prorettype = 'trigger'::regtype),
  1, 'crm.quote_revision_historical_guard() exists once, takes no argument and returns trigger');
select is(
  (select p.prosecdef from pg_proc p where p.oid = 'crm.quote_revision_historical_guard()'::regprocedure),
  false, 'the historical-revision guard is SECURITY INVOKER');
select is(
  (select array_to_string(p.proconfig, '|') from pg_proc p where p.oid = 'crm.quote_revision_historical_guard()'::regprocedure),
  'search_path=pg_catalog', 'the historical-revision guard pins search_path = pg_catalog and nothing else');
select is(
  (select pg_get_userbyid(p.proowner) from pg_proc p where p.oid = 'crm.quote_revision_historical_guard()'::regprocedure),
  'origenlab_owner', 'the historical-revision guard is owned by origenlab_owner');
select is(
  (select count(*)::int
     from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
    where p.oid = 'crm.quote_revision_historical_guard()'::regprocedure
      and (a.grantee = 0 or a.grantee::regrole::text in ('anon', 'authenticated', 'service_role', 'authenticator'))),
  0, 'PUBLIC, anon, authenticated, service_role and authenticator hold no EXECUTE grant on the guard');
select is(
  (select count(*)::int from unnest(array['anon', 'authenticated', 'service_role', 'authenticator']) r(rolname)
    where has_function_privilege(r.rolname, 'crm.quote_revision_historical_guard()'::regprocedure, 'EXECUTE')),
  0, 'no Data-API-facing role effectively holds EXECUTE on the guard');
select results_eq(
  $$ select c.oid::regclass::text collate "default", t.tgname::text collate "default",
            t.tgenabled::text collate "default",
            (t.tgtype & 1) <> 0,              -- ROW
            (t.tgtype & 2) <> 0,              -- BEFORE
            (t.tgtype & 4) <> 0,              -- INSERT
            (t.tgtype & 8) <> 0,              -- DELETE
            (t.tgtype & 16) <> 0,             -- UPDATE
            (t.tgtype & 32) <> 0              -- TRUNCATE
       from pg_trigger t join pg_class c on c.oid = t.tgrelid
      where t.tgfoid = 'crm.quote_revision_historical_guard()'::regprocedure and not t.tgisinternal $$,
  $$ values ('crm.quote_revision', 'quote_revision_historical_guard', 'O', true, true, false, true, true, false) $$,
  'the guard fires from exactly one enabled BEFORE UPDATE OR DELETE row trigger on crm.quote_revision');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')),
  33, 'the application schemas hold thirty-three functions: three Slice 0 helpers plus thirty declared later (065''s campaign-content guard, 066''s two audience-freeze guards, 068''s two campaign-planning guards, 069''s three W10 unsubscribe functions, the three campaign-block functions, 072''s archived-campaign guard, 073''s three sign-in version guards, 074''s session guard, 073''s audit-actor guard, 075''s HMAC helper and two PIN-attempt definers, 073''s campaign-content-archive guard and note guard from slice 6, and 091''s document-line review guard from slice 7)');

-- ── vocabulary ──────────────────────────────────────────────────────────────────────────────

select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('quote_revision', '40000000-0000-4000-8000-0000000000e1', 1, 'quote_revision.historical_recorded', 1, '{}'::jsonb, 'operator',
          '40000000-0000-4000-8000-000000000001') $$,
  'quote_revision.historical_recorded is an event type');
select lives_ok($$ insert into evidence.assertion (source_record_id, kind, value_norm, resolution, resolved_kind, resolved_id, resolved_at)
  values ('40000000-0000-4000-8000-0000000000d1', 'document_reference', 'sha256:' || repeat('a', 64), 'promoted', 'quote_revision',
          '40000000-0000-4000-8000-0000000000e1', now()) $$,
  'an assertion may resolve to a quote revision');

select * from finish();
rollback;
