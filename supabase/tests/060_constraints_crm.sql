-- Slice 0 — declarative invariants of the crm and platform tables, exercised as the owner (which
-- crosses RLS by ownership and holds every privilege, so only the constraints and structural
-- triggers speak). docs/DOMAIN.md §2, §3, §7; docs/WORKFLOWS.md §1.1, §1.2, §W3a.
-- SQLSTATEs: 23505 unique, 23514 check, 23P01 exclusion, 23503 foreign key, P0001 trigger guard.
begin;
create extension if not exists pgtap with schema extensions;
-- Fixture (rolled back): the owner may call pgTAP for the duration of this transaction.
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(101);

-- platform.operator / platform.command_receipt (#29, #30)
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
values ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000f1', 'admin@example.test', 'Admin', 'admin', 'active');
select throws_ok($$ insert into platform.operator (auth_user_id, email_norm, display_name, role, status)
  values ('00000000-0000-4000-8000-0000000000f1', 'other@example.test', 'X', 'sales', 'active') $$, '23505', null, 'operator: auth uid unique');
select throws_ok($$ insert into platform.operator (auth_user_id, email_norm, display_name, role, status)
  values (gen_random_uuid(), 'x@example.test', 'X', 'owner', 'active') $$, '23514', null, 'operator: role ∈ {admin, sales, viewer}');
select throws_ok($$ insert into platform.operator (auth_user_id, email_norm, display_name, role, status)
  values (gen_random_uuid(), 'Mixed@Example.test', 'X', 'sales', 'active') $$, '23514', null, 'operator: email_norm is lower-cased');
insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status)
values ('00000000-0000-4000-8000-0000000000a1', 'key-1', 'create_opportunity', repeat('a', 64), 'in_progress');
select throws_ok($$ insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status)
  values ('00000000-0000-4000-8000-0000000000a1', 'key-1', 'create_opportunity', repeat('b', 64), 'in_progress') $$, '23505', null, 'command_receipt: (operator, idempotency_key) unique');
select throws_ok($$ insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status)
  values ('00000000-0000-4000-8000-0000000000a1', 'key-2', 'create_opportunity', 'not-a-digest', 'in_progress') $$, '23514', null, 'command_receipt: request_digest is a sha256 hex');
select throws_ok($$ insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status, completed_at)
  values ('00000000-0000-4000-8000-0000000000a1', 'key-3', 'x', repeat('a', 64), 'in_progress', now()) $$, '23514', null, 'command_receipt: in_progress ⇔ completed_at IS NULL');

