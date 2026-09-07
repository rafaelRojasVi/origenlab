-- Slice 0 / M13 — row level security policies: one explicit named policy per (table, role, verb)
-- the role is granted.
--
-- docs/ARCHITECTURE.md §6.1 points 1, 2 and 5. RLS is ENABLEd on all 32 tables (in the migration
-- that creates each table) and is not FORCEd, so origenlab_owner — a NOLOGIN role — crosses it by
-- ownership; that is the only application-owned RLS exemption. Both runtime roles are NOBYPASSRLS,
-- so a table with no policy for a role is unreachable by that role: a table added later is
-- deny-by-default until a migration opens it deliberately. Row-level predicates are meaningful only
-- where a row belongs to one operator; a database session is a server role, never a person, so
-- every policy here is a role gate (`using (true)` / `with check (true)`), and authorization is
-- grants (layer A), the closed definer list (layer B) and FastAPI (layer C).
--
-- Generated from the same privilege matrix as the grants migration.

set role origenlab_owner;

-- crm.organization
create policy origenlab_api_select on crm.organization for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.organization for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.organization for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.organization for select to origenlab_worker using (true);

-- crm.organization_domain
create policy origenlab_api_select on crm.organization_domain for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.organization_domain for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.organization_domain for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.organization_domain for select to origenlab_worker using (true);

-- crm.organization_relationship
create policy origenlab_api_select on crm.organization_relationship for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.organization_relationship for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.organization_relationship for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.organization_relationship for select to origenlab_worker using (true);

-- crm.external_identifier
create policy origenlab_api_select on crm.external_identifier for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.external_identifier for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.external_identifier for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.external_identifier for select to origenlab_worker using (true);

-- crm.person
create policy origenlab_api_select on crm.person for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.person for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.person for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.person for select to origenlab_worker using (true);

-- crm.affiliation
create policy origenlab_api_select on crm.affiliation for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.affiliation for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.affiliation for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.affiliation for select to origenlab_worker using (true);

-- crm.contact_point
create policy origenlab_api_select on crm.contact_point for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.contact_point for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.contact_point for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.contact_point for select to origenlab_worker using (true);

-- crm.address
create policy origenlab_api_select on crm.address for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.address for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.address for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.address for select to origenlab_worker using (true);

-- crm.opportunity
create policy origenlab_api_select on crm.opportunity for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.opportunity for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.opportunity for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.opportunity for select to origenlab_worker using (true);

-- crm.opportunity_participant
create policy origenlab_api_select on crm.opportunity_participant for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.opportunity_participant for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.opportunity_participant for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.opportunity_participant for select to origenlab_worker using (true);

-- crm.task
create policy origenlab_api_select on crm.task for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.task for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.task for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.task for select to origenlab_worker using (true);

-- crm.activity
create policy origenlab_api_select on crm.activity for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.activity for insert to origenlab_api with check (true);
create policy origenlab_worker_select on crm.activity for select to origenlab_worker using (true);

-- crm.domain_event
create policy origenlab_api_select on crm.domain_event for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.domain_event for insert to origenlab_api with check (true);
create policy origenlab_worker_select on crm.domain_event for select to origenlab_worker using (true);

-- crm.quote
create policy origenlab_api_select on crm.quote for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.quote for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.quote for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.quote for select to origenlab_worker using (true);

-- crm.quote_revision
create policy origenlab_api_select on crm.quote_revision for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.quote_revision for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.quote_revision for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.quote_revision for select to origenlab_worker using (true);

-- crm.quote_line
create policy origenlab_api_select on crm.quote_line for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.quote_line for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.quote_line for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.quote_line for select to origenlab_worker using (true);

-- comms.mailbox
create policy origenlab_api_select on comms.mailbox for select to origenlab_api using (true);
create policy origenlab_worker_select on comms.mailbox for select to origenlab_worker using (true);
create policy origenlab_worker_insert on comms.mailbox for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on comms.mailbox for update to origenlab_worker using (true) with check (true);

-- comms.message
create policy origenlab_api_select on comms.message for select to origenlab_api using (true);
create policy origenlab_worker_select on comms.message for select to origenlab_worker using (true);
create policy origenlab_worker_insert on comms.message for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on comms.message for update to origenlab_worker using (true) with check (true);

