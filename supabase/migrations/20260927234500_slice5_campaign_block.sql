-- Slice 5 — campaign safety blocks: a durable, audited, admin-only pause that every campaign
-- boundary is refused by, and the September wave-2 incident hold recorded as one.
--
-- docs/WORKFLOWS.md §1.3 (campaign status), §2 (the send predicate, clause 2), §W4 (the campaign
-- lifecycle), §W13 (campaign blocks); docs/DOMAIN.md §7 #37.
--
-- `outbound.campaign.status = 'paused'` exists in the vocabulary and nothing enforces it: no status
-- machine is built, and the V1 incident of 2026-09-21 had to be contained by blocking recipients one
-- by one instead. A block is not a status. It is a separate, append-only fact with its own author,
-- reason and lifecycle, so placing or lifting one never rewrites a campaign row, a frozen snapshot
-- or a send attempt, and a campaign's status machine (still unbuilt) can never lift it by accident.
--
-- This migration adds:
--
--   1. `outbound.campaign_block` (#37) — one row per block. Three scopes: one V2 campaign
--      (`campaign`), every campaign (`all_campaigns`), or a campaign that lives in the V1 ledger
--      and is known here only by its V1 key (`legacy_campaign`). A block is active until an admin
--      lifts it; there is no expiry column, so nothing can silently expire one.
--   2. `outbound.campaign_block_guard` — the row's lifecycle: never deleted; inserted active and
--      only by an active admin operator (or by a migration, as the owner); updated exactly once,
--      to lift it, by an active admin with a reason, and immutable after that. The database is the
--      clock for placed_at and lifted_at.
--   3. `outbound.campaign_hold_refusals(campaign_id)` — the campaign-level refusals: an active
--      block on the campaign, an active block on every campaign, the campaign being `paused`.
--      `outbound.marketing_contact_refusals` now appends them, so the one send-time contract every
--      future send step must call refuses a blocked campaign's recipients too.
--   4. `outbound.campaign_hold_guard` — the same refusals, enforced by triggers on every write that
--      moves a campaign towards a send: freezing its audience, approving or activating it,
--      recording a dry run or an approval, reserving a recipient, and creating or dispatching a
--      marketing send attempt. Recording the outcome of an attempt already in flight is never
--      refused (WORKFLOWS.md §2.1).
--   5. Vocabulary: aggregate 'campaign_block', events 'campaign_block.placed' and
--      'campaign_block.lifted'.
--   6. The September wave-2 incident hold (2026-09-21), as an active `legacy_campaign` block on
--      V1 campaign `septiembre18-2026-wave2`, placed by this migration. It carries no event: a
--      migration writes no row into crm.domain_event (the zero-business-row foundation check), so
--      the row records its own provenance (placed_by_kind 'migrator', reference, reason).
--
-- What it deliberately does not do: no send flag moves, no campaign status changes, no recipient,
-- snapshot or attempt is touched, no SECURITY DEFINER function is added (the API writes the table
-- through its own grant; the guard proves the operator is an active admin), and nothing reads Gmail.

set role origenlab_owner;

-- ── 1. the table ──────────────────────────────────────────────────────────────────────────────
create table outbound.campaign_block (
  id uuid primary key default gen_random_uuid(),
  scope text not null,
  campaign_id uuid references outbound.campaign (id),
  legacy_campaign_key text,
  reason text not null,
  reference text,
  placed_at timestamptz not null default now(),
  placed_by_kind text not null,
  placed_by_operator_id uuid references platform.operator (id),
  lifted_at timestamptz,
  lifted_by_operator_id uuid references platform.operator (id),
  lift_reason text,
  version integer not null default 1,
  constraint campaign_block_scope_check check (scope in ('campaign', 'legacy_campaign', 'all_campaigns')),
  constraint campaign_block_target_shape check (
    case scope
      when 'campaign'        then campaign_id is not null and legacy_campaign_key is null
      when 'legacy_campaign' then campaign_id is null and legacy_campaign_key is not null
      when 'all_campaigns'   then campaign_id is null and legacy_campaign_key is null
    end
  ),
  constraint campaign_block_legacy_key_shape check (
    legacy_campaign_key is null or legacy_campaign_key ~ '^[a-z0-9][a-z0-9._-]{2,79}$'
  ),
  constraint campaign_block_reason_nonblank check (length(btrim(reason, E' \t\r\n')) > 0 and length(reason) <= 2000),
  constraint campaign_block_reference_shape check (reference is null or reference ~ '^[a-z0-9][a-z0-9._-]{2,79}$'),
  constraint campaign_block_placed_by_kind_check check (placed_by_kind in ('operator', 'migrator')),
  constraint campaign_block_placed_by_shape check ((placed_by_kind = 'operator') = (placed_by_operator_id is not null)),
  -- Lifting is all-or-none: when, who and why. Only an operator lifts; a migration never does.
  constraint campaign_block_lift_shape check (
    num_nonnulls(lifted_at, lifted_by_operator_id, lift_reason) in (0, 3)
  ),
  constraint campaign_block_lift_reason_nonblank check (
    lift_reason is null or (length(btrim(lift_reason, E' \t\r\n')) > 0 and length(lift_reason) <= 2000)
  ),
  constraint campaign_block_lift_after_place check (lifted_at is null or lifted_at >= placed_at),
  -- The compare-and-set token of the lift: 1 while active, 2 once lifted, never anything else.
  constraint campaign_block_version_shape check ((version = 2) = (lifted_at is not null) and version in (1, 2))
);

comment on table outbound.campaign_block is
  'DOMAIN.md §7 #37 — campaign safety blocks: an active row refuses every freeze, approval, dry run, reservation and dispatch of the campaigns it covers (outbound.campaign_hold_refusals). Placed and lifted only by an active admin (or placed by a migration); never deleted, never expires, immutable once lifted.';
comment on column outbound.campaign_block.legacy_campaign_key is
  'The V1 campaign_id of a campaign that lives in the V1 SQLite ledger (scope legacy_campaign). No V2 campaign row carries this key; the block is the durable V2 record of a hold on it.';
comment on column outbound.campaign_block.version is
  'Compare-and-set token of unblock-campaign: 1 while active, 2 once lifted.';

-- At most one active block per target. A second block on a blocked target is refused, not stacked.
create unique index campaign_block_one_active_per_target
  on outbound.campaign_block (scope, coalesce(campaign_id::text, legacy_campaign_key, '*'))
  where lifted_at is null;

-- Covering indexes for the three foreign keys (supabase/tests/090_foreign_key_indexes.sql).
create index campaign_block_campaign_idx on outbound.campaign_block (campaign_id);
create index campaign_block_placed_by_operator_idx on outbound.campaign_block (placed_by_operator_id);
create index campaign_block_lifted_by_operator_idx on outbound.campaign_block (lifted_by_operator_id);

alter table outbound.campaign_block enable row level security;

grant select, insert on outbound.campaign_block to origenlab_api;
grant update (lifted_at, lifted_by_operator_id, lift_reason, version) on outbound.campaign_block to origenlab_api;
grant select on outbound.campaign_block to origenlab_worker;

create policy origenlab_api_select on outbound.campaign_block for select to origenlab_api using (true);
create policy origenlab_api_insert on outbound.campaign_block for insert to origenlab_api with check (true);
create policy origenlab_api_update on outbound.campaign_block for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on outbound.campaign_block for select to origenlab_worker using (true);

-- ── 2. the block's own lifecycle ──────────────────────────────────────────────────────────────
create function outbound.campaign_block_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v_operator uuid;
begin
  if tg_op = 'DELETE' then
    raise exception 'a campaign block is never deleted; an admin lifts it with unblock-campaign'
      using errcode = 'P0001';
  end if;

  if tg_op = 'INSERT' then
    if new.lifted_at is not null or new.lifted_by_operator_id is not null or new.lift_reason is not null
       or new.version <> 1 then
      raise exception 'a campaign block is inserted active; lifting it is its own command'
        using errcode = 'P0001';
    end if;
    if new.placed_by_kind = 'migrator' and current_user <> 'origenlab_owner' then
      raise exception 'only a migration (as origenlab_owner) places a block without an operator'
        using errcode = '42501';
    end if;
    new.placed_at := now();
    v_operator := new.placed_by_operator_id;
  else
    if old.lifted_at is not null then
      raise exception 'campaign block % was lifted and is immutable', old.id
        using errcode = 'P0001';
    end if;
    if new.id <> old.id or new.scope <> old.scope
       or new.campaign_id is distinct from old.campaign_id
       or new.legacy_campaign_key is distinct from old.legacy_campaign_key
       or new.reason <> old.reason or new.reference is distinct from old.reference
       or new.placed_at <> old.placed_at or new.placed_by_kind <> old.placed_by_kind
       or new.placed_by_operator_id is distinct from old.placed_by_operator_id then
      raise exception 'a campaign block''s placement is write-once; only the lift may be recorded'
        using errcode = 'P0001';
    end if;
    if new.lifted_at is null or new.version <> old.version + 1 then
      raise exception 'the only change to an active campaign block is lifting it, at version %', old.version + 1
        using errcode = 'P0001';
    end if;
    new.lifted_at := now();
    v_operator := new.lifted_by_operator_id;
  end if;

  if v_operator is not null and not exists (
       select 1 from platform.operator o
        where o.id = v_operator and o.role = 'admin' and o.status = 'active') then
    raise exception 'only an active admin places or lifts a campaign block'
      using errcode = '42501';
  end if;
  return new;
end;
$$;

comment on function outbound.campaign_block_guard() is
  'Lifecycle of outbound.campaign_block: never deleted; inserted active by an active admin (or a migration as the owner); lifted exactly once by an active admin; immutable afterwards. placed_at and lifted_at are the database clock. SECURITY INVOKER.';

revoke all on function outbound.campaign_block_guard() from public, anon, authenticated, service_role;

create trigger campaign_block_guard
  before insert or update or delete on outbound.campaign_block
  for each row execute function outbound.campaign_block_guard();

-- ── 3. the campaign-level refusals ────────────────────────────────────────────────────────────
create function outbound.campaign_hold_refusals(p_campaign_id uuid) returns text[]
language sql
stable
set search_path = pg_catalog
as $$
  select case
    when not exists (select 1 from outbound.campaign c where c.id = p_campaign_id)
      then array['campaign_unknown']::text[]
    else array_remove(array[
      (select 'all_campaigns_blocked' where exists (
          select 1 from outbound.campaign_block b where b.scope = 'all_campaigns' and b.lifted_at is null)),
      (select 'campaign_blocked' where exists (
          select 1 from outbound.campaign_block b
           where b.scope = 'campaign' and b.campaign_id = p_campaign_id and b.lifted_at is null)),
      (select 'campaign_paused' where exists (
          select 1 from outbound.campaign c where c.id = p_campaign_id and c.status = 'paused'))
    ]::text[], null)
  end
$$;

comment on function outbound.campaign_hold_refusals(uuid) is
  'WORKFLOWS.md §W13 — the campaign-level refusals for one campaign: all_campaigns_blocked, campaign_blocked (active outbound.campaign_block rows), campaign_paused (status), or campaign_unknown. Empty = no hold. Enforced by outbound.campaign_hold_guard and appended to outbound.marketing_contact_refusals. SECURITY INVOKER, read-only.';

revoke all on function outbound.campaign_hold_refusals(uuid) from public, anon, authenticated, service_role;
grant execute on function outbound.campaign_hold_refusals(uuid) to origenlab_api, origenlab_worker;

-- The send-time contract (20260927230000) gains the campaign-level refusals. Everything before the
-- final concatenation is the W10 body, unchanged.
create or replace function outbound.marketing_contact_refusals(p_campaign_recipient_id uuid) returns text[]
language sql
stable
set search_path = pg_catalog
as $$
  with r as (
    select id, campaign_id, address_norm, split_part(address_norm, '@', 2) as domain, state, recontact_override_at
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
    ]::text[], null) || (select outbound.campaign_hold_refusals(r.campaign_id) from r)
  end
