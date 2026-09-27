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
-- A «BAJA» from an address that is neither known here nor provably the recipient of a recorded
-- outbound message is recorded as an *unresolved* unsubscribe_request: a durable review item that
-- itself holds the exact address out of every audience, freeze and send until an operator
-- confirms it — or, if it was a false positive, an admin dismisses it. No person, contact point or
-- organization is ever created for it.
--
-- This migration adds:
--
--   1. `outbound.add_contact_control` — the closed-list privileged writer of contact_control
--      (ARCHITECTURE.md §6.2) and the owner of the complete unsubscribe transaction: evidence,
--      control (or review hold) and exactly one crm.domain_event, atomically. Implemented for the
--      one kind this slice needs, a marketing unsubscribe; anything else (an admin block, a
--      revoke, a bounce) is refused until its own slice. It asserts the calling login, the
--      operator and the open receipt, validates every argument and proves the basis itself.
--      EXECUTE: origenlab_api only. search_path = pg_catalog, every relation schema-qualified,
--      no dynamic SQL.
--   2. Permanence. An unsubscribe block — and any block an unsubscribe request was linked to — is
--      never updated or deleted, by anyone below superuser. Its evidence (the source record and
--      the assertion) is never updated or deleted, and can only be created by the function above;
--      the only change ever made to it is that function deciding a held request once: confirmed
--      (resolved to its control) or dismissed (rejected, with the decision in its own event).
--      Re-subscribing does not exist, and neither does dismissing anything but a pending hold.
--   3. `outbound.marketing_contact_refusals(campaign_recipient_id)` — the send-time contract for
--      WORKFLOWS.md §2 clauses 4-6, evaluated against the *live* contact controls and review
--      holds, never against the snapshot. A recipient frozen before an unsubscribe is refused by
--      it. SECURITY INVOKER, read-only; nothing calls it to send, because no send path exists.
--   4. Vocabulary: assertion kind 'unsubscribe_request', events 'contact_control.evidence_linked',
--      'assertion.unsubscribe_review_opened' and 'assertion.unsubscribe_review_dismissed', frozen
--      notes 'unsubscribed' and 'unsubscribe_pending_review' (the sub-reasons behind a frozen
--      'block').
--
-- What it deliberately does not do: no table grant widens (the runtime roles still cannot write
-- contact_control or the unsubscribe evidence directly), no RLS policy changes, no send flag
-- moves, no send function exists, no function or foreign key beyond the three named here, and
-- nothing reads Gmail.

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
  'Closed observation vocabulary. document_reference records that a stored document names a commercial subject; unsubscribe_request records that a reply asked for no more marketing: resolved to the outbound.contact_control it created or joined, or unresolved while held for review — an unresolved one blocks its exact address from marketing (written only by outbound.add_contact_control).';

