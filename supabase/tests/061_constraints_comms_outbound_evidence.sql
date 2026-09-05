-- Slice 0 — declarative invariants of comms, outbound, evidence and procurement, exercised as the
-- owner. docs/DOMAIN.md §7; docs/DATA.md §6, §7; docs/WORKFLOWS.md §1.3-1.6, §2.1, §W12.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
set role origenlab_owner;
select plan(77);

insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
values ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000f1', 'admin@example.test', 'Admin', 'admin', 'active');

-- comms.mailbox (#15)
insert into comms.mailbox (id, address_norm, is_production_sender, authorization_state) values ('00000000-0000-4000-8000-000000000100', 'contacto@origenlab.example', true, 'authorized');
select throws_ok($$ insert into comms.mailbox (address_norm, is_production_sender) values ('otro@origenlab.example', true) $$, '23505', null, 'mailbox: at most one production sender');
select lives_ok($$ insert into comms.mailbox (id, address_norm) values ('00000000-0000-4000-8000-000000000101', 'staging@origenlab.example') $$, 'mailbox: a non-production mailbox may coexist');
select throws_ok($$ insert into comms.mailbox (address_norm) values ('contacto@origenlab.example') $$, '23505', null, 'mailbox: address unique');
select throws_ok($$ insert into comms.mailbox (address_norm, provider) values ('x@origenlab.example', 'outlook') $$, '23514', null, 'mailbox: provider is closed');
select throws_ok($$ insert into comms.mailbox (address_norm, authorization_state) values ('y@origenlab.example', 'maybe') $$, '23514', null, 'mailbox: authorization_state is closed');

-- comms.message (#16)
insert into comms.message (id, mailbox_id, provider_message_id, rfc822_message_id_norm, direction, internal_date)
values ('00000000-0000-4000-8000-000000000110', '00000000-0000-4000-8000-000000000100', 'gm-1', '<reused@example.test>', 'inbound', now());
select throws_ok($$ insert into comms.message (mailbox_id, provider_message_id, direction, internal_date) values ('00000000-0000-4000-8000-000000000100', 'gm-1', 'inbound', now()) $$, '23505', null, 'message: (mailbox_id, provider_message_id) unique');
select lives_ok($$ insert into comms.message (mailbox_id, provider_message_id, rfc822_message_id_norm, direction, internal_date) values ('00000000-0000-4000-8000-000000000100', 'gm-2', '<reused@example.test>', 'inbound', now()) $$, 'message: rfc822_message_id_norm is non-unique by design');
select lives_ok($$ insert into comms.message (mailbox_id, provider_message_id, direction, internal_date) values ('00000000-0000-4000-8000-000000000100', 'gm-3', 'inbound', now()) $$, 'message: rfc822_message_id_norm is nullable');
select lives_ok($$ insert into comms.message (mailbox_id, provider_message_id, direction, internal_date) values ('00000000-0000-4000-8000-000000000101', 'gm-1', 'inbound', now()) $$, 'message: the same provider id in another mailbox is a different message');
select throws_ok($$ insert into comms.message (mailbox_id, provider_message_id, direction, internal_date, parse_status) values ('00000000-0000-4000-8000-000000000100', 'gm-4', 'inbound', now(), 'parse_failed') $$, '23514', null, 'message: parse_failed carries a parse_error');
select throws_ok($$ insert into comms.message (mailbox_id, provider_message_id, direction, internal_date, eml_storage_path) values ('00000000-0000-4000-8000-000000000100', 'gm-5', 'inbound', now(), 'eml/x.eml') $$, '23514', null, 'message: a stored .eml carries its sha256');
select throws_ok($$ insert into comms.message (mailbox_id, provider_message_id, direction, internal_date) values ('00000000-0000-4000-8000-000000000100', 'gm-6', 'sideways', now()) $$, '23514', null, 'message: direction is closed');

-- comms.message_participant (#17), comms.attachment (#18)
insert into comms.message_participant (message_id, role, address_norm) values ('00000000-0000-4000-8000-000000000110', 'from', 'sender@example.test');
select throws_ok($$ insert into comms.message_participant (message_id, role, address_norm) values ('00000000-0000-4000-8000-000000000110', 'from', 'sender@example.test') $$, '23505', null, 'message_participant: (message_id, role, address_norm) unique');
select lives_ok($$ insert into comms.message_participant (message_id, role, address_norm) values ('00000000-0000-4000-8000-000000000110', 'to', 'sender@example.test') $$, 'message_participant: the same address in another role');
select throws_ok($$ insert into comms.message_participant (message_id, role, address_norm) values ('00000000-0000-4000-8000-000000000110', 'forwarded', 'x@example.test') $$, '23514', null, 'message_participant: role is closed');
select throws_ok($$ insert into comms.attachment (message_id, part_index, mime_type, storage_path) values ('00000000-0000-4000-8000-000000000110', 0, 'application/pdf', 'att/x.pdf') $$, '23514', null, 'attachment: sha256 present when stored');
select lives_ok($$ insert into comms.attachment (message_id, part_index, mime_type, storage_path, sha256) values ('00000000-0000-4000-8000-000000000110', 0, 'application/pdf', 'att/x.pdf', repeat('0', 64)) $$, 'attachment: stored with its hash');
select throws_ok($$ insert into comms.attachment (message_id, part_index, mime_type) values ('00000000-0000-4000-8000-000000000110', 0, 'text/plain') $$, '23505', null, 'attachment: (message_id, part_index) unique');

-- outbound.send_control (#19)
select throws_ok($$ insert into outbound.send_control (id, change_reason) values (2, 'second row') $$, '23514', null, 'send_control: only id = 1 may exist');
select throws_ok($$ delete from outbound.send_control where id = 1 $$, 'P0001', null, 'send_control: the single row is never deleted (trigger guard)');
select throws_ok($$ update outbound.send_control set marketing_enabled = true, change_reason = '   ' where id = 1 $$, '23514', null, 'send_control: every change carries a reason');
select results_eq($$ select marketing_enabled, transactional_enabled from outbound.send_control where id = 1 $$, $$ values (false, false) $$, 'send_control: both flags are false');

-- outbound.campaign (#20), outbound.campaign_recipient (#21)
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days) values ('00000000-0000-4000-8000-000000000200', 'Campaign A', '00000000-0000-4000-8000-000000000100', 100, 180);
insert into outbound.campaign (id, name, mailbox_id, max_sends, recontact_interval_days) values ('00000000-0000-4000-8000-000000000201', 'Campaign B', '00000000-0000-4000-8000-000000000100', 100, 180);
select throws_ok($$ insert into outbound.campaign (name, mailbox_id, max_sends, recontact_interval_days) values ('x', '00000000-0000-4000-8000-000000000100', 0, 180) $$, '23514', null, 'campaign: max_sends ≥ 1');
select throws_ok($$ update outbound.campaign set status = 'approved' where id = '00000000-0000-4000-8000-000000000200' $$, '23514', null, 'campaign: approved carries approver, time and override count');
select throws_ok($$ update outbound.campaign set status = 'sending' where id = '00000000-0000-4000-8000-000000000200' $$, '23514', null, 'campaign: status is closed');
insert into outbound.campaign_recipient (id, campaign_id, address_norm) values ('00000000-0000-4000-8000-000000000210', '00000000-0000-4000-8000-000000000200', 'lab@uni.example');
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm) values ('00000000-0000-4000-8000-000000000200', 'lab@uni.example') $$, '23505', null, 'campaign_recipient: (campaign_id, address_norm) unique');
select lives_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm) values ('00000000-0000-4000-8000-000000000201', 'lab@uni.example') $$, 'campaign_recipient: the same address may be frozen into another campaign');
select throws_ok($$ update outbound.campaign_recipient set state = 'excluded' where id = '00000000-0000-4000-8000-000000000210' $$, '23514', null, 'campaign_recipient: excluded carries an exclusion_reason');
select throws_ok($$ update outbound.campaign_recipient set exclusion_reason = 'block' where id = '00000000-0000-4000-8000-000000000210' $$, '23514', null, 'campaign_recipient: an exclusion_reason implies excluded');
select throws_ok($$ update outbound.campaign_recipient set recontact_override_reason = 'approved by manager' where id = '00000000-0000-4000-8000-000000000210' $$, '23514', null, 'campaign_recipient: the override triple is all-or-none');
select lives_ok($$ update outbound.campaign_recipient set recontact_override_by_operator_id = '00000000-0000-4000-8000-0000000000a1', recontact_override_reason = 'approved by manager', recontact_override_at = now() where id = '00000000-0000-4000-8000-000000000210' $$, 'campaign_recipient: the override triple set together');
select throws_ok($$ insert into outbound.campaign_recipient (campaign_id, address_norm) values ('00000000-0000-4000-8000-000000000200', 'Upper@uni.example') $$, '23514', null, 'campaign_recipient: address_norm is lower-cased');