$$;

comment on function outbound.marketing_contact_refusals(uuid) is
  'WORKFLOWS.md §2 clauses 2 and 4-6 for one campaign recipient, against the live contact controls and campaign blocks (never the frozen snapshot): unsubscribe, unsubscribe_pending_review (an unresolved «BAJA» held for review), block, block_domain, cooldown, prior_contact without a W12 override, and the campaign-level all_campaigns_blocked / campaign_blocked / campaign_paused (outbound.campaign_hold_refusals); plus not_snapshotted / recipient_unknown. Empty = those clauses hold. Every future send step must call it; a snapshot never makes a recipient sendable. SECURITY INVOKER, read-only.';

-- ── 4. enforcement on every write that moves a campaign towards a send ────────────────────────
create function outbound.campaign_hold_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v_campaign uuid;
  v_step text;
  v_refusals text[];
begin
  if tg_table_schema = 'outbound' and tg_table_name = 'campaign' then
    -- Freezing the audience, approving, activating. Planning, drafting and archiving are not
    -- steps towards a send and stay possible; a blocked campaign can still be cancelled.
    if (old.audience_frozen_at is null and new.audience_frozen_at is not null)
       or (new.status is distinct from old.status and new.status in ('audience_frozen', 'approved', 'active'))
       or (old.approved_at is null and new.approved_at is not null) then
      v_campaign := new.id;
      v_step := case
        when new.status is distinct from old.status and new.status in ('approved', 'active')
          then 'moving the campaign to ' || new.status
        when old.approved_at is null and new.approved_at is not null then 'approving the campaign'
        else 'freezing the audience'
      end;
    end if;
  elsif tg_table_schema = 'outbound' and tg_table_name = 'campaign_recipient' then
    if new.state = 'reserved' and old.state is distinct from 'reserved' then
      v_campaign := new.campaign_id;
      v_step := 'reserving a recipient';
    end if;
  elsif tg_table_schema = 'outbound' and tg_table_name = 'send_attempt' then
    if new.purpose = 'marketing' and (
         tg_op = 'INSERT'
         or (new.submission_state = 'dispatching' and old.submission_state is distinct from 'dispatching')) then
      v_campaign := new.campaign_id;
      v_step := case when tg_op = 'INSERT' then 'creating a send attempt' else 'dispatching a send attempt' end;
    end if;
  elsif tg_table_schema = 'crm' and tg_table_name = 'domain_event' then
    if new.aggregate_kind = 'campaign'
       and new.event_type in ('campaign.dry_run_recorded', 'campaign.approved', 'campaign.audience_frozen') then
      v_campaign := new.aggregate_id;
      v_step := 'recording ' || new.event_type;
    end if;
  end if;

  if v_campaign is null then
    return new;
  end if;

  v_refusals := outbound.campaign_hold_refusals(v_campaign);
  -- A status move out of `paused` is the status machine's business, not a hold; only blocks
  -- refuse it. Every other step is refused by a paused campaign too.
  if tg_table_name = 'campaign' then
    v_refusals := array_remove(v_refusals, 'campaign_paused');
  end if;
  v_refusals := array_remove(v_refusals, 'campaign_unknown');
  if cardinality(v_refusals) > 0 then
    raise exception 'campaign % is held (%): % is refused while a campaign block is active',
      v_campaign, array_to_string(v_refusals, ', '), v_step
      using errcode = 'P0001', hint = 'an admin lifts a block with unblock-campaign';
  end if;
  return new;
