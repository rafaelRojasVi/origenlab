-- Slice 9 — a per-line discount on authored quotes («Descuento %»), printed on the PDF.
--
--   * crm.quote_line.discount_pct: the fraction taken off an item line's list price (0.09 = 9 %).
--     Null = no discount. unit_price_qc / line_total_qc stay the FINAL (charged) amounts, so every
--     reader of them (invoicing, margins, economics) keeps working.
--   * crm.quote_line.list_unit_price_qc: the list price before the discount, stored only on a
--     discounted line that has a price (a line still without a cost keeps its discount_pct alone) (the PDF prints it; a typed price is recovered from it).
--   * crm.quote_revision.subtotal is now the sum of list totals and discount_total the sum of
--     discounts; tax_base stays the net charged. No discount: subtotal = tax_base, discount_total = 0.
--   * No new grant or policy: both columns sit on a table that already has them, and the
--     draft-only guard (crm.quote_line_authored_draft_only) covers every column.

set role origenlab_owner;

alter table crm.quote_line
  add column discount_pct numeric(5, 4),
  add column list_unit_price_qc numeric(18, 4),
  add constraint quote_line_discount_pct_range check (discount_pct is null or (discount_pct > 0 and discount_pct < 1)),
  add constraint quote_line_discount_item_only check (
    (list_unit_price_qc is null or discount_pct is not null) and (discount_pct is null or line_kind = 'item'));

comment on column crm.quote_line.discount_pct is
  'Item lines: the fraction off the list price (0 < x < 1, 4 decimals), null for none. unit_price_qc is the price after it.';
comment on column crm.quote_line.list_unit_price_qc is
  'Item lines with a discount and a price: the unit list price before the discount; null otherwise.';

reset role;