-- outbound.send_attempt (#22)
select throws_ok($$ insert into outbound.send_attempt (purpose, mailbox_id, address_norm) values ('marketing', '00000000-0000-4000-8000-000000000100', 'lab@uni.example') $$, '23514', null, 'send_attempt: a marketing attempt names its campaign and recipient');
select throws_ok($$ insert into outbound.send_attempt (purpose, mailbox_id, address_norm) values ('transactional', '00000000-0000-4000-8000-000000000100', 'lab@uni.example') $$, '23514', null, 'send_attempt: a transactional attempt names its quote revision');
select throws_ok($$ insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm) values ('marketing', '00000000-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000210', '00000000-0000-4000-8000-000000000100', 'lab@uni.example') $$, '23503', null, 'send_attempt: the recipient must belong to the attempt''s campaign');
insert into outbound.send_attempt (id, purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm)
values ('00000000-0000-4000-8000-000000000220', 'marketing', '00000000-0000-4000-8000-000000000200', '00000000-0000-4000-8000-000000000210', '00000000-0000-4000-8000-000000000100', 'lab@uni.example');
select throws_ok($$ insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm) values ('marketing', '00000000-0000-4000-8000-000000000200', '00000000-0000-4000-8000-000000000210', '00000000-0000-4000-8000-000000000100', 'lab@uni.example') $$, '23505', null, 'send_attempt: at most one open attempt per address');
select throws_ok($$ update outbound.send_attempt set rfc822_message_id = '<minted@origenlab.example>' where id = '00000000-0000-4000-8000-000000000220' $$, '23514', null, 'send_attempt: no minted id while reserved');
select throws_ok($$ update outbound.send_attempt set submission_state = 'dispatching', dispatch_started_at = now(), lease_expires_at = now() + interval '5 seconds' where id = '00000000-0000-4000-8000-000000000220' $$, '23514', null, 'send_attempt: dispatching requires the minted id');
select throws_ok($$ update outbound.send_attempt set delivery_state = 'pending' where id = '00000000-0000-4000-8000-000000000220' $$, '23514', null, 'send_attempt: delivery_state ≠ n/a only when accepted');
select lives_ok($$ update outbound.send_attempt set submission_state = 'dispatching', dispatch_started_at = now(), lease_expires_at = now() + interval '5 seconds', rfc822_message_id = '<minted-1@origenlab.example>' where id = '00000000-0000-4000-8000-000000000220' $$, 'send_attempt: dispatching with lease and minted id');
select throws_ok($$ update outbound.send_attempt set submission_state = 'accepted', delivery_state = 'pending' where id = '00000000-0000-4000-8000-000000000220' $$, '23514', null, 'send_attempt: accepted carries accepted_at');
select lives_ok($$ update outbound.send_attempt set submission_state = 'accepted', delivery_state = 'pending', accepted_at = now() where id = '00000000-0000-4000-8000-000000000220' $$, 'send_attempt: accepted with pending delivery');
select throws_ok($$ update outbound.send_attempt set bounce_class = 'hard' where id = '00000000-0000-4000-8000-000000000220' $$, '23514', null, 'send_attempt: bounce_class only with delivery_state bounced');
select lives_ok($$ update outbound.send_attempt set delivery_state = 'bounced', bounce_class = 'hard' where id = '00000000-0000-4000-8000-000000000220' $$, 'send_attempt: an accepted message that bounces stays accepted');
select is((select submission_state from outbound.send_attempt where id = '00000000-0000-4000-8000-000000000220'), 'accepted', 'send_attempt: submission_state is still accepted after the bounce');
select lives_ok($$ insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm) values ('marketing', '00000000-0000-4000-8000-000000000200', '00000000-0000-4000-8000-000000000210', '00000000-0000-4000-8000-000000000100', 'lab@uni.example') $$, 'send_attempt: a closed attempt releases the per-address lock');
select throws_ok($$ insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm, rfc822_message_id, submission_state, dispatch_started_at, lease_expires_at) values ('marketing', '00000000-0000-4000-8000-000000000200', '00000000-0000-4000-8000-000000000210', '00000000-0000-4000-8000-000000000100', 'other@uni.example', '<minted-1@origenlab.example>', 'dispatching', now(), now()) $$, '23505', null, 'send_attempt: minted RFC 822 ids are unique');
select throws_ok($$ update outbound.send_attempt set submission_state = 'rejected', delivery_state = 'n/a', bounce_class = null where id = '00000000-0000-4000-8000-000000000220' $$, '23514', null, 'send_attempt: rejected carries an error_class');
select throws_ok($$ update outbound.send_attempt set resolution_verdict = 'accepted' where id = '00000000-0000-4000-8000-000000000220' $$, '23514', null, 'send_attempt: the resolution fields are all-or-none');
select throws_ok($$ update outbound.send_attempt set retry_reason = 'x' where id = '00000000-0000-4000-8000-000000000220' $$, '23514', null, 'send_attempt: retry_reason only with retry_of_attempt_id');

