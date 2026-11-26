-- Slice 9 — a Cotizador quote in US dollars (owner 2026-11-26, an export client).
--
--   * quote_revision.quote_currency already allows any ISO code and price_decimals 0 or 2; an authored
--     revision in USD is priced in pesos as before, then converted at one rate and rounded to the cent.
--   * crm.quote_revision.quote_fx_rate / quote_fx_as_of / quote_fx_source: that rate (pesos per unit of
--     quote_currency), its day and its source, frozen with the revision so approving on a later day
--     prices it the same. All three or none; none on a CLP revision. A historical import in USD keeps none.
--   * No new grant or policy: the columns sit on a table that already has them, and the draft-only
--     guard on authored revisions covers every column.

set role origenlab_owner;

alter table crm.quote_revision
  add column quote_fx_rate numeric(18, 8),
  add column quote_fx_as_of date,
  add column quote_fx_source text,
  add constraint quote_revision_quote_fx_complete check (
    (quote_fx_rate is null) = (quote_fx_as_of is null) and (quote_fx_rate is null) = (quote_fx_source is null)),
  add constraint quote_revision_quote_fx_shape check (
    quote_fx_rate is null or (quote_fx_rate > 0 and quote_fx_rate <> 'NaN'::numeric and quote_currency <> 'CLP' and quote_fx_source in ('bcentral', 'supplier_quote', 'manual')));

comment on column crm.quote_revision.quote_fx_rate is
  'Authored revisions not in CLP: the pesos per unit of quote_currency the prices were converted at; null otherwise.';
comment on column crm.quote_revision.quote_fx_as_of is 'The day of quote_fx_rate.';
comment on column crm.quote_revision.quote_fx_source is 'Where quote_fx_rate came from, as a line''s fx_source: bcentral, supplier_quote or manual (typed in the Cotizador).';

reset role;
