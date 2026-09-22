-- OrigenLab V2 — what a clean-room database must contain.
--
-- Emits one `key|value` line per probe and nothing else. The expected values live in
-- supabase/cleanroom/expected_counts.json, with a reason attached to each; the comparison is
-- supabase/cleanroom/compare.py and it is exact in both directions, so a probe added here
-- without being declared there fails the verification rather than passing unnoticed.
--
-- Read-only by construction: the whole file runs inside `begin read only`, so `verify` can be
-- run against any database at any time without being able to change it.
\pset pager off
\pset tuples_only on
\pset format unaligned
\pset fieldsep '|'

begin read only;

select 'migrations.count', count(*)::text from supabase_migrations.schema_migrations
union all select 'migrations.head', coalesce(max(version), '<none>') from supabase_migrations.schema_migrations

-- Evidence intake. The total is asserted together with each of its parts, never on its own:
-- 24 records could be 24 Gmail messages and no manifest, or 7 fixtures and 17 real ones, and
-- both would be wrong in ways a total cannot see.
union all select 'source_record.total', count(*)::text from evidence.source_record
union all select 'source_record.gmail_message', count(*)::text
  from evidence.source_record where kind = 'gmail_message'
union all select 'source_record.migration_manifest', count(*)::text
  from evidence.source_record where kind = 'migration_manifest'
union all select 'source_record.gmail_message.pending', count(*)::text
  from evidence.source_record where kind = 'gmail_message' and review_status = 'pending'
union all select 'source_record.pytest_residue', count(*)::text
  from evidence.source_record where dedupe_key like 'pytest-%'

union all select 'assertion.total', count(*)::text from evidence.assertion
union all select 'assertion.from_gmail.total', count(*)::text
  from evidence.assertion a join evidence.source_record s on s.id = a.source_record_id
  where s.kind = 'gmail_message'
union all select 'assertion.from_gmail.unresolved', count(*)::text
  from evidence.assertion a join evidence.source_record s on s.id = a.source_record_id
  where s.kind = 'gmail_message' and a.resolution = 'unresolved'
union all select 'assertion.from_gmail.contact_address', count(*)::text
  from evidence.assertion a join evidence.source_record s on s.id = a.source_record_id
  where s.kind = 'gmail_message' and a.kind = 'contact_address'
union all select 'assertion.from_gmail.organization_name', count(*)::text
  from evidence.assertion a join evidence.source_record s on s.id = a.source_record_id
  where s.kind = 'gmail_message' and a.kind = 'organization_name'

union all select 'crm.organization', count(*)::text from crm.organization
union all select 'crm.person', count(*)::text from crm.person
union all select 'crm.contact_point', count(*)::text from crm.contact_point
union all select 'crm.contact_point.attached', count(*)::text
  from crm.contact_point where person_id is not null or organization_id is not null
union all select 'crm.domain_event', count(*)::text from crm.domain_event
union all select 'crm.domain_event.with_command_receipt', count(*)::text
  from crm.domain_event where command_receipt_id is not null
union all select 'crm.affiliation', count(*)::text from crm.affiliation
union all select 'crm.organization_domain', count(*)::text from crm.organization_domain
union all select 'crm.opportunity', count(*)::text from crm.opportunity
union all select 'crm.opportunity_organization', count(*)::text from crm.opportunity_organization
union all select 'crm.opportunity_interest', count(*)::text from crm.opportunity_interest
union all select 'crm.opportunity_evidence', count(*)::text from crm.opportunity_evidence
union all select 'crm.quote', count(*)::text from crm.quote
union all select 'crm.task', count(*)::text from crm.task

union all select 'platform.operator', count(*)::text from platform.operator
union all select 'platform.operator.pytest_residue', count(*)::text
  from platform.operator where email_norm like 'pytest-%'
union all select 'platform.command_receipt', count(*)::text from platform.command_receipt

union all select 'comms.mailbox', count(*)::text from comms.mailbox
union all select 'comms.message', count(*)::text from comms.message

union all select 'outbound.campaign', count(*)::text from outbound.campaign
union all select 'outbound.campaign_recipient', count(*)::text from outbound.campaign_recipient
union all select 'outbound.send_attempt', count(*)::text from outbound.send_attempt
union all select 'outbound.contact_control', count(*)::text from outbound.contact_control
union all select 'outbound.send_control', count(*)::text from outbound.send_control
union all select 'outbound.send_control.marketing_enabled', count(*)::text
  from outbound.send_control where marketing_enabled
union all select 'outbound.send_control.transactional_enabled', count(*)::text
  from outbound.send_control where transactional_enabled

union all select 'catalog.product', count(*)::text from catalog.product
union all select 'catalog.supplier_product', count(*)::text from catalog.supplier_product
union all select 'procurement.notice', count(*)::text from procurement.notice
;

commit;
