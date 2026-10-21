-- Several equipment lines may carry a quote's import expenses (owner 2026-10-21). A quote of four
-- imported balances spreads its freight and customs over the four, by cost, instead of loading them
-- all on one: Tatiana marks the lines in the Cotizador, each marked line stores `is_principal`.
--
--   * quote_line_one_principal_per_revision is dropped: a revision may have several principal items.
--     The engine shares the expenses among them by cost (quantity × unit cost in pesos) and gives
--     the rounding remainder to the costliest; with one marked line nothing changes.
--   * quote_line_principal_is_item stays: a principal is always an item line.

set role origenlab_owner;

drop index crm.quote_line_one_principal_per_revision;

comment on table crm.quote_line is
  'DOMAIN.md §7 #14 — items, logistics, fees, discounts with their own currency and FX: principal items carry the import expenses (several share them by cost); a logistics allocation target is an item line of the same revision.';

reset role;
