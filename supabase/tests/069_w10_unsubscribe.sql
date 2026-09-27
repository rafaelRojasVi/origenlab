-- Slice 5 — W10 unsubscribe, proven in the database.
--
-- Companion to `20260927230000_slice5_w10_unsubscribe.sql`:
--   * `outbound.add_contact_control` is the closed-list SECURITY DEFINER writer (ARCHITECTURE.md
--     §6.2): owned by origenlab_owner, pinned search_path, EXECUTE for origenlab_api only, and it
--     refuses any login but origenlab_api (session_user, not current_user). Its successful path
--     needs that real login and is exercised by apps/api's disposable-database tests; here pgTAP
--     runs as one login, so every call is refused — which is itself the proof of the assertion.
--   * no runtime role can write contact_control or unsubscribe evidence directly;
--   * an unsubscribe block, and any block an unsubscribe request is linked to, is never updated
--     or deleted, and its evidence is immutable;
--   * `outbound.marketing_contact_refusals` reads the live controls, so a recipient frozen before
--     a «BAJA» is refused, and a W12 approval never lifts it.
--
-- SQLSTATEs: 23514 check, P0001 trigger guard, 42501 privilege / login assertion.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
select plan(52);

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
create function pg_temp.query_as(p_role text, p_sql text) returns text
language plpgsql as $$
declare v text;
begin
  execute format('set role %I', p_role);
  execute p_sql into v;
  reset role;
  return v;
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$$;
grant execute on function pg_temp.run_as(text, text) to public;
grant execute on function pg_temp.query_as(text, text) to public;

-- ── catalogue: the privileged writer ─────────────────────────────────────────────────────────
select has_function('outbound', 'add_contact_control',
  array['text', 'text', 'text', 'text', 'uuid', 'uuid', 'jsonb'], 'outbound.add_contact_control exists');
select is((select prosecdef from pg_proc where oid = 'outbound.add_contact_control(text,text,text,text,uuid,uuid,jsonb)'::regprocedure),
  true, 'add_contact_control is SECURITY DEFINER (the closed list, ARCHITECTURE.md §6.2)');
select is((select proowner::regrole::text from pg_proc where oid = 'outbound.add_contact_control(text,text,text,text,uuid,uuid,jsonb)'::regprocedure),
  'origenlab_owner', 'add_contact_control is owned by the NOLOGIN owner');
select is((select proconfig from pg_proc where oid = 'outbound.add_contact_control(text,text,text,text,uuid,uuid,jsonb)'::regprocedure),
  array['search_path=pg_catalog'], 'add_contact_control pins search_path = pg_catalog');
select ok(has_function_privilege('origenlab_api', 'outbound.add_contact_control(text,text,text,text,uuid,uuid,jsonb)', 'EXECUTE'),
  'origenlab_api may EXECUTE add_contact_control');
select is(
  (select count(*)::int from (values ('origenlab_worker'), ('origenlab_migrator'), ('anon'), ('authenticated'), ('service_role')) r(n)
    where has_function_privilege(r.n, 'outbound.add_contact_control(text,text,text,text,uuid,uuid,jsonb)', 'EXECUTE')),
  0, 'the worker, the migrator, anon, authenticated and service_role may not EXECUTE add_contact_control');
select is(
  (select count(*)::int from pg_proc p, aclexplode(p.proacl) a
    where p.oid = 'outbound.add_contact_control(text,text,text,text,uuid,uuid,jsonb)'::regprocedure and a.grantee = 0),
  0, 'PUBLIC holds no EXECUTE on add_contact_control');

-- ── catalogue: the send-time contract and the guard ──────────────────────────────────────────
select is((select prosecdef from pg_proc where oid = 'outbound.marketing_contact_refusals(uuid)'::regprocedure),
  false, 'marketing_contact_refusals is SECURITY INVOKER');
select is((select provolatile::text from pg_proc where oid = 'outbound.marketing_contact_refusals(uuid)'::regprocedure),
  's', 'marketing_contact_refusals is STABLE (a read)');
select ok(has_function_privilege('origenlab_api', 'outbound.marketing_contact_refusals(uuid)', 'EXECUTE')
      and has_function_privilege('origenlab_worker', 'outbound.marketing_contact_refusals(uuid)', 'EXECUTE'),
  'both runtime roles may evaluate the send-time contract');
