import type { Computed, EconomicsReport, EconomicsRow, QuoteDraft, LineEconomics, QuoteEconomics, QuoteStoredLine, QuoteWorkspace } from "./quoteApi";

/** Test fixtures for the quote editor. Synthetic values only: this repository is public. */

export function computedFixture(net = "100000"): Computed {
  return {
    lines: [],
    expenses: [
      { line_no: 2, label: "Flete", amount: "150.5", currency: "USD", method: "profile", allocated_to_line_no: 1 },
      { line_no: 3, label: "Desaduanaje", amount: "45000", currency: "CLP", method: "profile", allocated_to_line_no: 1 },
    ],
    totals: {
      net,
      iva: "19000",
      total: "119000",
      cost_base: "70000",
      expenses_clp: "10000",
      markup: "0.35",
      margin: "0.26",
      margin_state: "in_range",
    },
    principal_line_no: 1,
    fx: [],
    warnings: [],
  };
}

export function draftFixture(): QuoteDraft {
  return {
    items: [
      { product_id: "p-1", description: "Equipo de prueba A", quantity: "1", margin_mode: "markup", margin_pct: null, is_principal: false },
      { product_id: null, description: "Servicio de prueba", quantity: "2", margin_mode: "markup", margin_pct: "0.3", is_principal: false },
    ],
    manual_expenses: [],
    expense_method: "profile",
    allocation_mode: "principal",
    principal_rule: "highest_cost_imported_equipment",
    fx_overrides: [],
    terms: { terms_extra: [] },
    recipient: null,
  };
}

export function workspaceFixture(overrides: Partial<NonNullable<QuoteWorkspace["current"]>> = {}): QuoteWorkspace {
  return {
    quote: { id: "q-1", quote_number: "00042-26", opportunity_id: "o-1" },
    case: {
      id: "o-1",
      title: "Caso de prueba",
      stage: "quoting",
      closed: false,
      institution: { id: "i-1", name: "Institución de prueba", rut: null },
      participants: [],
      contacts: [],
      lo_que_pidio: null,
    },
    revisions: [],
    current: {
      id: "r-1",
      revision_no: 1,
      status: "draft",
      origin: "authored",
      version: 3,
      quote_currency: "CLP",
      tax_rate: "0.19",
      subtotal: null,
      tax_total: null,
      grand_total: null,
      totals_computed_at: null,
      validity_days: null,
      valid_until: null,
      approved_at: null,
      sent_at: null,
      superseded_by_revision_no: null,
      recipient_participant_id: null,
      expense_method: "profile",
      expense_allocation_mode: "principal",
      principal_rule: "highest_cost_imported_equipment",
      updated_at: null,
      label: "00042-26",
      draft: { ...draftFixture(), recipient_participant_id: null },
      computed: computedFixture(),
      computed_error: null,
      needs_saving: false,
      ...overrides,
    },
    suggestions: { mentioned: [], kits: [] },
    price_history: null,
  };
}

/** A quote's economics, every line costed: 2 602 000 sold, 1 030 000 of products, 830 000 of import costs. */
export function economicsFixture(over: Partial<QuoteEconomics> = {}): QuoteEconomics {
  return {
    sale_net_clp: "2602000",
    product_cost_clp: "1030000",
    import_costs_clp: "830000",
    import_costs_split: { freight: "500000", agent: "120000", customs: "180000", transport: "30000", other: "0" },
    gross_margin_clp: "742000",
    gross_margin_pct: "0.2852",
    margin_state: "on_target",
    cost_coverage: { state: "full", item_lines: 2, lines_without_cost: 0 },
    ...over,
  };
}

/** The same quote with one line of two lacking a cost: no margin. */
export function partialEconomicsFixture(): QuoteEconomics {
  return economicsFixture({
    sale_net_clp: "1100000",
    product_cost_clp: "600000",
    import_costs_clp: "0",
    import_costs_split: { freight: "0", agent: "0", customs: "0", transport: "0", other: "0" },
    gross_margin_clp: null,
    gross_margin_pct: null,
    margin_state: "no_cost",
    cost_coverage: { state: "partial", item_lines: 2, lines_without_cost: 1 },
  });
}

export function economicsRowFixture(n: number, over: Partial<EconomicsRow> = {}): EconomicsRow {
  const number = `${String(40 + n).padStart(5, "0")}-26`;
  return {
    quote_id: `6bafddfa-0000-4000-8000-${String(n).padStart(12, "0")}`,
    revision_id: `r-${n}`,
    quote_number: number,
    label: number,
    client: `Universidad Ejemplo ${n}`,
    status: "approved",
    draft: false,
    date: "2026-10-16",
    month: "2026-10",
    currency: "CLP",
    source: "authored",
    economics: economicsFixture(),
    ...over,
  };
}

/** A period with one full quote, one partial one and a draft. */
export function economicsReportFixture(): EconomicsReport {
  const totals = {
    quotes: 2,
    sale_net_clp: "3702000",
    product_cost_clp: "1630000",
    import_costs_clp: "830000",
    import_costs_split: { freight: "500000", agent: "120000", customs: "180000", transport: "30000", other: "0" },
    gross_margin_clp: "742000",
    gross_margin_pct: "0.2852",
    coverage: {
      full: { quotes: 1, sale_net_clp: "2602000" },
      partial: { quotes: 1, sale_net_clp: "1100000" },
      none: { quotes: 0, sale_net_clp: "0" },
      full_share_of_sale: "0.7029",
    },
  };
  return {
    from: "2026-01",
    to: "2026-12",
    rows: [
      economicsRowFixture(1),
      economicsRowFixture(2, { economics: partialEconomicsFixture() }),
      economicsRowFixture(3, { status: "draft", draft: true, date: "2026-10-17" }),
    ],
    months: [{ month: "2026-10", ...totals }],
    totals: { ...totals, drafts: { quotes: 1, sale_net_clp: "2602000" } },
    params: { margin_target_min: "0.25", margin_target_max: "0.45" },
  };
}

/** One line's cost strip (`line_economics`): $ 450.000 + $ 430.000 of expenses, sold at $ 1.275.000. */
export function lineEconomics(over: Partial<LineEconomics> = {}): LineEconomics {
  return {
    line_no: 1,
    product_cost_clp: "450000",
    expenses_clp: "430000",
    cost_clp: "880000",
    sale_net_clp: "1275000",
    gain_clp: "395000",
    gross_margin: "0.3098",
    margin_state: "on_target",
    iva_clp: "242250",
    total_with_iva_clp: "1517250",
    ...over,
  };
}

/** One stored line of an issued revision (an item by default). */
export function storedLineFixture(over: Partial<QuoteStoredLine> = {}): QuoteStoredLine {
  return {
    line_no: 1, line_kind: "item", description: "Equipo de prueba", product_id: null, quantity: "1", item_type: "equipment",
    cost_currency: "CLP", unit_cost: "100000", fx_rate: null, fx_as_of: null, fx_source: null, margin_mode: "markup",
    margin_pct: "0.3", unit_price_qc: "150000", line_total_qc: "150000", is_principal: true, allocated_to_line_no: null,
    expense_method: null, supplier_organization_id: null, cost_observation_id: null, line_note_es: null,
    ...over,
  };
}
