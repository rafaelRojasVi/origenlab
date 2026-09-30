-- Slice 6 Part A — outbound.campaign_content (#42) and outbound.campaign_content_message (#43):
-- immutable historical content, the guard function, the grant matrix, and the shape constraints.
-- The companion to 072_archived_campaign_immutable.sql (that proves the campaign row itself is
-- immutable; this proves the recovered HTML is stored correctly beside it).
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
select plan(33);

grant origenlab_api to session_user with set true, inherit false;

create function pg_temp.run_as(p_role text, p_sql text) returns text
language plpgsql as $$
begin
  execute format('set role %I', p_role);
  execute p_sql;
  reset role;
  return 'ok';
exception when others then
  reset role;
  return sqlstate;
end
$$;
grant execute on function pg_temp.run_as(text, text) to public;

-- ── fixture: mailbox, archived campaign, evidence source record ────────────────────────────
set role origenlab_owner;
insert into comms.mailbox (id, address_norm) values
  ('73000000-0000-4000-8000-000000000100', 'ventas@example.test');
insert into outbound.campaign (id, name, status, mailbox_id, max_sends) values
  ('73000000-0000-4000-8000-000000000200', 'Hielscher 2026', 'archived', '73000000-0000-4000-8000-000000000100', 1126);
insert into evidence.source_record (id, kind, dedupe_key, payload) values
  ('73000000-0000-4000-8000-000000000500', 'migration_manifest',
   'migration_manifest:campaign-content-recovery:73000000-0000-4000-8000-000000000200:abcdef12',
   '{"policy_version": "campaign-content-attribution/2026-09-30.v1"}'::jsonb);
insert into outbound.campaign_recipient (id, campaign_id, address_norm, state) values
  ('73000000-0000-4000-8000-000000000550', '73000000-0000-4000-8000-000000000200', 'a@lab.example', 'sent');
insert into outbound.send_attempt (id, purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm,
                                   submission_state, delivery_state, accepted_at) values
  ('73000000-0000-4000-8000-000000000600', 'marketing', '73000000-0000-4000-8000-000000000200',
   '73000000-0000-4000-8000-000000000550', '73000000-0000-4000-8000-000000000100', 'a@lab.example',
   'accepted', 'pending', timestamptz '2026-09-02 13:08:06+00');
reset role;

-- ── catalogue: guard function ──────────────────────────────────────────────────────────────
select has_function('outbound', 'campaign_content_immutable', 'outbound.campaign_content_immutable exists');
select is((select prosecdef from pg_proc where oid = 'outbound.campaign_content_immutable()'::regprocedure),
  false, 'the guard is SECURITY INVOKER');
select is((select proconfig from pg_proc where oid = 'outbound.campaign_content_immutable()'::regprocedure),
  array['search_path=pg_catalog'], 'the guard pins search_path = pg_catalog');
select is((select pg_get_userbyid(proowner) from pg_proc where oid = 'outbound.campaign_content_immutable()'::regprocedure),
  'origenlab_owner', 'the guard is owned by origenlab_owner');

-- ── catalogue: triggers — exactly one BEFORE INSERT OR UPDATE OR DELETE row trigger each ──
select has_trigger('outbound', 'campaign_content', 'campaign_content_immutable',
  'outbound.campaign_content carries the guard trigger');
select has_trigger('outbound', 'campaign_content_message', 'campaign_content_message_immutable',
  'outbound.campaign_content_message carries the guard trigger');

-- one enabled BEFORE INSERT OR UPDATE OR DELETE row trigger
select is(
  (select count(*)::int from pg_trigger t
    where t.tgrelid = 'outbound.campaign_content'::regclass
      and t.tgfoid = 'outbound.campaign_content_immutable()'::regprocedure
      and t.tgenabled = 'O' and t.tgtype = 31),
  1, 'campaign_content has exactly one enabled BEFORE INSERT OR UPDATE OR DELETE row trigger');
select is(
  (select count(*)::int from pg_trigger t
    where t.tgrelid = 'outbound.campaign_content_message'::regclass
      and t.tgfoid = 'outbound.campaign_content_immutable()'::regprocedure
      and t.tgenabled = 'O' and t.tgtype = 31),
  1, 'campaign_content_message has exactly one enabled BEFORE INSERT OR UPDATE OR DELETE row trigger');

-- ── as owner: INSERT works ─────────────────────────────────────────────────────────────────
set role origenlab_owner;
insert into outbound.campaign_content (
  id, campaign_id, content_kind, variant_no, subject, body_html,
  body_html_sha256, body_html_normalized_sha256,
  message_count, first_sent_at, last_sent_at,
  attribution_method, attribution_confidence, attribution_policy_version,
  unmatched_attempt_count, origin_source_record_id
) values (
  '73000000-0000-4000-8000-000000000700',
  '73000000-0000-4000-8000-000000000200',
  'sent_html', 1, 'Hielscher Sonicators 2026',
  '<html><body>sonicator</body></html>',
  'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
  'f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2',
  995, timestamptz '2026-09-02 13:21:00+00', timestamptz '2026-09-03 10:00:00+00',
  'recipient_lineage_timestamp', 'corroborated',
  'campaign-content-attribution/2026-09-30.v1',
  82, '73000000-0000-4000-8000-000000000500'
);
insert into outbound.campaign_content_message (
  id, campaign_content_id, send_attempt_id, rfc822_message_id,
  sent_at, matched_delta_seconds, archive_folder
) values (
  '73000000-0000-4000-8000-000000000800',
  '73000000-0000-4000-8000-000000000700',
  '73000000-0000-4000-8000-000000000600',
  '<msg001@example.test>',
  timestamptz '2026-09-02 13:21:00+00', 2, '[Gmail]/Sent Mail'
);
reset role;

select is((select content_kind from outbound.campaign_content where id = '73000000-0000-4000-8000-000000000700'),
  'sent_html', 'the owner inserted campaign_content successfully');
select is((select rfc822_message_id from outbound.campaign_content_message where id = '73000000-0000-4000-8000-000000000800'),
  '<msg001@example.test>', 'the owner inserted campaign_content_message successfully');

-- ── as owner: UPDATE and DELETE are refused P0001 ─────────────────────────────────────────
select is(pg_temp.run_as('origenlab_owner', $$update outbound.campaign_content set variant_no = 2
  where id = '73000000-0000-4000-8000-000000000700'$$), 'P0001',
  'UPDATE on campaign_content is refused P0001 even for the owner');
select is(pg_temp.run_as('origenlab_owner', $$delete from outbound.campaign_content
  where id = '73000000-0000-4000-8000-000000000700'$$), 'P0001',
  'DELETE on campaign_content is refused P0001 even for the owner');
select is(pg_temp.run_as('origenlab_owner', $$update outbound.campaign_content_message set archive_folder = 'other'
  where id = '73000000-0000-4000-8000-000000000800'$$), 'P0001',
  'UPDATE on campaign_content_message is refused P0001 even for the owner');
select is(pg_temp.run_as('origenlab_owner', $$delete from outbound.campaign_content_message
  where id = '73000000-0000-4000-8000-000000000800'$$), 'P0001',
  'DELETE on campaign_content_message is refused P0001 even for the owner');

-- ── as api: INSERT is refused P0001 (guard) ───────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$insert into outbound.campaign_content (
    campaign_id, content_kind, variant_no, body_html, body_html_sha256, body_html_normalized_sha256,
    message_count, first_sent_at, last_sent_at, attribution_method, attribution_confidence,
    attribution_policy_version, unmatched_attempt_count, origin_source_record_id
  ) values ('73000000-0000-4000-8000-000000000200', 'sent_html', 2, '<html></html>',
    'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b3',
    'f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e3',
    1, now(), now(), 'recipient_lineage_timestamp', 'corroborated',
    'campaign-content-attribution/2026-09-30.v1', 0,
    '73000000-0000-4000-8000-000000000500')$$),
  '42501', 'api cannot INSERT into campaign_content: 42501 (no INSERT privilege; the guard is unreachable)');

