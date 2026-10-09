-- Slice 4 — mail triage records its proposals as evidence assertions.
--
-- docs/DOMAIN.md §7 #25; docs/ARCHITECTURE.md §8 (D2). The triage worker reads each captured
-- `gmail_message` source record and *proposes*; an operator decides. Two new observation kinds on
-- the existing table — no table, grant, policy or foreign key changes:
--
--   message_triage   one per source record and classifier version (value_norm = 'triage:v<N>'):
--                    the class (noise, quote request, purchase order, …), the rule reasons, the
--                    catalog candidates and, when the model ran, its structured reading (products,
--                    lead status, urgency, summary).
--   product_mention  one per product the message names (value_norm = the normalised model key,
--                    or the folded text when no model number was found), with the catalog product
--                    it matched, if any.
--
-- The worker already holds INSERT on evidence.assertion (Slice 0) and only ever inserts with
-- `on conflict do nothing`: a re-run of the same version adds nothing, a new classifier version
-- adds its own row beside the old one. Neither kind is ever resolved by the worker; nothing here
-- writes crm.* — no case, stage or interest is created from a triage proposal.

set role origenlab_owner;

alter table evidence.assertion drop constraint assertion_kind_check;
alter table evidence.assertion
  add constraint assertion_kind_check check (kind in (
    'organization_name', 'contact_address', 'postal_address', 'affiliation',
    'contacted_address', 'supplier_candidate', 'historical_quote_candidate',
    'document_reference', 'unsubscribe_request',
    'message_triage', 'product_mention'
  ));

comment on column evidence.assertion.kind is
  'Closed observation vocabulary. document_reference records that a stored document names a commercial subject; unsubscribe_request records that a reply asked for no more marketing: resolved to the outbound.contact_control it created or joined, or unresolved while held for review — an unresolved one blocks its exact address from marketing (written only by outbound.add_contact_control). message_triage is the mail-triage worker''s reading of one message (value_norm triage:v<N>); product_mention is one product a message names. Both are machine proposals: the worker never resolves them.';

reset role;
