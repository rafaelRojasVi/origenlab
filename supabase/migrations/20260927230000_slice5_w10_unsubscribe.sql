-- Slice 5 — W10 unsubscribe: a «BAJA» reply becomes a durable, permanent marketing suppression.
--
-- docs/WORKFLOWS.md §1.6 (the contact-control truth table), §W6, §W10 step 3; docs/ARCHITECTURE.md
-- §6.2 (the closed SECURITY DEFINER list); docs/DOMAIN.md §7 #23-#25.
--
-- No new suppression model. An unsubscribe is what §1.6 already says it is: an
-- `outbound.contact_control` row (scope address, kind block, purpose marketing, reason
-- 'unsubscribe', source 'unsubscribe_handler'). The message that asked for it is evidence, recorded
-- where evidence lives: an `evidence.source_record` (kind gmail_message) holding the reply as it was
-- received, and an `evidence.assertion` (kind unsubscribe_request) resolving it to the control.
-- A second «BAJA» from an address that is already suppressed adds evidence and nothing else.
--
-- This migration adds:
--
--   1. `outbound.add_contact_control` — the closed-list privileged writer of contact_control
--      (ARCHITECTURE.md §6.2), implemented for the one kind this slice needs: a marketing
--      unsubscribe. Anything else (an admin block, a revoke, a bounce) is refused until its own
--      slice. It asserts the calling login, validates every argument, writes the evidence, the
--      control or the link, and exactly one crm.domain_event. EXECUTE: origenlab_api only.
--   2. Permanence. An unsubscribe block — and any block an unsubscribe request was linked to — is
--      never updated or deleted, by anyone below superuser. Its evidence (the source record and
--      the assertion) is never updated or deleted, and can only be created by the function above.
--      Re-subscribing does not exist.
--   3. `outbound.marketing_contact_refusals(campaign_recipient_id)` — the send-time contract for
--      WORKFLOWS.md §2 clauses 4-6, evaluated against the *live* contact controls, never against
--      the snapshot. A recipient frozen before an unsubscribe is refused by it. SECURITY INVOKER,
--      read-only; nothing calls it to send, because no send path exists.
--   4. Vocabulary: assertion kind 'unsubscribe_request', event 'contact_control.evidence_linked',
--      frozen note 'unsubscribed' (the sub-reason behind a frozen 'block').
--
-- What it deliberately does not do: no table grant widens (the runtime roles still cannot write
-- contact_control or the unsubscribe evidence directly), no RLS policy changes, no send flag
-- moves, no send function exists, and nothing reads Gmail.

set role origenlab_owner;

-- ── 4. vocabulary ─────────────────────────────────────────────────────────────────────────────
alter table evidence.assertion drop constraint assertion_kind_check;
alter table evidence.assertion
  add constraint assertion_kind_check check (kind in (
    'organization_name', 'contact_address', 'postal_address', 'affiliation',
    'contacted_address', 'supplier_candidate', 'historical_quote_candidate',
    'document_reference', 'unsubscribe_request'
  ));

comment on column evidence.assertion.kind is
  'Closed observation vocabulary. document_reference records that a stored document names a commercial subject; unsubscribe_request records that a reply asked for no more marketing and is always resolved to the outbound.contact_control it created or joined (written only by outbound.add_contact_control).';

alter table outbound.campaign_recipient drop constraint campaign_recipient_frozen_notes_vocabulary;
alter table outbound.campaign_recipient
  add constraint campaign_recipient_frozen_notes_vocabulary check (
    frozen_notes <@ array[
      'shared_mailbox', 'no_contact_point', 'multiple_institutions', 'institution_mismatch',
      'identity_reviewed_include', 'identity_reviewed_exclude', 'possible_supplier_unreviewed',
      'operator_excluded', 'malformed_address',
      'recontact_approved', 'recontact_kept_excluded', 'recontact_not_reviewed',
      'unsubscribed'
    ]::text[]
  );

