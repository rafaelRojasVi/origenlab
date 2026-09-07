-- Slice 0 / D1A.2 — covering indexes for every foreign key that had none.
--
-- The local performance advisors reported 53 `unindexed_foreign_keys`. That finding is
-- structural, not an artefact of the empty database: an unindexed referencing column costs a
-- sequential scan of the child table on *every* parent UPDATE or DELETE (PostgreSQL runs
-- `SELECT 1 FROM ONLY child x WHERE x.fkcol = $1 FOR KEY SHARE OF x` to enforce NO ACTION), and
-- again on every join along the relationship. OrigenLab joins organizations, people,
-- affiliations, opportunities, quote revisions, recipients, messages, evidence and procurement
-- notices through exactly these columns (docs/WORKFLOWS.md, docs/DOMAIN.md).
--
-- An independent catalogue audit of all 97 foreign keys in the seven application schemas found:
--
--   25  already covered by a plain (non-partial) index whose leading key columns are the
--       referencing columns — mostly a PK or a UNIQUE constraint;
--   18  covered by a Slice 0 partial index `... where <fk column> is not null`. That predicate is
--       implied by the referential-action lookup itself, because `x.fkcol = $1` is strict, so the
--       planner uses these indexes for FK enforcement and for joins. They are kept as they are:
--       on the polymorphic tables (crm.external_identifier, crm.contact_point,
--       crm.opportunity_participant) a full index would additionally index the NULLs that no FK
--       lookup can ever reach;
--   54  had no usable covering index. Those are the 54 created below.
--
-- 54, not 53: `crm.task_owner_operator_id_fkey` is a false negative of the Supabase linter, which
-- ignores `pg_index.indpred`. The only index leading with `owner_operator_id` was
-- `task_owner_open_due_idx ... where status = 'open'`, whose predicate is *not* implied by an
-- owner lookup, so deleting a platform.operator row would have scanned every non-open task. The
-- pgTAP gate in supabase/tests/090_foreign_key_indexes.sql tests the implication rather than
-- trusting the advisor, and therefore catches this class.
--
-- Index names follow the Slice 0 convention: the table, then the referencing columns with a
-- trailing `_id` dropped, then `_idx`. `opportunity_won_quote_belongs_idx` takes its
-- constraint's distinguishing word instead, because the derived name would have ended in a bare
-- `id` and read as if the index were on `won_quote_id` alone.
--
-- Every entry below is the minimal set: one index per distinct referencing-column list, in the
-- constraint's own column order. No column list is a prefix of another on the same table, so no
-- index here covers more than one constraint and none is redundant with another. No INCLUDE
-- columns, no partial predicates, no CREATE INDEX CONCURRENTLY (this file runs inside the
-- `supabase db reset` migration chain, which is transactional).
--
-- Relationships: docs/ARCHITECTURE.md §10. Invariant: docs/MIGRATION.md §5.2.
-- Audit gate: supabase/tests/090_foreign_key_indexes.sql.

set role origenlab_owner;

-- catalog.product
create index product_created_by_operator_idx
  on catalog.product (created_by_operator_id);  -- product_created_by_operator_id_fkey -> platform.operator(id)

-- catalog.supplier_product
create index supplier_product_origin_source_record_idx
  on catalog.supplier_product (origin_source_record_id);  -- supplier_product_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.activity
create index activity_recorded_by_operator_idx
  on crm.activity (recorded_by_operator_id);  -- activity_recorded_by_operator_id_fkey -> platform.operator(id)

-- crm.address
create index address_confirmed_by_operator_idx
  on crm.address (confirmed_by_operator_id);  -- address_confirmed_by_operator_id_fkey -> platform.operator(id)
create index address_origin_source_record_idx
  on crm.address (origin_source_record_id);  -- address_origin_source_record_id_fkey -> evidence.source_record(id)
create index address_superseded_by_address_idx
  on crm.address (superseded_by_address_id);  -- address_superseded_by_address_id_fkey -> crm.address(id)

-- crm.affiliation
create index affiliation_confirmed_by_operator_idx
  on crm.affiliation (confirmed_by_operator_id);  -- affiliation_confirmed_by_operator_id_fkey -> platform.operator(id)