-- outbound.contact_control (#23)
insert into outbound.contact_control (id, scope, value_norm, kind, purpose, reason, source) values ('00000000-0000-4000-8000-000000000230', 'address', 'lab@uni.example', 'prior_contact', 'marketing', 'accepted send', 'send_accepted');
select throws_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values ('address', 'x@uni.example', 'prior_contact', 'all', 'r', 'wave1a_union') $$, '23514', null, 'contact_control: prior_contact is marketing only');
select throws_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source, until_at) values ('address', 'x@uni.example', 'cooldown', 'all', 'r', 'send_accepted', now()) $$, '23514', null, 'contact_control: cooldown is marketing only');
select throws_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values ('address', 'x@uni.example', 'cooldown', 'marketing', 'r', 'send_accepted') $$, '23514', null, 'contact_control: cooldown carries until_at');
select throws_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source, until_at) values ('address', 'x@uni.example', 'block', 'all', 'r', 'operator_command', now()) $$, '23514', null, 'contact_control: only cooldown carries until_at');
select lives_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values ('address', 'lab@uni.example', 'block', 'all', 'hard bounce', 'ndr_handler') $$, 'contact_control: a block coexists with prior_contact on the same address');
select throws_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values ('address', 'lab@uni.example', 'prior_contact', 'marketing', 'again', 'send_accepted') $$, '23505', null, 'contact_control: (scope, value_norm, kind, purpose) unique');
select throws_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values ('domain', 'uni.example', 'prior_contact', 'marketing', 'r', 'wave1a_union') $$, '23514', null, 'contact_control: a domain-scoped control is a block');
select lives_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values ('domain', 'competitor.example', 'block', 'marketing', 'domain policy', 'wave1a_suppression') $$, 'contact_control: a marketing domain block');
select throws_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values ('domain', 'Upper.Example', 'block', 'all', 'r', 'operator_command') $$, '23514', null, 'contact_control: domain value_norm is lower-cased');
select throws_ok($$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values ('address', 'x@uni.example', 'block', 'transactional', 'r', 'operator_command') $$, '23514', null, 'contact_control: purpose ∈ {all, marketing}');
select throws_ok($$ delete from outbound.contact_control where id = '00000000-0000-4000-8000-000000000230' $$, 'P0001', null, 'contact_control: prior_contact is never deleted (trigger guard, even for the owner)');
select throws_ok($$ update outbound.contact_control set reason = 'edited' where id = '00000000-0000-4000-8000-000000000230' $$, 'P0001', null, 'contact_control: prior_contact is never rewritten');
select lives_ok($$ delete from outbound.contact_control where scope = 'domain' and value_norm = 'competitor.example' $$, 'contact_control: a block may be revoked (by the privileged command in Slice 5)');

