-- Slice 6 Part B — CRM authoring: lifecycle columns on person/organization/contact_point/
-- organization_domain/external_identifier, never-deleted guards, crm.note (#44) immutability
-- and guard, crm.organization_product_line (#45) closed-list and active-link constraints,
-- domain_event vocabulary additions, and the exclusive-domain partial-unique fix.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
select plan(74);

grant origenlab_api    to session_user with set true, inherit false;
grant origenlab_worker to session_user with set true, inherit false;

create function pg_temp.run_as(p_role text, p_sql text) returns text
language plpgsql as $$
begin
  execute format('set role %I', p_role);
  execute p_sql;
  reset role;
  return 'ok';
exception when others then
  reset role;
  return sqlstate;
end
$$;
grant execute on function pg_temp.run_as(text, text) to public;

-- ── fixture ────────────────────────────────────────────────────────────────────────────────
set role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('74000000-0000-4000-8000-000000000001', gen_random_uuid(), 'op@test.example', 'Op', 'sales', 'active');
insert into crm.organization (id, kind, name, confirmation) values
  ('74000000-0000-4000-8000-000000000100', 'company', 'Acme', 'confirmed');
insert into crm.person (id, display_name, confirmation, version) values
  ('74000000-0000-4000-8000-000000000200', 'Alex', 'confirmed', 1);
insert into crm.contact_point (id, kind, value_norm, value_display, usage, confirmation) values
  ('74000000-0000-4000-8000-000000000300', 'email', 'alex@acme.test', 'alex@acme.test', 'unattributed', 'confirmed');
insert into crm.organization_domain (id, organization_id, domain_norm, scope) values
  ('74000000-0000-4000-8000-000000000400', '74000000-0000-4000-8000-000000000100', 'acme.test', 'exclusive');
reset role;

-- ── B1a. lifecycle columns: person ────────────────────────────────────────────────────────
select has_column('crm', 'person', 'title', 'person has title column');
select has_column('crm', 'person', 'status', 'person has status column');
select has_column('crm', 'person', 'archived_at', 'person has archived_at column');
select has_column('crm', 'person', 'archived_by_operator_id', 'person has archived_by_operator_id column');
select has_column('crm', 'person', 'archive_reason', 'person has archive_reason column');

-- archived shape: status=archived ⇔ archived_at not null ⇔ by not null ⇔ reason not null
select is(pg_temp.run_as('origenlab_owner', $$update crm.person set status = 'archived' where id = '74000000-0000-4000-8000-000000000200'$$),
  '23514', 'archived person without archived_at is refused (23514)');

-- ── B1b. lifecycle columns: organization ─────────────────────────────────────────────────
select has_column('crm', 'organization', 'status', 'organization has status column');
select has_column('crm', 'organization', 'archived_at', 'organization has archived_at column');
select has_column('crm', 'organization', 'archived_by_operator_id', 'organization has archived_by_operator_id column');
select has_column('crm', 'organization', 'archive_reason', 'organization has archive_reason column');

-- ── B1c. lifecycle columns: contact_point ────────────────────────────────────────────────
select has_column('crm', 'contact_point', 'status', 'contact_point has status column');
select has_column('crm', 'contact_point', 'version', 'contact_point has version column');
select has_column('crm', 'contact_point', 'note', 'contact_point has note column');
select has_column('crm', 'contact_point', 'deactivated_at', 'contact_point has deactivated_at column');
select has_column('crm', 'contact_point', 'deactivated_by_operator_id', 'contact_point has deactivated_by_operator_id column');

-- inactive ⇔ deactivated_at
select is(pg_temp.run_as('origenlab_owner', $$update crm.contact_point set status = 'inactive' where id = '74000000-0000-4000-8000-000000000300'$$),
  '23514', 'inactive contact_point without deactivated_at is refused (23514)');

-- ── B1d. removal triple: organization_domain ─────────────────────────────────────────────
select has_column('crm', 'organization_domain', 'removed_at', 'organization_domain has removed_at column');
select has_column('crm', 'organization_domain', 'removed_by_operator_id', 'organization_domain has removed_by_operator_id column');
select has_column('crm', 'organization_domain', 'remove_reason', 'organization_domain has remove_reason column');

-- all-or-none check: only removed_at set without operator/reason is refused
select is(pg_temp.run_as('origenlab_owner', $$update crm.organization_domain set removed_at = now() where id = '74000000-0000-4000-8000-000000000400'$$),
  '23514', 'partial removal of organization_domain (only removed_at) is refused (23514)');

-- ── B1e. removal triple: external_identifier ─────────────────────────────────────────────
select has_column('crm', 'external_identifier', 'removed_at', 'external_identifier has removed_at column');
select has_column('crm', 'external_identifier', 'removed_by_operator_id', 'external_identifier has removed_by_operator_id column');
select has_column('crm', 'external_identifier', 'remove_reason', 'external_identifier has remove_reason column');

-- ── B2. never-deleted triggers ────────────────────────────────────────────────────────────
select has_trigger('crm', 'person', 'person_never_deleted', 'person_never_deleted trigger exists');
select has_trigger('crm', 'organization', 'organization_never_deleted', 'organization_never_deleted trigger exists');
select has_trigger('crm', 'contact_point', 'contact_point_never_deleted', 'contact_point_never_deleted trigger exists');

-- as owner: P0001 for all three
select is(pg_temp.run_as('origenlab_owner', $$delete from crm.person where false$$),
  'P0001', 'person is never deleted: P0001 even for the owner');
select is(pg_temp.run_as('origenlab_owner', $$delete from crm.organization where false$$),
  'P0001', 'organization is never deleted: P0001 even for the owner');
select is(pg_temp.run_as('origenlab_owner', $$delete from crm.contact_point where false$$),
  'P0001', 'contact_point is never deleted: P0001 even for the owner');

-- as api: 42501 (no DELETE privilege)
select is(pg_temp.run_as('origenlab_api', $$delete from crm.person where false$$),
  '42501', 'api cannot delete person (no DELETE privilege)');
select is(pg_temp.run_as('origenlab_api', $$delete from crm.organization where false$$),
  '42501', 'api cannot delete organization (no DELETE privilege)');
select is(pg_temp.run_as('origenlab_api', $$delete from crm.contact_point where false$$),
  '42501', 'api cannot delete contact_point (no DELETE privilege)');

-- ── B3. crm.note ──────────────────────────────────────────────────────────────────────────
select has_function('crm', 'note_guard', 'crm.note_guard exists');
select is((select prosecdef from pg_proc where oid = 'crm.note_guard()'::regprocedure),
  false, 'note_guard is SECURITY INVOKER');
select is((select proconfig from pg_proc where oid = 'crm.note_guard()'::regprocedure),
  array['search_path=pg_catalog'], 'note_guard pins search_path = pg_catalog');
select has_trigger('crm', 'note', 'note_guard', 'crm.note carries the note_guard trigger');
select is(
  (select count(*)::int from pg_trigger t where t.tgrelid = 'crm.note'::regclass
    and t.tgfoid = 'crm.note_guard()'::regprocedure and t.tgenabled = 'O' and t.tgtype = 27),
  1, 'note has exactly one enabled BEFORE UPDATE OR DELETE row trigger');

-- api INSERT works
select is(pg_temp.run_as('origenlab_api', $$insert into crm.note (id, subject_kind, subject_id, body, author_operator_id, version)
  values ('74000000-0000-4000-8000-000000000900', 'organization', '74000000-0000-4000-8000-000000000100',
    'Test note body', '74000000-0000-4000-8000-000000000001', 1)$$),
  'ok', 'api can INSERT a note');

-- UPDATE that changes body is refused P0001
select is(pg_temp.run_as('origenlab_api', $$update crm.note set body = 'changed body', version = 2
  where id = '74000000-0000-4000-8000-000000000900'$$),
  'P0001', 'note body cannot be changed: P0001');

-- UPDATE that archives with version+1 works
select is(pg_temp.run_as('origenlab_api', $$update crm.note
  set status = 'archived', archived_at = now(), archived_by_operator_id = '74000000-0000-4000-8000-000000000001',
      archive_reason = 'test', version = 2
  where id = '74000000-0000-4000-8000-000000000900'$$),
  'ok', 'api can archive a note with version+1');

-- Second archive-record change is refused P0001
select is(pg_temp.run_as('origenlab_api', $$update crm.note
  set archived_at = now() + interval '1 second', version = 3
  where id = '74000000-0000-4000-8000-000000000900'$$),
  'P0001', 'archived note archive record is immutable: P0001');

-- version not +1 refused P0001 (insert a new active note first)
set role origenlab_owner;
insert into crm.note (id, subject_kind, subject_id, body, author_operator_id, version) values
  ('74000000-0000-4000-8000-000000000901', 'person', '74000000-0000-4000-8000-000000000200',
   'Another note', '74000000-0000-4000-8000-000000000001', 1);
reset role;
select is(pg_temp.run_as('origenlab_api', $$update crm.note
  set status = 'archived', archived_at = now(), archived_by_operator_id = '74000000-0000-4000-8000-000000000001',
      archive_reason = 'skip', version = 99
  where id = '74000000-0000-4000-8000-000000000901'$$),
  'P0001', 'note version must advance by exactly one: P0001');

-- DELETE refused P0001 (even for owner)
select is(pg_temp.run_as('origenlab_owner', $$delete from crm.note where false$$),
  'P0001', 'note is never deleted: P0001');

-- api column UPDATE list exact: only status, archived_at, archived_by_operator_id, archive_reason, version
select is(
  (select count(*)::int from information_schema.columns c
    where c.table_schema = 'crm' and c.table_name = 'note'
      and c.column_name not in ('status','archived_at','archived_by_operator_id','archive_reason','version')
      and has_column_privilege('origenlab_api', 'crm.note', c.column_name::text, 'UPDATE')),
  0, 'api has column UPDATE only on the five archiving columns of crm.note');

-- worker SELECT only
select is(has_table_privilege('origenlab_worker', 'crm.note', 'SELECT'), true,
  'origenlab_worker holds SELECT on crm.note');
select is(has_table_privilege('origenlab_worker', 'crm.note', 'INSERT'), false,
  'origenlab_worker does not hold INSERT on crm.note');

-- ── B4. crm.organization_product_line ────────────────────────────────────────────────────
select has_table('crm', 'organization_product_line', 'crm.organization_product_line exists');

-- line_id closed list: 'bosch' is refused (23514)
select is(pg_temp.run_as('origenlab_api', $$insert into crm.organization_product_line
  (organization_id, line_id, linked_by_operator_id)
  values ('74000000-0000-4000-8000-000000000100', 'bosch', '74000000-0000-4000-8000-000000000001')$$),
  '23514', 'unknown line_id bosch is refused (23514)');

-- valid line_id works
select is(pg_temp.run_as('origenlab_api', $$insert into crm.organization_product_line
  (id, organization_id, line_id, linked_by_operator_id)
  values ('74000000-0000-4000-8000-000000000500', '74000000-0000-4000-8000-000000000100',
    'hielscher', '74000000-0000-4000-8000-000000000001')$$),
  'ok', 'a valid line_id is accepted');

-- one active link per (org, line): second active link to hielscher fails (23505)
select is(pg_temp.run_as('origenlab_api', $$insert into crm.organization_product_line
  (id, organization_id, line_id, linked_by_operator_id)
  values ('74000000-0000-4000-8000-000000000501', '74000000-0000-4000-8000-000000000100',
    'hielscher', '74000000-0000-4000-8000-000000000001')$$),
  '23505', 'duplicate active (org, line) is refused (23505)');

-- never deleted
select has_trigger('crm', 'organization_product_line', 'organization_product_line_never_deleted',
  'organization_product_line_never_deleted trigger exists');
select is(pg_temp.run_as('origenlab_owner', $$delete from crm.organization_product_line where false$$),
  'P0001', 'organization_product_line is never deleted: P0001');

-- api column UPDATE list: valid_to, unlinked_by_operator_id, note, updated_at
select is(
  (select count(*)::int from information_schema.columns c
    where c.table_schema = 'crm' and c.table_name = 'organization_product_line'
      and c.column_name not in ('valid_to','unlinked_by_operator_id','note','updated_at')
      and has_column_privilege('origenlab_api', 'crm.organization_product_line', c.column_name::text, 'UPDATE')),
  0, 'api has column UPDATE only on the four closing columns of organization_product_line');

-- ── B5. vocabulary ────────────────────────────────────────────────────────────────────────
-- 'note' as aggregate_kind
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('note', '74000000-0000-4000-8000-000000000900', 1, 'note.created', 1, '{}'::jsonb, 'operator')$$,
  'note.created with aggregate_kind=note is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('note', '74000000-0000-4000-8000-000000000900', 2, 'note.revised', 1, '{}'::jsonb, 'operator')$$,
  'note.revised is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('note', '74000000-0000-4000-8000-000000000900', 3, 'note.archived', 1, '{}'::jsonb, 'operator')$$,
  'note.archived is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('person', '74000000-0000-4000-8000-000000000200', 1, 'person.updated', 1, '{}'::jsonb, 'operator')$$,
  'person.updated is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('person', '74000000-0000-4000-8000-000000000200', 2, 'person.archived', 1, '{}'::jsonb, 'operator')$$,
  'person.archived is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('person', '74000000-0000-4000-8000-000000000200', 3, 'person.restored', 1, '{}'::jsonb, 'operator')$$,
  'person.restored is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('contact_point', '74000000-0000-4000-8000-000000000300', 1, 'contact_point.updated', 1, '{}'::jsonb, 'operator')$$,
  'contact_point.updated is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('contact_point', '74000000-0000-4000-8000-000000000300', 2, 'contact_point.deactivated', 1, '{}'::jsonb, 'operator')$$,
  'contact_point.deactivated is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 1, 'organization.updated', 1, '{}'::jsonb, 'operator')$$,
  'organization.updated is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 2, 'organization.archived', 1, '{}'::jsonb, 'operator')$$,
  'organization.archived is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 3, 'organization.restored', 1, '{}'::jsonb, 'operator')$$,
  'organization.restored is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 4, 'organization.identifier_added', 1, '{}'::jsonb, 'operator')$$,
  'organization.identifier_added is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 5, 'organization.identifier_removed', 1, '{}'::jsonb, 'operator')$$,
  'organization.identifier_removed is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 6, 'organization.domain_added', 1, '{}'::jsonb, 'operator')$$,
  'organization.domain_added is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 7, 'organization.domain_removed', 1, '{}'::jsonb, 'operator')$$,
  'organization.domain_removed is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 8, 'organization.product_line_linked', 1, '{}'::jsonb, 'operator')$$,
  'organization.product_line_linked is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 9, 'organization.product_line_unlinked', 1, '{}'::jsonb, 'operator')$$,
  'organization.product_line_unlinked is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('assertion', gen_random_uuid(), 1, 'assertion.supplier_candidate_confirmed', 1, '{}'::jsonb, 'operator')$$,
  'assertion.supplier_candidate_confirmed is accepted');
select lives_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('assertion', gen_random_uuid(), 1, 'assertion.supplier_candidate_rejected', 1, '{}'::jsonb, 'operator')$$,
  'assertion.supplier_candidate_rejected is accepted');