alter table outbound.campaign_recipient drop constraint campaign_recipient_frozen_notes_vocabulary;
alter table outbound.campaign_recipient
  add constraint campaign_recipient_frozen_notes_vocabulary check (
    frozen_notes <@ array[
      'shared_mailbox', 'no_contact_point', 'multiple_institutions', 'institution_mismatch',
      'identity_reviewed_include', 'identity_reviewed_exclude', 'possible_supplier_unreviewed',
      'operator_excluded', 'malformed_address',
      'recontact_approved', 'recontact_kept_excluded', 'recontact_not_reviewed',
      'unsubscribed', 'unsubscribe_pending_review'
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
  'assertion.promoted', 'assertion.unsubscribe_review_opened', 'assertion.unsubscribe_review_dismissed',
  'source_record.quarantined', 'source_record.migration_manifest_recorded',
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
  -- outbound.add_contact_control (whose current_user is its owner) and never changed afterwards —
  -- with one exception, also only inside that function: a request held for review is decided
  -- exactly once. Confirmed, it resolves to the control it created or joined (and its record goes
  -- from pending to promoted); dismissed, it becomes rejected with no target (and its record goes
  -- from pending to reviewed). Every other column stays as it was received. Nothing else moves:
  -- a decided request is never decided again, and a confirmed one never becomes rejected.
  if tg_op = 'INSERT' then
    if current_user <> 'origenlab_owner' then
      raise exception '%.%: unsubscribe evidence is written only by outbound.add_contact_control', tg_table_schema, tg_table_name
        using errcode = '42501';
    end if;
    return new;
  end if;
  -- (Nested IFs: each branch names only the columns of its own table.)
  if tg_op = 'UPDATE' and current_user = 'origenlab_owner' then
    if tg_table_name = 'assertion' then
      if old.kind = 'unsubscribe_request' and old.resolution = 'unresolved'
         and new.resolution in ('promoted', 'linked') and new.resolved_kind = 'contact_control'
         and (to_jsonb(new) - array['resolution', 'resolved_kind', 'resolved_id', 'resolved_at', 'resolved_by_operator_id', 'updated_at'])
           = (to_jsonb(old) - array['resolution', 'resolved_kind', 'resolved_id', 'resolved_at', 'resolved_by_operator_id', 'updated_at']) then
        return new;
      end if;
      if old.kind = 'unsubscribe_request' and old.resolution = 'unresolved'
         and new.resolution = 'rejected' and new.resolved_kind is null and new.resolved_id is null
         and new.resolved_at is not null and new.resolved_by_operator_id is not null
         and (to_jsonb(new) - array['resolution', 'resolved_at', 'resolved_by_operator_id', 'updated_at'])
           = (to_jsonb(old) - array['resolution', 'resolved_at', 'resolved_by_operator_id', 'updated_at']) then
        return new;
      end if;
    elsif tg_table_name = 'source_record' then
      if old.review_status = 'pending' and new.review_status in ('promoted', 'reviewed')
         and (to_jsonb(new) - array['review_status', 'updated_at']) = (to_jsonb(old) - array['review_status', 'updated_at']) then
        return new;
      end if;
    end if;
  end if;
  raise exception '%.%: % refused — unsubscribe evidence is immutable', tg_table_schema, tg_table_name, tg_op
    using errcode = 'P0001';
end;
$$;

comment on function outbound.unsubscribe_permanent() is
  'Trigger guard (WORKFLOWS.md §W10): an unsubscribe block, and any block an unsubscribe request is linked to, is never updated or deleted; unsubscribe evidence is inserted only by outbound.add_contact_control and never changed, except that a request held for review is decided once, inside that function: resolved to its control, or dismissed (rejected, no target). SECURITY INVOKER.';

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
-- One call is one complete unsubscribe transaction: the immutable evidence, the permanent control
-- (or the durable review hold) and exactly one crm.domain_event commit together or not at all. It
-- is a single SQL statement from the caller's side, so a failure anywhere inside leaves nothing.
--
-- p_evidence->>'basis' says why the address may be suppressed, and the function proves it itself:
--
--   known_address     the address is already known here (a contact control, a campaign
--                     recipient or a CRM email contact point) — checked below.
--   outbound_lineage  the reply's In-Reply-To names a recorded outbound comms.message whose
--                     to/cc/bcc recipients include exactly this address — checked below. No
--                     person, contact point or organization is created: the control is enough.
--   pending_review    neither could be proven (review_reason lineage_missing or
--                     recipient_mismatch): the evidence is recorded with an *unresolved*
--                     unsubscribe_request. That unresolved request is itself the hold — audience
--                     preview, freeze and outbound.marketing_contact_refusals all refuse the exact
--                     address — and no contact_control is written until an operator reviews it.
--   review_confirmed  an operator reviewed a pending request (assertion_id, note): the control is
--                     created or linked and the request resolved to it. Confirming again is a
--                     no-op.
--   review_dismissed  an *admin* reviewed a pending request and found it a false positive
--                     (assertion_id, review_sha256, explanation): the request becomes rejected, its
--                     record reviewed, and one assertion.unsubscribe_review_dismissed event records
--                     the decision, the operator and the explanation. No control is written or
--                     changed. Only a pending hold can be dismissed: a confirmed request, a
--                     contact control or a request already decided is refused, and so is a
--                     review_sha256 that is not the request's current one (a stale screen). The
--                     reply evidence and the request's own fields stay exactly as received.
--
-- review_sha256 is the review's version: sha256 over 'unsubscribe-review/v1', the request's id,
-- address, resolution, source record id, the record's payload_sha256 and the request's value,
-- '|'-joined. The suppressions read serves the same expression.
--
-- For the first three, p_evidence is the reply as the API staged it:
--   message_id_sha256  sha256 of the normalized RFC 822 Message-ID (the dedupe identity)
--   observed_at        when the mailbox received it (ISO 8601 with offset)
--   grammar_version    the reply grammar that accepted it
--   policy_version     the unsubscribe policy that chose the basis
--   input_sha256       the batch fingerprint the operator confirmed
--   payload            the stored message: body_text, body_sha256, message_id, from_address,
--                      in_reply_to, ...
-- The function recomputes body_sha256 itself and trusts neither the hash nor the basis.
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
                                                    'review_dismissed')
     or coalesce(p_evidence->>'policy_version', '') !~ '^[a-z0-9][a-z0-9._/-]{2,79}$' then
    raise exception 'outbound.add_contact_control: malformed evidence' using errcode = '22023';
  end if;
  v_basis := p_evidence->>'basis';
  v_command := case v_basis when 'review_confirmed' then 'resolve-unsubscribe-review'
                            when 'review_dismissed' then 'dismiss-unsubscribe-review'
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
    if v_request.resolution <> 'unresolved' then
      raise exception 'outbound.add_contact_control: the request is %, not pending', v_request.resolution using errcode = 'P0001';
    end if;
    v_source_id := v_request.source_record_id;
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
     where id = v_source_id and review_status = 'pending';
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
              'lineage_message_id', v_lineage_id)),
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

