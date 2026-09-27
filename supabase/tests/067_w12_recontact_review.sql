-- Slice 5 — W12 recontact review, proven in the database.
--
-- Companion to `20260927200000_slice5_w12_recontact_review.sql`: a recontact decision lives on
-- a snapshot row only, names its operator, note, mode and policy version; an approval lifts
-- prior_contact and nothing else and is exactly the row's override triple; a kept exclusion
-- keeps prior_contact; and once the campaign is frozen neither the decision nor the triple is
-- ever written again — W12 cannot be granted after the freeze.
--
-- Exercised as the owner, so only constraints and triggers speak. SQLSTATEs: 23514 check,
-- P0001 trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(24);

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('67000000-0000-4000-8000-000000000001', '67000000-0000-4000-8000-0000000000f1', 'w12.sales@example.test', 'W12 Sales', 'sales', 'active'),
  ('67000000-0000-4000-8000-000000000002', '67000000-0000-4000-8000-0000000000f2', 'w12.admin@example.test', 'W12 Admin', 'admin', 'active');
insert into comms.mailbox (id, address_norm) values ('67000000-0000-4000-8000-000000000100', 'ventas-w12@example.test');
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days, subject, body_html, body_text, content_sha256,
  content_frozen_at, audience_criteria, audience_criteria_version, audience_frozen_at, audience_policy_version, audience_sha256) values
  ('67000000-0000-4000-8000-000000000200', 'W12', '67000000-0000-4000-8000-000000000100', 50, 90, 'A', '<p>B</p>', 'B', repeat('a', 64),
   now(), '{"version": 1}'::jsonb, 1, now(), 'marketing-audience/2026-09-27.v2-w12', repeat('b', 64));

-- Every insert below shares these snapshot columns; only the W12 ones vary.
create temporary view w12_cols as select
  '67000000-0000-4000-8000-000000000200'::uuid as campaign_id, now() as frozen_at, 'sin_informacion'::text as relevance,
  '[]'::jsonb as interest_evidence, 3 as campaign_version, repeat('a', 64) as content_sha256,
  'marketing-audience/2026-09-27.v2-w12'::text as policy_version;

create function pg_temp.rv(decision text, note text default 'El cliente pidió la ficha nueva',
                           op text default '67000000-0000-4000-8000-000000000001', mode text default 'individual')
returns jsonb language sql as $$
  select jsonb_build_object('decision', decision, 'note', note, 'operator_id', op, 'mode', mode,
                            'policy_version', 'recontact-review/2026-09-27.v1',
                            'prior_contact', jsonb_build_object('destination', 'x', 'last_contact_at', null))
$$;

create function pg_temp.ins(address text, inclusion text, reasons text[], notes text[], review jsonb,
                            op uuid, reason text, at timestamptz) returns void language sql as $$
  insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons, frozen_at, frozen_inclusion,
    frozen_reasons, frozen_notes, relevance, interest_evidence, campaign_version, content_sha256, policy_version,
    recontact_review, recontact_override_by_operator_id, recontact_override_reason, recontact_override_at)
  select c.campaign_id, address, case inclusion when 'included' then 'snapshotted' else 'excluded' end, reasons,
         c.frozen_at, inclusion, reasons, notes, c.relevance, c.interest_evidence, c.campaign_version, c.content_sha256,
         c.policy_version, review, op, reason, at
    from w12_cols c
$$;

-- ── what a freeze may write ──────────────────────────────────────────────────────────────────
select lives_ok($$ select pg_temp.ins('ok@lab.example', 'included', '{}', '{recontact_approved}', pg_temp.rv('approve', mode => 'bulk'),
  '67000000-0000-4000-8000-000000000001', 'El cliente pidió la ficha nueva', now()) $$,
  'an approved prior contact is included, with its decision and the override triple naming the same operator and note');
