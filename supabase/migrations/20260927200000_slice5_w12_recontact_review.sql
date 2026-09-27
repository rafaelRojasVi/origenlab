-- Slice 5 (third step) — W12 recontact review, decided at the audience freeze and frozen with it.
--
-- docs/WORKFLOWS.md §W12, §W4 step 2; docs/DOMAIN.md §7 #21.
--
-- A permanent `prior_contact` address is excluded from every marketing audience. W12 is the
-- one way to contact it again, for one campaign. The decision is taken on the freeze
-- confirmation screen, recomputed and revalidated inside the freeze transaction, and written
-- with the recipient snapshot — so it is as immutable as the rest of the snapshot. No command
-- writes it later: the recontact override triple on a snapshot row is sealed at insert.
--
--   1. outbound.campaign_recipient.recontact_review — the individual audited decision for one
--      recipient: `approve` or `keep_excluded`, the operator's note, the operator, whether it
--      was taken for that row alone or as part of a reviewed bulk selection, the W12 policy
--      version, and the prior-contact facts the operator was shown (last contact date,
--      campaign/source, destination). NULL means nobody decided, and then `prior_contact`
--      stays an exclusion reason.
--   2. An approval can lift `prior_contact` and nothing else. An approved row carries no
--      exclusion reason except `manual_hold` (an operator exclusion or an identity-review
--      exclude taken on the same screen): never block, block_domain, cooldown,
--      policy_supplier, invalid_address or already_in_audience, and never prior_contact.
--   3. The override triple (WORKFLOWS.md §W12) is set exactly on approved snapshot rows, by
--      the operator named in the decision, with the decision's note as its reason.
--   4. The snapshot guard now also seals `recontact_review` and the override triple.
--   5. frozen_notes gains the three W12 notes.
--
-- Rows without snapshot facts (the archived V1 campaign's import) are untouched by all of it.

set role origenlab_owner;

-- ── 1. the decision column ───────────────────────────────────────────────────────────────────
alter table outbound.campaign_recipient
  add column recontact_review jsonb;

comment on column outbound.campaign_recipient.recontact_review is
  'W12: the audited recontact decision for this recipient, taken at freeze and write-once — decision (approve | keep_excluded), note, operator_id, mode (individual | bulk), policy_version, and the prior-contact facts shown. NULL: nobody decided; prior_contact stays an exclusion reason.';

alter table outbound.campaign_recipient
  add constraint campaign_recipient_recontact_review_shape check (
    recontact_review is null or (
      frozen_at is not null
      and jsonb_typeof(recontact_review) = 'object'
      and recontact_review->>'decision' in ('approve', 'keep_excluded')
      and recontact_review->>'mode' in ('individual', 'bulk')
      and length(btrim(coalesce(recontact_review->>'note', ''))) >= 3
      and recontact_review->>'operator_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      and recontact_review->>'policy_version' ~ '^[a-z0-9][a-z0-9._/-]{2,79}$'
      and jsonb_typeof(recontact_review->'prior_contact') = 'object'
    )
  ),
  -- 2. an approval lifts prior_contact and nothing else
  add constraint campaign_recipient_recontact_approval_lifts_prior_contact_only check (
    recontact_review is null or recontact_review->>'decision' <> 'approve' or (
      frozen_reasons <@ array['manual_hold']::text[]
      and 'recontact_approved' = any(frozen_notes)
    )
  ),
  add constraint campaign_recipient_recontact_kept_excluded check (
    recontact_review is null or recontact_review->>'decision' <> 'keep_excluded' or (
      frozen_inclusion = 'excluded'
      and 'prior_contact' = any(frozen_reasons)
      and 'recontact_kept_excluded' = any(frozen_notes)
    )
  ),
  -- 3. the override triple is exactly the approval, on a snapshot row
  add constraint campaign_recipient_override_is_the_w12_approval check (
    frozen_at is null
    or (recontact_override_at is not null) = coalesce(recontact_review->>'decision' = 'approve', false)
  ),
  add constraint campaign_recipient_override_names_the_decision check (
    frozen_at is null or recontact_override_at is null or (
      recontact_override_by_operator_id::text = recontact_review->>'operator_id'
      and recontact_override_reason = recontact_review->>'note'
    )
  );

-- ── 5. the three W12 notes ───────────────────────────────────────────────────────────────────
alter table outbound.campaign_recipient drop constraint campaign_recipient_frozen_notes_vocabulary;
alter table outbound.campaign_recipient
  add constraint campaign_recipient_frozen_notes_vocabulary check (
    frozen_notes <@ array[
      'shared_mailbox', 'no_contact_point', 'multiple_institutions', 'institution_mismatch',
      'identity_reviewed_include', 'identity_reviewed_exclude', 'possible_supplier_unreviewed',
      'operator_excluded', 'malformed_address',
      'recontact_approved', 'recontact_kept_excluded', 'recontact_not_reviewed'
    ]::text[]
  );

-- ── 4. the snapshot guard seals the decision and the triple ─────────────────────────────────
create or replace function outbound.campaign_recipient_snapshot_guard() returns trigger
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
     or new.recontact_review     is distinct from old.recontact_review
     or new.recontact_override_by_operator_id is distinct from old.recontact_override_by_operator_id
     or new.recontact_override_reason         is distinct from old.recontact_override_reason
     or new.recontact_override_at             is distinct from old.recontact_override_at
     or new.campaign_version     is distinct from old.campaign_version
     or new.content_sha256       is distinct from old.content_sha256
     or new.policy_version       is distinct from old.policy_version
     or new.created_at           is distinct from old.created_at then
    raise exception 'a frozen recipient snapshot is never rewritten, its recontact decision included; freeze a new draft instead'
      using errcode = 'P0001';
  end if;
  return new;
end;
$$;

reset role;