-- ── grant matrix: api SELECT only ─────────────────────────────────────────────────────────
select is(has_table_privilege('origenlab_api', 'outbound.campaign_content', 'SELECT'), true,
  'origenlab_api holds SELECT on campaign_content');
select is(has_table_privilege('origenlab_api', 'outbound.campaign_content', 'INSERT'), false,
  'origenlab_api does not hold INSERT on campaign_content (table-level)');
select is(has_table_privilege('origenlab_api', 'outbound.campaign_content', 'UPDATE'), false,
  'origenlab_api does not hold UPDATE on campaign_content');
select is(has_table_privilege('origenlab_api', 'outbound.campaign_content_message', 'SELECT'), true,
  'origenlab_api holds SELECT on campaign_content_message');
select is(has_table_privilege('origenlab_api', 'outbound.campaign_content_message', 'INSERT'), false,
  'origenlab_api does not hold INSERT on campaign_content_message (table-level)');

-- api can SELECT through the policy
select is(pg_temp.run_as('origenlab_api', $$select count(*) from outbound.campaign_content$$), 'ok',
  'origenlab_api can SELECT from campaign_content');

-- ── shape constraints ─────────────────────────────────────────────────────────────────────
-- sent_html needs message_count >= 1 and a window (23514)
select is(pg_temp.run_as('origenlab_owner', $$insert into outbound.campaign_content (
    campaign_id, content_kind, variant_no, body_html, body_html_sha256, body_html_normalized_sha256,
    message_count, attribution_method, attribution_confidence, attribution_policy_version,
    unmatched_attempt_count, origin_source_record_id
  ) values ('73000000-0000-4000-8000-000000000200', 'sent_html', 2, '<html>x</html>',
    'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b4',
    'f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e4',
    0, 'recipient_lineage_timestamp', 'corroborated',
    'campaign-content-attribution/2026-09-30.v1', 0,
    '73000000-0000-4000-8000-000000000500')$$),
  '23514', 'sent_html with message_count=0 is refused (23514)');

