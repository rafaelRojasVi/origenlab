-- Slice 3 — what the commercial case commands are allowed to say.
--
-- docs/DOMAIN.md §3.6, §3.6.1, §3.6.5; docs/WORKFLOWS.md §1.1 (the stage machine), §W2;
-- docs/DATA.md §1.1 (the audit stream).
--
-- ── Why this migration exists at all ────────────────────────────────────────────────────────
--
-- `20260922170000_slice3_crm_commercial_case.sql` built the three case tables and deliberately
-- stopped there, on a stated rule: *an event type nothing can emit would be a promise, not a
-- contract*. The commands now exist, so this is the other half of that sentence — the audit
-- vocabulary they need, and the two structural rules the command boundary must not be the only
-- thing enforcing.
--
-- It adds no table and no column. The inventory stays at 36.
--
-- ── Three changes, and why each one is here rather than in Python ───────────────────────────
--
-- 1. **The audit vocabulary.** `crm.domain_event` has a closed `aggregate_kind` list, a closed
--    `event_type` list and a validator that ties one to the other. The three case tables are in
--    none of them, so `open_commercial_case` could not have written its own audit trail. Five
--    event types are added and not one more: every one of them is emitted by a command in this
--    same change, so the list keeps describing what the system can actually do.
--
-- 2. **The stage machine.** `docs/WORKFLOWS.md` §1.1 has always been a table in a document, and
--    Slice 0 recorded it as a Slice 2 command trigger. `advance_case_stage` refuses an illegal
--    transition first, with a sentence naming the rule; this repeats the refusal where no code
--    path, no future second writer and no hand-typed UPDATE as `origenlab_api` can miss it.
--    A terminal case is never revived, and a case is born at `lead` — otherwise an INSERT
--    straight into `negotiating` would walk around the whole table.
--
--    Note what this does *not* need to add. "`qualified` requires a human-confirmed requesting
--    institution" is already unrepresentable, by composition of three shipped rules:
--    `opportunity_organization_required_from_qualified` (organization_id NOT NULL from
--    `qualified`), the deferred agreement trigger (organization_id *is* the current
--    `requesting_institution`), and `opportunity_organization_requesting_is_human`
--    (`requesting_institution` ⇒ `confirmed` with a named operator). A fourth guard would only
--    restate them. `supabase/tests/063_*.sql` proves the composition instead of assuming it.
--
-- 3. **Same-day correction.** `opportunity_organization_validity` demanded
--    `valid_to > valid_from`, which made a row opened today impossible to close today — and an
--    operator who mislabels an institution almost always notices within the hour. The rule
--    §3.6.1 actually states is *rows are never deleted; close `valid_to` and open the right
--    row*, and that rule was unreachable on day one. The bound is relaxed to `>=`: a
--    zero-length row says "this reading was opened and withdrawn the same day", which is a
--    true sentence and a visible one. `daterange(d, d, '[)')` is empty, so it overlaps nothing
--    and the exclusion constraint neither loosens nor tightens; the partial unique index for
--    the current requesting institution keys on `valid_to IS NULL` and is untouched.

set role origenlab_owner;

-- ── 1. The audit vocabulary ─────────────────────────────────────────────────────────────────

-- The validator gains three families. Replaced rather than dropped: it is referenced by
-- `domain_event_payload_valid`, and an IMMUTABLE SQL function may be redefined in place.
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
  'CHECK helper for crm.domain_event: payload is an object, payload_version is defined, event_type family matches aggregate_kind — including the three commercial-case families (DOMAIN.md §3.6). SECURITY INVOKER.';

alter table crm.domain_event drop constraint domain_event_aggregate_kind_check;
alter table crm.domain_event add constraint domain_event_aggregate_kind_check check (aggregate_kind in (
  'organization', 'person', 'affiliation', 'address', 'contact_point', 'organization_relationship',
  'opportunity', 'opportunity_participant', 'opportunity_organization', 'opportunity_interest',
  'opportunity_evidence', 'task', 'activity', 'quote', 'quote_revision',
  'campaign', 'send_attempt', 'contact_control', 'send_control', 'operator',
  'assertion', 'source_record', 'product'
));

-- Five new types, and not one more. Each is emitted by a command that exists as of this change:
--
--   case_organization.added          add_case_organization; set_case_organization_role's second half
--   case_organization.role_confirmed set_case_organization_role, confirming a proposal in place
--   case_organization.ended          set_case_organization_role's first half
--   case_interest.added              record_case_interest
--   case_evidence.linked             open_commercial_case (the origin link); link_case_evidence
--
-- Deliberately absent: `case_interest.withdrawn` and `case_evidence.unlinked`. The columns for
-- both exist and no command writes them yet, so their event types stay unwritten too.
--
-- The four `opportunity.*` types were already here and are reused as they stand: a case is an
-- opportunity, so opening one is `opportunity.created`, moving it is `opportunity.staged`,
-- naming its requesting institution is `opportunity.organization_set` and ending it is
-- `opportunity.closed`. A parallel `case.*` family would have been a second vocabulary for one
-- aggregate.
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
  'campaign.transitioned', 'campaign.approved', 'campaign.override_granted', 'campaign.dry_run_recorded',
  'send_attempt.submission_changed', 'send_attempt.delivery_changed',
  'contact_control.added', 'contact_control.revoked',
  'send_control.changed',
  'operator.changed',
  'assertion.promoted', 'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'product.created'
));

comment on column crm.domain_event.event_type is
  'Closed event vocabulary (docs/DATA.md §1.1). The five case_* types are the commercial case (DOMAIN.md §3.6); the four opportunity.* types are reused for it, because a case IS an opportunity and a second vocabulary for one aggregate would be two names for one fact.';