select is(
  (select count(*)::int from (values ('anon'), ('authenticated'), ('service_role')) r(n)
    where has_function_privilege(r.n, 'outbound.marketing_contact_refusals(uuid)', 'EXECUTE')
       or has_function_privilege(r.n, 'outbound.unsubscribe_permanent()', 'EXECUTE')),
  0, 'no Data-API-facing role may execute the refusals function or the guard');
select is((select prosecdef from pg_proc where oid = 'outbound.unsubscribe_permanent()'::regprocedure),
  false, 'the permanence guard is SECURITY INVOKER');
select set_eq(
  $$ select c.relnamespace::regnamespace::text || '.' || c.relname || ':' || t.tgname
       from pg_trigger t join pg_class c on c.oid = t.tgrelid
      where t.tgfoid = 'outbound.unsubscribe_permanent()'::regprocedure and not t.tgisinternal and t.tgenabled = 'O' $$,
  array['outbound.contact_control:contact_control_unsubscribe_permanent',
        'evidence.source_record:source_record_unsubscribe_insert',
        'evidence.source_record:source_record_unsubscribe_update',
        'evidence.source_record:source_record_unsubscribe_delete',
        'evidence.assertion:assertion_unsubscribe_insert',
        'evidence.assertion:assertion_unsubscribe_update',
        'evidence.assertion:assertion_unsubscribe_delete'],
  'the guard fires from exactly seven enabled triggers on the three tables');
select has_index('evidence', 'assertion', 'assertion_unsubscribe_request_control_idx',
  'unsubscribe requests are indexed by the control they name');
select is((select pg_get_expr(i.indpred, i.indrelid) from pg_index i
            where i.indexrelid = 'evidence.assertion_unsubscribe_request_control_idx'::regclass),
  '(kind = ''unsubscribe_request''::text)', 'the index is partial on unsubscribe requests');

-- ── grants did not widen ─────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source)
  values ('address', 'direct@lab.example', 'block', 'marketing', 'unsubscribe', 'unsubscribe_handler')$$), 5), '42501',
  'the api cannot write an unsubscribe block directly');
select is(left(pg_temp.run_as('origenlab_api', $$insert into evidence.source_record (kind, dedupe_key, payload)
  values ('gmail_message', 'gmail_unsubscribe:' || repeat('1', 64), '{}')$$), 5), '42501',
  'the api cannot write unsubscribe evidence directly');
select is(left(pg_temp.run_as('origenlab_worker', $$insert into evidence.source_record (kind, dedupe_key, payload)
  values ('gmail_message', 'gmail_unsubscribe:' || repeat('2', 64), '{}')$$), 5), '42501',
  'the worker (which may write evidence) cannot create unsubscribe evidence: the guard refuses it');

-- ── the privileged writer refuses every caller that is not the origenlab_api login ────────────
select is(left(pg_temp.run_as('origenlab_api', $$select outbound.add_contact_control('block', 'marketing', 'x@lab.example', 'unsubscribe',
  gen_random_uuid(), gen_random_uuid(), '{}'::jsonb)$$), 5), '42501',
  'set role origenlab_api from another login is refused: the assertion is on session_user, not current_user');
select is(left(pg_temp.run_as('origenlab_worker', $$select outbound.add_contact_control('block', 'marketing', 'x@lab.example', 'unsubscribe',
  gen_random_uuid(), gen_random_uuid(), '{}'::jsonb)$$), 5), '42501', 'the worker has no EXECUTE');
select is(left(pg_temp.run_as('service_role', $$select outbound.add_contact_control('block', 'marketing', 'x@lab.example', 'unsubscribe',
  gen_random_uuid(), gen_random_uuid(), '{}'::jsonb)$$), 5), '42501', 'service_role has no EXECUTE');
select is(left(pg_temp.run_as('anon', $$select outbound.add_contact_control('block', 'marketing', 'x@lab.example', 'unsubscribe',
  gen_random_uuid(), gen_random_uuid(), '{}'::jsonb)$$), 5), '42501', 'anon has no EXECUTE');
select is(left(pg_temp.run_as('authenticated', $$select outbound.add_contact_control('block', 'marketing', 'x@lab.example', 'unsubscribe',
  gen_random_uuid(), gen_random_uuid(), '{}'::jsonb)$$), 5), '42501', 'authenticated has no EXECUTE');

