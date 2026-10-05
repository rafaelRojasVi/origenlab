-- Slice 7 — restore the two email → cases event types (corrective).
--
-- 20261005120000_slice7_mail_auto_case_corrections added `case_evidence.unlinked` and
-- `opportunity.stage_corrected` to crm.domain_event_type_check. The two catalog 1a migrations
-- that follow it in timestamp order (20261005134832, 20261005140628) were written on a parallel
-- branch and rebuild the same CHECK from their own list, so the chain as merged drops both types:
-- undoing a rules-engine link and correcting a machine-set stage are refused. Shipped migrations
-- are not rewritten; this one rebuilds the CHECK as the union of both branches.
--
-- No table, column, function, grant or policy changes.
set role origenlab_owner;

alter table crm.domain_event drop constraint domain_event_type_check;
alter table crm.domain_event add constraint domain_event_type_check check (event_type in (
  'organization.created', 'organization.confirmed', 'organization.merged',
  'organization.updated', 'organization.archived', 'organization.restored',
  'organization.identifier_added', 'organization.identifier_removed',
  'organization.domain_added', 'organization.domain_removed', 'organization.domain_restored',
  'organization.product_line_linked', 'organization.product_line_unlinked',
  'person.created', 'person.confirmed', 'person.merged',
  'person.updated', 'person.archived', 'person.restored',
  'affiliation.opened', 'affiliation.closed',
  'address.added', 'address.superseded',
  'contact_point.created', 'contact_point.confirmed',
  'contact_point.updated', 'contact_point.deactivated',
  'relationship.changed',
  'opportunity.created', 'opportunity.staged', 'opportunity.organization_set', 'opportunity.closed',
  'opportunity.stage_corrected',
  'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
  'case_organization.added', 'case_organization.role_confirmed', 'case_organization.ended',
  'case_interest.added',
  'case_evidence.linked', 'case_evidence.unlinked',
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
  'assertion.supplier_candidate_confirmed', 'assertion.supplier_candidate_rejected',
  'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'note.created', 'note.revised', 'note.archived',
  'product.created', 'product.updated', 'product.content_confirmed', 'product.image_added', 'product.image_updated',
  'product.cost_recorded',
  'organization.supplier_terms_set',
  'fx_rate.recorded', 'cost_parameter.set', 'source_record.document_line_reviewed'
));

reset role;
