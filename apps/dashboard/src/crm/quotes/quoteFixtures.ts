import type { Computed, QuoteDraft, QuoteWorkspace } from "./quoteApi";

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
