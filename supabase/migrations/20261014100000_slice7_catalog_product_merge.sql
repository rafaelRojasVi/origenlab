-- Slice 7 — catalog.product.merged_into_product_id: merging a product loaded twice.
--
-- The 2026-10-06 supplier-documents import created makers from the names printed on offers
-- («Hielscher Ultrasonics GmbH», «Ortoalresa (Alvarez Redondo, S.A.)») instead of matching the
-- price-list makers, so some models exist twice. The cleanup (scripts/catalog/merge_makers.py)
-- marks the duplicate maker merged (crm.organization.merged_into_organization_id, which exists
-- since Slice 0) and, for a model both makers carry, switches the duplicate product off and points
-- it at its twin. Nothing is deleted and no cost observation is rewritten: catalog reads follow
-- the merge (a product's cost history includes the observations of the products merged into it).
--
-- Event: product.merged. The list below is the live CHECK (last rebuilt by 20261013120000) plus it.

set role origenlab_owner;

alter table catalog.product
  add column merged_into_product_id uuid references catalog.product (id),
  add constraint product_no_self_merge check (merged_into_product_id is distinct from id),
  add constraint product_merged_is_inactive check (merged_into_product_id is null or not active);

comment on column catalog.product.merged_into_product_id is
  'The product this duplicate was merged into (same model, same maker under another name). A merged product is inactive; its cost observations stay and are read with its twin.';

create index product_merged_into_idx on catalog.product (merged_into_product_id);

alter table crm.domain_event drop constraint domain_event_type_check;
alter table crm.domain_event add constraint domain_event_type_check check (event_type in (
  'organization.created', 'organization.confirmed', 'organization.merged', 'organization.updated',
  'organization.archived', 'organization.restored', 'organization.identifier_added',
  'organization.identifier_removed', 'organization.domain_added', 'organization.domain_removed',
  'organization.domain_restored', 'organization.product_line_linked',
  'organization.product_line_unlinked', 'person.created', 'person.confirmed', 'person.merged',
  'person.updated', 'person.archived', 'person.restored', 'affiliation.opened',
  'affiliation.closed', 'address.added', 'address.superseded', 'contact_point.created',
  'contact_point.confirmed', 'contact_point.updated', 'contact_point.deactivated',
  'relationship.changed', 'opportunity.created', 'opportunity.staged',
  'opportunity.organization_set', 'opportunity.closed', 'opportunity.stage_corrected',
  'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
  'case_organization.added', 'case_organization.role_confirmed', 'case_organization.ended',
  'case_interest.added', 'case_evidence.linked', 'case_evidence.unlinked', 'task.created',
  'task.completed', 'task.cancelled', 'activity.linked', 'quote.created',
  'quote_revision.transitioned', 'quote_revision.superseded', 'quote_revision.historical_recorded',
  'campaign.transitioned', 'campaign.approved', 'campaign.override_granted',
  'campaign.dry_run_recorded', 'campaign.draft_created', 'campaign.draft_content_saved',
  'campaign.audience_frozen', 'campaign.planning_set', 'campaign.planning_cleared',
  'campaign_block.placed', 'campaign_block.lifted', 'send_attempt.submission_changed',
  'send_attempt.delivery_changed', 'contact_control.added', 'contact_control.revoked',
  'contact_control.evidence_linked', 'send_control.changed', 'operator.changed',
  'assertion.promoted', 'assertion.unsubscribe_review_opened',
  'assertion.unsubscribe_review_dismissed', 'assertion.supplier_candidate_confirmed',
  'assertion.supplier_candidate_rejected', 'source_record.quarantined',
  'source_record.migration_manifest_recorded', 'source_record.review_noted', 'note.created',
  'note.revised', 'note.archived', 'product.created', 'product.updated',
  'product.content_confirmed', 'product.image_added', 'product.image_updated',
  'product.cost_recorded', 'organization.supplier_terms_set', 'fx_rate.recorded',
  'cost_parameter.set', 'source_record.document_line_reviewed', 'assertion.triage_reviewed',
  'opportunity.document_attached', 'opportunity.document_removed', 'product.document_recorded',
  'product.document_retired', 'organization.price_list_recorded', 'organization.price_list_retired',
  'product.merged'
));

reset role;