-- crm.organization (#1)
insert into crm.organization (id, kind, name, confirmation) values ('00000000-0000-4000-8000-000000000001', 'company', 'Root Org', 'confirmed');
insert into crm.organization (id, kind, name, confirmation, parent_organization_id) values ('00000000-0000-4000-8000-000000000002', 'unit', 'Faculty', 'confirmed', '00000000-0000-4000-8000-000000000001');
insert into crm.organization (id, kind, name, confirmation, parent_organization_id) values ('00000000-0000-4000-8000-000000000003', 'unit', 'Laboratory', 'confirmed', '00000000-0000-4000-8000-000000000002');
select throws_ok($$ update crm.organization set parent_organization_id = '00000000-0000-4000-8000-000000000003' where id = '00000000-0000-4000-8000-000000000001' $$, 'P0001', null, 'organization: a parent cycle is rejected by trigger');
-- On UPDATE the BEFORE trigger fires before the CHECK, so a self-parent is caught as a cycle (P0001);
-- on INSERT the new row is not yet visible to the trigger's walk, so the CHECK constraint speaks.
select throws_ok($$ update crm.organization set parent_organization_id = id where id = '00000000-0000-4000-8000-000000000001' $$, 'P0001', null, 'organization: a self parent on UPDATE is a cycle');
select throws_ok($$ insert into crm.organization (id, kind, name, confirmation, parent_organization_id) values ('00000000-0000-4000-8000-000000000009', 'unit', 'Self', 'confirmed', '00000000-0000-4000-8000-000000000009') $$, '23514', null, 'organization: no self parent (CHECK on INSERT)');
select lives_ok($$ insert into crm.organization (kind, name, confirmation, parent_organization_id) values ('unit', 'Depth 3', 'confirmed', '00000000-0000-4000-8000-000000000003') $$, 'organization: hierarchy depth is unconstrained');
select throws_ok($$ insert into crm.organization (kind, name, confirmation) values ('Bad Kind', 'x', 'confirmed') $$, '23514', null, 'organization: kind is a lower_snake_case token');
select throws_ok($$ insert into crm.organization (kind, name, confirmation) values ('company', 'x', 'guessed') $$, '23514', null, 'organization: confirmation ∈ {machine_proposed, confirmed}');
select throws_ok($$ insert into crm.organization (kind, name, confirmation, confirmed_by_operator_id) values ('company', 'x', 'machine_proposed', '00000000-0000-4000-8000-0000000000a1') $$, '23514', null, 'organization: a machine-proposed row has no confirmer');
select throws_ok($$ insert into crm.organization (kind, name, confirmation) values ('company', '   ', 'confirmed') $$, '23514', null, 'organization: name is not blank');

-- crm.organization_domain (#2)
insert into crm.organization_domain (organization_id, domain_norm, scope) values ('00000000-0000-4000-8000-000000000001', 'uni.example', 'exclusive');
select throws_ok($$ insert into crm.organization_domain (organization_id, domain_norm, scope) values ('00000000-0000-4000-8000-000000000002', 'uni.example', 'exclusive') $$, '23505', null, 'organization_domain: at most one exclusive owner per domain');
select lives_ok($$ insert into crm.organization_domain (organization_id, domain_norm, scope) values ('00000000-0000-4000-8000-000000000002', 'uni.example', 'shared') $$, 'organization_domain: a shared mapping may coexist');
select throws_ok($$ insert into crm.organization_domain (organization_id, domain_norm, scope) values ('00000000-0000-4000-8000-000000000001', 'Upper.Example', 'exclusive') $$, '23514', null, 'organization_domain: domain_norm is lower-cased');
select throws_ok($$ insert into crm.organization_domain (organization_id, domain_norm, scope) values ('00000000-0000-4000-8000-000000000001', 'other.example', 'primary') $$, '23514', null, 'organization_domain: scope ∈ {exclusive, shared}');

-- crm.organization_relationship (#3)
insert into crm.organization_relationship (organization_id, role, valid_from) values ('00000000-0000-4000-8000-000000000001', 'customer', '2024-01-01');
select throws_ok($$ insert into crm.organization_relationship (organization_id, role, valid_from) values ('00000000-0000-4000-8000-000000000001', 'customer', '2025-06-01') $$, '23P01', null, 'organization_relationship: the same role may not overlap itself in time');
select lives_ok($$ insert into crm.organization_relationship (organization_id, role, valid_from) values ('00000000-0000-4000-8000-000000000001', 'supplier', '2024-01-01') $$, 'organization_relationship: roles coexist');
select throws_ok($$ insert into crm.organization_relationship (organization_id, role, valid_from, valid_to) values ('00000000-0000-4000-8000-000000000002', 'prospect', '2024-01-01', '2024-01-01') $$, '23514', null, 'organization_relationship: valid_to > valid_from');
select throws_ok($$ insert into crm.organization_relationship (organization_id, role, valid_from) values ('00000000-0000-4000-8000-000000000002', 'lead', '2024-01-01') $$, '23514', null, 'organization_relationship: role is closed (no lead role)');

-- crm.person (#5) and crm.affiliation (#6)
insert into crm.person (id, display_name, confirmation) values ('00000000-0000-4000-8000-000000000010', 'Dr. Example', 'confirmed');
insert into crm.affiliation (id, person_id, organization_id, role_title, valid_from, confirmation)
values ('00000000-0000-4000-8000-000000000020', '00000000-0000-4000-8000-000000000010', '00000000-0000-4000-8000-000000000001', 'Director', '2024-01-01', 'confirmed');
select throws_ok($$ insert into crm.affiliation (person_id, organization_id, role_title, valid_from, confirmation)
  values ('00000000-0000-4000-8000-000000000010', '00000000-0000-4000-8000-000000000001', '  director ', '2025-01-01', 'confirmed') $$, '23P01', null, 'affiliation: the same normalized role may not overlap itself in time');
select lives_ok($$ insert into crm.affiliation (person_id, organization_id, role_title, valid_from, confirmation)
  values ('00000000-0000-4000-8000-000000000010', '00000000-0000-4000-8000-000000000001', 'Purchasing contact', '2024-01-01', 'confirmed') $$, 'affiliation: concurrent distinct roles at the same organization are allowed');
update crm.affiliation set valid_to = '2025-01-01' where id = '00000000-0000-4000-8000-000000000020';
select lives_ok($$ insert into crm.affiliation (person_id, organization_id, role_title, valid_from, confirmation)
  values ('00000000-0000-4000-8000-000000000010', '00000000-0000-4000-8000-000000000001', 'Director', '2025-01-01', 'confirmed') $$, 'affiliation: the same role may resume once the previous period is closed ([) ranges)');
select throws_ok($$ delete from crm.affiliation where id = '00000000-0000-4000-8000-000000000020' $$, 'P0001', null, 'affiliation: rows are never deleted (trigger guard, even for the owner)');
select throws_ok($$ insert into crm.affiliation (person_id, organization_id, valid_from, valid_to, confirmation)
  values ('00000000-0000-4000-8000-000000000010', '00000000-0000-4000-8000-000000000002', '2024-02-01', '2024-01-01', 'confirmed') $$, '23514', null, 'affiliation: valid_to > valid_from');

-- crm.contact_point (#7)
insert into crm.contact_point (id, kind, value_norm, value_display, person_id, usage, confirmation)
values ('00000000-0000-4000-8000-000000000030', 'email', 'dr@example.test', 'Dr@Example.test', '00000000-0000-4000-8000-000000000010', 'personal', 'confirmed');
select throws_ok($$ insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) values ('email', 'dr@example.test', 'x', 'unattributed', 'confirmed') $$, '23505', null, 'contact_point: (kind, value_norm) globally unique');
select throws_ok($$ insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) values ('email', 'work@example.test', 'x', '00000000-0000-4000-8000-000000000010', 'work', 'confirmed') $$, '23514', null, 'contact_point: work ⇒ organization NOT NULL');
select throws_ok($$ insert into crm.contact_point (kind, value_norm, value_display, person_id, organization_id, usage, confirmation) values ('email', 'p2@example.test', 'x', '00000000-0000-4000-8000-000000000010', '00000000-0000-4000-8000-000000000001', 'personal', 'confirmed') $$, '23514', null, 'contact_point: personal ⇒ organization NULL');
select throws_ok($$ insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) values ('email', 'shared@example.test', 'x', '00000000-0000-4000-8000-000000000010', 'shared_mailbox', 'confirmed') $$, '23514', null, 'contact_point: shared_mailbox ⇒ person NULL');
select throws_ok($$ insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) values ('email', 'u@example.test', 'x', '00000000-0000-4000-8000-000000000010', 'unattributed', 'confirmed') $$, '23514', null, 'contact_point: unattributed ⇒ both NULL');
select throws_ok($$ insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) values ('email', 'Upper@example.test', 'x', 'unattributed', 'confirmed') $$, '23514', null, 'contact_point: email value_norm is lower-cased');
select throws_ok($$ insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) values ('phone', '56912345678', 'x', 'unattributed', 'confirmed') $$, '23514', null, 'contact_point: phone value_norm is E.164');
select lives_ok($$ insert into crm.contact_point (kind, value_norm, value_display, organization_id, usage, confirmation) values ('email', 'compras@uni.example', 'compras@uni.example', '00000000-0000-4000-8000-000000000001', 'shared_mailbox', 'confirmed') $$, 'contact_point: a shared mailbox operated by the root organization');
select throws_ok($$ insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) values ('fax', '+56912345678', 'x', 'unattributed', 'confirmed') $$, '23514', null, 'contact_point: kind ∈ {email, phone}');

