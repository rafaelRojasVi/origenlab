-- Slice 5 — W10: confirm what the mail triage read as an unsubscribe (STATUS.md §2.7.86).
--
-- The triage worker reads a reply whose subject is «REMOVER», or whose first line is «BAJA» with
-- more text after it, as class `unsubscribe` — and records only a message_triage assertion
-- (evidence, never a suppression). The reply grammar keeps refusing such bodies, so until now the
-- address stayed in every audience unless an operator typed the reply into the apply tool.
--
-- This corrective migration gives the W10 helper one more basis, `triage_reading_confirmed`: an
-- operator who read the email confirms the reading (its assertion id, the address, a note) and
-- the function writes exactly what a confirmed «BAJA» writes — the permanent marketing block (or
-- the link to an existing one), one resolved unsubscribe_request on the capture record, and one
-- crm.domain_event — after proving that the reading is of an inbound captured message whose From
-- is that address. Nothing is suppressed without that click; the grammar and the sender policy
-- are unchanged, and no triage reading feeds the rules.
--
-- The body below is the shipped helper (20260927230000, renamed and made SECURITY INVOKER by
-- 20261007160000) with the new branch and two payload fields; every other path is unchanged.

set role origenlab_owner;

create or replace function outbound.add_unsubscribe_contact_control(
  p_kind text,
  p_purpose text,
  p_address text,
  p_reason text,
  p_operator_id uuid,
  p_command_receipt_id uuid,
  p_evidence jsonb
) returns jsonb
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_operator      platform.operator%rowtype;
  v_receipt       platform.command_receipt%rowtype;
  v_basis         text;
  v_command       text;
  v_payload       jsonb;
  v_observed_at   timestamptz;
  v_dedupe_key    text;
  v_source_id     uuid;
  v_request       evidence.assertion%rowtype;
  v_control       outbound.contact_control%rowtype;
  v_outcome       text;
  v_assertion_id  uuid;
  v_event_id      uuid;
  v_in_reply_to   text;
  v_lineage_id    uuid;
  v_review_reason text;
  v_note          text;
  v_review_sha256 text;
  v_reading       evidence.assertion%rowtype;
  v_existing      evidence.assertion%rowtype;
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
  if p_evidence is null or jsonb_typeof(p_evidence) <> 'object'
     or coalesce(p_evidence->>'basis', '') not in ('known_address', 'outbound_lineage', 'pending_review', 'review_confirmed',
                                                    'review_dismissed', 'triage_reading_confirmed')
     or coalesce(p_evidence->>'policy_version', '') !~ '^[a-z0-9][a-z0-9._/-]{2,79}$' then
    raise exception 'outbound.add_contact_control: malformed evidence' using errcode = '22023';
  end if;
  v_basis := p_evidence->>'basis';
  v_command := case v_basis when 'review_confirmed' then 'resolve-unsubscribe-review'
                            when 'review_dismissed' then 'dismiss-unsubscribe-review'
                            when 'triage_reading_confirmed' then 'confirm-triage-unsubscribe'
                            else 'apply-unsubscribe-replies' end;

  select * into v_operator from platform.operator where id = p_operator_id;
  if not found or v_operator.status <> 'active' or v_operator.role not in ('sales', 'admin') then
    raise exception 'outbound.add_contact_control: the operator is not an active sales or admin operator'
      using errcode = '42501';
  end if;
  if v_basis = 'review_dismissed' and v_operator.role <> 'admin' then
    raise exception 'outbound.add_contact_control: only an active admin may dismiss a held unsubscribe'
      using errcode = '42501';
  end if;
  select * into v_receipt from platform.command_receipt where id = p_command_receipt_id for update;
  if not found or v_receipt.operator_id <> p_operator_id or v_receipt.status <> 'in_progress'
     or v_receipt.command_name <> v_command then
    raise exception 'outbound.add_contact_control: no open % receipt for this operator', v_command
      using errcode = '42501';
  end if;

  if v_basis = 'review_dismissed' then
    -- ── an admin dismisses a pending request as a false positive ──────────────────────────────
    v_note := btrim(coalesce(p_evidence->>'explanation', ''), E' \t\r\n');
    if length(v_note) not between 1 and 1000 or v_note !~ '[^[:space:]]'
       or coalesce(p_evidence->>'assertion_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       or coalesce(p_evidence->>'review_sha256', '') !~ '^[0-9a-f]{64}$' then
      raise exception 'outbound.add_contact_control: a dismissal needs the request id, its review_sha256 and an explanation'
        using errcode = '22023';
    end if;
    select * into v_request from evidence.assertion
     where id = (p_evidence->>'assertion_id')::uuid and kind = 'unsubscribe_request'
       for update;
    if not found or v_request.value_norm <> p_address then
      raise exception 'outbound.add_contact_control: no unsubscribe request for this address' using errcode = '22023';
    end if;
    if v_request.resolution in ('promoted', 'linked') then
      raise exception 'outbound.add_contact_control: the request is a confirmed unsubscribe; it is never dismissed'
        using errcode = 'P0001';
    end if;
    select encode(sha256(convert_to(concat_ws('|', 'unsubscribe-review/v1', v_request.id::text, v_request.value_norm,
                                              v_request.resolution, v_request.source_record_id::text,
                                              coalesce(s.payload_sha256, ''), v_request.value::text), 'UTF8')), 'hex')
      into v_review_sha256
      from evidence.source_record s where s.id = v_request.source_record_id;
    if v_review_sha256 is distinct from p_evidence->>'review_sha256' then
      raise exception 'outbound.add_contact_control: the review changed since it was read (review_sha256 differs)'
        using errcode = 'P0001';
    end if;
    if v_request.resolution <> 'unresolved' then
      raise exception 'outbound.add_contact_control: the request is %, not pending', v_request.resolution using errcode = 'P0001';
    end if;

    update evidence.assertion
       set resolution = 'rejected', resolved_at = now(), resolved_by_operator_id = p_operator_id
     where id = v_request.id;
    update evidence.source_record set review_status = 'reviewed'
     where id = v_request.source_record_id and review_status = 'pending';

    insert into crm.domain_event
        (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload,
         actor_kind, actor_operator_id, command_receipt_id)
    values ('assertion', v_request.id,
            (select coalesce(max(e.seq), 0) + 1 from crm.domain_event e
              where e.aggregate_kind = 'assertion' and e.aggregate_id = v_request.id),
            'assertion.unsubscribe_review_dismissed', 1,
            jsonb_strip_nulls(jsonb_build_object(
              'kind', 'unsubscribe_request', 'decision', 'dismissed',
              'resolution_from', 'unresolved', 'resolution_to', 'rejected',
              'explanation', v_note, 'review_sha256', v_review_sha256,
              'source_record_id', v_request.source_record_id,
              'review_reason', v_request.value->>'review_reason',
              'message_id_sha256', v_request.value->>'message_id_sha256',
              'grammar_version', v_request.value->>'grammar_version',
              'policy_version', p_evidence->>'policy_version')),
            'operator', p_operator_id, p_command_receipt_id)
    returning id into v_event_id;

    return jsonb_build_object('outcome', 'dismissed', 'assertion_id', v_request.id,
                              'source_record_id', v_request.source_record_id,
                              'review_sha256', v_review_sha256, 'event_id', v_event_id);
  elsif v_basis = 'review_confirmed' then
    -- ── an operator confirms a pending request ────────────────────────────────────────────────
    v_note := btrim(coalesce(p_evidence->>'note', ''), E' \t\r\n');
    if length(v_note) not between 1 and 500 or v_note !~ '[^[:space:]]'
       or coalesce(p_evidence->>'assertion_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
      raise exception 'outbound.add_contact_control: a review needs the request id and a note' using errcode = '22023';
    end if;
    select * into v_request from evidence.assertion
     where id = (p_evidence->>'assertion_id')::uuid and kind = 'unsubscribe_request'
       for update;
    if not found or v_request.value_norm <> p_address then
      raise exception 'outbound.add_contact_control: no unsubscribe request for this address' using errcode = '22023';
    end if;
    if v_request.resolution in ('promoted', 'linked') then
      return jsonb_build_object('outcome', 'already_resolved', 'contact_control_id', v_request.resolved_id,
                                'assertion_id', v_request.id, 'source_record_id', v_request.source_record_id);
    end if;
    -- A dismissed (rejected) request may still be confirmed: it only adds suppression.
    if v_request.resolution not in ('unresolved', 'rejected') then
      raise exception 'outbound.add_contact_control: the request is %, neither pending nor dismissed', v_request.resolution
        using errcode = 'P0001';
    end if;
    v_source_id := v_request.source_record_id;
  elsif v_basis = 'triage_reading_confirmed' then
    -- ── an operator confirms what the mail triage read as an unsubscribe ──────────────────────
    -- The reading is a message_triage assertion of class `unsubscribe` on a captured message whose
    -- From is exactly this address. The body was never stored, so nothing is re-read here: the
    -- operator read the email and says so in the note. The result is the same permanent control a
    -- confirmed «BAJA» gets, recorded as a resolved unsubscribe_request on the capture record.
    v_note := btrim(coalesce(p_evidence->>'note', ''), E' \t\r\n');
    if length(v_note) not between 1 and 500 or v_note !~ '[^[:space:]]'
       or coalesce(p_evidence->>'assertion_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
      raise exception 'outbound.add_contact_control: a confirmation needs the reading id and a note' using errcode = '22023';
    end if;
    select a.* into v_reading from evidence.assertion a
     where a.id = (p_evidence->>'assertion_id')::uuid and a.kind = 'message_triage'
       and a.value->>'class' = 'unsubscribe'
       for share;
    if not found then
      raise exception 'outbound.add_contact_control: no unsubscribe reading with that id' using errcode = '22023';
    end if;
    if not exists (
        select 1 from evidence.source_record sr
        join comms.message m on 'gmail_message:' || m.provider_message_id = sr.dedupe_key
        join comms.message_participant p on p.message_id = m.id and p.role = 'from'
       where sr.id = v_reading.source_record_id and sr.kind = 'gmail_message'
         and m.direction = 'inbound' and p.address_norm = p_address) then
      raise exception 'outbound.add_contact_control: the reading is not of an inbound email from this address'
        using errcode = '22023';
    end if;
    -- Confirmed before (this reading, or a «BAJA» of the same message): nothing to add.
    select a.* into v_existing from evidence.assertion a
     where a.source_record_id = v_reading.source_record_id and a.kind = 'unsubscribe_request'
       and a.value_norm = p_address and a.resolution in ('promoted', 'linked')
     order by a.created_at limit 1;
    if found then
      return jsonb_build_object('outcome', 'already_resolved', 'contact_control_id', v_existing.resolved_id,
                                'assertion_id', v_existing.id, 'source_record_id', v_existing.source_record_id);
    end if;
    v_source_id := v_reading.source_record_id;
  else
    -- ── a reply: validate the evidence, then prove the basis ──────────────────────────────────
    if coalesce(p_evidence->>'message_id_sha256', '') !~ '^[0-9a-f]{64}$'
       or coalesce(p_evidence->>'input_sha256', '') !~ '^[0-9a-f]{64}$'
       or coalesce(p_evidence->>'grammar_version', '') !~ '^[a-z0-9][a-z0-9._/-]{2,79}$'
       or jsonb_typeof(p_evidence->'payload') is distinct from 'object' then
      raise exception 'outbound.add_contact_control: malformed evidence' using errcode = '22023';
    end if;
    v_payload := p_evidence->'payload';
    if jsonb_typeof(v_payload->'body_text') is distinct from 'string'
       or v_payload->>'body_sha256' is distinct from encode(sha256(convert_to(v_payload->>'body_text', 'UTF8')), 'hex')
       or v_payload->>'from_address' is distinct from p_address
       or v_payload->>'message_id_sha256' is distinct from p_evidence->>'message_id_sha256'
       or v_payload->>'grammar_version' is distinct from p_evidence->>'grammar_version'
       or v_payload->>'policy_version' is distinct from p_evidence->>'policy_version' then
      raise exception 'outbound.add_contact_control: the evidence does not match its own hashes, versions or address'
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

    if v_basis = 'known_address' then
      if not (exists (select 1 from outbound.contact_control c where c.scope = 'address' and c.value_norm = p_address)
              or exists (select 1 from outbound.campaign_recipient r where r.address_norm = p_address)
              or exists (select 1 from crm.contact_point p where p.kind = 'email' and p.value_norm = p_address)) then
        raise exception 'outbound.add_contact_control: the address is not known here; it can only be held for review'
          using errcode = '22023';
      end if;
    elsif v_basis = 'outbound_lineage' then
      -- In-Reply-To normalized exactly as the API does: trimmed, one pair of angle brackets
      -- removed, lower-cased. The stored rfc822_message_id_norm is matched with or without them.
      v_in_reply_to := lower(btrim(coalesce(v_payload->>'in_reply_to', '')));
      if v_in_reply_to like '<%>' then
        v_in_reply_to := btrim(substr(v_in_reply_to, 2, length(v_in_reply_to) - 2));
      end if;
      if v_in_reply_to !~ '^[^\s<>@]+@[^\s<>@]+$' then
        raise exception 'outbound.add_contact_control: the reply names no outbound message' using errcode = '22023';
      end if;
      select m.id into v_lineage_id
        from comms.message m
       where m.direction = 'outbound'
         and m.rfc822_message_id_norm in (v_in_reply_to, '<' || v_in_reply_to || '>')
         and exists (select 1 from comms.message_participant mp
                      where mp.message_id = m.id and mp.role in ('to', 'cc', 'bcc') and mp.address_norm = p_address)
       order by m.internal_date, m.id
       limit 1;
      if v_lineage_id is null then
        raise exception 'outbound.add_contact_control: no recorded outbound message was sent to this address under that Message-ID'
          using errcode = '22023';
      end if;
    else  -- pending_review
      v_review_reason := p_evidence->>'review_reason';
      if v_review_reason is null or v_review_reason not in ('lineage_missing', 'recipient_mismatch') then
        raise exception 'outbound.add_contact_control: a pending review needs its reason' using errcode = '22023';
      end if;
    end if;

    -- The same message twice is one fact. The existing evidence must already name its request
    -- for this address; evidence without one would mean a suppression went missing.
    v_dedupe_key := 'gmail_unsubscribe:' || (p_evidence->>'message_id_sha256');
    insert into evidence.source_record (kind, dedupe_key, payload, payload_sha256, review_status)
    values ('gmail_message', v_dedupe_key, v_payload,
            encode(sha256(convert_to(v_payload::text, 'UTF8')), 'hex'),
            case v_basis when 'pending_review' then 'pending' else 'promoted' end)
    on conflict (dedupe_key) do nothing
    returning id into v_source_id;

    if v_source_id is null then
      select a.* into v_request
        from evidence.source_record s
        join evidence.assertion a on a.source_record_id = s.id and a.kind = 'unsubscribe_request'
       where s.dedupe_key = v_dedupe_key and a.value_norm = p_address;
      if not found then
        raise exception 'outbound.add_contact_control: the message is recorded without its request for this address'
          using errcode = 'P0001';
      end if;
      return jsonb_build_object(
        'outcome', case v_request.resolution when 'unresolved' then 'already_pending' else 'already_recorded' end,
        'contact_control_id', v_request.resolved_id, 'assertion_id', v_request.id,
        'source_record_id', v_request.source_record_id);
    end if;

    if v_basis = 'pending_review' then
      insert into evidence.assertion (source_record_id, kind, value_norm, value)
      values (v_source_id, 'unsubscribe_request', p_address,
              jsonb_build_object('message_id_sha256', p_evidence->>'message_id_sha256',
                                 'observed_at', v_observed_at,
                                 'grammar_version', p_evidence->>'grammar_version',
                                 'policy_version', p_evidence->>'policy_version',
                                 'basis', v_basis, 'review_reason', v_review_reason))
      returning id into v_assertion_id;

      insert into crm.domain_event
          (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload,
           actor_kind, actor_operator_id, command_receipt_id)
      values ('assertion', v_assertion_id, 1, 'assertion.unsubscribe_review_opened', 1,
              jsonb_build_object(
                'kind', 'unsubscribe_request', 'resolution', 'unresolved', 'holds', 'marketing',
                'review_reason', v_review_reason, 'source_record_id', v_source_id,
                'message_id_sha256', p_evidence->>'message_id_sha256',
                'observed_at', v_observed_at,
                'grammar_version', p_evidence->>'grammar_version',
                'policy_version', p_evidence->>'policy_version',
                'input_sha256', p_evidence->>'input_sha256'),
              'operator', p_operator_id, p_command_receipt_id)
      returning id into v_event_id;

      return jsonb_build_object('outcome', 'pending_review', 'assertion_id', v_assertion_id,
                                'source_record_id', v_source_id, 'review_reason', v_review_reason,
                                'event_id', v_event_id);
    end if;
  end if;

  -- ── the control: created, or the existing marketing block linked ──────────────────────────
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

  if v_basis = 'review_confirmed' then
    update evidence.assertion
       set resolution = case v_outcome when 'added' then 'promoted' else 'linked' end,
           resolved_kind = 'contact_control', resolved_id = v_control.id, resolved_at = now(),
           resolved_by_operator_id = p_operator_id
     where id = v_request.id;
    update evidence.source_record set review_status = 'promoted'
     where id = v_source_id and review_status in ('pending', 'reviewed');
    v_assertion_id := v_request.id;
  else
    insert into evidence.assertion
        (source_record_id, kind, value_norm, value, resolution, resolved_kind, resolved_id, resolved_at,
         resolved_by_operator_id)
    values (v_source_id, 'unsubscribe_request', p_address,
            jsonb_strip_nulls(jsonb_build_object(
              'message_id_sha256', p_evidence->>'message_id_sha256',
              'observed_at', v_observed_at,
              'grammar_version', p_evidence->>'grammar_version',
              'policy_version', p_evidence->>'policy_version',
              'basis', v_basis,
              'lineage_message_id', v_lineage_id,
              'triage_assertion_id', v_reading.id,
              'triage_version', v_reading.value_norm,
              'triage_reasons', v_reading.value->'reasons',
              'review_note', v_note)),
            case v_outcome when 'added' then 'promoted' else 'linked' end,
            'contact_control', v_control.id, now(), p_operator_id)
    returning id into v_assertion_id;
  end if;

  insert into crm.domain_event
      (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload,
       actor_kind, actor_operator_id, command_receipt_id)
  values ('contact_control', v_control.id,
          (select coalesce(max(e.seq), 0) + 1 from crm.domain_event e
            where e.aggregate_kind = 'contact_control' and e.aggregate_id = v_control.id),
          case v_outcome when 'added' then 'contact_control.added' else 'contact_control.evidence_linked' end,
          1,
          jsonb_strip_nulls(jsonb_build_object(
            'scope', 'address', 'kind', 'block', 'purpose', 'marketing',
            'reason', v_control.reason, 'source', v_control.source,
            'requested', 'unsubscribe', 'basis', v_basis,
            'source_record_id', v_source_id, 'assertion_id', v_assertion_id,
            'lineage_message_id', v_lineage_id,
            'review_note', v_note,
            'triage_assertion_id', v_reading.id,
            'triage_version', v_reading.value_norm,
            'resolution_from', v_request.resolution,
            'overrides_dismissal', case when v_request.resolution = 'rejected' then true end,
            'message_id_sha256', coalesce(p_evidence->>'message_id_sha256', v_request.value->>'message_id_sha256'),
            'observed_at', coalesce(v_observed_at, (v_request.value->>'observed_at')::timestamptz),
            'grammar_version', coalesce(p_evidence->>'grammar_version', v_request.value->>'grammar_version'),
            'policy_version', p_evidence->>'policy_version',
            'input_sha256', p_evidence->>'input_sha256')),
          'operator', p_operator_id, p_command_receipt_id)
  returning id into v_event_id;

  return jsonb_build_object('outcome', v_outcome, 'contact_control_id', v_control.id,
                            'contact_control_reason', v_control.reason,
                            'source_record_id', v_source_id, 'assertion_id', v_assertion_id,
                            'event_id', v_event_id);
end;
$$;


comment on function outbound.add_unsubscribe_contact_control(text, text, text, text, uuid, uuid, jsonb) is
  'Internal SECURITY INVOKER implementation of the W10 unsubscribe transaction, including the confirmation of a mail-triage unsubscribe reading (basis triage_reading_confirmed). Direct runtime EXECUTE is revoked; outbound.add_contact_control is the only entry point.';

revoke all on function outbound.add_unsubscribe_contact_control(text, text, text, text, uuid, uuid, jsonb)
  from public, anon, authenticated, service_role, origenlab_api, origenlab_worker, origenlab_migrator;

reset role;