-- ── fixtures, as the owner (the only role the guard lets create unsubscribe evidence) ────────
set role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('69000000-0000-4000-8000-000000000001', '69000000-0000-4000-8000-0000000000f1', 'w10.sales@example.test', 'W10 Sales', 'sales', 'active');
insert into evidence.source_record (id, kind, dedupe_key, payload, payload_sha256, review_status) values
  ('69000000-0000-4000-8000-000000000300', 'gmail_message', 'gmail_unsubscribe:' || repeat('a', 64), '{"body_text": "BAJA"}', repeat('d', 64), 'promoted'),
  ('69000000-0000-4000-8000-000000000301', 'gmail_message', 'gmail_unsubscribe:' || repeat('b', 64), '{"body_text": "baja."}', repeat('e', 64), 'promoted'),
  ('69000000-0000-4000-8000-000000000302', 'workbook_import', 'plain-record', '{}', null, 'pending');
insert into outbound.contact_control (id, scope, value_norm, kind, purpose, reason, source, created_by_operator_id, origin_source_record_id) values
  ('69000000-0000-4000-8000-000000000400', 'address', 'baja@lab.example', 'block', 'marketing', 'unsubscribe', 'unsubscribe_handler',
   '69000000-0000-4000-8000-000000000001', '69000000-0000-4000-8000-000000000300'),
  ('69000000-0000-4000-8000-000000000401', 'address', 'linked@lab.example', 'block', 'marketing', 'suppression list', 'wave1a_suppression', null, null),
  ('69000000-0000-4000-8000-000000000402', 'address', 'ordinary@lab.example', 'block', 'marketing', 'domain policy', 'operator_command', null, null),
  ('69000000-0000-4000-8000-000000000403', 'address', 'bounced@lab.example', 'block', 'all', 'hard bounce', 'ndr_handler', null, null);
insert into evidence.assertion (id, source_record_id, kind, value_norm, resolution, resolved_kind, resolved_id, resolved_at, resolved_by_operator_id) values
  ('69000000-0000-4000-8000-000000000500', '69000000-0000-4000-8000-000000000300', 'unsubscribe_request', 'baja@lab.example',
   'promoted', 'contact_control', '69000000-0000-4000-8000-000000000400', now(), '69000000-0000-4000-8000-000000000001'),
  ('69000000-0000-4000-8000-000000000501', '69000000-0000-4000-8000-000000000301', 'unsubscribe_request', 'linked@lab.example',
   'linked', 'contact_control', '69000000-0000-4000-8000-000000000401', now(), '69000000-0000-4000-8000-000000000001');

-- ── vocabulary ───────────────────────────────────────────────────────────────────────────────
select lives_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('contact_control', '69000000-0000-4000-8000-000000000401', 1, 'contact_control.evidence_linked', 1, '{}', 'operator', '69000000-0000-4000-8000-000000000001') $$,
  'contact_control.evidence_linked is an event type');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('contact_control', '69000000-0000-4000-8000-000000000400', 1, 'contact_control.resubscribed', 1, '{}', 'operator', '69000000-0000-4000-8000-000000000001') $$,
  '23514', null, 're-subscribing has no event type');
select throws_ok($$ insert into evidence.assertion (source_record_id, kind, value_norm) values ('69000000-0000-4000-8000-000000000302', 'unsubscribe_reply', 'x@lab.example') $$,
  '23514', null, 'the assertion vocabulary stays closed');

-- ── permanence ───────────────────────────────────────────────────────────────────────────────
select throws_ok($$ delete from outbound.contact_control where id = '69000000-0000-4000-8000-000000000400' $$,
  'P0001', null, 'an unsubscribe is never deleted, even by the owner');
select throws_ok($$ update outbound.contact_control set purpose = 'all' where id = '69000000-0000-4000-8000-000000000400' $$,
  'P0001', null, 'an unsubscribe is never rewritten');
select throws_ok($$ update outbound.contact_control set reason = 'lifted' where id = '69000000-0000-4000-8000-000000000400' $$,
  'P0001', null, 'an unsubscribe is never weakened by relabelling');
select throws_ok($$ delete from outbound.contact_control where id = '69000000-0000-4000-8000-000000000401' $$,
  'P0001', null, 'a block an unsubscribe request was linked to is never deleted either');
