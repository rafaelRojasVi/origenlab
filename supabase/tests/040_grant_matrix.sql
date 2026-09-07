-- Slice 0 — the exact privilege matrix for the runtime roles (docs/ARCHITECTURE.md §3.1, §6.1
-- point 3; docs/MIGRATION.md §5.2 checks 6-8). The expectation rows below are generated from the
-- same matrix as the grants and policies migrations. Catalogue proofs first, then behavioural
-- probes as each runtime role.
begin;
create extension if not exists pgtap with schema extensions;
select plan(41);

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
  return sqlstate || ': ' || sqlerrm;
end
$$;

-- (schema, table, role, table-level verbs S/I/U/D, column-level UPDATE columns or null, all verbs)
create temp table expected (
  schema_name text, table_name text, role_name text, table_verbs text, update_columns text[], all_verbs text
);
insert into expected values
('crm', 'organization', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'organization', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'organization_domain', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'organization_domain', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'organization_relationship', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'organization_relationship', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'external_identifier', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'external_identifier', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'person', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'person', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'affiliation', 'origenlab_api', 'SI', array['valid_to', 'confirmation', 'confirmed_by_operator_id', 'note', 'updated_at'], 'SIU'),
    ('crm', 'affiliation', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'contact_point', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'contact_point', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'address', 'origenlab_api', 'SI', array['valid_to', 'superseded_by_address_id', 'confirmation', 'confirmed_by_operator_id', 'note', 'updated_at'], 'SIU'),
    ('crm', 'address', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'opportunity', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'opportunity', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'opportunity_participant', 'origenlab_api', 'SI', array['person_id', 'is_primary', 'valid_to', 'confirmation', 'confirmed_by_operator_id', 'note', 'updated_at'], 'SIU'),
    ('crm', 'opportunity_participant', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'task', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'task', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'activity', 'origenlab_api', 'SI', null, 'SI'),
    ('crm', 'activity', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'domain_event', 'origenlab_api', 'SI', null, 'SI'),
    ('crm', 'domain_event', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'quote', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'quote', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'quote_revision', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'quote_revision', 'origenlab_worker', 'S', null, 'S'),
    ('crm', 'quote_line', 'origenlab_api', 'SIU', null, 'SIU'),
    ('crm', 'quote_line', 'origenlab_worker', 'S', null, 'S'),
    ('comms', 'mailbox', 'origenlab_api', 'S', null, 'S'),
    ('comms', 'mailbox', 'origenlab_worker', 'SIU', null, 'SIU'),
    ('comms', 'message', 'origenlab_api', 'S', null, 'S'),
    ('comms', 'message', 'origenlab_worker', 'SI', array['labels', 'send_attempt_id', 'parse_status', 'parse_error', 'eml_storage_path', 'eml_sha256', 'size_bytes'], 'SIU'),
    ('comms', 'message_participant', 'origenlab_api', 'S', array['resolved_contact_point_id'], 'SU'),
    ('comms', 'message_participant', 'origenlab_worker', 'SI', null, 'SI'),
    ('comms', 'attachment', 'origenlab_api', 'S', null, 'S'),
    ('comms', 'attachment', 'origenlab_worker', 'SI', array['storage_path', 'sha256', 'size_bytes'], 'SIU'),
    ('outbound', 'send_control', 'origenlab_api', 'S', null, 'S'),
    ('outbound', 'send_control', 'origenlab_worker', 'S', null, 'S'),
    ('outbound', 'campaign', 'origenlab_api', 'SIU', null, 'SIU'),
    ('outbound', 'campaign', 'origenlab_worker', 'S', null, 'S'),
    ('outbound', 'campaign_recipient', 'origenlab_api', 'SIU', null, 'SIU'),
    ('outbound', 'campaign_recipient', 'origenlab_worker', 'S', null, 'S'),
    ('outbound', 'send_attempt', 'origenlab_api', 'S', null, 'S'),
    ('outbound', 'send_attempt', 'origenlab_worker', 'S', null, 'S'),
    ('outbound', 'contact_control', 'origenlab_api', 'S', null, 'S'),
    ('outbound', 'contact_control', 'origenlab_worker', 'S', null, 'S'),
    ('evidence', 'source_record', 'origenlab_api', 'S', array['review_status', 'is_quarantined', 'quarantine_reason', 'quarantined_at', 'updated_at'], 'SU'),
    ('evidence', 'source_record', 'origenlab_worker', 'SIU', null, 'SIU'),
    ('evidence', 'assertion', 'origenlab_api', 'S', array['resolution', 'resolved_kind', 'resolved_id', 'resolved_at', 'resolved_by_operator_id', 'ambiguity_note', 'updated_at'], 'SU'),
    ('evidence', 'assertion', 'origenlab_worker', 'SI', null, 'SI'),
    ('catalog', 'product', 'origenlab_api', 'SIU', null, 'SIU'),
    ('catalog', 'product', 'origenlab_worker', 'S', null, 'S'),
    ('catalog', 'supplier_product', 'origenlab_api', 'SI', null, 'SI'),
    ('catalog', 'supplier_product', 'origenlab_worker', 'SI', null, 'SI'),
    ('procurement', 'notice', 'origenlab_api', 'S', array['promoted_opportunity_id', 'updated_at'], 'SU'),
    ('procurement', 'notice', 'origenlab_worker', 'SIU', null, 'SIU'),
    ('platform', 'operator', 'origenlab_api', 'SIU', null, 'SIU'),
    ('platform', 'operator', 'origenlab_worker', 'S', null, 'S'),
    ('platform', 'command_receipt', 'origenlab_api', 'SIU', null, 'SIU'),
    ('platform', 'command_receipt', 'origenlab_worker', 'S', null, 'S');