-- crm.address (#31)
insert into crm.address (id, organization_id, street_line_1, locality, administrative_area, country_code, valid_from, confirmation)
values ('00000000-0000-4000-8000-000000000040', '00000000-0000-4000-8000-000000000001', 'Av. Ejemplo 123', 'Santiago', 'Región Metropolitana', 'CL', '2024-01-01', 'confirmed');
select throws_ok($$ insert into crm.address (organization_id, street_line_1, locality, administrative_area, country_code, valid_from, confirmation)
  values ('00000000-0000-4000-8000-000000000001', '  av. ejemplo   123 ', 'santiago', 'región metropolitana', 'CL', '2024-06-01', 'confirmed') $$, '23505', null, 'address: an exact duplicate among current rows is refused (normalized address_key)');
select throws_ok($$ insert into crm.address (organization_id, street_line_1, locality, country_code, valid_from, confirmation, superseded_by_address_id)
  values ('00000000-0000-4000-8000-000000000001', 'Other 1', 'Santiago', 'CL', '2024-01-01', 'confirmed', '00000000-0000-4000-8000-000000000040') $$, '23514', null, 'address: a superseded row must have valid_to set');
select throws_ok($$ insert into crm.address (organization_id, street_line_1, locality, country_code, valid_from, confirmation)
  values ('00000000-0000-4000-8000-000000000001', 'Other 2', 'Santiago', 'cl', '2024-01-01', 'confirmed') $$, '23514', null, 'address: country_code is ISO 3166-1 alpha-2 upper-case');
select throws_ok($$ insert into crm.address (organization_id, street_line_1, locality, country_code, valid_from, confirmation)
  values ('00000000-0000-4000-8000-000000000001', '', 'Santiago', 'CL', '2024-01-01', 'confirmed') $$, '23514', null, 'address: street_line_1 is required');
select throws_ok($$ delete from crm.address where id = '00000000-0000-4000-8000-000000000040' $$, 'P0001', null, 'address: rows are never deleted (trigger guard)');
select lives_ok($$
  insert into crm.address (id, organization_id, street_line_1, locality, administrative_area, country_code, valid_from, confirmation)
  values ('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001', 'Av. Ejemplo 456', 'Santiago', 'Región Metropolitana', 'CL', '2025-01-01', 'confirmed');
  update crm.address set valid_to = '2025-01-01', superseded_by_address_id = '00000000-0000-4000-8000-000000000041' where id = '00000000-0000-4000-8000-000000000040';
$$, 'address: supersession closes the old row and links the new one');
select lives_ok($$ insert into crm.address (organization_id, street_line_1, locality, administrative_area, country_code, valid_from, confirmation)
  values ('00000000-0000-4000-8000-000000000001', 'Av. Ejemplo 123', 'Santiago', 'Región Metropolitana', 'CL', '2026-01-01', 'confirmed') $$, 'address: a closed row no longer blocks the same place from being current again');

-- crm.opportunity (#8) and crm.opportunity_participant (#32)
insert into crm.opportunity (id, title, stage, owner_operator_id) values ('00000000-0000-4000-8000-000000000050', 'Lead without organization', 'lead', '00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ insert into crm.opportunity (title, stage, owner_operator_id) values ('x', 'qualified', '00000000-0000-4000-8000-0000000000a1') $$, '23514', null, 'opportunity: organization is mandatory from qualified onward');
select throws_ok($$ insert into crm.opportunity (title, stage, owner_operator_id, organization_id) values ('x', 'won', '00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-000000000001') $$, '23514', null, 'opportunity: won ⇔ (won_quote_id, won_revision_no) set');
select throws_ok($$ insert into crm.opportunity (title, stage, owner_operator_id, organization_id) values ('x', 'lost', '00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-000000000001') $$, '23514', null, 'opportunity: a terminal stage ⇔ closed_at set');
select throws_ok($$ insert into crm.opportunity (title, stage, owner_operator_id, closed_at) values ('x', 'lead', '00000000-0000-4000-8000-0000000000a1', now()) $$, '23514', null, 'opportunity: an open stage has no closed_at');
select throws_ok($$ insert into crm.opportunity (title, stage, owner_operator_id) values ('x', 'converted', '00000000-0000-4000-8000-0000000000a1') $$, '23514', null, 'opportunity: stage is closed');
insert into crm.opportunity_participant (id, opportunity_id, contact_point_id, role, is_primary, valid_from, confirmation)
values ('00000000-0000-4000-8000-000000000060', '00000000-0000-4000-8000-000000000050', '00000000-0000-4000-8000-000000000030', 'purchasing', true, '2024-01-01', 'confirmed');
select throws_ok($$ insert into crm.opportunity_participant (opportunity_id, role, valid_from, confirmation) values ('00000000-0000-4000-8000-000000000050', 'other', '2024-01-01', 'confirmed') $$, '23514', null, 'participant: a person and/or a contact point is required');
select throws_ok($$ insert into crm.opportunity_participant (opportunity_id, contact_point_id, role, valid_from, confirmation) values ('00000000-0000-4000-8000-000000000050', '00000000-0000-4000-8000-000000000030', 'purchasing', '2025-01-01', 'confirmed') $$, '23P01', null, 'participant: the same subject and role may not overlap in time');
select throws_ok($$ insert into crm.opportunity_participant (opportunity_id, person_id, role, is_primary, valid_from, confirmation) values ('00000000-0000-4000-8000-000000000050', '00000000-0000-4000-8000-000000000010', 'purchasing', true, '2024-01-01', 'confirmed') $$, '23505', null, 'participant: at most one current primary per role');
select lives_ok($$ insert into crm.opportunity_participant (opportunity_id, person_id, role, is_primary, valid_from, confirmation) values ('00000000-0000-4000-8000-000000000050', '00000000-0000-4000-8000-000000000010', 'technical', true, '2024-01-01', 'confirmed');
  insert into crm.opportunity_participant (opportunity_id, person_id, role, is_primary, valid_from, confirmation) values ('00000000-0000-4000-8000-000000000050', '00000000-0000-4000-8000-000000000010', 'quote_recipient', true, '2024-01-01', 'confirmed') $$, 'participant: one person may hold several concurrent roles, each with its own primary');
select lives_ok($$ update crm.opportunity_participant set person_id = '00000000-0000-4000-8000-000000000010' where id = '00000000-0000-4000-8000-000000000060' $$, 'participant: link_participant_person updates the same row');
select throws_ok($$ delete from crm.opportunity_participant where id = '00000000-0000-4000-8000-000000000060' $$, 'P0001', null, 'participant: rows are never deleted (trigger guard)');
select throws_ok($$ insert into crm.opportunity_participant (opportunity_id, person_id, role, valid_from, confirmation) values ('00000000-0000-4000-8000-000000000050', '00000000-0000-4000-8000-000000000010', 'champion', '2024-01-01', 'confirmed') $$, '23514', null, 'participant: role is closed');

-- crm.task (#9)
select throws_ok($$ insert into crm.task (opportunity_id, owner_operator_id, title, due_at, status) values ('00000000-0000-4000-8000-000000000050', '00000000-0000-4000-8000-0000000000a1', 'x', now(), 'done') $$, '23514', null, 'task: done ⇒ completed_at set');
select throws_ok($$ insert into crm.task (opportunity_id, owner_operator_id, title, due_at, status, completed_at) values ('00000000-0000-4000-8000-000000000050', '00000000-0000-4000-8000-0000000000a1', 'x', now(), 'open', now()) $$, '23514', null, 'task: completed_at ⇒ done');
select throws_ok($$ insert into crm.task (opportunity_id, owner_operator_id, title, due_at, status) values ('00000000-0000-4000-8000-000000000050', '00000000-0000-4000-8000-0000000000a1', 'x', now(), 'cancelled') $$, '23514', null, 'task: cancelled ⇒ cancel_reason');

-- crm.domain_event (#11)
insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
values ('opportunity', '00000000-0000-4000-8000-000000000050', 1, 'opportunity.created', 1, '{"stage": "lead"}', 'operator', '00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '00000000-0000-4000-8000-000000000050', 1, 'opportunity.staged', 1, '{}', 'operator', '00000000-0000-4000-8000-0000000000a1') $$, '23505', null, 'domain_event: (aggregate_kind, aggregate_id, seq) unique');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '00000000-0000-4000-8000-000000000050', 2, 'opportunity.deleted', 1, '{}', 'operator', '00000000-0000-4000-8000-0000000000a1') $$, '23514', null, 'domain_event: event_type is closed');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '00000000-0000-4000-8000-000000000050', 2, 'task.created', 1, '{}', 'operator', '00000000-0000-4000-8000-0000000000a1') $$, '23514', null, 'domain_event: the event_type family must match aggregate_kind');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '00000000-0000-4000-8000-000000000050', 2, 'opportunity.staged', 1, '[]', 'operator', '00000000-0000-4000-8000-0000000000a1') $$, '23514', null, 'domain_event: payload is a JSON object');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '00000000-0000-4000-8000-000000000050', 2, 'opportunity.staged', 2, '{}', 'operator', '00000000-0000-4000-8000-0000000000a1') $$, '23514', null, 'domain_event: payload_version must be a defined version');
select throws_ok($$ insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('opportunity', '00000000-0000-4000-8000-000000000050', 2, 'opportunity.staged', 1, '{}', 'operator') $$, '23514', null, 'domain_event: an operator event names its operator');
select throws_ok($$ update crm.domain_event set payload = '{}' where aggregate_id = '00000000-0000-4000-8000-000000000050' $$, 'P0001', null, 'domain_event: no UPDATE, even for the owner');
select throws_ok($$ delete from crm.domain_event where aggregate_id = '00000000-0000-4000-8000-000000000050' $$, 'P0001', null, 'domain_event: no DELETE, even for the owner');

-- crm.quote (#12), crm.quote_revision (#13), crm.quote_line (#14)
insert into crm.opportunity (id, title, stage, owner_operator_id, organization_id) values ('00000000-0000-4000-8000-000000000051', 'Quoting opportunity', 'quoting', '00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-000000000001');
insert into crm.opportunity (id, title, stage, owner_operator_id, organization_id) values ('00000000-0000-4000-8000-000000000052', 'Another opportunity', 'negotiating', '00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-000000000001');
insert into crm.quote (id, opportunity_id, quote_number) values ('00000000-0000-4000-8000-000000000070', '00000000-0000-4000-8000-000000000051', 'Q-2026-0001');
select throws_ok($$ insert into crm.quote (opportunity_id, quote_number) values ('00000000-0000-4000-8000-000000000052', 'Q-2026-0001') $$, '23505', null, 'quote: number unique');
insert into crm.quote_revision (id, quote_id, revision_no, status, quote_currency, price_decimals)
values ('00000000-0000-4000-8000-000000000080', '00000000-0000-4000-8000-000000000070', 1, 'draft', 'CLP', 0);
select throws_ok($$ insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals) values ('00000000-0000-4000-8000-000000000070', 2, 'in_review', 'CLP', 0) $$, '23505', null, 'quote_revision: at most one revision per quote in {draft, in_review}');
select throws_ok($$ insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals, party_snapshot, party_snapshot_version) values ('00000000-0000-4000-8000-000000000070', 2, 'void', 'CLP', 0, '{}', 1);
  update crm.quote_revision set party_snapshot = '{}', party_snapshot_version = 1 where id = '00000000-0000-4000-8000-000000000080' $$, '23514', null, 'quote_revision: the party snapshot is NULL while draft');
select throws_ok($$ update crm.quote_revision set status = 'approved', approved_at = now(), approved_by_operator_id = '00000000-0000-4000-8000-0000000000a1' where id = '00000000-0000-4000-8000-000000000080' $$, '23514', null, 'quote_revision: approved requires stored totals and the party snapshot');
select throws_ok($$ insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals) values ('00000000-0000-4000-8000-000000000070', 3, 'draft', 'CLP', 3) $$, '23514', null, 'quote_revision: price_decimals ∈ {0, 2}');
select throws_ok($$ insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals) values ('00000000-0000-4000-8000-000000000070', 3, 'draft', 'clp', 0) $$, '23514', null, 'quote_revision: currency is ISO 4217 upper-case');
select throws_ok($$ update crm.quote_revision set superseded_by_revision_no = 2, superseded_at = now() where id = '00000000-0000-4000-8000-000000000080' $$, '23514', null, 'quote_revision: only an approved or sent revision can be superseded');
select lives_ok($$
  update crm.quote_revision
     set status = 'approved', approved_at = now(), approved_by_operator_id = '00000000-0000-4000-8000-0000000000a1',
         subtotal = 100, discount_total = 0, tax_base = 100, tax_total = 19, grand_total = 119, totals_computed_at = now(),
         party_snapshot = '{"sold_to": {}}', party_snapshot_version = 1
   where id = '00000000-0000-4000-8000-000000000080' $$, 'quote_revision: approval with totals and snapshot present');
select throws_ok($$ update crm.quote_revision set status = 'sent', sent_at = now() where id = '00000000-0000-4000-8000-000000000080' $$, '23514', null, 'quote_revision: sent requires pdf_sha256 and exactly one sending evidence id');
select throws_ok($$ update crm.quote_revision set party_snapshot = null, party_snapshot_version = null where id = '00000000-0000-4000-8000-000000000080' $$, '23514', null, 'quote_revision: an approved revision keeps its snapshot');
insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, is_principal) values ('00000000-0000-4000-8000-000000000080', 1, 'item', 'Instrument', 1, true);
insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity) values ('00000000-0000-4000-8000-000000000080', 2, 'fee', 'Installation', 1);
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, is_principal) values ('00000000-0000-4000-8000-000000000080', 3, 'item', 'Second', 1, true) $$, '23505', null, 'quote_line: at most one principal item per revision');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, is_principal) values ('00000000-0000-4000-8000-000000000080', 3, 'logistics', 'Freight', 1, true) $$, '23514', null, 'quote_line: a principal is always an item line');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, allocated_to_line_no) values ('00000000-0000-4000-8000-000000000080', 3, 'logistics', 'Freight', 1, 2) $$, '23503', null, 'quote_line: logistics may be allocated only to an item line');
select lives_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, allocated_to_line_no) values ('00000000-0000-4000-8000-000000000080', 3, 'logistics', 'Freight', 1, 1) $$, 'quote_line: logistics allocated to the item line');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, allocated_to_line_no) values ('00000000-0000-4000-8000-000000000080', 4, 'fee', 'Fee', 1, 1) $$, '23514', null, 'quote_line: only logistics lines allocate');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, cost_currency, unit_cost) values ('00000000-0000-4000-8000-000000000080', 4, 'item', 'Partial', 1, 'USD', 10) $$, '23514', null, 'quote_line: the cost/FX snapshot is all five columns or none');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, margin_mode, margin_pct) values ('00000000-0000-4000-8000-000000000080', 4, 'item', 'Margin', 1, 'margin', 1.0) $$, '23514', null, 'quote_line: margin_pct < 1 in margin mode');
select throws_ok($$ insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, margin_mode) values ('00000000-0000-4000-8000-000000000080', 4, 'item', 'Qty', 0, 'none') $$, '23514', null, 'quote_line: quantity > 0');

