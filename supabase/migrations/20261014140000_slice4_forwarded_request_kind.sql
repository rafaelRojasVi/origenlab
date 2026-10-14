-- Slice 4 — evidence.source_record kind `forwarded_request` (spec 2026-10-14, forwarded requests).
--
-- Tatiana forwards to contacto@ requests that reached her elsewhere (her Labdelivery mailbox, a
-- supplier passing on a marketplace lead). The worker reads who really asked and stores it as a
-- record of its own: the customer's name, email, institution, RUT, phone and what they asked for,
-- the forward's origin and the id of the email it was read from — never the email's text. A case
-- opened from it («Crear solicitud») has this record as its origin, so a forward that came only from
-- the Labdelivery mailbox never makes that email a case's evidence (owner decision, 2026-10-14).
-- Staged like every acquisition: `pending` until a person creates the request (`promoted`) or
-- dismisses it (`rejected`).

set role origenlab_owner;

alter table evidence.source_record drop constraint source_record_kind_check;
alter table evidence.source_record add constraint source_record_kind_check check (kind in (
  'workbook_import', 'chilecompra_notice', 'migration_manifest',
  'v1_parse_failure', 'v1_evidence_edge', 'v1_supplier_candidate', 'v1_historical_quote_candidate',
  'gmail_message', 'drive_file', 'quote_document', 'forwarded_request'
));
comment on column evidence.source_record.kind is
  'Closed acquisition vocabulary: where this record came from. gmail_message, drive_file, quote_document and forwarded_request are staged, never-trusted observations — they arrive review_status = ''pending'' and reach crm.* only through operator-reviewed promotion (docs/DOMAIN.md §5). A quote_document carries its lines in evidence.document_line (#54); a forwarded_request carries only the customer fields the worker read out of a forward we sent to contacto@, never its text.';

reset role;