select is((select count(*)::int from expected), 64, 'the matrix covers all 32 tables for both runtime roles');

-- Table-level grants match the matrix exactly.
select results_eq(
  $$ select schema_name, table_name, role_name,
            (case when has_table_privilege(role_name, format('%I.%I', schema_name, table_name), 'SELECT') then 'S' else '' end)
         || (case when has_table_privilege(role_name, format('%I.%I', schema_name, table_name), 'INSERT') then 'I' else '' end)
         || (case when has_table_privilege(role_name, format('%I.%I', schema_name, table_name), 'UPDATE') then 'U' else '' end)
         || (case when has_table_privilege(role_name, format('%I.%I', schema_name, table_name), 'DELETE') then 'D' else '' end)
       from expected order by 1, 2, 3 $$,
  $$ select schema_name, table_name, role_name, table_verbs from expected order by 1, 2, 3 $$,
  'table-level SELECT/INSERT/UPDATE/DELETE grants equal the matrix for every (table, role)');

-- Nothing beyond the matrix.
select is(
  (select count(*)::int
     from pg_class c join pg_namespace n on n.oid = c.relnamespace
    cross join (values ('origenlab_api'), ('origenlab_worker')) r(n)
    cross join (values ('DELETE'), ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')) p(v)
    where c.relkind = 'r'
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and has_table_privilege(r.n, c.oid, p.v)),
  0, 'no runtime role holds DELETE, TRUNCATE, REFERENCES or TRIGGER on any table');
select is(
  (select count(*)::int
     from pg_class c join pg_namespace n on n.oid = c.relnamespace
    cross join (values ('origenlab_api'), ('origenlab_worker')) r(n)
    cross join (values ('USAGE'), ('SELECT'), ('UPDATE')) p(v)
    where c.relkind = 'S'
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and has_sequence_privilege(r.n, c.oid, p.v)),
  0, 'no runtime role holds a sequence privilege (identity columns need none)');
select is(
  (select count(*)::int
     from pg_class c join pg_namespace n on n.oid = c.relnamespace, aclexplode(c.relacl) a
     join pg_roles g on g.oid = a.grantee
    where c.relkind in ('r', 'S')
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and g.rolname not in ('origenlab_owner', 'origenlab_api', 'origenlab_worker')),
  0, 'no role other than the owner and the two runtime roles holds any table or sequence privilege');

-- Column-level UPDATE: exactly the listed columns, and never the whole table.
select is(
  (select count(*)::int from expected e
    where e.update_columns is not null
      and not (has_any_column_privilege(e.role_name, format('%I.%I', e.schema_name, e.table_name), 'UPDATE')
               and not has_table_privilege(e.role_name, format('%I.%I', e.schema_name, e.table_name), 'UPDATE'))),
  0, 'column-restricted UPDATE is granted at column level only, never at table level');
select is(
  (select count(*)::int from expected e, unnest(e.update_columns) col
    where not has_column_privilege(e.role_name, format('%I.%I', e.schema_name, e.table_name), col, 'UPDATE')),
  0, 'every listed column is updatable by its role');
select is(
  (select count(*)::int from expected e
     join information_schema.columns c on c.table_schema = e.schema_name and c.table_name = e.table_name
    where e.update_columns is not null
      and not (c.column_name = any (e.update_columns))
      and has_column_privilege(e.role_name, format('%I.%I', e.schema_name, e.table_name), c.column_name::text, 'UPDATE')),
  0, 'no unlisted column is updatable by a column-restricted role');
select is(
  (select count(*)::int from expected e
    where e.update_columns is null and position('U' in e.all_verbs) = 0
      and has_any_column_privilege(e.role_name, format('%I.%I', e.schema_name, e.table_name), 'UPDATE')),
  0, 'roles without UPDATE in the matrix hold no column-level UPDATE either');

-- Function privileges: the CHECK helper for the only role that inserts domain events; nothing else.
select is(has_function_privilege('origenlab_api', 'crm.domain_event_is_valid(text, text, smallint, jsonb)', 'EXECUTE'), true,
  'origenlab_api may execute crm.domain_event_is_valid (evaluated by the CHECK as the inserting role)');
select is(has_function_privilege('origenlab_worker', 'crm.domain_event_is_valid(text, text, smallint, jsonb)', 'EXECUTE'), false,
  'origenlab_worker may not execute crm.domain_event_is_valid (it inserts no domain events)');
select is(has_function_privilege('origenlab_api', 'platform.reject_mutation()', 'EXECUTE'), false,
  'trigger functions carry no EXECUTE for the runtime roles (none is needed for a trigger to fire)');
select is(has_function_privilege('origenlab_api', 'crm.organization_reject_parent_cycle()', 'EXECUTE'), false,
  'the cycle trigger function carries no EXECUTE for origenlab_api');

-- Behavioural probes (checks 6-8). Zero-row statements still exercise the privilege check.
-- api: crm writes allowed; the guarded verbs refused.
select is(left(pg_temp.run_as('origenlab_api', $$insert into crm.organization (kind, name, confirmation) values ('company', 'Probe', 'confirmed')$$), 2), 'ok',
  'api may insert crm.organization (the parent-cycle trigger fires without any EXECUTE grant)');
select is(left(pg_temp.run_as('origenlab_api', $$insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind)
  values ('source_record', gen_random_uuid(), 1, 'source_record.migration_manifest_recorded', 1, '{}', 'migrator')$$), 2), 'ok',
  'api may insert crm.domain_event (identity column needs no sequence privilege; CHECK helper executable)');
