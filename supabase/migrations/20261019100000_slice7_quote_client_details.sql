-- Cotizador: the client's details as this quote prints them (owner 2026-10-19). Tatiana completes
-- or corrects them on the draft — contact name, title, unit, phone, the institution's legal name,
-- RUT, address, city — and approval freezes them into party_snapshot. A new revision copies them.
--
--   * crm.quote_revision.client_details: a JSON object of optional strings, or NULL (nothing typed:
--     the PDF prints what the CRM knows). Draft-only by construction: quote_revision_authored_frozen
--     freezes every column not in its mutable list, so an issued revision's details never change.
--   * Nothing is written to crm.person / crm.organization here: saving them to the CRM is the
--     Cotizador client-data project (spec 2026-10-18), not this column.

set role origenlab_owner;

alter table crm.quote_revision
  add column client_details jsonb,
  add constraint quote_revision_client_details_object
    check (client_details is null or jsonb_typeof(client_details) = 'object');

comment on column crm.quote_revision.client_details is
  'The details this revision prints for the client, typed in the Cotizador (JSON object: institution_name, rut, contact_name, contact_role, department, phone, address, city); NULL when none. Frozen into party_snapshot at approval.';

reset role;