select lives_ok($$ select pg_temp.ins('held@lab.example', 'excluded', '{manual_hold}', '{recontact_approved,identity_reviewed_exclude}',
  pg_temp.rv('approve'), '67000000-0000-4000-8000-000000000001', 'El cliente pidió la ficha nueva', now()) $$,
  'an approval may still be held by an operator on the same screen (manual_hold)');
select lives_ok($$ select pg_temp.ins('kept@lab.example', 'excluded', '{prior_contact}', '{recontact_kept_excluded}',
  pg_temp.rv('keep_excluded', 'Ya pidió no ser contactada'), null, null, null) $$,
  'a kept exclusion keeps prior_contact and carries no override');
select lives_ok($$ select pg_temp.ins('none@lab.example', 'excluded', '{prior_contact}', '{recontact_not_reviewed}', null, null, null, null) $$,
  'without a decision prior_contact stays an exclusion reason');

-- ── an approval lifts prior_contact and nothing else ─────────────────────────────────────────
select throws_ok($$ select pg_temp.ins('c1@lab.example', 'excluded', '{cooldown}', '{recontact_approved}', pg_temp.rv('approve'),
  '67000000-0000-4000-8000-000000000001', 'El cliente pidió la ficha nueva', now()) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_approval_lifts_prior_contact_only"',
  'an approval never coexists with an active cooldown');
select throws_ok($$ select pg_temp.ins('c2@lab.example', 'excluded', '{block}', '{recontact_approved}', pg_temp.rv('approve'),
  '67000000-0000-4000-8000-000000000001', 'El cliente pidió la ficha nueva', now()) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_approval_lifts_prior_contact_only"',
  'an approval never coexists with a block (an unsubscribe included)');
select throws_ok($$ select pg_temp.ins('c3@lab.example', 'excluded', '{policy_supplier}', '{recontact_approved}', pg_temp.rv('approve'),
  '67000000-0000-4000-8000-000000000001', 'El cliente pidió la ficha nueva', now()) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_approval_lifts_prior_contact_only"',
  'an approval never coexists with a supplier exclusion');
select throws_ok($$ select pg_temp.ins('c4@lab.example', 'excluded', '{invalid_address}', '{recontact_approved}', pg_temp.rv('approve'),
  '67000000-0000-4000-8000-000000000001', 'El cliente pidió la ficha nueva', now()) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_approval_lifts_prior_contact_only"',
  'an approval never coexists with a malformed or bounced destination');
select throws_ok($$ select pg_temp.ins('c5@lab.example', 'excluded', '{already_in_audience}', '{recontact_approved}', pg_temp.rv('approve'),
  '67000000-0000-4000-8000-000000000001', 'El cliente pidió la ficha nueva', now()) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_approval_lifts_prior_contact_only"',
  'an approval never coexists with a duplicate destination');
select throws_ok($$ select pg_temp.ins('c6@lab.example', 'excluded', '{prior_contact}', '{recontact_approved}', pg_temp.rv('approve'),
  '67000000-0000-4000-8000-000000000001', 'El cliente pidió la ficha nueva', now()) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_approval_lifts_prior_contact_only"',
  'an approved row no longer carries prior_contact');
select throws_ok($$ select pg_temp.ins('c7@lab.example', 'excluded', '{cooldown}', '{recontact_kept_excluded}',
  pg_temp.rv('keep_excluded'), null, null, null) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_kept_excluded"',
  'a kept exclusion is a decision about prior_contact, which the row must carry');

-- ── the override triple is exactly the approval ──────────────────────────────────────────────
select throws_ok($$ select pg_temp.ins('t1@lab.example', 'included', '{}', '{}', null,
  '67000000-0000-4000-8000-000000000001', 'aprobado', now()) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_override_is_the_w12_approval"',
  'no override triple on a snapshot row without an approved W12 decision');