select lives_ok($$ delete from outbound.contact_control where id = '69000000-0000-4000-8000-000000000402' $$,
  'an ordinary block with no unsubscribe behind it is still revocable (W10 step 5)');
select throws_ok($$ update evidence.source_record set review_status = 'rejected' where id = '69000000-0000-4000-8000-000000000300' $$,
  'P0001', null, 'unsubscribe evidence is never reviewed away');
select throws_ok($$ update evidence.source_record set is_quarantined = true, quarantine_reason = 'x', quarantined_at = now() where id = '69000000-0000-4000-8000-000000000300' $$,
  'P0001', null, 'unsubscribe evidence is never quarantined');
select throws_ok($$ delete from evidence.source_record where id = '69000000-0000-4000-8000-000000000300' $$,
  'P0001', null, 'unsubscribe evidence is never deleted');
select throws_ok($$ update evidence.source_record set dedupe_key = 'gmail_unsubscribe:' || repeat('c', 64) where id = '69000000-0000-4000-8000-000000000302' $$,
  'P0001', null, 'an ordinary record cannot be relabelled as unsubscribe evidence');
select throws_ok($$ update evidence.assertion set resolution = 'rejected', resolved_kind = null, resolved_id = null, resolved_at = null where id = '69000000-0000-4000-8000-000000000500' $$,
  'P0001', null, 'an unsubscribe request is never un-resolved');
select throws_ok($$ delete from evidence.assertion where id = '69000000-0000-4000-8000-000000000500' $$,
  'P0001', null, 'an unsubscribe request is never deleted');
reset role;
select is(left(pg_temp.run_as('origenlab_api', $$update evidence.source_record set review_status = 'rejected' where id = '69000000-0000-4000-8000-000000000300'$$), 5), 'P0001',
  'the api''s column grant on review_status does not reach unsubscribe evidence');
select is(left(pg_temp.run_as('origenlab_worker', $$insert into evidence.assertion (source_record_id, kind, value_norm) values ('69000000-0000-4000-8000-000000000302', 'unsubscribe_request', 'fake@lab.example')$$), 5), '42501',
  'the worker cannot forge an unsubscribe request');

-- ── the send-time contract ───────────────────────────────────────────────────────────────────
set role origenlab_owner;
insert into comms.mailbox (id, address_norm) values ('69000000-0000-4000-8000-000000000100', 'ventas-w10@example.test');
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days, subject, body_html, body_text, content_sha256,
  content_frozen_at, audience_criteria, audience_criteria_version, audience_frozen_at, audience_policy_version, audience_sha256) values
  ('69000000-0000-4000-8000-000000000200', 'W10', '69000000-0000-4000-8000-000000000100', 50, 90, 'A', '<p>B</p>', 'B', repeat('a', 64),
   now(), '{"version": 1}'::jsonb, 1, now(), 'marketing-audience/2026-09-27.v2-w12', repeat('b', 64));

create function pg_temp.ins(p_id uuid, address text, inclusion text, reasons text[], notes text[], review jsonb, at timestamptz)
returns void language sql as $$
  insert into outbound.campaign_recipient (id, campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
    frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version,
    recontact_review, recontact_override_by_operator_id, recontact_override_reason, recontact_override_at)
  values (p_id, '69000000-0000-4000-8000-000000000200', address,
          case inclusion when 'included' then 'snapshotted' else 'excluded' end, reasons, now(), inclusion, reasons, notes,
          'sin_informacion', '[]', 1, repeat('a', 64), 'marketing-audience/2026-09-27.v2-w12', review,
          case when at is null then null else '69000000-0000-4000-8000-000000000001'::uuid end,
          case when at is null then null else 'El cliente pidió la ficha nueva' end, at)
$$;
select pg_temp.ins('69000000-0000-4000-8000-000000000601', 'late@lab.example', 'included', '{}', '{}', null, null);
select pg_temp.ins('69000000-0000-4000-8000-000000000602', 'clean@lab.example', 'included', '{}', '{}', null, null);
select pg_temp.ins('69000000-0000-4000-8000-000000000603', 'recontact@lab.example', 'included', '{}', '{recontact_approved}',
  jsonb_build_object('decision', 'approve', 'note', 'El cliente pidió la ficha nueva', 'operator_id', '69000000-0000-4000-8000-000000000001',
                     'mode', 'individual', 'policy_version', 'recontact-review/2026-09-27.v1', 'prior_contact', '{}'::jsonb), now());