alter table crm.domain_event drop constraint domain_event_type_check;
alter table crm.domain_event add constraint domain_event_type_check check (event_type in (
  'organization.created', 'organization.confirmed', 'organization.merged',
  'person.created', 'person.confirmed', 'person.merged',
  'affiliation.opened', 'affiliation.closed',
  'address.added', 'address.superseded',
  'contact_point.created', 'contact_point.confirmed',
  'relationship.changed',
  'opportunity.created', 'opportunity.staged', 'opportunity.organization_set', 'opportunity.closed',
  'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
  'case_organization.added', 'case_organization.role_confirmed', 'case_organization.ended',
  'case_interest.added',
  'case_evidence.linked',
  'task.created', 'task.completed', 'task.cancelled',
  'activity.linked',
  'quote.created', 'quote_revision.transitioned', 'quote_revision.superseded',
  'quote_revision.historical_recorded',
  'campaign.transitioned', 'campaign.approved', 'campaign.override_granted', 'campaign.dry_run_recorded',
  'campaign.draft_created', 'campaign.draft_content_saved',
  'campaign.audience_frozen',
  'campaign.planning_set', 'campaign.planning_cleared',
  'send_attempt.submission_changed', 'send_attempt.delivery_changed',
  'contact_control.added', 'contact_control.revoked', 'contact_control.evidence_linked',
  'send_control.changed',
  'operator.changed',
  'assertion.promoted', 'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'product.created'
));

-- The permanence check and the read path look an unsubscribe request up by the control it names.
create index assertion_unsubscribe_request_control_idx
  on evidence.assertion (resolved_id) where kind = 'unsubscribe_request';

