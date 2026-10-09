-- Slice 4 — automatic V2 hard-bounce blocking.
--
-- A captured Gmail NDR is still evidence first. The worker may turn only a conservatively
-- classified, single-recipient "no such user" delivery failure into the existing global
-- outbound.contact_control block. Temporary delivery failures, quota/full-mailbox, DNS/NXDOMAIN,
-- SPF/policy/access-denied and ambiguous bounces remain observations only.
--
-- This deliberately reuses outbound.add_contact_control instead of adding a suppression table or
-- a second privileged writer. The W10 unsubscribe body is retained as an INVOKER helper called by
-- the canonical wrapper for API sessions; the canonical function remains the one SECURITY DEFINER
-- on the closed list and gains a tightly checked worker branch.

set role origenlab_owner;

-- One versioned machine observation per NDR message. It contains only the conservative result,
-- never the raw NDR body.
alter table evidence.assertion drop constraint assertion_kind_check;
alter table evidence.assertion
  add constraint assertion_kind_check check (kind in (
    'organization_name', 'contact_address', 'postal_address', 'affiliation',
    'contacted_address', 'supplier_candidate', 'historical_quote_candidate',
    'document_reference', 'unsubscribe_request',
    'message_triage', 'product_mention', 'delivery_failure'
  ));

comment on column evidence.assertion.kind is
  'Closed observation vocabulary. document_reference records that a stored document names a commercial subject; unsubscribe_request records that a reply asked for no more marketing; message_triage is the mail-triage worker reading of one message; product_mention is one product a message names; delivery_failure is the versioned conservative NDR analysis for one captured message. Machine observations are evidence, not a second CRM or suppression authority.';

-- Keep the mature W10 implementation byte-for-byte in behaviour, but move it behind the canonical
-- wrapper. SECURITY INVOKER is intentional: when the SECURITY DEFINER wrapper calls it,
-- current_user remains origenlab_owner while session_user remains the API login, so its existing
-- authorization, receipt, evidence and permanence checks still run exactly as before.
alter function outbound.add_contact_control(text, text, text, text, uuid, uuid, jsonb)
  rename to add_unsubscribe_contact_control;
alter function outbound.add_unsubscribe_contact_control(text, text, text, text, uuid, uuid, jsonb)
  security invoker;

revoke all on function outbound.add_unsubscribe_contact_control(text, text, text, text, uuid, uuid, jsonb)
  from public, anon, authenticated, service_role, origenlab_api, origenlab_worker, origenlab_migrator;

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
     or p_evidence->>'triage_value_norm' is distinct from 'triage:v1'
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

comment on function outbound.add_unsubscribe_contact_control(text, text, text, text, uuid, uuid, jsonb) is
  'Internal SECURITY INVOKER implementation of the W10 unsubscribe transaction. Direct runtime EXECUTE is revoked; outbound.add_contact_control is the only entry point.';

comment on function outbound.add_contact_control(text, text, text, text, uuid, uuid, jsonb) is
  'ARCHITECTURE.md §6.2 closed-list contact-control writer. API sessions retain the complete W10 unsubscribe path. The worker may add only an address-scoped global invalid_address block after this function independently proves a captured Gmail message was triaged as bounce and has delivery:v1 Batch-A no-such-user evidence for that exact address. Existing blocks are never overwritten. SECURITY DEFINER, search_path pg_catalog, static SQL.';

revoke all on function outbound.add_contact_control(text, text, text, text, uuid, uuid, jsonb)
  from public, anon, authenticated, service_role, origenlab_migrator;
grant execute on function outbound.add_contact_control(text, text, text, text, uuid, uuid, jsonb)
  to origenlab_api, origenlab_worker;

reset role;