-- comms.message_participant
create policy origenlab_api_select on comms.message_participant for select to origenlab_api using (true);
create policy origenlab_api_update on comms.message_participant for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on comms.message_participant for select to origenlab_worker using (true);
create policy origenlab_worker_insert on comms.message_participant for insert to origenlab_worker with check (true);

-- comms.attachment
create policy origenlab_api_select on comms.attachment for select to origenlab_api using (true);
create policy origenlab_worker_select on comms.attachment for select to origenlab_worker using (true);
create policy origenlab_worker_insert on comms.attachment for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on comms.attachment for update to origenlab_worker using (true) with check (true);

-- outbound.send_control
create policy origenlab_api_select on outbound.send_control for select to origenlab_api using (true);
create policy origenlab_worker_select on outbound.send_control for select to origenlab_worker using (true);

-- outbound.campaign
create policy origenlab_api_select on outbound.campaign for select to origenlab_api using (true);
create policy origenlab_api_insert on outbound.campaign for insert to origenlab_api with check (true);
create policy origenlab_api_update on outbound.campaign for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on outbound.campaign for select to origenlab_worker using (true);

-- outbound.campaign_recipient
create policy origenlab_api_select on outbound.campaign_recipient for select to origenlab_api using (true);
create policy origenlab_api_insert on outbound.campaign_recipient for insert to origenlab_api with check (true);
create policy origenlab_api_update on outbound.campaign_recipient for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on outbound.campaign_recipient for select to origenlab_worker using (true);

-- outbound.send_attempt
create policy origenlab_api_select on outbound.send_attempt for select to origenlab_api using (true);
create policy origenlab_worker_select on outbound.send_attempt for select to origenlab_worker using (true);

-- outbound.contact_control
create policy origenlab_api_select on outbound.contact_control for select to origenlab_api using (true);
create policy origenlab_worker_select on outbound.contact_control for select to origenlab_worker using (true);

-- evidence.source_record
create policy origenlab_api_select on evidence.source_record for select to origenlab_api using (true);
create policy origenlab_api_update on evidence.source_record for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on evidence.source_record for select to origenlab_worker using (true);
create policy origenlab_worker_insert on evidence.source_record for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on evidence.source_record for update to origenlab_worker using (true) with check (true);

-- evidence.assertion
create policy origenlab_api_select on evidence.assertion for select to origenlab_api using (true);
create policy origenlab_api_update on evidence.assertion for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on evidence.assertion for select to origenlab_worker using (true);
create policy origenlab_worker_insert on evidence.assertion for insert to origenlab_worker with check (true);

-- catalog.product
create policy origenlab_api_select on catalog.product for select to origenlab_api using (true);
create policy origenlab_api_insert on catalog.product for insert to origenlab_api with check (true);
create policy origenlab_api_update on catalog.product for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on catalog.product for select to origenlab_worker using (true);

-- catalog.supplier_product
create policy origenlab_api_select on catalog.supplier_product for select to origenlab_api using (true);
create policy origenlab_api_insert on catalog.supplier_product for insert to origenlab_api with check (true);
create policy origenlab_worker_select on catalog.supplier_product for select to origenlab_worker using (true);
create policy origenlab_worker_insert on catalog.supplier_product for insert to origenlab_worker with check (true);

-- procurement.notice
create policy origenlab_api_select on procurement.notice for select to origenlab_api using (true);
create policy origenlab_api_update on procurement.notice for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on procurement.notice for select to origenlab_worker using (true);
create policy origenlab_worker_insert on procurement.notice for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on procurement.notice for update to origenlab_worker using (true) with check (true);

-- platform.operator
create policy origenlab_api_select on platform.operator for select to origenlab_api using (true);
create policy origenlab_api_insert on platform.operator for insert to origenlab_api with check (true);
create policy origenlab_api_update on platform.operator for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on platform.operator for select to origenlab_worker using (true);

-- platform.command_receipt
create policy origenlab_api_select on platform.command_receipt for select to origenlab_api using (true);
create policy origenlab_api_insert on platform.command_receipt for insert to origenlab_api with check (true);
create policy origenlab_api_update on platform.command_receipt for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on platform.command_receipt for select to origenlab_worker using (true);

reset role;
