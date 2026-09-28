-- Slice 5 — an archived campaign is immutable, proven in the database.
--
-- Companion to `20260928090000_slice5_archived_campaign_immutable.sql`: an archived campaign,
-- its recipients and its send attempts are never updated or deleted — not by the runtime role,
-- not by the owner — and only the historical importer (the owner) adds rows to one. A reply to
-- it stays insertable. A draft is untouched by the guard.
--
-- SQLSTATE P0001 is the trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
select plan(27);

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

set role origenlab_owner;
insert into comms.mailbox (id, address_norm) values
  ('72000000-0000-4000-8000-000000000100', 'ventas@example.test');
-- The importer's shape: an archived campaign, then its audience and attempts, as the owner.
insert into outbound.campaign (id, name, status, mailbox_id, max_sends) values
  ('72000000-0000-4000-8000-000000000200', 'Histórica', 'archived', '72000000-0000-4000-8000-000000000100', 1000);
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days) values
  ('72000000-0000-4000-8000-000000000201', 'Borrador', '72000000-0000-4000-8000-000000000100', 50, 90);
insert into outbound.campaign_recipient (id, campaign_id, address_norm, state) values
  ('72000000-0000-4000-8000-000000000300', '72000000-0000-4000-8000-000000000200', 'a@lab.example', 'sent'),
  ('72000000-0000-4000-8000-000000000301', '72000000-0000-4000-8000-000000000201', 'b@lab.example', 'snapshotted');
insert into outbound.send_attempt (id, purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm,
                                   submission_state, delivery_state, accepted_at) values
  ('72000000-0000-4000-8000-000000000400', 'marketing', '72000000-0000-4000-8000-000000000200',
   '72000000-0000-4000-8000-000000000300', '72000000-0000-4000-8000-000000000100', 'a@lab.example',
   'accepted', 'pending', timestamptz '2026-09-02 13:08:06+00');
reset role;

-- ── catalogue ─────────────────────────────────────────────────────────────────────────────────
select has_function('outbound', 'archived_campaign_immutable', 'outbound.archived_campaign_immutable exists');
select is((select prosecdef from pg_proc where oid = 'outbound.archived_campaign_immutable()'::regprocedure),
  false, 'the guard is SECURITY INVOKER: it decides on current_user');
select has_trigger('outbound', 'campaign', 'campaign_archived_immutable', 'campaign carries the guard');
select has_trigger('outbound', 'campaign_recipient', 'campaign_recipient_archived_immutable', 'campaign_recipient carries the guard');
select has_trigger('outbound', 'send_attempt', 'send_attempt_archived_immutable', 'send_attempt carries the guard');

-- ── the importer's own inserts were accepted ──────────────────────────────────────────────────
select is((select count(*)::int from outbound.campaign_recipient where campaign_id = '72000000-0000-4000-8000-000000000200'),
  1, 'the owner adds an archived campaign''s audience');

-- ── the runtime role may neither create nor extend history ────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$insert into outbound.campaign (name, status, mailbox_id, max_sends)
  values ('Otra histórica', 'archived', '72000000-0000-4000-8000-000000000100', 10)$$),
  'P0001', 'the API cannot create a campaign that is born archived');
select is(pg_temp.run_as('origenlab_api', $$insert into outbound.campaign_recipient (campaign_id, address_norm, state)
  values ('72000000-0000-4000-8000-000000000200', 'c@lab.example', 'sent')$$),
  'P0001', 'the API cannot add a recipient to an archived campaign');
select is(pg_temp.run_as('origenlab_api', $$insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id,
    mailbox_id, address_norm, submission_state)
  values ('marketing', '72000000-0000-4000-8000-000000000200', '72000000-0000-4000-8000-000000000300',
    '72000000-0000-4000-8000-000000000100', 'a@lab.example', 'reserved')$$),
  '42501', 'the API cannot reserve a send on an archived campaign (it holds no write on send_attempt at all)');

-- ── nobody edits, reopens or deletes it ───────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$update outbound.campaign set name = 'Renombrada'
  where id = '72000000-0000-4000-8000-000000000200'$$), 'P0001', 'the API cannot rename an archived campaign');
select is(pg_temp.run_as('origenlab_api', $$update outbound.campaign set status = 'draft'
  where id = '72000000-0000-4000-8000-000000000200'$$), 'P0001', 'the API cannot reopen an archived campaign');
select is(pg_temp.run_as('origenlab_owner', $$update outbound.campaign set status = 'draft'
  where id = '72000000-0000-4000-8000-000000000200'$$), 'P0001', 'not even the owner reopens an archived campaign');