end;
$$;

comment on function outbound.campaign_hold_guard() is
  'WORKFLOWS.md §W13 — refuses, while outbound.campaign_hold_refusals is non-empty, every write that moves a campaign towards a send: audience freeze, approval, activation, a dry-run/approval/freeze event, reserving a recipient, creating or dispatching a marketing attempt. Never refuses recording an in-flight attempt''s outcome. SECURITY INVOKER.';

revoke all on function outbound.campaign_hold_guard() from public, anon, authenticated, service_role;

create trigger campaign_hold_guard
  before update on outbound.campaign
  for each row execute function outbound.campaign_hold_guard();

create trigger campaign_hold_guard
  before update on outbound.campaign_recipient
  for each row execute function outbound.campaign_hold_guard();

create trigger campaign_hold_guard
  before insert or update on outbound.send_attempt
  for each row execute function outbound.campaign_hold_guard();

create trigger campaign_hold_guard
  before insert on crm.domain_event
  for each row execute function outbound.campaign_hold_guard();

-- ── 5. vocabulary ─────────────────────────────────────────────────────────────────────────────
create or replace function crm.domain_event_is_valid(
  p_aggregate_kind text,
  p_event_type text,
  p_payload_version smallint,
  p_payload jsonb
) returns boolean
language sql
immutable
set search_path = pg_catalog
as $$
  select p_payload is not null
     and jsonb_typeof(p_payload) = 'object'
     and p_payload_version = 1
     and split_part(p_event_type, '.', 1) = case p_aggregate_kind
           when 'organization'              then 'organization'
           when 'person'                    then 'person'
           when 'affiliation'               then 'affiliation'
           when 'address'                   then 'address'
           when 'contact_point'             then 'contact_point'
           when 'organization_relationship' then 'relationship'
           when 'opportunity'               then 'opportunity'
           when 'opportunity_participant'   then 'participant'
           when 'opportunity_organization'  then 'case_organization'
           when 'opportunity_interest'      then 'case_interest'
           when 'opportunity_evidence'      then 'case_evidence'
           when 'task'                      then 'task'
           when 'activity'                  then 'activity'
           when 'quote'                     then 'quote'
           when 'quote_revision'            then 'quote_revision'
           when 'campaign'                  then 'campaign'
           when 'campaign_block'            then 'campaign_block'
           when 'send_attempt'              then 'send_attempt'
           when 'contact_control'           then 'contact_control'
           when 'send_control'              then 'send_control'
           when 'operator'                  then 'operator'
           when 'assertion'                 then 'assertion'
           when 'source_record'             then 'source_record'
           when 'product'                   then 'product'
         end;