-- ── 2. The stage machine ────────────────────────────────────────────────────────────────────

create function crm.opportunity_stage_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_op = 'INSERT' then
    -- A case is born at `lead`. Without this, an INSERT straight into `negotiating` would walk
    -- around the whole transition table, and a case would exist that never passed any of it.
    if new.stage <> 'lead' then
      raise exception
        'crm.opportunity: a case is opened at stage ''lead'' and moves from there; % is not an opening stage',
        new.stage
        using errcode = 'P0001';
    end if;
    return new;
  end if;

  -- The transition table of WORKFLOWS.md §1.1, as data. Inlined rather than extracted into a
  -- helper function on purpose: PostgreSQL checks EXECUTE on a nested call at *call* time, so a
  -- helper would have to be granted to `origenlab_api` — widening the runtime role's surface to
  -- make a trigger readable. A trigger function itself needs no grant (EXECUTE is checked when
  -- the trigger is created), so keeping the whole rule inside it costs nothing and grants
  -- nothing. `supabase/tests/063_*.sql` drives every pair through real UPDATEs.
  if new.stage <> old.stage and not (new.stage = any (case old.stage
        when 'lead'        then array['qualifying', 'abandoned', 'lost']
        when 'qualifying'  then array['qualified', 'lead', 'abandoned', 'lost']
        when 'qualified'   then array['quoting', 'qualifying', 'abandoned', 'lost']
        when 'quoting'     then array['negotiating', 'qualified', 'abandoned', 'lost']
        when 'negotiating' then array['won', 'lost', 'quoting', 'abandoned']
        -- won, lost, abandoned: terminal. Reopening is a NEW opportunity that references the
        -- old one (§1.1), which is why this list is empty rather than permissive.
        else array[]::text[]
      end))
  then
    if old.stage in ('won', 'lost', 'abandoned') then
      raise exception
        'crm.opportunity %: stage ''%'' is terminal and is never revived — reopening is a new case that references this one',
        old.id, old.stage
        using errcode = 'P0001';
    end if;
    raise exception
      'crm.opportunity %: ''%'' → ''%'' is not an allowed transition (WORKFLOWS.md §1.1)',
      old.id, old.stage, new.stage
      using errcode = 'P0001';
  end if;

  -- Deliberately *not* here: a rule that `version` must advance on every update. Optimistic
  -- concurrency is the command boundary's contract with an operator — it reads a version,
  -- shows it, and writes back under it — and every case command does that with a
  -- `where version = %s` compare-and-set that a database-backed test drives with two live,
  -- overlapping transactions. Making it a trigger as well would put the owner, every
  -- migration and every future data repair under an application's bookkeeping rule, which is
  -- not what the column is for.
  return new;
end
$$;

comment on function crm.opportunity_stage_guard() is
  'WORKFLOWS.md §1.1 — a case opens at `lead`, moves only along the transition table, and is never revived once terminal. SECURITY INVOKER.';

revoke all on function crm.opportunity_stage_guard() from public, anon, authenticated, service_role;

create trigger opportunity_stage_guard
  before insert or update on crm.opportunity
  for each row execute function crm.opportunity_stage_guard();

create trigger opportunity_never_deleted
  before delete on crm.opportunity
  for each row execute function platform.reject_mutation('never deleted (close it with a stage and a motive instead)');

-- ── 3. A case nobody could abandon ──────────────────────────────────────────────────────────
--
-- Found by running the command, not by reading the constraint.
--
-- `opportunity_organization_required_from_qualified` reads
-- `stage IN ('lead', 'qualifying') OR organization_id IS NOT NULL`, which lumps the two exits
-- in with the forward stages. DOMAIN.md §3.3 and §3.6.5 say the organization is mandatory
-- *from `qualified` onward* — and `lost` and `abandoned` are not onward from anything. They are
-- available from `lead` itself (WORKFLOWS.md §1.1), and a case that never found out who was
-- asking is precisely the case an operator abandons.
--
-- As shipped, such a case could be opened and then never closed: `advance_case_stage` to
-- `abandoned` failed on a CHECK, and the only reachable states were `lead` and `qualifying`,
-- forever. Nothing had noticed because nothing had ever moved a case.
--
-- `won` keeps the requirement, and gains nothing from this change: a case cannot be won
-- without a customer, and `opportunity_won_shape` requires a quote revision besides.

alter table crm.opportunity drop constraint opportunity_organization_required_from_qualified;
alter table crm.opportunity add constraint opportunity_organization_required_from_qualified
  check (stage in ('lead', 'qualifying', 'lost', 'abandoned') or organization_id is not null);

comment on column crm.opportunity.organization_id is
  'DOMAIN.md §3.6.1 — the confirmed requesting institution, and only ever that: a DEFERRABLE INITIALLY DEFERRED constraint trigger checks the agreement in both directions. Required from `qualified` onward and for `won`; optional at `lead` and `qualifying` (§3.6.5) and for the two exits, `lost` and `abandoned`, which are reachable from `lead` and must stay reachable for a case that never found out who was asking.';

-- ── 4. Same-day correction ──────────────────────────────────────────────────────────────────

alter table crm.opportunity_organization drop constraint opportunity_organization_validity;
alter table crm.opportunity_organization add constraint opportunity_organization_validity
  check (valid_to is null or valid_to >= valid_from);

comment on column crm.opportunity_organization.valid_to is
  'DOMAIN.md §3.6.1 — NULL means current. Equal to valid_from means the reading was opened and withdrawn the same day: a zero-length daterange, which overlaps nothing and is therefore invisible to the exclusion constraint, while the row itself stays readable forever.';

reset role;
