-- Slice 0 / M10b — campaign content freeze (A), typed audience criteria (B) and multi-reason
-- exclusions (D). Additive-forward over 20260905230814_slice0_outbound_tables.sql; that file is
-- never rewritten (CLAUDE.md — corrective migrations only).
--
-- docs/DOMAIN.md §7 #20, #21; docs/WORKFLOWS.md §1.3-1.4 (audience freeze), §2 (the send
-- predicate). Declarative here: content and criteria are present from `audience_frozen` onward,
-- the content fingerprint is a sha256, criteria is a versioned JSON object, and an excluded
-- recipient carries *every* reason it was excluded for, not the first one that fired.
--
-- Nothing here grants a send capability: no SECURITY DEFINER function is added, send_control keeps
-- both flags false, and outbound.send_attempt stays SELECT-only for both runtime roles.

set role origenlab_owner;

-- ── A. Campaign content, frozen with the audience ───────────────────────────────────────────────
-- One campaign row is one execution (Slice 0 collapses definition and run), so the content lives on
-- the campaign itself and freezes when the audience does. `content_sha256` is the fingerprint that
-- makes "what exactly was approved" answerable after the fact.
alter table outbound.campaign
  add column subject           text,
  add column body_text         text,
  add column body_html         text,
  add column content_sha256    text,
  add column content_frozen_at timestamptz;

alter table outbound.campaign
  add constraint campaign_subject_nonblank
    check (subject is null or length(btrim(subject)) > 0),
  add constraint campaign_body_text_nonblank
    check (body_text is null or length(btrim(body_text)) > 0),
  add constraint campaign_body_html_nonblank
    check (body_html is null or length(btrim(body_html)) > 0),
  add constraint campaign_content_sha256_shape
    check (content_sha256 is null or content_sha256 ~ '^[0-9a-f]{64}$'),
  add constraint campaign_content_frozen_together
    check (num_nonnulls(content_sha256, content_frozen_at) in (0, 2)),
  -- From audience_frozen onward the content facts are present. draft has not frozen yet;
  -- cancelled and archived are exempt (the archived V1 campaign predates V2 content freeze —
  -- the same carve-out campaign_approval_shape makes for approval).
  add constraint campaign_content_shape check (
    status in ('draft', 'cancelled', 'archived')
    or (subject is not null and body_text is not null
        and content_sha256 is not null and content_frozen_at is not null)
  );

comment on column outbound.campaign.content_sha256 is
  'sha256 over the canonical content serialization (subject \0 body_text \0 body_html); set with content_frozen_at at audience freeze and never rewritten.';

-- ── B. Typed audience criteria ──────────────────────────────────────────────────────────────────
alter table outbound.campaign
  add column audience_criteria         jsonb,
  add column audience_criteria_version smallint,
  add column audience_frozen_at        timestamptz;

alter table outbound.campaign
  add constraint campaign_audience_criteria_object
    check (audience_criteria is null or jsonb_typeof(audience_criteria) = 'object'),
  add constraint campaign_audience_criteria_version_check
    check (audience_criteria_version is null or audience_criteria_version = 1),
  add constraint campaign_audience_criteria_together
    check (num_nonnulls(audience_criteria, audience_criteria_version, audience_frozen_at) in (0, 3)),
  add constraint campaign_audience_criteria_shape check (
    status in ('draft', 'cancelled', 'archived') or audience_criteria is not null
  );

comment on column outbound.campaign.audience_criteria is
  'Canonical serialization of the typed AudienceCriteria struct (v1). The database checks object-ness and version only — field validation belongs to the application, as with send_attempt.search_evidence.';

-- ── D. exclusion_reason → exclusion_reasons text[] ──────────────────────────────────────────────
-- An operator who fixes one reason must not discover a second one afterwards: eligibility
-- evaluation is complete, so the snapshot is too.
alter table outbound.campaign_recipient
  add column exclusion_reasons text[] not null default '{}';

update outbound.campaign_recipient
   set exclusion_reasons = array[exclusion_reason]
 where exclusion_reason is not null;

alter table outbound.campaign_recipient
  drop constraint campaign_recipient_exclusion_reason_check,
  drop constraint campaign_recipient_exclusion_shape,
  drop column exclusion_reason;

alter table outbound.campaign_recipient
  -- The seven Slice 0 codes plus the nine reconciled from candidate_export_gate /
  -- outbound_campaign_gate (invalid_email, internal_domain, domain_suppression, outreach_replied,
  -- noise_email, noise_organization, manual_inactive, manual_hold) and the per-person dedup rule.
  add constraint campaign_recipient_exclusion_reasons_vocabulary check (
    exclusion_reasons <@ array[
      'block', 'prior_contact', 'cooldown', 'policy_supplier',
      'policy_no_channel', 'precheck_block', 'precheck_switch',
      'invalid_address', 'policy_internal_domain', 'block_domain',
      'prior_reply', 'policy_noise_address', 'policy_noise_organization',
      'manual_inactive', 'manual_hold', 'already_in_audience'
    ]::text[]
  ),
  -- `<@` yields NULL (and so passes) for an array holding a NULL element; close that hole.
  add constraint campaign_recipient_exclusion_reasons_no_nulls
    check (array_position(exclusion_reasons, null) is null),
  add constraint campaign_recipient_exclusion_reasons_shape
    check ((state = 'excluded') = (cardinality(exclusion_reasons) > 0));

comment on column outbound.campaign_recipient.exclusion_reasons is
  'Every reason this recipient was excluded, never only the first that fired. Sorted and de-duplicated by the application (freeze_campaign) — a CHECK cannot express it without a subquery, and the Slice 0 function list is closed.';

reset role;