create index affiliation_origin_source_record_idx
  on crm.affiliation (origin_source_record_id);  -- affiliation_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.contact_point
create index contact_point_origin_source_record_idx
  on crm.contact_point (origin_source_record_id);  -- contact_point_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.domain_event
create index domain_event_actor_operator_idx
  on crm.domain_event (actor_operator_id);  -- domain_event_actor_operator_id_fkey -> platform.operator(id)

-- crm.external_identifier
create index external_identifier_origin_source_record_idx
  on crm.external_identifier (origin_source_record_id);  -- external_identifier_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.opportunity
create index opportunity_origin_source_record_idx
  on crm.opportunity (origin_source_record_id);  -- opportunity_origin_source_record_id_fkey -> evidence.source_record(id)
create index opportunity_reopened_from_opportunity_idx
  on crm.opportunity (reopened_from_opportunity_id);  -- opportunity_reopened_from_opportunity_id_fkey -> crm.opportunity(id)
create index opportunity_won_quote_belongs_idx
  on crm.opportunity (won_quote_id, id);  -- opportunity_won_quote_belongs_fkey -> crm.quote(id,opportunity_id)
create index opportunity_won_quote_won_revision_no_idx
  on crm.opportunity (won_quote_id, won_revision_no);  -- opportunity_won_revision_fkey -> crm.quote_revision(quote_id,revision_no)

-- crm.opportunity_participant
create index opportunity_participant_confirmed_by_operator_idx
  on crm.opportunity_participant (confirmed_by_operator_id);  -- opportunity_participant_confirmed_by_operator_id_fkey -> platform.operator(id)
create index opportunity_participant_origin_source_record_idx
  on crm.opportunity_participant (origin_source_record_id);  -- opportunity_participant_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.organization
create index organization_confirmed_by_operator_idx
  on crm.organization (confirmed_by_operator_id);  -- organization_confirmed_by_operator_id_fkey -> platform.operator(id)
create index organization_origin_source_record_idx
  on crm.organization (origin_source_record_id);  -- organization_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.organization_domain
create index organization_domain_origin_source_record_idx
  on crm.organization_domain (origin_source_record_id);  -- organization_domain_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.organization_relationship
create index organization_relationship_origin_source_record_idx
  on crm.organization_relationship (origin_source_record_id);  -- organization_relationship_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.person
create index person_confirmed_by_operator_idx
  on crm.person (confirmed_by_operator_id);  -- person_confirmed_by_operator_id_fkey -> platform.operator(id)
create index person_origin_source_record_idx
  on crm.person (origin_source_record_id);  -- person_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.quote
create index quote_created_by_operator_idx
  on crm.quote (created_by_operator_id);  -- quote_created_by_operator_id_fkey -> platform.operator(id)
create index quote_origin_source_record_idx
  on crm.quote (origin_source_record_id);  -- quote_origin_source_record_id_fkey -> evidence.source_record(id)

-- crm.quote_line
create index quote_line_quote_revision_allocated_to_line_no_idx
  on crm.quote_line (quote_revision_id, allocated_to_line_no);  -- quote_line_allocation_target_is_item_fkey -> crm.quote_line(quote_revision_id,item_line_no)

-- crm.quote_revision
create index quote_revision_approved_by_operator_idx
  on crm.quote_revision (approved_by_operator_id);  -- quote_revision_approved_by_operator_id_fkey -> platform.operator(id)
create index quote_revision_billing_address_idx
  on crm.quote_revision (billing_address_id);  -- quote_revision_billing_address_id_fkey -> crm.address(id)
create index quote_revision_created_by_operator_idx
  on crm.quote_revision (created_by_operator_id);  -- quote_revision_created_by_operator_id_fkey -> platform.operator(id)
create index quote_revision_delivery_address_idx
  on crm.quote_revision (delivery_address_id);  -- quote_revision_delivery_address_id_fkey -> crm.address(id)
create index quote_revision_quote_superseded_by_revision_no_idx
  on crm.quote_revision (quote_id, superseded_by_revision_no);  -- quote_revision_superseded_by_fkey -> crm.quote_revision(quote_id,revision_no)
create index quote_revision_recipient_participant_idx
  on crm.quote_revision (recipient_participant_id);  -- quote_revision_recipient_participant_id_fkey -> crm.opportunity_participant(id)
