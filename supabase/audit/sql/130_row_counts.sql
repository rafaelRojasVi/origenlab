-- a12 — MIGRATION.md §5 slice 0: the foundation carries zero rows of business data.
--
-- The thirty-three table names below are written out rather than discovered, because a fixed,
-- reviewed query file is the audit's read-only guarantee: no dynamic SQL is constructed and
-- executed anywhere in this bank. The list cannot drift silently — a08 pins the same inventory
-- against the committed baseline and fails first if a table is added, removed or renamed.
--
-- outbound.send_control is the one deliberate exception: it is a single control row, not business
-- data, and a11 asserts its shape. It is counted here and excluded from the zero-row rule by
-- supabase/audit/olaudit/checks.py, not by omitting it.
--
-- Read-only: one SELECT, no mutation, no session change.
with counts as (
  select 'catalog' as schema_name, 'product' as table_name, count(*) as row_count from catalog.product
  union all
  select 'catalog' as schema_name, 'supplier_product' as table_name, count(*) as row_count from catalog.supplier_product
  union all
  select 'comms' as schema_name, 'attachment' as table_name, count(*) as row_count from comms.attachment
  union all
  select 'comms' as schema_name, 'mailbox' as table_name, count(*) as row_count from comms.mailbox
  union all
  select 'comms' as schema_name, 'message' as table_name, count(*) as row_count from comms.message
  union all
  select 'comms' as schema_name, 'message_participant' as table_name, count(*) as row_count from comms.message_participant
  union all
  select 'crm' as schema_name, 'activity' as table_name, count(*) as row_count from crm.activity
  union all
  select 'crm' as schema_name, 'address' as table_name, count(*) as row_count from crm.address
  union all
  select 'crm' as schema_name, 'affiliation' as table_name, count(*) as row_count from crm.affiliation
  union all
  select 'crm' as schema_name, 'contact_point' as table_name, count(*) as row_count from crm.contact_point
  union all
  select 'crm' as schema_name, 'domain_event' as table_name, count(*) as row_count from crm.domain_event
  union all
  select 'crm' as schema_name, 'external_identifier' as table_name, count(*) as row_count from crm.external_identifier
  union all
  select 'crm' as schema_name, 'opportunity' as table_name, count(*) as row_count from crm.opportunity
  union all
  select 'crm' as schema_name, 'opportunity_participant' as table_name, count(*) as row_count from crm.opportunity_participant
  union all
  select 'crm' as schema_name, 'organization' as table_name, count(*) as row_count from crm.organization
  union all
  select 'crm' as schema_name, 'organization_domain' as table_name, count(*) as row_count from crm.organization_domain
  union all
  select 'crm' as schema_name, 'organization_relationship' as table_name, count(*) as row_count from crm.organization_relationship
  union all
  select 'crm' as schema_name, 'person' as table_name, count(*) as row_count from crm.person
  union all
  select 'crm' as schema_name, 'quote' as table_name, count(*) as row_count from crm.quote
  union all
  select 'crm' as schema_name, 'quote_line' as table_name, count(*) as row_count from crm.quote_line
  union all
  select 'crm' as schema_name, 'quote_revision' as table_name, count(*) as row_count from crm.quote_revision
  union all
  select 'crm' as schema_name, 'task' as table_name, count(*) as row_count from crm.task
  union all
  select 'evidence' as schema_name, 'assertion' as table_name, count(*) as row_count from evidence.assertion
  union all
  select 'evidence' as schema_name, 'source_record' as table_name, count(*) as row_count from evidence.source_record
  union all
  select 'outbound' as schema_name, 'campaign' as table_name, count(*) as row_count from outbound.campaign
  union all
  select 'outbound' as schema_name, 'campaign_recipient' as table_name, count(*) as row_count from outbound.campaign_recipient
  union all
  select 'outbound' as schema_name, 'campaign_reply' as table_name, count(*) as row_count from outbound.campaign_reply
  union all
  select 'outbound' as schema_name, 'contact_control' as table_name, count(*) as row_count from outbound.contact_control
  union all
  select 'outbound' as schema_name, 'send_attempt' as table_name, count(*) as row_count from outbound.send_attempt
  union all
  select 'outbound' as schema_name, 'send_control' as table_name, count(*) as row_count from outbound.send_control
  union all
  select 'platform' as schema_name, 'command_receipt' as table_name, count(*) as row_count from platform.command_receipt
  union all
  select 'platform' as schema_name, 'operator' as table_name, count(*) as row_count from platform.operator
  union all
  select 'procurement' as schema_name, 'notice' as table_name, count(*) as row_count from procurement.notice
)
select jsonb_build_object(
  'check', 'a12',
  'data', jsonb_build_object(
    'counted_tables', (select count(*) from counts),
    'counts', coalesce((select jsonb_agg(jsonb_build_object(
                                 'schema', counts.schema_name,
                                 'table',  counts.table_name,
                                 'rows',   counts.row_count)
                               order by counts.schema_name, counts.table_name)
                          from counts), '[]'::jsonb),
    'non_empty', coalesce((select jsonb_agg(jsonb_build_object(
                                    'schema', counts.schema_name,
                                    'table',  counts.table_name,
                                    'rows',   counts.row_count)
                                  order by counts.schema_name, counts.table_name)
                             from counts where counts.row_count > 0), '[]'::jsonb)
  )
)::text;