-- mismatched family refused (23514): organization aggregate with note event
select throws_ok($$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('organization', '74000000-0000-4000-8000-000000000100', 99, 'note.created', 1, '{}'::jsonb, 'operator')$$,
  '23514', null, 'organization aggregate with note.created event_type is refused (23514)');

-- ── B6. exclusive-domain partial unique: excludes soft-removed rows ───────────────────────
-- A domain can only have one exclusive owner at a time. After soft-removing the exclusive
-- claim from org A, a different org B can claim exclusive ownership of the same domain.
set role origenlab_owner;
insert into crm.organization (id, kind, name, confirmation) values
  ('74000000-0000-4000-8000-000000000110', 'company', 'Acme Successor', 'confirmed');
update crm.organization_domain
  set removed_at = now(), removed_by_operator_id = '74000000-0000-4000-8000-000000000001',
      remove_reason = 'test removal'
  where id = '74000000-0000-4000-8000-000000000400';
reset role;
select is(pg_temp.run_as('origenlab_owner', $$insert into crm.organization_domain
  (id, organization_id, domain_norm, scope)
  values ('74000000-0000-4000-8000-000000000401', '74000000-0000-4000-8000-000000000110',
    'acme.test', 'exclusive')$$),
  'ok', 'a different org can claim exclusive ownership after the first was soft-removed');

select * from finish();
rollback;
