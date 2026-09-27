-- Slice 5 (second step) — audience freeze: a draft campaign's content and audience become one
-- immutable recipient snapshot.
--
-- docs/DOMAIN.md §7 #20, #21; docs/WORKFLOWS.md §1.3-1.4, §W4 step 2 (`freeze_audience`).
--
-- The freeze is W4 step 2 and nothing after it: `draft → audience_frozen`, recipients inserted
-- `snapshotted` or `excluded`. It approves nothing, reserves nothing and sends nothing; no send
-- flag moves and no grant widens (the runtime role already holds INSERT on campaign_recipient
-- and UPDATE on campaign). What this migration adds is what makes the snapshot a record rather
-- than a starting point for the send lifecycle:
--
--   1. outbound.campaign — `audience_policy_version` (which eligibility rules produced the
--      snapshot) and `audience_sha256` (a fingerprint over the recipient snapshot), set with the
--      rest of the freeze facts; and a key (id, content_sha256, audience_policy_version) the
--      recipient rows reference, so a row cannot claim a content or policy its campaign lacks.
--   2. Freeze facts are write-once. Once `content_frozen_at` or `audience_frozen_at` is set,
--      content, criteria, fingerprints and policy version are never rewritten, and a campaign
--      never returns to `draft`. A changed draft or audience is a new campaign and a new freeze
--      (the editor's «Nueva versión», create-campaign-draft with duplicated_from_campaign_id).
--   3. outbound.campaign_recipient — the snapshot columns: the inclusion decision and every
--      reason at freeze time (`frozen_inclusion`, `frozen_reasons`, `frozen_notes`), relevance
--      kept apart from permission (`relevance`, `interest_evidence`, `evidence_observed_at`),
--      the identity-review decision, and the campaign version, content fingerprint and policy
--      version it was frozen against. `state` and `exclusion_reasons` stay the live send-
--      lifecycle columns (§1.4) and are not what the snapshot means.
--   4. A snapshot row is inserted only while its campaign is a draft, and is never updated in
--      its snapshot columns and never deleted. Rows without snapshot facts (the archived V1
--      campaign's import) are untouched by all three rules.
--   5. The audit event 'campaign.audience_frozen'.

set role origenlab_owner;

-- ── 1. campaign: policy version and audience fingerprint ─────────────────────────────────────
alter table outbound.campaign
  add column audience_policy_version text,
  add column audience_sha256 text;

alter table outbound.campaign
  add constraint campaign_audience_policy_version_shape
    check (audience_policy_version is null or audience_policy_version ~ '^[a-z0-9][a-z0-9._/-]{2,79}$'),
  add constraint campaign_audience_sha256_shape
    check (audience_sha256 is null or audience_sha256 ~ '^[0-9a-f]{64}$'),
  add constraint campaign_audience_snapshot_together
    check ((audience_policy_version is null) = (audience_sha256 is null)),
  add constraint campaign_audience_snapshot_needs_criteria
    check (audience_policy_version is null or audience_criteria is not null),
  -- From audience_frozen onward the snapshot facts are present; the same carve-out as
  -- campaign_content_shape (draft has not frozen; the archived V1 campaign predates V2).
  add constraint campaign_audience_snapshot_shape check (
    status in ('draft', 'cancelled', 'archived') or audience_policy_version is not null
  ),
  add constraint campaign_freeze_key unique (id, content_sha256, audience_policy_version);

comment on column outbound.campaign.content_sha256 is
  'sha256 over subject \0 preheader \0 body_text \0 body_html (an absent part is the empty string); set with content_frozen_at at audience freeze and never rewritten.';
comment on column outbound.campaign.audience_policy_version is
  'The eligibility policy the frozen audience was evaluated under (application constant, e.g. marketing-audience/2026-09-27.v1). Write-once.';
comment on column outbound.campaign.audience_sha256 is
  'sha256 over the canonical serialization of the recipient snapshot (every row, sorted by address). Write-once.';

-- ── 2. freeze facts are write-once ───────────────────────────────────────────────────────────
create function outbound.campaign_freeze_facts_write_once() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if old.status <> 'draft' and new.status = 'draft' then
    raise exception 'a campaign never returns to draft (status is %); a changed campaign is a new draft', old.status
      using errcode = 'P0001';
  end if;
  if (old.content_frozen_at is not null or old.audience_frozen_at is not null)
     and (new.subject                   is distinct from old.subject
       or new.preheader                 is distinct from old.preheader
       or new.body_text                 is distinct from old.body_text
       or new.body_html                 is distinct from old.body_html
       or new.content_sha256            is distinct from old.content_sha256
       or new.content_frozen_at         is distinct from old.content_frozen_at
       or new.audience_criteria         is distinct from old.audience_criteria
       or new.audience_criteria_version is distinct from old.audience_criteria_version
       or new.audience_frozen_at        is distinct from old.audience_frozen_at
       or new.audience_policy_version   is distinct from old.audience_policy_version
       or new.audience_sha256           is distinct from old.audience_sha256
       or new.recontact_interval_days   is distinct from old.recontact_interval_days) then
    raise exception 'a frozen campaign''s content and audience are never rewritten; freeze a new draft instead'
      using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger campaign_freeze_facts_write_once
  before update on outbound.campaign
  for each row execute function outbound.campaign_freeze_facts_write_once();

-- ── 3. recipient snapshot columns ────────────────────────────────────────────────────────────
alter table outbound.campaign_recipient
  add column frozen_at            timestamptz,
  add column frozen_inclusion     text,
  add column frozen_reasons       text[],
  add column frozen_notes         text[],
  add column relevance            text,
  add column interest_evidence    jsonb,
  add column evidence_observed_at timestamptz,
  add column identity_review      jsonb,
  add column campaign_version     integer,
  add column content_sha256       text,
  add column policy_version       text;

alter table outbound.campaign_recipient
  add constraint campaign_recipient_snapshot_together check (
    num_nonnulls(frozen_at, frozen_inclusion, frozen_reasons, frozen_notes, relevance,
                 interest_evidence, campaign_version, content_sha256, policy_version) in (0, 9)
  ),
  add constraint campaign_recipient_frozen_inclusion_check
    check (frozen_inclusion is null or frozen_inclusion in ('included', 'excluded')),
  add constraint campaign_recipient_frozen_reasons_vocabulary check (
    frozen_reasons <@ array[
      'block', 'prior_contact', 'cooldown', 'policy_supplier',
      'policy_no_channel', 'precheck_block', 'precheck_switch',
      'invalid_address', 'policy_internal_domain', 'block_domain',
      'prior_reply', 'policy_noise_address', 'policy_noise_organization',
      'manual_inactive', 'manual_hold', 'already_in_audience'
    ]::text[]
  ),
  add constraint campaign_recipient_frozen_reasons_no_nulls
    check (frozen_reasons is null or array_position(frozen_reasons, null) is null),
  add constraint campaign_recipient_frozen_inclusion_shape
    check (frozen_inclusion is null or (frozen_inclusion = 'excluded') = (cardinality(frozen_reasons) > 0)),
  -- Facts shown beside the decision that do not by themselves exclude, and the sub-reasons
  -- behind a generic exclusion code (manual_hold for an unreviewed supplier candidate).
  add constraint campaign_recipient_frozen_notes_vocabulary check (
    frozen_notes <@ array[
      'shared_mailbox', 'no_contact_point', 'multiple_institutions', 'institution_mismatch',
      'identity_reviewed_include', 'identity_reviewed_exclude', 'possible_supplier_unreviewed',
      'operator_excluded', 'malformed_address'
    ]::text[]
  ),
  add constraint campaign_recipient_frozen_notes_no_nulls
    check (frozen_notes is null or array_position(frozen_notes, null) is null),
  -- Relevance is not permission and has no "low": either evidence exists, or «Sin información».
  add constraint campaign_recipient_relevance_check
    check (relevance is null or relevance in ('evidenced', 'sin_informacion')),
  add constraint campaign_recipient_interest_evidence_array
    check (interest_evidence is null or jsonb_typeof(interest_evidence) = 'array'),
  add constraint campaign_recipient_relevance_shape check (
    relevance is null
    or (relevance = 'evidenced' and jsonb_array_length(interest_evidence) > 0)
    or (relevance = 'sin_informacion' and jsonb_array_length(interest_evidence) = 0
        and evidence_observed_at is null)
  ),
  add constraint campaign_recipient_identity_review_object
    check (identity_review is null or jsonb_typeof(identity_review) = 'object'),
  add constraint campaign_recipient_campaign_version_positive
    check (campaign_version is null or campaign_version >= 1),
  -- The row names the content and policy its campaign was frozen with, or none at all.
  add constraint campaign_recipient_frozen_against_campaign_fkey
    foreign key (campaign_id, content_sha256, policy_version)
    references outbound.campaign (id, content_sha256, audience_policy_version);

-- Every foreign key is index-covered (20260906200836, supabase/tests/090).
create index campaign_recipient_frozen_against_campaign_idx
  on outbound.campaign_recipient (campaign_id, content_sha256, policy_version);

comment on column outbound.campaign_recipient.frozen_inclusion is
  'The freeze decision: included (state snapshotted) or excluded (state excluded, frozen_reasons non-empty). Write-once; state may later move with the send lifecycle, this never does.';
comment on column outbound.campaign_recipient.relevance is
  'evidenced — interest_evidence lists each basis, source and date; sin_informacion — no evidence, which is not low interest. Never a permission to contact.';

-- ── 4. snapshot rows: inserted only while draft, then never rewritten or deleted ────────────
create function outbound.campaign_recipient_snapshot_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  campaign_status text;
begin
  if tg_op = 'INSERT' then
    if new.frozen_at is not null then
      select c.status into campaign_status from outbound.campaign c where c.id = new.campaign_id for share;
      if campaign_status is distinct from 'draft' then
        raise exception 'a recipient snapshot is inserted only while its campaign is a draft (status is %)', campaign_status
          using errcode = 'P0001';
      end if;
      if new.state <> (case new.frozen_inclusion when 'included' then 'snapshotted' else 'excluded' end)
         or new.exclusion_reasons is distinct from new.frozen_reasons then
        raise exception 'a new snapshot row starts in the state and with the reasons it was frozen with'
          using errcode = 'P0001';
      end if;
    end if;
    return new;
  end if;
  if old.frozen_at is null then
    return case tg_op when 'DELETE' then old else new end;
  end if;
  if tg_op = 'DELETE' then
    raise exception 'a frozen recipient snapshot is never deleted' using errcode = 'P0001';
  end if;
  if new.campaign_id          is distinct from old.campaign_id
     or new.address_norm         is distinct from old.address_norm
     or new.contact_point_id     is distinct from old.contact_point_id
     or new.person_id            is distinct from old.person_id
     or new.organization_id      is distinct from old.organization_id
     or new.frozen_at            is distinct from old.frozen_at
     or new.frozen_inclusion     is distinct from old.frozen_inclusion
     or new.frozen_reasons       is distinct from old.frozen_reasons
     or new.frozen_notes         is distinct from old.frozen_notes
     or new.relevance            is distinct from old.relevance
     or new.interest_evidence    is distinct from old.interest_evidence
     or new.evidence_observed_at is distinct from old.evidence_observed_at
     or new.identity_review      is distinct from old.identity_review
     or new.campaign_version     is distinct from old.campaign_version
     or new.content_sha256       is distinct from old.content_sha256
     or new.policy_version       is distinct from old.policy_version
     or new.created_at           is distinct from old.created_at then
    raise exception 'a frozen recipient snapshot is never rewritten; freeze a new draft instead'
      using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger campaign_recipient_snapshot_guard
  before insert or update or delete on outbound.campaign_recipient
  for each row execute function outbound.campaign_recipient_snapshot_guard();

-- ── 5. audit vocabulary ──────────────────────────────────────────────────────────────────────
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
  'send_attempt.submission_changed', 'send_attempt.delivery_changed',
  'contact_control.added', 'contact_control.revoked',
  'send_control.changed',
  'operator.changed',
  'assertion.promoted', 'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'product.created'
));

reset role;