$$;

comment on function crm.domain_event_is_valid(text, text, smallint, jsonb) is
  'CHECK helper for crm.domain_event: payload is an object, payload_version is defined, event_type family matches aggregate_kind — including the three commercial-case families (DOMAIN.md §3.6) and campaign_block (§7 #37). SECURITY INVOKER.';

alter table crm.domain_event drop constraint domain_event_aggregate_kind_check;
alter table crm.domain_event add constraint domain_event_aggregate_kind_check check (aggregate_kind in (
  'organization', 'person', 'affiliation', 'address', 'contact_point', 'organization_relationship',
  'opportunity', 'opportunity_participant', 'opportunity_organization', 'opportunity_interest',
  'opportunity_evidence', 'task', 'activity', 'quote', 'quote_revision',
  'campaign', 'campaign_block', 'send_attempt', 'contact_control', 'send_control', 'operator',
  'assertion', 'source_record', 'product'
));

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
  'campaign_block.placed', 'campaign_block.lifted',
  'send_attempt.submission_changed', 'send_attempt.delivery_changed',
  'contact_control.added', 'contact_control.revoked', 'contact_control.evidence_linked',
  'send_control.changed',
  'operator.changed',
  'assertion.promoted', 'assertion.unsubscribe_review_opened', 'assertion.unsubscribe_review_dismissed',
  'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'product.created'
));

-- ── 6. the September wave-2 incident hold ─────────────────────────────────────────────────────
-- Contained in the V1 ledger on 2026-09-21T02:40:32Z by blocking its 279 remaining candidates one
-- by one, because no campaign pause existed. This is that hold as a campaign-level fact. It names
-- the campaign by its V1 key and carries no address.
insert into outbound.campaign_block (scope, legacy_campaign_key, reason, reference, placed_by_kind)
values (
  'legacy_campaign',
  'septiembre18-2026-wave2',
  'Retención por incidente del 2026-09-21: la segunda ola de la campaña de septiembre de 2026 queda '
  'detenida. Sus 279 candidatos restantes se contuvieron en el registro V1 (estado blocked, '
  'incident_hold_september_2026) porque no existía una pausa de campaña. Sólo un comando explícito de '
  'un administrador la levanta; no vence.',
  'incident_hold_september_2026',
  'migrator'
);

reset role;