select pg_temp.ins('69000000-0000-4000-8000-000000000604', 'linked@lab.example', 'included', '{}', '{}', null, null);
select pg_temp.ins('69000000-0000-4000-8000-000000000605', 'someone@dept.blocked.example', 'included', '{}', '{}', null, null);
select pg_temp.ins('69000000-0000-4000-8000-000000000606', 'cool@lab.example', 'included', '{}', '{}', null, null);
select pg_temp.ins('69000000-0000-4000-8000-000000000607', 'prior@lab.example', 'included', '{}', '{}', null, null);
select pg_temp.ins('69000000-0000-4000-8000-000000000608', 'bounced@lab.example', 'included', '{}', '{}', null, null);
select lives_ok($$ select pg_temp.ins('69000000-0000-4000-8000-000000000609', 'baja@lab.example', 'excluded', '{block}', '{unsubscribed}', null, null) $$,
  'a freeze records an unsubscribe as the block reason with the unsubscribed note');

-- After the freeze: late@ answers «BAJA»; recontact@ (W12-approved) answers «BAJA» too; the rest
-- get the other controls.
insert into evidence.source_record (id, kind, dedupe_key, payload, payload_sha256, review_status) values
  ('69000000-0000-4000-8000-000000000310', 'gmail_message', 'gmail_unsubscribe:' || repeat('f', 64), '{"body_text": "Baja"}', repeat('d', 64), 'promoted');
insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source, created_by_operator_id, origin_source_record_id, until_at) values
  ('address', 'late@lab.example', 'block', 'marketing', 'unsubscribe', 'unsubscribe_handler', '69000000-0000-4000-8000-000000000001', '69000000-0000-4000-8000-000000000310', null),
  ('address', 'recontact@lab.example', 'block', 'marketing', 'unsubscribe', 'unsubscribe_handler', '69000000-0000-4000-8000-000000000001', '69000000-0000-4000-8000-000000000310', null),
  ('address', 'recontact@lab.example', 'prior_contact', 'marketing', 'wave 1A', 'wave1a_union', null, null, null),
  ('domain', 'blocked.example', 'block', 'marketing', 'domain policy', 'operator_command', null, null, null),
  ('address', 'cool@lab.example', 'cooldown', 'marketing', 'accepted send', 'send_accepted', null, null, now() + interval '30 days'),
  ('address', 'prior@lab.example', 'prior_contact', 'marketing', 'accepted send', 'send_accepted', null, null, null);
-- Evaluated as the owner here (PUBLIC, and so this test's login, holds no EXECUTE); the last
-- assertion evaluates it as origenlab_api.

select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000601'), array['unsubscribe'],
  'a recipient frozen before its «BAJA» is refused at send time: the snapshot never makes it sendable');
select is((select frozen_inclusion || '/' || state from outbound.campaign_recipient where id = '69000000-0000-4000-8000-000000000601'),
  'included/snapshotted', '— while its snapshot stays exactly as frozen (the contract, not the snapshot, refuses)');
select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000602'), '{}'::text[],
  'a recipient with no live control passes clauses 4-6');
select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000603'), array['unsubscribe'],
  'a W12 approval lifts prior_contact and never an unsubscribe');
select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000604'), array['unsubscribe'],
  'a block an unsubscribe request was linked to reads as an unsubscribe');
select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000605'), array['block_domain'],
  'a block on a parent domain refuses a subdomain address');
select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000606'), array['cooldown'],
  'an active cooldown refuses');
select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000607'), array['prior_contact'],
  'a prior contact recorded after the freeze refuses a recipient without a W12 override');
select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000608'), array['block'],
  'a purpose = all block (a bounce) refuses marketing');
select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000609'), array['not_snapshotted', 'unsubscribe'],
  'an excluded recipient is refused twice over');
select is(outbound.marketing_contact_refusals('69000000-0000-4000-8000-0000000006ff'), array['recipient_unknown'],
  'an unknown recipient is refused, never passed');
reset role;
select is(pg_temp.query_as('origenlab_api', $$select outbound.marketing_contact_refusals('69000000-0000-4000-8000-000000000601')::text$$),
  '{unsubscribe}', 'the api role evaluates the contract under its own grants');

select * from finish();
rollback;