create index quote_revision_signatory_participant_idx
  on crm.quote_revision (signatory_participant_id);  -- quote_revision_signatory_participant_id_fkey -> crm.opportunity_participant(id)

-- crm.task
create index task_created_by_operator_idx
  on crm.task (created_by_operator_id);  -- task_created_by_operator_id_fkey -> platform.operator(id)
create index task_owner_operator_idx
  on crm.task (owner_operator_id);  -- task_owner_operator_id_fkey -> platform.operator(id)

-- evidence.assertion
create index assertion_resolved_by_operator_idx
  on evidence.assertion (resolved_by_operator_id);  -- assertion_resolved_by_operator_id_fkey -> platform.operator(id)

-- evidence.source_record
create index source_record_superseded_by_source_record_idx
  on evidence.source_record (superseded_by_source_record_id);  -- source_record_superseded_by_source_record_id_fkey -> evidence.source_record(id)

-- outbound.campaign
create index campaign_approved_by_operator_idx
  on outbound.campaign (approved_by_operator_id);  -- campaign_approved_by_operator_id_fkey -> platform.operator(id)
create index campaign_created_by_operator_idx
  on outbound.campaign (created_by_operator_id);  -- campaign_created_by_operator_id_fkey -> platform.operator(id)
create index campaign_mailbox_idx
  on outbound.campaign (mailbox_id);  -- campaign_mailbox_id_fkey -> comms.mailbox(id)
create index campaign_origin_source_record_idx
  on outbound.campaign (origin_source_record_id);  -- campaign_origin_source_record_id_fkey -> evidence.source_record(id)

-- outbound.campaign_recipient
create index campaign_recipient_contact_point_idx
  on outbound.campaign_recipient (contact_point_id);  -- campaign_recipient_contact_point_id_fkey -> crm.contact_point(id)
create index campaign_recipient_organization_idx
  on outbound.campaign_recipient (organization_id);  -- campaign_recipient_organization_id_fkey -> crm.organization(id)
create index campaign_recipient_person_idx
  on outbound.campaign_recipient (person_id);  -- campaign_recipient_person_id_fkey -> crm.person(id)
create index campaign_recipient_recontact_override_by_operator_idx
  on outbound.campaign_recipient (recontact_override_by_operator_id);  -- campaign_recipient_recontact_override_by_operator_id_fkey -> platform.operator(id)

-- outbound.contact_control
create index contact_control_created_by_operator_idx
  on outbound.contact_control (created_by_operator_id);  -- contact_control_created_by_operator_id_fkey -> platform.operator(id)
create index contact_control_origin_source_record_idx
  on outbound.contact_control (origin_source_record_id);  -- contact_control_origin_source_record_id_fkey -> evidence.source_record(id)

-- outbound.send_attempt
create index send_attempt_campaign_recipient_campaign_idx
  on outbound.send_attempt (campaign_recipient_id, campaign_id);  -- send_attempt_recipient_in_campaign_fkey -> outbound.campaign_recipient(id,campaign_id)
create index send_attempt_mailbox_idx
  on outbound.send_attempt (mailbox_id);  -- send_attempt_mailbox_id_fkey -> comms.mailbox(id)
create index send_attempt_origin_source_record_idx
  on outbound.send_attempt (origin_source_record_id);  -- send_attempt_origin_source_record_id_fkey -> evidence.source_record(id)
create index send_attempt_resolved_by_operator_idx
  on outbound.send_attempt (resolved_by_operator_id);  -- send_attempt_resolved_by_operator_id_fkey -> platform.operator(id)
create index send_attempt_retry_of_attempt_idx
  on outbound.send_attempt (retry_of_attempt_id);  -- send_attempt_retry_of_attempt_id_fkey -> outbound.send_attempt(id)

-- outbound.send_control
create index send_control_changed_by_operator_idx
  on outbound.send_control (changed_by_operator_id);  -- send_control_changed_by_operator_id_fkey -> platform.operator(id)

-- platform.operator
create index operator_invited_by_operator_idx
  on platform.operator (invited_by_operator_id);  -- operator_invited_by_operator_id_fkey -> platform.operator(id)

reset role;
