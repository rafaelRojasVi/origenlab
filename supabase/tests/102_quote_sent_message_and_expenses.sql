-- Slice 7 — constraints after migrations 20261021100000 and 20261021200000:
--   * quote_revision_sent_message_key is now (sent_message_id, quote_id), not unique(sent_message_id).
--   * quote_line_one_principal_per_revision was dropped; a revision may have several principal items.
-- Synthetic fixtures only. Run as the owner (crosses RLS by ownership).
begin;
create extension if not exists pgtap with schema extensions;
select plan(8);

-- ── unique constraint shape on quote_revision ───────────────────────────────────────────────
-- After 20261021100000 the unique key is (sent_message_id, quote_id): one message sends at
-- most one revision per quote, but may send revisions of several quotes.
select ok(
  (select count(*)::int = 1
     from pg_constraint c
     join pg_class r on r.oid = c.conrelid
     join pg_namespace n on n.oid = r.relnamespace
    where n.nspname = 'crm' and r.relname = 'quote_revision'
      and c.conname = 'quote_revision_sent_message_key'
      and c.contype = 'u'),
  'quote_revision_sent_message_key unique constraint exists');

select is(
  (select array_agg(a.attname order by k.ord)
     from pg_constraint c
     join pg_class r on r.oid = c.conrelid
     join pg_namespace n on n.oid = r.relnamespace
     join unnest(c.conkey) with ordinality k(attnum, ord) on true
     join pg_attribute a on a.attrelid = r.oid and a.attnum = k.attnum
    where n.nspname = 'crm' and r.relname = 'quote_revision'
      and c.conname = 'quote_revision_sent_message_key'
    group by c.oid),
  array['sent_message_id', 'quote_id'],
  'quote_revision_sent_message_key columns are (sent_message_id, quote_id) in that order');

-- ── quote_line_one_principal_per_revision was dropped ───────────────────────────────────────
select hasnt_index('crm', 'quote_line', 'quote_line_one_principal_per_revision',
  'quote_line_one_principal_per_revision index is absent (dropped by 20261021200000)');

-- ── fixtures ─────────────────────────────────────────────────────────────────────────────────
set local role origenlab_owner;

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
values ('a2000000-0000-4000-a200-000000000001', 'a2000000-0000-4000-a200-0000000000f1',
        'expenses.test@example.test', 'Expenses Test', 'sales', 'active');

insert into crm.organization (id, kind, name, confirmation)
values ('a2000000-0000-4000-a200-000000000010', 'company', 'Expenses Test Org', 'confirmed');

insert into crm.opportunity (id, title, stage, owner_operator_id, organization_id)
values ('a2000000-0000-4000-a200-000000000020', 'Expenses Case',
        'lead', 'a2000000-0000-4000-a200-000000000001', 'a2000000-0000-4000-a200-000000000010');

-- Two quotes for the same opportunity (used by the sent-message uniqueness tests).
insert into crm.quote (id, opportunity_id, quote_number)
values ('a2000000-0000-4000-a200-000000000030', 'a2000000-0000-4000-a200-000000000020', 'CN99202'),
       ('a2000000-0000-4000-a200-000000000031', 'a2000000-0000-4000-a200-000000000020', 'CN99203');

-- Authored draft revision on CN99202 (for the two-principal-lines test).
-- origin defaults to 'authored'; status 'draft' lets lines be modified.
insert into crm.quote_revision (id, quote_id, revision_no, status, quote_currency, price_decimals)
values ('a2000000-0000-4000-a200-000000000040',
        'a2000000-0000-4000-a200-000000000030', 1, 'draft', 'CLP', 0);

-- Minimal mailbox + message for the FK on sent_message_id.
insert into comms.mailbox (id, address_norm)
values ('a2000000-0000-4000-a200-000000000100', 'expenses.test@example.test');

-- One outbound message that will be used as the sending evidence for two quotes.
insert into comms.message (id, mailbox_id, provider_message_id, direction, internal_date)
values ('a2000000-0000-4000-a200-00000000f001',
        'a2000000-0000-4000-a200-000000000100',
        'provider-msg-expenses-test-001', 'outbound', now());