-- ── 2. permanence ─────────────────────────────────────────────────────────────────────────────
create function outbound.unsubscribe_permanent() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_table_schema = 'outbound' and tg_table_name = 'contact_control' then
    -- Fired for blocks only (the trigger's WHEN). A block is permanent when it is an unsubscribe,
    -- or when an unsubscribe request was linked to it: revoking it would silently lift a «BAJA».
    if old.reason = 'unsubscribe' or exists (
         select 1 from evidence.assertion a
          where a.kind = 'unsubscribe_request' and a.resolved_kind = 'contact_control'
            and a.resolved_id = old.id) then
      raise exception 'outbound.contact_control: % refused — an unsubscribe is never deleted or weakened; re-subscribing does not exist', tg_op
        using errcode = 'P0001';
    end if;
    return case tg_op when 'DELETE' then old else new end;
  end if;

  -- evidence.source_record / evidence.assertion: unsubscribe evidence is created only inside
  -- outbound.add_contact_control (whose current_user is its owner) and never changed afterwards.
  if tg_op = 'INSERT' then
    if current_user <> 'origenlab_owner' then
      raise exception '%.%: unsubscribe evidence is written only by outbound.add_contact_control', tg_table_schema, tg_table_name
        using errcode = '42501';
    end if;
    return new;
  end if;
  raise exception '%.%: % refused — unsubscribe evidence is immutable', tg_table_schema, tg_table_name, tg_op
    using errcode = 'P0001';
end;
$$;

comment on function outbound.unsubscribe_permanent() is
  'Trigger guard (WORKFLOWS.md §W10): an unsubscribe block, and any block an unsubscribe request is linked to, is never updated or deleted; unsubscribe evidence is inserted only by outbound.add_contact_control and never changed. SECURITY INVOKER.';

revoke all on function outbound.unsubscribe_permanent() from public, anon, authenticated, service_role;

create trigger contact_control_unsubscribe_permanent
  before update or delete on outbound.contact_control
  for each row when (old.kind = 'block')
  execute function outbound.unsubscribe_permanent();

create trigger source_record_unsubscribe_insert
  before insert on evidence.source_record
  for each row when (new.dedupe_key like 'gmail_unsubscribe:%')
  execute function outbound.unsubscribe_permanent();
create trigger source_record_unsubscribe_update
  before update on evidence.source_record
  for each row when (old.dedupe_key like 'gmail_unsubscribe:%' or new.dedupe_key like 'gmail_unsubscribe:%')
  execute function outbound.unsubscribe_permanent();
create trigger source_record_unsubscribe_delete
  before delete on evidence.source_record
  for each row when (old.dedupe_key like 'gmail_unsubscribe:%')
  execute function outbound.unsubscribe_permanent();

create trigger assertion_unsubscribe_insert
  before insert on evidence.assertion
  for each row when (new.kind = 'unsubscribe_request')
  execute function outbound.unsubscribe_permanent();
create trigger assertion_unsubscribe_update
  before update on evidence.assertion
  for each row when (old.kind = 'unsubscribe_request' or new.kind = 'unsubscribe_request')
  execute function outbound.unsubscribe_permanent();
create trigger assertion_unsubscribe_delete
  before delete on evidence.assertion
  for each row when (old.kind = 'unsubscribe_request')
  execute function outbound.unsubscribe_permanent();

-- ── 1. the privileged writer ──────────────────────────────────────────────────────────────────
--
-- p_evidence is the reply as the API staged it:
--   message_id_sha256  sha256 of the normalized RFC 822 Message-ID (the dedupe identity)
--   observed_at        when the mailbox received it (ISO 8601 with offset)
--   grammar_version    the BAJA grammar that accepted it
--   input_sha256       the batch fingerprint the operator confirmed
--   payload            the stored message: body_text, body_sha256, message_id, from_header, ...
-- The function recomputes body_sha256 and the payload fingerprint itself; it trusts neither.
create function outbound.add_contact_control(
  p_kind text,
  p_purpose text,
  p_address text,
  p_reason text,
  p_operator_id uuid,
  p_command_receipt_id uuid,
  p_evidence jsonb
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
  v_operator      platform.operator%rowtype;
  v_receipt       platform.command_receipt%rowtype;
  v_payload       jsonb;
  v_observed_at   timestamptz;
  v_dedupe_key    text;
  v_source_id     uuid;
  v_control       outbound.contact_control%rowtype;
  v_outcome       text;
  v_assertion_id  uuid;
  v_event_id      uuid;
begin
  -- The login, not current_user (which is this function's owner here). ARCHITECTURE.md §6.2 pt 7.
  if session_user <> 'origenlab_api' then
    raise exception 'outbound.add_contact_control: refused for login %', session_user using errcode = '42501';
  end if;

  -- The one operation implemented in this slice (WORKFLOWS.md §1.6 row 2, §W10 step 3).
  if p_kind is distinct from 'block' or p_purpose is distinct from 'marketing' or p_reason is distinct from 'unsubscribe' then
    raise exception 'outbound.add_contact_control: only (block, marketing, unsubscribe) is implemented'
      using errcode = '22023';
  end if;
  if p_address is null or p_address <> lower(btrim(p_address))
     or p_address !~ '^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;"]+$' then
    raise exception 'outbound.add_contact_control: the address is not a normalized destination'
      using errcode = '22023';
  end if;

  select * into v_operator from platform.operator where id = p_operator_id;
  if not found or v_operator.status <> 'active' or v_operator.role not in ('sales', 'admin') then
    raise exception 'outbound.add_contact_control: the operator is not an active sales or admin operator'
      using errcode = '42501';
  end if;
  select * into v_receipt from platform.command_receipt where id = p_command_receipt_id for update;
  if not found or v_receipt.operator_id <> p_operator_id or v_receipt.status <> 'in_progress'
     or v_receipt.command_name <> 'apply-unsubscribe-replies' then
    raise exception 'outbound.add_contact_control: no open apply-unsubscribe-replies receipt for this operator'
      using errcode = '42501';
  end if;

  if p_evidence is null or jsonb_typeof(p_evidence) <> 'object'
     or coalesce(p_evidence->>'message_id_sha256', '') !~ '^[0-9a-f]{64}$'
     or coalesce(p_evidence->>'input_sha256', '') !~ '^[0-9a-f]{64}$'
     or coalesce(p_evidence->>'grammar_version', '') !~ '^[a-z0-9][a-z0-9._/-]{2,79}$'
     or jsonb_typeof(p_evidence->'payload') is distinct from 'object' then
    raise exception 'outbound.add_contact_control: malformed evidence' using errcode = '22023';
  end if;
  v_payload := p_evidence->'payload';
  if jsonb_typeof(v_payload->'body_text') is distinct from 'string'
     or v_payload->>'body_sha256' is distinct from encode(sha256(convert_to(v_payload->>'body_text', 'UTF8')), 'hex')
     or v_payload->>'from_address' is distinct from p_address
     or v_payload->>'message_id_sha256' is distinct from p_evidence->>'message_id_sha256' then
    raise exception 'outbound.add_contact_control: the evidence does not match its own hashes or address'
      using errcode = '22023';
  end if;
  begin
    v_observed_at := (p_evidence->>'observed_at')::timestamptz;
  exception when others then
    raise exception 'outbound.add_contact_control: observed_at is not a timestamp' using errcode = '22023';
  end;
  if v_observed_at is null or v_observed_at > now() + interval '5 minutes' then
    raise exception 'outbound.add_contact_control: observed_at is missing or in the future' using errcode = '22023';
  end if;

  -- The same message twice is one fact. The existing evidence must already name its control;
  -- evidence without one would mean a suppression went missing, and that is never "done".
  v_dedupe_key := 'gmail_unsubscribe:' || (p_evidence->>'message_id_sha256');
  insert into evidence.source_record (kind, dedupe_key, payload, payload_sha256, review_status)
  values ('gmail_message', v_dedupe_key, v_payload,
          encode(sha256(convert_to(v_payload::text, 'UTF8')), 'hex'), 'promoted')
  on conflict (dedupe_key) do nothing
  returning id into v_source_id;

  if v_source_id is null then
    select c.* into v_control
      from evidence.source_record s
      join evidence.assertion a on a.source_record_id = s.id and a.kind = 'unsubscribe_request'
      join outbound.contact_control c on c.id = a.resolved_id and c.kind = 'block'
     where s.dedupe_key = v_dedupe_key and a.value_norm = p_address;
    if not found then
      raise exception 'outbound.add_contact_control: the message is recorded without its suppression for this address'
        using errcode = 'P0001';
    end if;
    return jsonb_build_object('outcome', 'already_recorded', 'contact_control_id', v_control.id,
                              'source_record_id', (select id from evidence.source_record where dedupe_key = v_dedupe_key));
  end if;

  insert into outbound.contact_control
      (scope, value_norm, kind, purpose, reason, source, created_by_operator_id, origin_source_record_id)
  values ('address', p_address, 'block', 'marketing', 'unsubscribe', 'unsubscribe_handler', p_operator_id, v_source_id)
  on conflict (scope, value_norm, kind, purpose) do nothing
  returning * into v_control;

  if v_control.id is null then
    -- Already suppressed for marketing (an earlier «BAJA», or another marketing block): link the
    -- new evidence to it. Linking also makes that block permanent (the trigger above).
    -- Locked, so the block cannot be revoked between this read and the assertion that makes it
    -- permanent (the assertion has no foreign key to hold it).
    select * into v_control from outbound.contact_control
     where scope = 'address' and value_norm = p_address and kind = 'block' and purpose = 'marketing'
       for update;
    if not found then
      raise exception 'outbound.add_contact_control: the marketing block vanished concurrently' using errcode = 'P0001';
    end if;
    v_outcome := 'evidence_linked';
  else
    v_outcome := 'added';
  end if;

  insert into evidence.assertion
      (source_record_id, kind, value_norm, value, resolution, resolved_kind, resolved_id, resolved_at,
       resolved_by_operator_id)
  values (v_source_id, 'unsubscribe_request', p_address,
          jsonb_build_object('message_id_sha256', p_evidence->>'message_id_sha256',
                             'observed_at', v_observed_at,
                             'grammar_version', p_evidence->>'grammar_version'),
          case v_outcome when 'added' then 'promoted' else 'linked' end,
          'contact_control', v_control.id, now(), p_operator_id)
  returning id into v_assertion_id;

  insert into crm.domain_event
      (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload,
       actor_kind, actor_operator_id, command_receipt_id)
  values ('contact_control', v_control.id,
          (select coalesce(max(e.seq), 0) + 1 from crm.domain_event e
            where e.aggregate_kind = 'contact_control' and e.aggregate_id = v_control.id),
          case v_outcome when 'added' then 'contact_control.added' else 'contact_control.evidence_linked' end,
          1,
          jsonb_build_object(
            'scope', 'address', 'kind', 'block', 'purpose', 'marketing',
            'reason', v_control.reason, 'source', v_control.source,
            'requested', 'unsubscribe',
            'source_record_id', v_source_id, 'assertion_id', v_assertion_id,
            'message_id_sha256', p_evidence->>'message_id_sha256',
            'observed_at', v_observed_at,
            'grammar_version', p_evidence->>'grammar_version',
            'input_sha256', p_evidence->>'input_sha256'),
          'operator', p_operator_id, p_command_receipt_id)
  returning id into v_event_id;

  return jsonb_build_object('outcome', v_outcome, 'contact_control_id', v_control.id,
                            'contact_control_reason', v_control.reason,
                            'source_record_id', v_source_id, 'assertion_id', v_assertion_id,
                            'event_id', v_event_id);
end;
$$;

comment on function outbound.add_contact_control(text, text, text, text, uuid, uuid, jsonb) is
  'ARCHITECTURE.md §6.2 closed list — the privileged writer of outbound.contact_control. Implemented for (block, marketing, unsubscribe) only: records the reply evidence, the permanent suppression (or links to the existing one) and one crm.domain_event. session_user must be origenlab_api. SECURITY DEFINER.';

revoke all on function outbound.add_contact_control(text, text, text, text, uuid, uuid, jsonb)
  from public, anon, authenticated, service_role;
grant execute on function outbound.add_contact_control(text, text, text, text, uuid, uuid, jsonb)
  to origenlab_api;

-- ── 3. the send-time contract ─────────────────────────────────────────────────────────────────
create function outbound.marketing_contact_refusals(p_campaign_recipient_id uuid) returns text[]
language sql
stable
set search_path = pg_catalog
as $$
  with r as (
    select id, address_norm, split_part(address_norm, '@', 2) as domain, state, recontact_override_at
      from outbound.campaign_recipient where id = p_campaign_recipient_id
  )
  select case
    when not exists (select 1 from r) then array['recipient_unknown']::text[]
    else array_remove(array[
      (select 'not_snapshotted' from r where r.state <> 'snapshotted'),
      (select 'unsubscribe' from r where exists (
          select 1 from outbound.contact_control c
           where c.scope = 'address' and c.value_norm = r.address_norm and c.kind = 'block'
             and c.purpose in ('all', 'marketing')
             and (c.reason = 'unsubscribe' or exists (
                   select 1 from evidence.assertion a
                    where a.kind = 'unsubscribe_request' and a.resolved_kind = 'contact_control'
                      and a.resolved_id = c.id)))),
      (select 'block' from r where exists (
          select 1 from outbound.contact_control c
           where c.scope = 'address' and c.value_norm = r.address_norm and c.kind = 'block'
             and c.purpose in ('all', 'marketing') and c.reason <> 'unsubscribe'
             and not exists (
                   select 1 from evidence.assertion a
                    where a.kind = 'unsubscribe_request' and a.resolved_kind = 'contact_control'
                      and a.resolved_id = c.id))),
      (select 'block_domain' from r where exists (
          select 1 from outbound.contact_control c
           where c.scope = 'domain' and c.kind = 'block' and c.purpose in ('all', 'marketing')
             and (r.domain = c.value_norm
                  or right(r.domain, length(c.value_norm) + 1) = '.' || c.value_norm))),
      (select 'cooldown' from r where exists (
          select 1 from outbound.contact_control c
           where c.scope = 'address' and c.value_norm = r.address_norm and c.kind = 'cooldown'
             and c.until_at > now())),
      (select 'prior_contact' from r where r.recontact_override_at is null and exists (
          select 1 from outbound.contact_control c
           where c.scope = 'address' and c.value_norm = r.address_norm and c.kind = 'prior_contact'))
    ]::text[], null)
  end
$$;

comment on function outbound.marketing_contact_refusals(uuid) is
  'WORKFLOWS.md §2 clauses 4-6 for one campaign recipient, against the live contact controls (never the frozen snapshot): unsubscribe, block, block_domain, cooldown, prior_contact without a W12 override; plus not_snapshotted / recipient_unknown. Empty = those clauses hold. Every future send step must call it; a snapshot never makes a recipient sendable. SECURITY INVOKER, read-only.';

revoke all on function outbound.marketing_contact_refusals(uuid) from public, anon, authenticated, service_role;
grant execute on function outbound.marketing_contact_refusals(uuid) to origenlab_api, origenlab_worker;

reset role;