-- Won links: the won quote belongs to this opportunity and the revision exists.
select throws_ok($$ update crm.opportunity set stage = 'won', won_quote_id = '00000000-0000-4000-8000-000000000070', won_revision_no = 1, closed_at = now() where id = '00000000-0000-4000-8000-000000000052' $$, '23503', null, 'opportunity: the won quote must belong to this opportunity');
select throws_ok($$ update crm.opportunity set stage = 'won', won_quote_id = '00000000-0000-4000-8000-000000000070', won_revision_no = 9, closed_at = now() where id = '00000000-0000-4000-8000-000000000051' $$, '23503', null, 'opportunity: the won revision must exist on the won quote');
select lives_ok($$ update crm.opportunity set stage = 'won', won_quote_id = '00000000-0000-4000-8000-000000000070', won_revision_no = 1, closed_at = now() where id = '00000000-0000-4000-8000-000000000051' $$, 'opportunity: won with its own quote and revision');

-- crm.external_identifier (#4)
insert into crm.external_identifier (scheme, value_norm, organization_id) values ('rut', '76.123.456-7', '00000000-0000-4000-8000-000000000001');
select throws_ok($$ insert into crm.external_identifier (scheme, value_norm, organization_id) values ('rut', '76.123.456-7', '00000000-0000-4000-8000-000000000002') $$, '23505', null, 'external_identifier: (scheme, value_norm) unique');
select throws_ok($$ insert into crm.external_identifier (scheme, value_norm, organization_id, person_id) values ('v1_contact', '42', '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000010') $$, '23514', null, 'external_identifier: exactly one typed subject (two refused)');
select throws_ok($$ insert into crm.external_identifier (scheme, value_norm) values ('v1_contact', '43') $$, '23514', null, 'external_identifier: exactly one typed subject (none refused)');
select throws_ok($$ insert into crm.external_identifier (scheme, value_norm, person_id) values ('linkedin', 'x', '00000000-0000-4000-8000-000000000010') $$, '23514', null, 'external_identifier: scheme is closed');
select lives_ok($$ insert into crm.external_identifier (scheme, value_norm, quote_id) values ('v1_quote', '1042', '00000000-0000-4000-8000-000000000070') $$, 'external_identifier: a quote subject');

-- crm.activity (#10)
insert into crm.activity (id, opportunity_id, kind, occurred_at, summary, recorded_by_operator_id) values ('00000000-0000-4000-8000-000000000090', '00000000-0000-4000-8000-000000000051', 'note', now(), 'Called the lab', '00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ insert into crm.activity (opportunity_id, kind, occurred_at, recorded_by_operator_id) values ('00000000-0000-4000-8000-000000000051', 'email', now(), '00000000-0000-4000-8000-0000000000a1') $$, '23514', null, 'activity: kind email ⇔ a linked message');
select throws_ok($$ insert into crm.activity (opportunity_id, kind, occurred_at, recorded_by_operator_id) values ('00000000-0000-4000-8000-000000000051', 'note', now(), '00000000-0000-4000-8000-0000000000a1') $$, '23514', null, 'activity: a note carries a summary');
select throws_ok($$ update crm.activity set summary = 'edited' where id = '00000000-0000-4000-8000-000000000090' $$, 'P0001', null, 'activity: append-only (no UPDATE, even for the owner)');
select throws_ok($$ delete from crm.activity where id = '00000000-0000-4000-8000-000000000090' $$, 'P0001', null, 'activity: append-only (no DELETE, even for the owner)');

-- catalog.product (#26), catalog.supplier_product (#27)
insert into catalog.product (id, manufacturer_organization_id, model_number) values ('00000000-0000-4000-8000-0000000000b0', '00000000-0000-4000-8000-000000000001', 'MX-100');
select throws_ok($$ insert into catalog.product (manufacturer_organization_id, model_number) values ('00000000-0000-4000-8000-000000000001', 'MX-100') $$, '23505', null, 'product: (manufacturer, model_number) unique');
insert into catalog.supplier_product (id, supplier_organization_id, product_id, as_of, price, currency, provenance_note) values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000000b0', '2026-01-01', 1000, 'USD', 'price list');
select throws_ok($$ insert into catalog.supplier_product (supplier_organization_id, product_id, as_of, price, currency, provenance_note) values ('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000000b0', '2026-01-01', 900, 'USD', 'dup') $$, '23505', null, 'supplier_product: (supplier, product, as_of) unique');
select throws_ok($$ insert into catalog.supplier_product (supplier_organization_id, product_id, as_of, price, currency) values ('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000000b0', '2026-02-01', 900, 'USD') $$, '23514', null, 'supplier_product: a provenance is required');
select throws_ok($$ update catalog.supplier_product set price = 1 where id = '00000000-0000-4000-8000-0000000000b1' $$, 'P0001', null, 'supplier_product: append-only (no UPDATE, even for the owner)');
select throws_ok($$ delete from catalog.supplier_product where id = '00000000-0000-4000-8000-0000000000b1' $$, 'P0001', null, 'supplier_product: append-only (no DELETE, even for the owner)');

select * from finish();
rollback;