comment on function outbound.add_contact_control(text, text, text, text, uuid, uuid, jsonb) is
  'ARCHITECTURE.md §6.2 closed list — the privileged writer of outbound.contact_control and the owner of the complete unsubscribe transaction. Implemented for (block, marketing, unsubscribe) only: records the reply as immutable evidence, then the permanent suppression (a known address, or proven outbound lineage to this exact recipient), or an unresolved request that holds the address for review, or the confirmation of such a review, or an admin''s dismissal of a pending one (never of a confirmed unsubscribe) — and exactly one crm.domain_event, atomically. session_user must be origenlab_api. SECURITY DEFINER, search_path = pg_catalog, no dynamic SQL.';

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
      -- Only an unresolved request holds; one an admin dismissed (rejected) no longer does. Every
      -- permanent unsubscribe above still refuses, whatever happened to any review.
      (select 'unsubscribe_pending_review' from r where exists (
          select 1 from evidence.assertion a
           where a.kind = 'unsubscribe_request' and a.resolution = 'unresolved' and a.value_norm = r.address_norm)),
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
  'WORKFLOWS.md §2 clauses 4-6 for one campaign recipient, against the live contact controls (never the frozen snapshot): unsubscribe, unsubscribe_pending_review (an unresolved «BAJA» held for review), block, block_domain, cooldown, prior_contact without a W12 override; plus not_snapshotted / recipient_unknown. Empty = those clauses hold. Every future send step must call it; a snapshot never makes a recipient sendable. SECURITY INVOKER, read-only.';

revoke all on function outbound.marketing_contact_refusals(uuid) from public, anon, authenticated, service_role;
grant execute on function outbound.marketing_contact_refusals(uuid) to origenlab_api, origenlab_worker;

reset role;
