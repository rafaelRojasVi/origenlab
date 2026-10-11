-- Slice 4 — the hard-bounce boundary accepts any triage version (STATUS §2.7.91).
--
-- 20261007160000 pinned the worker's hard-bounce evidence to `triage_value_norm = 'triage:v1'`.
-- The triage is versioned on purpose (`TRIAGE_VERSION` in apps/worker `triage_rules.py`,
-- OPERATIONS §8.11): a rule change bumps it and every recent message is read again under the new
-- `value_norm`, beside the old reading. With the literal pin, the first bump would have made every
-- new no-such-user bounce refuse its block — the worker passes its current version — and the
-- sweep would retry that message forever. The pin added nothing the boundary does not already
-- prove: the next check requires a `message_triage` assertion of class `bounce` under exactly the
-- version the caller names. So the literal becomes the shape `triage:v<N>`; everything else in the
-- function is byte-for-byte the 20261007160000 definition.

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
     or p_evidence->>'delivery_value_norm' is distinct from 'delivery:v1'
     or p_evidence->>'reason_code' is distinct from 'bounce_no_such_user' then
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
       and a.value->>'reason_code' = 'bounce_no_such_user'
       and a.value->>'batch' = 'A'
       and a.value->>'batch_reason' = 'no_such_user_final'
       and a.value->'auto_block_addresses' ? p_address
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
         'reason_code', 'bounce_no_such_user'
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
  'ARCHITECTURE.md §6.2 closed-list contact-control writer. API sessions retain the complete W10 unsubscribe path. The worker may add only an address-scoped global invalid_address block after this function independently proves a captured Gmail message was triaged as bounce under the triage version the evidence names (triage:v<N>) and has delivery:v1 Batch-A no-such-user evidence for that exact address. Existing blocks are never overwritten. SECURITY DEFINER, search_path pg_catalog, static SQL.';

reset role;
