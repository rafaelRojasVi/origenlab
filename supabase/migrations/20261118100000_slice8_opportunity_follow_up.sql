-- Slice 8 — crm.opportunity.follow_up_operator_id / follow_up_channel: who follows a case with a
-- delivered quote up, and by which channel («Ventas › Seguimiento»).
--
--   * `follow_up_operator_id` (uuid, FK platform.operator, nullable): the encargado. Null = «Sin
--     asignar» (the dashboard shows a suggestion while null).
--   * `follow_up_channel` (text, nullable): 'llamada', 'correo' or 'whatsapp'; null = not chosen.
--   * One writer: the follow-up command (apps/api), which writes one `opportunity.follow_up_set`
--     event per change. That event type is added to domain_event_type_check below (full list as left
--     by 20261113110000_slice8_opportunity_pause_event.sql, plus the new type); the aggregate kind
--     'opportunity' and crm.domain_event_is_valid already cover it.
--   * No new grant: origenlab_api already has table-level select/insert/update on crm.opportunity
--     and the worker select, exactly as for paused_until / pause_reason. RLS is unchanged.

set role origenlab_owner;

alter table crm.opportunity
  add column follow_up_operator_id uuid references platform.operator (id),
  add column follow_up_channel text;

alter table crm.opportunity
  add constraint opportunity_follow_up_channel_check check (
    follow_up_channel is null or follow_up_channel in ('llamada', 'correo', 'whatsapp')
  );

comment on column crm.opportunity.follow_up_operator_id is
  'Encargado del seguimiento (platform.operator). Null = sin asignar. Set only by the follow-up command.';
comment on column crm.opportunity.follow_up_channel is
  'Seguimiento channel: llamada, correo, whatsapp, or null = not chosen. Set only by the follow-up command.';

create index opportunity_follow_up_operator_idx on crm.opportunity (follow_up_operator_id)
  where follow_up_operator_id is not null;

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
  'product.merged',
  'quote_revision.opened', 'quote_revision.draft_saved',
  'product.model_alias_added', 'product.model_alias_retired',
  'bidder.profile_saved', 'bidder.document_added', 'bidder.document_retired',
  'opportunity.bid_preparation_requested', 'opportunity.tender_status_set', 'notice.promoted',
  'notice.reviewed',
  'source_record.prospect_reviewed', 'source_record.prospect_edited',
  'source_record.prospect_search_requested', 'source_record.prospect_email_drafted',
  'todo.created', 'todo.updated', 'todo.moved', 'todo.completed', 'todo.reopened', 'todo.deleted',
  'invoice.imported', 'invoice.case_set', 'invoice.paid', 'invoice.unpaid',
  'opportunity.origin_set',
  'opportunity.pause_set',
  'opportunity.follow_up_set'
));

reset role;
