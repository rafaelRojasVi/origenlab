-- Slice 9 — crm.note.call_outcome 'whatsapp': a WhatsApp sent is a contact record. Fictitious
-- fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(3);

grant origenlab_api to session_user with set true, inherit false;

create function pg_temp.run_as(p_role text, p_sql text) returns text
language plpgsql as $f$
begin
  execute format('set role %I', p_role);
  execute p_sql;
  reset role;
  return 'ok';
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$f$;

set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('99000000-0000-4000-9900-000000004001', '99000000-0000-4000-9900-0000000040f1',
          'operador@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id)
  values ('99000000-0000-4000-9900-000000004000', 'Caso Ficticio', 'lead',
          '99000000-0000-4000-9900-000000004001');
reset role;

select is(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome)
  values ('opportunity', '99000000-0000-4000-9900-000000004000',
          '99000000-0000-4000-9900-000000004001', 'WhatsApp enviado', 'llamada', 'whatsapp') $$),
  'ok', 'the API logs a WhatsApp sent');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome)
  values ('opportunity', '99000000-0000-4000-9900-000000004000',
          '99000000-0000-4000-9900-000000004001', 'Otro', 'llamada', 'telegrama') $$),
  '23514: new row for relation "note" violates check constraint "note_call_outcome_check"',
  'an outcome outside the list is still refused');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, call_outcome)
  values ('opportunity', '99000000-0000-4000-9900-000000004000',
          '99000000-0000-4000-9900-000000004001', 'Nota', 'whatsapp') $$),
  '23514: new row for relation "note" violates check constraint "note_call_shape"',
  'whatsapp needs note_kind llamada, like every outcome');

select * from finish();
rollback;
