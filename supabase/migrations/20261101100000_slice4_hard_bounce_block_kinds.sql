-- Slice 4 — the worker blocks the four hard bounce kinds (owner 2026-10-29).
--
-- 20261007160000 let the worker turn only a Batch-A «no such user» delivery report into the global
-- invalid_address block. The owner then approved by hand, on 2026-10-19 and 2026-10-21, 230 + 114
-- campaign bounces of four kinds that all mean «this address cannot receive mail»: the mailbox does
-- not exist, Microsoft 365 «550 5.4.1 Recipient address rejected: Access denied» (recipient not in
-- the organization's directory), the domain does not exist (NXDOMAIN / null MX / no mail route) and
-- a disabled or inactive account — and asked that they block by themselves.
--
-- The worker's analysis is now `delivery:v2` and names its kind in `block_reason`. This function
-- accepts a v2 proposal only when the stored v2 observation for that message names the same kind
-- and exactly that one address; v1 proposals keep their original proof. Delays, temporary
-- failures, full mailboxes, mail loops, spam/policy rejections and multi-recipient reports are
-- never proposed (apps/worker `bounce.py`) and cannot be forged here: the observation decides.
-- Everything else in the function is the 20261011120000 definition; the event now records the kind.

set role origenlab_owner;

create or replace function outbound.add_contact_control(
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
  v_source_id uuid;
  v_control outbound.contact_control%rowtype;
  v_event_id uuid;
begin
  -- Existing API callers keep the complete W10 path in the retained helper.
  if session_user = 'origenlab_api' then
    return outbound.add_unsubscribe_contact_control(
      p_kind, p_purpose, p_address, p_reason,
      p_operator_id, p_command_receipt_id, p_evidence
    );
  end if;

  if session_user <> 'origenlab_worker' then
    raise exception 'outbound.add_contact_control: refused for login %', session_user
      using errcode = '42501';
  end if;

  -- Worker authority is intentionally one operation, not a generic block writer.
  if p_kind is distinct from 'block'
     or p_purpose is distinct from 'all'
     or p_reason is distinct from 'invalid_address'
     or p_operator_id is not null
     or p_command_receipt_id is not null then
    raise exception 'outbound.add_contact_control: worker may add only (block, all, invalid_address)'
      using errcode = '22023';
  end if;

  if p_address is null or p_address <> lower(btrim(p_address))
     or p_address !~ '^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;"]+$' then
    raise exception 'outbound.add_contact_control: the address is not a normalized destination'
      using errcode = '22023';
  end if;

  if p_evidence is null or jsonb_typeof(p_evidence) <> 'object'
     or coalesce(p_evidence->>'source_record_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     or coalesce(p_evidence->>'triage_value_norm', '') !~ '^triage:v[0-9]+$'
     or not (
       (p_evidence->>'delivery_value_norm' = 'delivery:v1'
        and p_evidence->>'reason_code' = 'bounce_no_such_user')
       or (p_evidence->>'delivery_value_norm' = 'delivery:v2'
           and p_evidence->>'block_reason' in ('no_such_user', 'not_in_directory', 'domain_not_found', 'mailbox_disabled'))
     ) then
    raise exception 'outbound.add_contact_control: malformed hard-bounce evidence'
      using errcode = '22023';
  end if;

  v_source_id := (p_evidence->>'source_record_id')::uuid;

  -- The privileged boundary re-proves the worker's proposal against canonical evidence. A caller
  -- cannot obtain a global block merely by passing an address and saying "hard bounce".
  if not exists (
    select 1
      from evidence.source_record sr
     where sr.id = v_source_id and sr.kind = 'gmail_message'
  ) then
    raise exception 'outbound.add_contact_control: hard-bounce source record is not a Gmail message'
      using errcode = '22023';
  end if;

  if not exists (
    select 1
      from evidence.assertion a
     where a.source_record_id = v_source_id
       and a.kind = 'message_triage'
       and a.value_norm = p_evidence->>'triage_value_norm'
       and a.value->>'class' = 'bounce'
  ) then
    raise exception 'outbound.add_contact_control: source message is not triaged as a bounce'
      using errcode = '22023';
  end if;

  if not exists (
    select 1
      from evidence.assertion a
     where a.source_record_id = v_source_id
       and a.kind = 'delivery_failure'
       and a.value_norm = p_evidence->>'delivery_value_norm'
       and a.value->'auto_block_addresses' ? p_address
       and jsonb_array_length(a.value->'auto_block_addresses') = 1
       and (
         (a.value_norm = 'delivery:v1'
          and a.value->>'reason_code' = 'bounce_no_such_user'
          and a.value->>'batch' = 'A'
          and a.value->>'batch_reason' = 'no_such_user_final')
         or (a.value_norm = 'delivery:v2'
             and a.value->>'block_reason' = p_evidence->>'block_reason')
       )
  ) then
    raise exception 'outbound.add_contact_control: no eligible hard-bounce observation for this address'
      using errcode = '22023';
  end if;

  insert into outbound.contact_control
      (scope, value_norm, kind, purpose, reason, source, origin_source_record_id)
  values
      ('address', p_address, 'block', 'all', 'invalid_address', 'ndr_handler', v_source_id)
  on conflict (scope, value_norm, kind, purpose) do nothing
  returning * into v_control;

  if v_control.id is null then
    select * into v_control
      from outbound.contact_control
     where scope = 'address' and value_norm = p_address
       and kind = 'block' and purpose = 'all';
    if not found then
      raise exception 'outbound.add_contact_control: global block vanished concurrently'
        using errcode = 'P0001';
    end if;

    -- Never rewrite a pre-existing operator/import block merely because a later NDR agrees.
    return jsonb_build_object(
      'outcome', 'already_blocked',
      'contact_control_id', v_control.id,
      'contact_control_reason', v_control.reason
    );
  end if;

  insert into crm.domain_event
      (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values
      ('contact_control', v_control.id,
       (select coalesce(max(e.seq), 0) + 1
          from crm.domain_event e
         where e.aggregate_kind = 'contact_control' and e.aggregate_id = v_control.id),
       'contact_control.added', 1,
       jsonb_build_object(
         'scope', 'address',
         'kind', 'block',
         'purpose', 'all',
         'reason', 'invalid_address',
         'source', 'ndr_handler',
         'source_record_id', v_source_id,
         'triage_value_norm', p_evidence->>'triage_value_norm',
         'delivery_value_norm', p_evidence->>'delivery_value_norm',
         'reason_code', p_evidence->>'reason_code',
         'block_reason', coalesce(p_evidence->>'block_reason', 'no_such_user')
       ),
       'worker')
  returning id into v_event_id;

  return jsonb_build_object(
    'outcome', 'added',
    'contact_control_id', v_control.id,
    'contact_control_reason', v_control.reason,
    'source_record_id', v_source_id,
    'event_id', v_event_id
  );
end;
$$;

comment on function outbound.add_contact_control(text, text, text, text, uuid, uuid, jsonb) is
  'ARCHITECTURE.md §6.2 closed-list contact-control writer. API sessions retain the complete W10 unsubscribe path. The worker may add only an address-scoped global invalid_address block after this function independently proves a captured Gmail message was triaged as bounce under the triage version the evidence names (triage:v<N>) and has a single-address hard-bounce observation for that exact address: delivery:v1 Batch-A no-such-user, or delivery:v2 whose block_reason (no_such_user, not_in_directory, domain_not_found, mailbox_disabled) matches the proposal. Existing blocks are never overwritten. SECURITY DEFINER, search_path pg_catalog, static SQL.';

reset role;