-- historical_draft needs 0 message_count and unsent_draft method (23514)
select is(pg_temp.run_as('origenlab_owner', $$insert into outbound.campaign_content (
    campaign_id, content_kind, variant_no, body_html, body_html_sha256, body_html_normalized_sha256,
    message_count, first_sent_at, last_sent_at, attribution_method, attribution_confidence,
    attribution_policy_version, unmatched_attempt_count, origin_source_record_id
  ) values ('73000000-0000-4000-8000-000000000200', 'historical_draft', 1, '<html>d</html>',
    'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b5',
    'f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e5',
    1, now(), now(), 'unsent_draft', 'corroborated',
    'campaign-content-attribution/2026-09-30.v1', 0,
    '73000000-0000-4000-8000-000000000500')$$),
  '23514', 'historical_draft with message_count=1 and a window is refused (23514)');

-- hash shape: not 64 hex chars (23514)
select is(pg_temp.run_as('origenlab_owner', $$insert into outbound.campaign_content (
    campaign_id, content_kind, variant_no, body_html, body_html_sha256, body_html_normalized_sha256,
    message_count, first_sent_at, last_sent_at, attribution_method, attribution_confidence,
    attribution_policy_version, unmatched_attempt_count, origin_source_record_id
  ) values ('73000000-0000-4000-8000-000000000200', 'sent_html', 2, '<html>y</html>',
    'TOOSHORT', 'f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e6',
    1, now(), now(), 'recipient_lineage_timestamp', 'corroborated',
    'campaign-content-attribution/2026-09-30.v1', 0,
    '73000000-0000-4000-8000-000000000500')$$),
  '23514', 'non-hex sha256 is refused (23514)');

-- uniqueness per (campaign, kind, normalized hash) (23505)
select is(pg_temp.run_as('origenlab_owner', $$insert into outbound.campaign_content (
    campaign_id, content_kind, variant_no, body_html, body_html_sha256, body_html_normalized_sha256,
    message_count, first_sent_at, last_sent_at, attribution_method, attribution_confidence,
    attribution_policy_version, unmatched_attempt_count, origin_source_record_id
  ) values ('73000000-0000-4000-8000-000000000200', 'sent_html', 3, '<html><body>sonicator</body></html>',
    'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b7',
    'f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2d3c4b5a6f1e2',
    10, now(), now(), 'recipient_lineage_timestamp', 'corroborated',
    'campaign-content-attribution/2026-09-30.v1', 0,
    '73000000-0000-4000-8000-000000000500')$$),
  '23505', 'duplicate (campaign, kind, normalized_sha256) is refused (23505)');

-- uniqueness per (content, rfc822 id) on campaign_content_message (23505)
select is(pg_temp.run_as('origenlab_owner', $$insert into outbound.campaign_content_message (
    campaign_content_id, rfc822_message_id, sent_at, matched_delta_seconds, archive_folder
  ) values ('73000000-0000-4000-8000-000000000700', '<msg001@example.test>',
    now(), 1, '[Gmail]/Sent Mail')$$),
  '23505', 'duplicate (campaign_content_id, rfc822_message_id) is refused (23505)');

-- unique send_attempt_id on campaign_content_message (23505)
select is(pg_temp.run_as('origenlab_owner', $$insert into outbound.campaign_content_message (
    campaign_content_id, send_attempt_id, rfc822_message_id, sent_at, matched_delta_seconds, archive_folder
  ) values ('73000000-0000-4000-8000-000000000700', '73000000-0000-4000-8000-000000000600',
    '<msg002@example.test>', now(), 1, '[Gmail]/Sent Mail')$$),
  '23505', 'duplicate send_attempt_id is refused (23505)');

-- ── no address column on either table ─────────────────────────────────────────────────────
select hasnt_column('outbound', 'campaign_content', 'address_norm',
  'campaign_content has no address_norm column');
select hasnt_column('outbound', 'campaign_content', 'email',
  'campaign_content has no email column');
select hasnt_column('outbound', 'campaign_content_message', 'address_norm',
  'campaign_content_message has no address_norm column');
select hasnt_column('outbound', 'campaign_content_message', 'email',
  'campaign_content_message has no email column');

-- ── RLS ───────────────────────────────────────────────────────────────────────────────────
select is(
  (select relrowsecurity from pg_class where oid = 'outbound.campaign_content'::regclass),
  true, 'RLS is enabled on campaign_content');
select is(
  (select relrowsecurity from pg_class where oid = 'outbound.campaign_content_message'::regclass),
  true, 'RLS is enabled on campaign_content_message');

select * from finish();
rollback;