-- ── two principal lines on one revision are accepted ────────────────────────────────────────
-- Before 20261021200000 the partial-unique index quote_line_one_principal_per_revision would
-- have rejected this INSERT; after the index was dropped it must succeed.
select lives_ok($$ insert into crm.quote_line
    (quote_revision_id, line_no, line_kind, description, quantity, item_type, is_principal)
  values
    ('a2000000-0000-4000-a200-000000000040', 1, 'item', 'Balanza 1', 1, 'equipment', true),
    ('a2000000-0000-4000-a200-000000000040', 2, 'item', 'Balanza 2', 1, 'equipment', true) $$,
  'two principal item lines on the same revision are accepted');

select is(
  (select count(*)::int from crm.quote_line
    where quote_revision_id = 'a2000000-0000-4000-a200-000000000040' and is_principal),
  2, 'both principal lines were inserted');

-- quote_line_principal_is_item still holds: a logistics line cannot be principal.
select throws_ok($$ insert into crm.quote_line
    (quote_revision_id, line_no, line_kind, description, quantity, is_principal)
  values
    ('a2000000-0000-4000-a200-000000000040', 3, 'logistics', 'Flete', 1, true) $$,
  '23514',
  'a logistics line cannot be marked principal (quote_line_principal_is_item still enforced)');

-- ── sent-message uniqueness: one message may send several quotes ─────────────────────────────
-- Both sent revisions below share the same sent_message_id. Before 20261021100000, the UNIQUE
-- constraint was on (sent_message_id) alone, so only the first would have been accepted.
-- After the migration it is UNIQUE (sent_message_id, quote_id): the second INSERT must succeed.
insert into crm.quote_revision (id, quote_id, revision_no, status, quote_currency, price_decimals,
  subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at,
  party_snapshot, party_snapshot_version, approved_at, approved_by_operator_id,
  sent_message_id, sent_at, pdf_sha256)
values
  -- CN99202 revision 2, sent via message f001
  ('a2000000-0000-4000-a200-000000000041',
   'a2000000-0000-4000-a200-000000000030', 2, 'sent', 'CLP', 0,
   1000, 0, 1000, 190, 1190, now(),
   '{"sold_to": {}}'::jsonb, 1, now(), 'a2000000-0000-4000-a200-000000000001',
   'a2000000-0000-4000-a200-00000000f001', now(),
   'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'),
  -- CN99203 revision 1, SAME message f001 — allowed after 20261021100000
  ('a2000000-0000-4000-a200-000000000042',
   'a2000000-0000-4000-a200-000000000031', 1, 'sent', 'CLP', 0,
   2000, 0, 2000, 380, 2380, now(),
   '{"sold_to": {}}'::jsonb, 1, now(), 'a2000000-0000-4000-a200-000000000001',
   'a2000000-0000-4000-a200-00000000f001', now(),
   'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');

select is(
  (select count(*)::int from crm.quote_revision
    where sent_message_id = 'a2000000-0000-4000-a200-00000000f001'),
  2, 'same message may be sending evidence for two different quotes');

-- The old per-quote uniqueness still holds: the same message cannot send CN99202 a second time.
select throws_ok($$ insert into crm.quote_revision
    (quote_id, revision_no, status, quote_currency, price_decimals,
     subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at,
     party_snapshot, party_snapshot_version, approved_at, approved_by_operator_id,
     sent_message_id, sent_at, pdf_sha256)
  values
    ('a2000000-0000-4000-a200-000000000030', 3, 'sent', 'CLP', 0,
     1000, 0, 1000, 190, 1190, now(),
     '{"sold_to": {}}'::jsonb, 1, now(), 'a2000000-0000-4000-a200-000000000001',
     'a2000000-0000-4000-a200-00000000f001', now(),
     'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc') $$,
  '23505',
  'same message cannot be sending evidence for the same quote twice');

select * from finish();
rollback;