select throws_ok($$ select pg_temp.ins('t2@lab.example', 'included', '{}', '{recontact_approved}', pg_temp.rv('approve'), null, null, null) $$,
  '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_override_is_the_w12_approval"', 'an approval always sets the override triple');
select throws_ok($$ select pg_temp.ins('t3@lab.example', 'included', '{}', '{recontact_approved}', pg_temp.rv('approve'),
  '67000000-0000-4000-8000-000000000002', 'El cliente pidió la ficha nueva', now()) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_override_names_the_decision"',
  'the override names the operator who decided, not another');
select throws_ok($$ select pg_temp.ins('t4@lab.example', 'included', '{}', '{recontact_approved}', pg_temp.rv('approve'),
  '67000000-0000-4000-8000-000000000001', 'otro motivo', now()) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_override_names_the_decision"',
  'the override reason is the decision''s note');

-- ── the decision's own shape ─────────────────────────────────────────────────────────────────
select throws_ok($$ select pg_temp.ins('s1@lab.example', 'excluded', '{prior_contact}', '{recontact_kept_excluded}',
  pg_temp.rv('keep_excluded', '  '), null, null, null) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_review_shape"', 'the note is mandatory');
select throws_ok($$ select pg_temp.ins('s2@lab.example', 'excluded', '{prior_contact}', '{recontact_kept_excluded}',
  pg_temp.rv('override_block'), null, null, null) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_review_shape"', 'the decision is approve or keep_excluded, nothing else');
select throws_ok($$ select pg_temp.ins('s3@lab.example', 'excluded', '{prior_contact}', '{recontact_kept_excluded}',
  pg_temp.rv('keep_excluded', mode => 'auto'), null, null, null) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_review_shape"', 'the mode is individual or bulk');
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm, state, recontact_review)
  values ('67000000-0000-4000-8000-000000000200', 's4@lab.example', 'snapshotted', pg_temp.rv('approve')) $$, '23514', 'new row for relation "campaign_recipient" violates check constraint "campaign_recipient_recontact_review_shape"',
  'a recontact decision exists only on a snapshot row');

-- ── once frozen, W12 is sealed: no later grant, no rewrite ───────────────────────────────────
update outbound.campaign set status = 'audience_frozen' where id = '67000000-0000-4000-8000-000000000200';
select throws_ok($$ update outbound.campaign_recipient set recontact_review = pg_temp.rv('approve', 'reescrito')
  where address_norm = 'ok@lab.example' $$, 'P0001', null, 'a frozen recontact decision is never rewritten');
select throws_ok($$ update outbound.campaign_recipient set recontact_override_by_operator_id = null, recontact_override_reason = null,
  recontact_override_at = null where address_norm = 'ok@lab.example' $$, 'P0001', null, 'a frozen override is never withdrawn');
select throws_ok($$ update outbound.campaign_recipient set recontact_review = pg_temp.rv('approve'), frozen_reasons = '{}',
  recontact_override_by_operator_id = '67000000-0000-4000-8000-000000000001',
  recontact_override_reason = 'El cliente pidió la ficha nueva', recontact_override_at = now()
  where address_norm = 'none@lab.example' $$, 'P0001', null,
  'W12 is never granted after the freeze: a changed decision is a new draft and a new freeze');
select is((select count(*)::int from outbound.campaign_recipient
            where campaign_id = '67000000-0000-4000-8000-000000000200' and recontact_override_at is not null),
  2, 'the frozen campaign carries exactly the two overrides its freeze wrote');
select is((select count(*)::int from pg_proc p
            where p.oid = 'outbound.campaign_recipient_snapshot_guard()'::regprocedure
              and not p.prosecdef and array_to_string(p.proconfig, '|') = 'search_path=pg_catalog'
              and pg_get_userbyid(p.proowner) = 'origenlab_owner'),
  1, 'the replaced snapshot guard is still SECURITY INVOKER, pinned to pg_catalog and owned by origenlab_owner');

select * from finish();
rollback;
