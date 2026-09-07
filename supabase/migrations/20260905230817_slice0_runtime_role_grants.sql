-- Slice 0 / M12 — runtime role grants: per table, per verb, for origenlab_api and origenlab_worker.
--
-- docs/ARCHITECTURE.md §3.1 (one-writer rules), §6.1 point 3 (grants are the primary
-- least-privilege mechanism; no GRANT ALL ON ALL TABLES IN SCHEMA), §6.4 (boundary);
-- docs/MIGRATION.md §5.2 checks 6-8.
--
-- Rules encoded here:
--   * origenlab_api holds DML on crm.*, platform.* and the campaign lifecycle tables, SELECT
--     elsewhere, plus the narrow UPDATEs FastAPI performs for operator resolution and promotion.
--   * origenlab_worker holds DML on comms.*, evidence.*, procurement.* and catalog.*, SELECT
--     elsewhere. It holds no INSERT on crm.domain_event and no DML on crm.* in Slice 0.
--   * outbound.send_attempt, outbound.contact_control and outbound.send_control: SELECT only for
--     both roles — every write arrives through the closed SECURITY DEFINER list in Slice 5.
--   * No runtime role receives DELETE on any table. Never-deleted and append-only tables are also
--     guarded by triggers; where a command later needs a deletion it is granted by migration.
--   * Where a table is "never edited" but a command closes or links a row, UPDATE is granted
--     column by column (crm.affiliation, crm.address, crm.opportunity_participant, comms.message,
--     comms.message_participant, comms.attachment, evidence.*, procurement.notice).
--   * Trigger functions need no EXECUTE for the invoking role; PostgreSQL checks EXECUTE on a
--     trigger function at CREATE TRIGGER time, not when the trigger fires. A CHECK constraint that
--     calls a function is evaluated as the inserting role, so crm.domain_event_is_valid is granted
--     to the only role that inserts domain events.
--
-- Generated from one privilege matrix together with the policies migration and the pgTAP
-- expectations (supabase/tests), so the three cannot drift apart silently.

set role origenlab_owner;

grant select, insert, update on crm.organization to origenlab_api;
grant select on crm.organization to origenlab_worker;

grant select, insert, update on crm.organization_domain to origenlab_api;
grant select on crm.organization_domain to origenlab_worker;

grant select, insert, update on crm.organization_relationship to origenlab_api;
grant select on crm.organization_relationship to origenlab_worker;

grant select, insert, update on crm.external_identifier to origenlab_api;
grant select on crm.external_identifier to origenlab_worker;

grant select, insert, update on crm.person to origenlab_api;
grant select on crm.person to origenlab_worker;

grant select, insert on crm.affiliation to origenlab_api;
grant update (valid_to, confirmation, confirmed_by_operator_id, note, updated_at) on crm.affiliation to origenlab_api;
grant select on crm.affiliation to origenlab_worker;

grant select, insert, update on crm.contact_point to origenlab_api;
grant select on crm.contact_point to origenlab_worker;

grant select, insert on crm.address to origenlab_api;
grant update (valid_to, superseded_by_address_id, confirmation, confirmed_by_operator_id, note, updated_at) on crm.address to origenlab_api;
grant select on crm.address to origenlab_worker;

grant select, insert, update on crm.opportunity to origenlab_api;
grant select on crm.opportunity to origenlab_worker;

grant select, insert on crm.opportunity_participant to origenlab_api;
grant update (person_id, is_primary, valid_to, confirmation, confirmed_by_operator_id, note, updated_at) on crm.opportunity_participant to origenlab_api;
grant select on crm.opportunity_participant to origenlab_worker;

grant select, insert, update on crm.task to origenlab_api;
grant select on crm.task to origenlab_worker;

grant select, insert on crm.activity to origenlab_api;
grant select on crm.activity to origenlab_worker;

grant select, insert on crm.domain_event to origenlab_api;
grant select on crm.domain_event to origenlab_worker;

grant select, insert, update on crm.quote to origenlab_api;
grant select on crm.quote to origenlab_worker;

grant select, insert, update on crm.quote_revision to origenlab_api;
grant select on crm.quote_revision to origenlab_worker;

grant select, insert, update on crm.quote_line to origenlab_api;
grant select on crm.quote_line to origenlab_worker;

grant select on comms.mailbox to origenlab_api;
grant select, insert, update on comms.mailbox to origenlab_worker;

grant select on comms.message to origenlab_api;
grant select, insert on comms.message to origenlab_worker;
grant update (labels, send_attempt_id, parse_status, parse_error, eml_storage_path, eml_sha256, size_bytes) on comms.message to origenlab_worker;

grant select on comms.message_participant to origenlab_api;
grant update (resolved_contact_point_id) on comms.message_participant to origenlab_api;
grant select, insert on comms.message_participant to origenlab_worker;

grant select on comms.attachment to origenlab_api;
grant select, insert on comms.attachment to origenlab_worker;
grant update (storage_path, sha256, size_bytes) on comms.attachment to origenlab_worker;

grant select on outbound.send_control to origenlab_api;
grant select on outbound.send_control to origenlab_worker;

grant select, insert, update on outbound.campaign to origenlab_api;
grant select on outbound.campaign to origenlab_worker;

grant select, insert, update on outbound.campaign_recipient to origenlab_api;
grant select on outbound.campaign_recipient to origenlab_worker;

grant select on outbound.send_attempt to origenlab_api;
grant select on outbound.send_attempt to origenlab_worker;

grant select on outbound.contact_control to origenlab_api;
grant select on outbound.contact_control to origenlab_worker;

grant select on evidence.source_record to origenlab_api;
grant update (review_status, is_quarantined, quarantine_reason, quarantined_at, updated_at) on evidence.source_record to origenlab_api;
grant select, insert, update on evidence.source_record to origenlab_worker;

grant select on evidence.assertion to origenlab_api;
grant update (resolution, resolved_kind, resolved_id, resolved_at, resolved_by_operator_id, ambiguity_note, updated_at) on evidence.assertion to origenlab_api;
grant select, insert on evidence.assertion to origenlab_worker;

grant select, insert, update on catalog.product to origenlab_api;
grant select on catalog.product to origenlab_worker;

grant select, insert on catalog.supplier_product to origenlab_api;
grant select, insert on catalog.supplier_product to origenlab_worker;

grant select on procurement.notice to origenlab_api;
grant update (promoted_opportunity_id, updated_at) on procurement.notice to origenlab_api;
grant select, insert, update on procurement.notice to origenlab_worker;

grant select, insert, update on platform.operator to origenlab_api;
grant select on platform.operator to origenlab_worker;

grant select, insert, update on platform.command_receipt to origenlab_api;
grant select on platform.command_receipt to origenlab_worker;

-- Function privileges (see the note above).
grant execute on function crm.domain_event_is_valid(text, text, smallint, jsonb) to origenlab_api;

reset role;