select is(pg_temp.run_as('origenlab_owner', $$update outbound.campaign set subject = 'Asunto reconstruido'
  where id = '72000000-0000-4000-8000-000000000200'$$), 'P0001', 'content is never reconstructed onto an archived campaign');
select is(pg_temp.run_as('origenlab_owner', $$delete from outbound.campaign
  where id = '72000000-0000-4000-8000-000000000200'$$), 'P0001', 'an archived campaign is never deleted');
select is(pg_temp.run_as('origenlab_api', $$update outbound.campaign_recipient set state = 'bounced'
  where id = '72000000-0000-4000-8000-000000000300'$$), 'P0001', 'a historical recipient''s outcome is never rewritten');
select is(pg_temp.run_as('origenlab_owner', $$delete from outbound.campaign_recipient
  where id = '72000000-0000-4000-8000-000000000300'$$), 'P0001', 'a historical recipient is never deleted');
select is(pg_temp.run_as('origenlab_owner', $$update outbound.send_attempt set delivery_state = 'sent_copy_confirmed'
  where id = '72000000-0000-4000-8000-000000000400'$$), 'P0001', 'a historical attempt''s delivery is never rewritten');
select is(pg_temp.run_as('origenlab_owner', $$delete from outbound.send_attempt
  where id = '72000000-0000-4000-8000-000000000400'$$), 'P0001', 'a historical attempt is never deleted');

select is((select name || '/' || status from outbound.campaign where id = '72000000-0000-4000-8000-000000000200'),
  'Histórica/archived', 'the archived campaign is exactly as imported');
select is((select state from outbound.campaign_recipient where id = '72000000-0000-4000-8000-000000000300'),
  'sent', 'its recipient is exactly as imported');
select is((select submission_state || '/' || delivery_state from outbound.send_attempt where id = '72000000-0000-4000-8000-000000000400'),
  'accepted/pending', 'its attempt is exactly as imported');

-- ── who an address turned out to be is CRM truth; the recorded send is not ─────────────────
set role origenlab_owner;
insert into crm.contact_point (id, kind, value_norm, value_display, usage, confirmation) values
  ('72000000-0000-4000-8000-000000000600', 'email', 'a@lab.example', 'a@lab.example', 'unattributed', 'machine_proposed');
reset role;
select is(pg_temp.run_as('origenlab_owner', $$update outbound.campaign_recipient set contact_point_id = '72000000-0000-4000-8000-000000000600', updated_at = now()
  where id = '72000000-0000-4000-8000-000000000300'$$), 'ok', 'a historical recipient is linked to its CRM contact point (v2_promote.marketing)');
select is(pg_temp.run_as('origenlab_owner', $$update outbound.campaign_recipient set contact_point_id = null, state = 'replied'
  where id = '72000000-0000-4000-8000-000000000300'$$), 'P0001', 'a link change that also rewrites the outcome is refused whole');
select is(pg_temp.run_as('origenlab_owner', $$update outbound.campaign_recipient set address_norm = 'otra@lab.example'
  where id = '72000000-0000-4000-8000-000000000300'$$), 'P0001', 'the recorded address never changes');

-- ── a later reply is new evidence, not an edit ────────────────────────────────────────────────
set role origenlab_owner;
insert into comms.message (id, mailbox_id, provider_message_id, direction, internal_date) values
  ('72000000-0000-4000-8000-000000000500', '72000000-0000-4000-8000-000000000100', 'gm-72-reply', 'inbound',
   timestamptz '2026-09-05 12:00+00');
reset role;
select is(pg_temp.run_as('origenlab_owner', $$insert into outbound.campaign_reply (campaign_id, campaign_recipient_id,
    message_id, send_attempt_id, received_at, proposed_class, proposed_by)
  values ('72000000-0000-4000-8000-000000000200', '72000000-0000-4000-8000-000000000300',
    '72000000-0000-4000-8000-000000000500', '72000000-0000-4000-8000-000000000400',
    timestamptz '2026-09-05 12:00+00', 'human_reply', 'ingest_classifier')$$),
  'ok', 'a reply to an archived campaign is still recorded');

-- ── a draft is not history ────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_owner', $$update outbound.campaign set name = 'Borrador renombrado'
  where id = '72000000-0000-4000-8000-000000000201'$$), 'ok', 'a draft is still editable');
select is(pg_temp.run_as('origenlab_owner', $$update outbound.campaign_recipient set state = 'excluded', exclusion_reasons = array['manual_hold']
  where id = '72000000-0000-4000-8000-000000000301'$$), 'ok', 'a draft''s recipient is still writable where its own rules allow');

select * from finish();
rollback;
