-- Slice 2 — the one audit event an evidence review decision needs that Slice 0 has no name for.
--
-- docs/WORKFLOWS.md §W1 (evidence → promotion); docs/DOMAIN.md §5; docs/DATA.md §1.1.
--
-- Additive only. No existing row changes, no grant widens, no RLS policy relaxes, no send
-- flag moves, and no table gains a column. The event vocabulary stays closed: naming a new
-- kind of durable decision is still a migration, by design.

set role origenlab_owner;

-- ── crm.domain_event: an operator reviewed a source record and decided not to decide ───────────
--
-- W1 names three outcomes for a source record: an assertion is promoted, the record is
-- quarantined, or nothing happens. The third is the common one and, until now, the only one
-- that left no trace — which makes it indistinguishable from a record nobody has opened.
--
-- Those are different facts. "Twenty records are pending" and "twenty records were read by a
-- named operator who judged the evidence insufficient" call for different next steps, and the
-- second is a durable human decision: it is exactly the kind of fact DOMAIN.md §5 says the
-- CRM records and a machine projection may not own.
--
-- The event changes no state. `evidence.source_record.review_status` stays 'pending' —
-- deliberately, because the record IS still pending and a review that silently advanced it
-- would be the promotion the operator declined to make. What the event adds is the operator,
-- the moment and the mandatory reason, attached to the record's own audit stream.
--
-- It is not a quarantine. 'source_record.quarantined' means the evidence contradicts itself
-- and must be kept out of the queue; this means the evidence is intact and insufficient.
alter table crm.domain_event
  drop constraint domain_event_type_check;

alter table crm.domain_event
  add constraint domain_event_type_check check (event_type in (
    'organization.created', 'organization.confirmed', 'organization.merged',
    'person.created', 'person.confirmed', 'person.merged',
    'affiliation.opened', 'affiliation.closed',
    'address.added', 'address.superseded',
    'contact_point.created', 'contact_point.confirmed',
    'relationship.changed',
    'opportunity.created', 'opportunity.staged', 'opportunity.organization_set', 'opportunity.closed',
    'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
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
  'Closed event vocabulary (docs/DATA.md §1.1). source_record.review_noted records that a named operator reviewed a source record and deliberately left it pending, with a mandatory reason; it changes no state and is not a quarantine.';

reset role;
