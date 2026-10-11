-- Slice 7 — the quote reader (STATUS §2.7.94; private spec 2026-10-11-quote-pdf-reader-design).
--
-- The worker reads each registered quote revision's PDF (from the captured .eml) into the same
-- evidence the 2026-10-06 quote-history import wrote: one `quote_document` source record and its
-- `evidence.document_line` rows. Until now only origenlab_api could insert lines. The worker gains
-- exactly SELECT and INSERT on evidence.document_line (with the matching RLS policies) — never
-- UPDATE: reviewing a disputed line stays the API's `review-document-line`.
--
-- A reading the worker refuses (not an OrigenLab quote, too many disputed lines, the neto does not
-- match its lines, the model refused) writes no line; it records one `quote_read` observation on the
-- revision's gmail_message evidence instead, so a person can see why and the reader does not retry
-- it every ten minutes. `quote_read` joins the closed assertion vocabulary of 20261007160000.

set role origenlab_owner;

alter table evidence.assertion drop constraint assertion_kind_check;
alter table evidence.assertion
  add constraint assertion_kind_check check (kind in (
    'organization_name', 'contact_address', 'postal_address', 'affiliation',
    'contacted_address', 'supplier_candidate', 'historical_quote_candidate',
    'document_reference', 'unsubscribe_request',
    'message_triage', 'product_mention', 'delivery_failure', 'quote_read'
  ));

comment on column evidence.assertion.kind is
  'Closed observation vocabulary. document_reference records that a stored document names a commercial subject; unsubscribe_request records that a reply asked for no more marketing; message_triage is the mail-triage worker reading of one message; product_mention is one product a message names; delivery_failure is the versioned conservative NDR analysis for one captured message; quote_read records that the quote reader refused to read a quote PDF, with the reason code (value_norm quote_read:v<N>:<ISO week>). Machine observations are evidence, not a second CRM or suppression authority.';

grant select, insert on evidence.document_line to origenlab_worker;
create policy origenlab_worker_select on evidence.document_line for select to origenlab_worker using (true);
create policy origenlab_worker_insert on evidence.document_line for insert to origenlab_worker with check (true);

reset role;