select is(left(pg_temp.run_as('origenlab_api', 'update crm.domain_event set seq = 2 where false'), 5), '42501', 'api may not UPDATE crm.domain_event');
select is(left(pg_temp.run_as('origenlab_api', 'delete from crm.domain_event where false'), 5), '42501', 'api may not DELETE crm.domain_event');
select is(left(pg_temp.run_as('origenlab_api', 'delete from crm.task where false'), 5), '42501', 'api may not DELETE crm.task');
select is(left(pg_temp.run_as('origenlab_api', 'update crm.activity set summary = null where false'), 5), '42501', 'api may not UPDATE crm.activity (append-only)');
select is(left(pg_temp.run_as('origenlab_api', $$update crm.address set street_line_1 = 'x' where false$$), 5), '42501', 'api may not edit a structured address field');
select is(left(pg_temp.run_as('origenlab_api', 'update crm.address set valid_to = current_date where false'), 2), 'ok', 'api may close an address (valid_to)');
select is(left(pg_temp.run_as('origenlab_api', 'update crm.affiliation set role_title = ''x'' where false'), 5), '42501', 'api may not edit an affiliation role');
select is(left(pg_temp.run_as('origenlab_api', 'update crm.opportunity_participant set role = ''other'' where false'), 5), '42501', 'api may not change a participant role in place');
select is(left(pg_temp.run_as('origenlab_api', 'update crm.opportunity_participant set person_id = null where false'), 2), 'ok', 'api may link a participant person');
-- api: outbound safety tables read-only; campaign lifecycle writable.
select is(left(pg_temp.run_as('origenlab_api', 'update outbound.send_control set marketing_enabled = true where id = 1'), 5), '42501', 'api may not flip a send flag directly');
select is(left(pg_temp.run_as('origenlab_api', 'insert into outbound.send_attempt (purpose, mailbox_id, address_norm) select ''marketing'', gen_random_uuid(), ''a@example.test'' where false'), 5), '42501', 'api may not write outbound.send_attempt');
select is(left(pg_temp.run_as('origenlab_api', 'insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) select ''address'', ''a@example.test'', ''block'', ''all'', ''r'', ''operator_command'' where false'), 5), '42501', 'api may not write outbound.contact_control directly');
select is(left(pg_temp.run_as('origenlab_api', 'insert into outbound.campaign (name, mailbox_id, max_sends, recontact_interval_days) select ''c'', gen_random_uuid(), 1, 1 where false'), 2), 'ok', 'api may write the campaign lifecycle');
-- api: comms and evidence narrow writes.
select is(left(pg_temp.run_as('origenlab_api', 'insert into comms.mailbox (address_norm) select ''m@example.test'' where false'), 5), '42501', 'api may not write comms.mailbox');
select is(left(pg_temp.run_as('origenlab_api', 'update comms.message_participant set resolved_contact_point_id = null where false'), 2), 'ok', 'api may resolve a message participant');
select is(left(pg_temp.run_as('origenlab_api', 'update comms.message_participant set address_norm = ''x'' where false'), 5), '42501', 'api may not rewrite a message participant address');
select is(left(pg_temp.run_as('origenlab_api', 'update evidence.assertion set resolution = ''rejected'' where false'), 2), 'ok', 'api may resolve an assertion');
select is(left(pg_temp.run_as('origenlab_api', 'update evidence.assertion set value_norm = ''x'' where false'), 5), '42501', 'api may not rewrite an assertion value');
-- worker: comms/evidence/procurement/catalog writes allowed; crm, outbound and platform writes refused.
select is(left(pg_temp.run_as('origenlab_worker', 'insert into comms.mailbox (address_norm) select ''m@example.test'' where false'), 2), 'ok', 'worker may write comms.mailbox');
select is(left(pg_temp.run_as('origenlab_worker', 'insert into evidence.source_record (kind, dedupe_key, payload) select ''workbook_import'', ''k'', ''{}''::jsonb where false'), 2), 'ok', 'worker may write evidence.source_record');
select is(left(pg_temp.run_as('origenlab_worker', 'insert into procurement.notice (codigo_externo, head) select ''x'', ''{}''::jsonb where false'), 2), 'ok', 'worker may write procurement.notice');
select is(left(pg_temp.run_as('origenlab_worker', 'insert into crm.organization (kind, name, confirmation) select ''company'', ''w'', ''confirmed'' where false'), 5), '42501', 'worker may not write crm.organization');
select is(left(pg_temp.run_as('origenlab_worker', 'insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind) select ''task'', gen_random_uuid(), 1, ''task.created'', 1, ''{}''::jsonb, ''worker'' where false'), 5), '42501', 'worker may not write crm.domain_event directly');
select is(left(pg_temp.run_as('origenlab_worker', 'update crm.quote_revision set pdf_sha256 = null where false'), 5), '42501', 'worker may not write quote_revision.pdf_sha256 directly (only through crm.record_quote_pdf, Slice 3)');
select is(left(pg_temp.run_as('origenlab_worker', 'insert into outbound.send_attempt (purpose, mailbox_id, address_norm) select ''marketing'', gen_random_uuid(), ''a@example.test'' where false'), 5), '42501', 'worker may not write outbound.send_attempt directly');
select is(left(pg_temp.run_as('origenlab_worker', 'insert into platform.operator (auth_user_id, email_norm, display_name, role, status) select gen_random_uuid(), ''o@example.test'', ''o'', ''admin'', ''active'' where false'), 5), '42501', 'worker may not write platform.operator');

select * from finish();
rollback;