-- evidence.source_record (#24), evidence.assertion (#25)
insert into evidence.source_record (id, kind, dedupe_key, payload) values ('00000000-0000-4000-8000-000000000300', 'workbook_import', 'wb:1', '{"row": 1}');
select throws_ok($$ insert into evidence.source_record (kind, dedupe_key, payload) values ('workbook_import', 'wb:1', '{}') $$, '23505', null, 'source_record: dedupe_key unique');
select throws_ok($$ insert into evidence.source_record (kind, dedupe_key, payload) values ('scraped', 'x:1', '{}') $$, '23514', null, 'source_record: kind is closed');
select throws_ok($$ update evidence.source_record set is_quarantined = true where id = '00000000-0000-4000-8000-000000000300' $$, '23514', null, 'source_record: quarantine carries a reason and a time');
select lives_ok($$ update evidence.source_record set is_quarantined = true, quarantine_reason = 'contradicts accepted fact', quarantined_at = now() where id = '00000000-0000-4000-8000-000000000300' $$, 'source_record: quarantined with reason');
select throws_ok($$ insert into evidence.source_record (kind, dedupe_key, payload) values ('workbook_import', 'wb:2', '[]') $$, '23514', null, 'source_record: payload is an object');
insert into evidence.assertion (id, source_record_id, kind, value_norm) values ('00000000-0000-4000-8000-000000000310', '00000000-0000-4000-8000-000000000300', 'contact_address', 'a@uni.example');
select throws_ok($$ insert into evidence.assertion (source_record_id, kind, value_norm) values ('00000000-0000-4000-8000-000000000300', 'contact_address', 'a@uni.example') $$, '23505', null, 'assertion: (source_record_id, kind, value_norm) unique');
select throws_ok($$ insert into evidence.assertion (source_record_id, kind, value_norm) values ('00000000-0000-4000-8000-000000000300', 'guess', 'x') $$, '23514', null, 'assertion: kind is closed');
select throws_ok($$ update evidence.assertion set resolution = 'promoted' where id = '00000000-0000-4000-8000-000000000310' $$, '23514', null, 'assertion: promoted carries resolved_kind, resolved_id and resolved_at');
select throws_ok($$ update evidence.assertion set resolution = 'ambiguous' where id = '00000000-0000-4000-8000-000000000310' $$, '23514', null, 'assertion: ambiguous carries a note');
select lives_ok($$ update evidence.assertion set resolution = 'promoted', resolved_kind = 'contact_point', resolved_id = gen_random_uuid(), resolved_at = now(), resolved_by_operator_id = '00000000-0000-4000-8000-0000000000a1' where id = '00000000-0000-4000-8000-000000000310' $$, 'assertion: a logical resolution to a contact point');

-- procurement.notice (#28)
insert into procurement.notice (id, codigo_externo, head) values ('00000000-0000-4000-8000-000000000400', '1234-56-LE26', '{"title": "x"}');
select throws_ok($$ insert into procurement.notice (codigo_externo, head) values ('1234-56-LE26', '{}') $$, '23505', null, 'notice: codigo_externo unique');
select throws_ok($$ insert into procurement.notice (codigo_externo, head, head_history) values ('9999-99-LE26', '{}', '{}') $$, '23514', null, 'notice: head_history is an array');
select throws_ok($$ delete from procurement.notice where id = '00000000-0000-4000-8000-000000000400' $$, 'P0001', null, 'notice: never deleted (trigger guard)');
select lives_ok($$ update procurement.notice set disappeared_at = now() where id = '00000000-0000-4000-8000-000000000400' $$, 'notice: withdrawal sets disappeared_at');

select * from finish();
rollback;
