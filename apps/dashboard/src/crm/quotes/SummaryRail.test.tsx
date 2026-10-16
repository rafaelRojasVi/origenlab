/**
 * The Cotizador's right rail: totals and margin, exchange rates, «Avisos» and the principal model's
 * past prices. Every value is invented; every number on screen is the API's, only formatted.
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CatalogIndex } from "../catalog/catalogIndex";
import type { PriceHistory } from "../catalog/catalogApi";
import { initialDraftState, setFxOverride, type DraftEditorState } from "./draftEditor";
import { QuoteRefusal, type Computed, type QuoteDraft, type QuoteWorkspace } from "./quoteApi";
import { computedFixture, workspaceFixture } from "./quoteFixtures";
import { SummaryRail } from "./SummaryRail";

const INDEX: CatalogIndex = {
  version: "v1",
  suppliers: [],
  items: [
    {
      id: "p-1",
      model_number: "AG-100",
      model_key: "AG100",
      name: "Agitador de prueba",
      category_es: null,
      kind: "equipment",
      manufacturer: "Fabricante Uno",
      supplier_ids: [],
      image_id: null,
      has_datasheet: false,
      name_en: null,
    },
    {
      id: "p-2",
      model_number: "BX-7",
      model_key: "BX7",
      name: "Baño de prueba",
      category_es: null,
      kind: "equipment",
      manufacturer: "Fabricante Uno",
      supplier_ids: [],
      image_id: null,
      has_datasheet: false,
      name_en: null,
    },
  ],
};

function history(over: Partial<PriceHistory> = {}): PriceHistory {
  const row = (date: string, price: string, institution: string | null, client_type: string | null = null) => ({
    date,
    line_total: price,
    qty: "1",
    unit_price: price,
    currency: "CLP",
    check_status: null,
    client_type,
    document_number: `000${date.slice(8)}-25`,
    case: institution ? { id: `c-${date}`, institution, outcome: "open" as const } : null,
  });
  return {
    model_key: "AG100",
    median_last_5: "1100000",
    items: [
      row("2025-01-10", "900000", "Instituto Uno"),
      row("2025-06-02", "1200000", "Laboratorio Dos"),
      row("2025-03-15", "1000000", null, "universidad"),
      row("2025-09-20", "1150000", "Centro Cuatro"),
      row("2025-11-05", "1100000", "Empresa Cinco"),
    ],
    ...over,
  };
}

function priced(over: Partial<Computed> = {}): Computed {
  return {
    ...computedFixture(),
    totals: {
      net: "1748786",
      iva: "332269",
      total: "2081055",
      cost_base: "1235277",
      expenses_clp: "100000",
      markup: "0.4157",
      margin: "0.2937",
      margin_state: "on_target",
    },
    fx: [{ currency: "EUR", clp_per_unit: "1099.02", as_of: "2026-10-15", source: "bcentral" }],
    ...over,
  };
}

type Current = NonNullable<QuoteWorkspace["current"]>;

function stateFor(
  current: Partial<Current> = {},
  over: Partial<DraftEditorState> = {},
  ws: Partial<QuoteWorkspace> = {},
): DraftEditorState {
  const workspace = { ...workspaceFixture({ computed: priced(), ...current }), ...ws };
  const s = initialDraftState(workspace);
  // The fixture's first line is the principal one (`principal_line_no: 1`).
  return { ...s, ...over };
}

function setup({
  state = stateFor(),
  clientView = false,
  stale = false,
  disabled = false,
  loadPriceHistory = vi.fn(() => Promise.resolve(history({ model_key: "BX7", median_last_5: "500000", items: [] }))),
}: {
  state?: DraftEditorState;
  clientView?: boolean;
  stale?: boolean;
  disabled?: boolean;
  loadPriceHistory?: (modelKey: string) => Promise<PriceHistory>;
} = {}) {
  const edit = vi.fn();
  const saveNow = vi.fn(() => Promise.resolve({ ok: true, version: 4 }));
  const view = render(
    <SummaryRail
      state={state}
      stale={stale}
      disabled={disabled}
      clientView={clientView}
      edit={edit}
      onSaveNow={saveNow}
      loadIndex={() => Promise.resolve(INDEX)}
      loadPriceHistory={loadPriceHistory}
    />,
  );
  /** The draft the last edit produced, from `draft`. */
  const applied = (draft: QuoteDraft = state.draft): QuoteDraft => edit.mock.calls.at(-1)![0](draft);
  return { edit, saveNow, applied, view, loadPriceHistory };
}

async function settled() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

describe("SummaryRail — totals and margin", () => {
  it("shows the API's net, IVA and total, and the margin on the target band", async () => {
    setup();
    await settled();
    const totals = screen.getByRole("region", { name: "Totales" });
    expect(within(totals).getByText("Neto").nextSibling).toHaveTextContent("$ 1.748.786");
    expect(within(totals).getByText("IVA 19 %").nextSibling).toHaveTextContent("$ 332.269");
    expect(within(totals).getByText("Total").nextSibling).toHaveTextContent("$ 2.081.055");
    expect(within(totals).getByText("29,4 %")).toBeInTheDocument();
    expect(within(totals).getByText("En rango")).toBeInTheDocument();
    expect(within(totals).getByText("objetivo 25–45 %")).toBeInTheDocument();
    expect(within(totals).getByText("Recargo sobre costo 41,6 % · costo total $ 1.235.277")).toBeInTheDocument();
    const band = within(totals).getByRole("img", { name: /Margen bruto 29,4 %/ });
    expect(band.querySelector("[data-marker]")).toHaveStyle({ left: "calc(29.37% - 6px)" });
  });

  it("says when the margin is below the target, in words and not only in colour", async () => {
    setup({ state: stateFor({ computed: priced({ totals: { ...priced().totals, margin: "0.12", margin_state: "below_target" } }) }) });
    await settled();
    expect(screen.getByText("Bajo el objetivo")).toBeInTheDocument();
  });

  it("without a margin (a line has no cost) says so and draws no marker", async () => {
    setup({ state: stateFor({ computed: priced({ totals: { ...priced().totals, margin: null, markup: null, margin_state: "no_cost" } }) }) });
    await settled();
    const totals = screen.getByRole("region", { name: "Totales" });
    expect(within(totals).getByText("Sin costo")).toBeInTheDocument();
    expect(totals.querySelector("[data-marker]")).toBeNull();
  });

  it("«Vista cliente» hides the margin, the warnings and the price history (B7)", async () => {
    setup({
      clientView: true,
      state: stateFor({ computed: priced({ warnings: ["no_cost:2"] }) }, {}, { price_history: history() }),
    });
    await settled();
    const totals = screen.getByRole("region", { name: "Totales" });
    expect(within(totals).getByText("Total")).toBeInTheDocument();
    expect(screen.queryByText("Margen bruto")).not.toBeInTheDocument();
    expect(screen.queryByText(/Recargo sobre costo/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Avisos" })).not.toBeInTheDocument();
    expect(screen.queryByText("AG-100 antes")).not.toBeInTheDocument();
  });

  it("dims the totals while they price an older draft", async () => {
    setup({ stale: true });
    await settled();
    expect(screen.getByRole("region", { name: "Totales" })).toHaveAttribute("aria-busy", "true");
  });

  it("an issued revision shows its stored totals and no exchange-rate change", async () => {
    const state = stateFor({
      status: "approved",
      subtotal: "500000",
      tax_total: "95000",
      grand_total: "595000",
      draft: undefined,
      computed: undefined,
      lines: [
        {
          line_no: 1, line_kind: "item", description: "Equipo de prueba A", product_id: "p-1", quantity: "1", item_type: "equipment",
          cost_currency: "EUR", unit_cost: "300", fx_rate: "1050.5", fx_as_of: "2026-10-01", fx_source: "bcentral",
          margin_mode: "markup", margin_pct: "0.4", unit_price_qc: "500000", line_total_qc: "500000", is_principal: true,
          allocated_to_line_no: null, expense_method: null, supplier_organization_id: null, cost_observation_id: null, line_note_es: null,
        },
      ],
    });
    setup({ state });
    await settled();
    const totals = screen.getByRole("region", { name: "Totales" });
    expect(within(totals).getByText("Neto").nextSibling).toHaveTextContent("$ 500.000");
    expect(within(totals).getByText("Total").nextSibling).toHaveTextContent("$ 595.000");
    expect(screen.queryByText("Margen bruto")).not.toBeInTheDocument();
    const fx = screen.getByRole("region", { name: "Tipo de cambio" });
    expect(within(fx).getByText("$ 1.050,50")).toBeInTheDocument();
    expect(within(fx).queryByRole("button", { name: /Cambiar/ })).not.toBeInTheDocument();
  });
});

describe("SummaryRail — exchange rates", () => {
  it("shows each rate with its source and day", async () => {
    setup();
    await settled();
    const fx = screen.getByRole("region", { name: "Tipo de cambio" });
    expect(within(fx).getByText("EUR")).toBeInTheDocument();
    expect(within(fx).getByText("$ 1.099,02")).toBeInTheDocument();
    expect(within(fx).getByText(/Banco Central, 15 oct/)).toBeInTheDocument();
  });

  it("«Cambiar» sets a manual rate with its reason", async () => {
    const { applied } = setup();
    await settled();
    fireEvent.click(screen.getByRole("button", { name: "Cambiar EUR" }));
    fireEvent.change(screen.getByLabelText("Tipo de cambio EUR (pesos)"), { target: { value: "1.105,5" } });
    fireEvent.change(screen.getByLabelText("Motivo"), { target: { value: "tasa del proveedor" } });
    fireEvent.click(screen.getByRole("button", { name: "Usar este tipo de cambio" }));
    expect(applied().fx_overrides).toEqual([{ currency: "EUR", clp_per_unit: "1105.5", reason: "tasa del proveedor" }]);
    expect(screen.queryByLabelText("Motivo")).not.toBeInTheDocument();
  });

  it("asks for a reason of three letters and a number before changing the rate", async () => {
    const { edit } = setup();
    await settled();
    fireEvent.click(screen.getByRole("button", { name: "Cambiar EUR" }));
    fireEvent.change(screen.getByLabelText("Tipo de cambio EUR (pesos)"), { target: { value: "1100" } });
    fireEvent.change(screen.getByLabelText("Motivo"), { target: { value: "ok" } });
    fireEvent.click(screen.getByRole("button", { name: "Usar este tipo de cambio" }));
    expect(screen.getByText("Indica por qué cambias el tipo de cambio (mínimo 3 letras).")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Motivo"), { target: { value: "tasa fija" } });
    fireEvent.change(screen.getByLabelText("Tipo de cambio EUR (pesos)"), { target: { value: "mil" } });
    fireEvent.click(screen.getByRole("button", { name: "Usar este tipo de cambio" }));
    expect(screen.getByText("Escribe el tipo de cambio en pesos, por ejemplo 1.099,02.")).toBeInTheDocument();
    expect(edit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByLabelText("Motivo")).not.toBeInTheDocument();
  });

  it("a manual rate shows its reason and goes back to the Banco Central", async () => {
    const base = stateFor({ computed: priced({ fx: [{ currency: "EUR", clp_per_unit: "1105.5", as_of: "2026-10-16", source: "manual" }] }) });
    const state = { ...base, draft: setFxOverride(base.draft, "EUR", "1105.5", "tasa del proveedor") };
    const { applied } = setup({ state });
    await settled();
    expect(screen.getByText("manual: tasa del proveedor")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Volver al Banco Central" }));
    expect(applied(state.draft).fx_overrides).toEqual([]);
  });

  it("no change while the editor is frozen", async () => {
    setup({ disabled: true });
    await settled();
    expect(screen.getByRole("button", { name: "Cambiar EUR" })).toBeDisabled();
  });

  it("says when no line is in a foreign currency", async () => {
    setup({ state: stateFor({ computed: priced({ fx: [] }) }) });
    await settled();
    expect(screen.getByText("Todas las líneas están en pesos.")).toBeInTheDocument();
  });
});

describe("SummaryRail — avisos", () => {
  it("says the known warnings in Spanish, numbering lines as the table does, and leaves the expense ones to «Gastos»", async () => {
    const base = stateFor({
      computed: priced({
        warnings: ["principal_missing", "no_cost:2", "profile_parameter_missing:profile_freight_clp", "dhl_fallback:dhl_rate_missing", "algo_nuevo"],
      }),
    });
    setup({ state: base });
    await settled();
    const avisos = screen.getByRole("region", { name: "Avisos" });
    expect(within(avisos).getByText("Hay gastos de importación pero ningún equipo principal.")).toBeInTheDocument();
    expect(within(avisos).getByText("La línea 2 no tiene costo.")).toBeInTheDocument();
    expect(within(avisos).getByText("algo_nuevo")).toBeInTheDocument();
    expect(within(avisos).queryByText(/profile_freight_clp/)).not.toBeInTheDocument();
    expect(within(avisos).queryByText(/Faltan pesos/)).not.toBeInTheDocument();
  });

  it("a «no_cost» line keeps the number the table shows after an earlier line is removed", async () => {
    const base = stateFor({ computed: priced({ warnings: ["no_cost:2"] }) });
    const state = { ...base, draft: { ...base.draft, items: base.draft.items.slice(1) } };
    setup({ state });
    await settled();
    expect(screen.getByText("La línea 1 no tiene costo.")).toBeInTheDocument();
  });

  it("shows why the draft could not be priced", async () => {
    setup({ state: stateFor({ computed: null, computed_error: { code: "fx_unavailable", message: "No hay tipo de cambio para EUR." } }) });
    await settled();
    expect(within(screen.getByRole("region", { name: "Avisos" })).getByText("No hay tipo de cambio para EUR.")).toBeInTheDocument();
  });

  it("shows a refused preview in the API's words", async () => {
    setup({ state: stateFor({}, { previewError: new QuoteRefusal(422, "fx_unavailable", "No hay tipo de cambio para USD.") }) });
    await settled();
    expect(screen.getByText("No hay tipo de cambio para USD.")).toBeInTheDocument();
  });

  it("has no card when there is nothing to say", async () => {
    setup();
    await settled();
    expect(screen.queryByRole("region", { name: "Avisos" })).not.toBeInTheDocument();
  });
});

describe("SummaryRail — prices changed since the last save (B4)", () => {
  const B4 = "Los precios cambiaron desde el último guardado (tipo de cambio o parámetros). Guarda para usarlos.";

  it("asks to save when the saved totals are older than today's prices, and saves at once", async () => {
    const { saveNow } = setup({ state: stateFor({ needs_saving: true, totals_computed_at: "2026-10-10T12:00:00Z" }) });
    await settled();
    expect(screen.getByText(B4)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Guardar ahora" }));
    expect(saveNow).toHaveBeenCalledTimes(1);
  });

  it("says nothing for a draft never priced, or once it was saved here", async () => {
    setup({ state: stateFor({ needs_saving: true, totals_computed_at: null }) });
    await settled();
    expect(screen.queryByText(B4)).not.toBeInTheDocument();
  });

  it("says nothing after a save in this page", async () => {
    setup({ state: stateFor({ needs_saving: true, totals_computed_at: "2026-10-10T12:00:00Z" }, { lastSavedAt: 1 }) });
    await settled();
    expect(screen.queryByText(B4)).not.toBeInTheDocument();
  });
});

describe("SummaryRail — the principal model before", () => {
  it("shows the median and the last four quotes, newest first", async () => {
    setup({ state: stateFor({}, {}, { price_history: history() }) });
    await settled();
    const card = screen.getByRole("region", { name: "AG-100 antes" });
    expect(within(card).getByText("mediana $ 1.100.000")).toBeInTheDocument();
    const rows = within(card).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("Empresa Cinco");
    expect(rows[0]).toHaveTextContent("$ 1.100.000");
    expect(rows[3]).toHaveTextContent("universidad");
    expect(within(card).queryByText("Instituto Uno")).not.toBeInTheDocument();
  });

  it("says so when the model was never quoted", async () => {
    const { loadPriceHistory } = setup({ state: stateFor({}, {}, { price_history: null }) });
    await settled();
    expect(screen.getByText("Este equipo no aparece en cotizaciones anteriores.")).toBeInTheDocument();
    // The workspace's history is the saved principal's: nothing more is read for it.
    expect(loadPriceHistory).not.toHaveBeenCalled();
  });

  it("reads the new principal's history when the operator picks another one", async () => {
    const base = stateFor({}, {}, { price_history: history() });
    const items = base.draft.items.map((i, n) => (n === 1 ? { ...i, product_id: "p-2", description: "Baño de prueba" } : i));
    const state = { ...base, draft: { ...base.draft, items }, computed: priced({ principal_line_no: 2 }) };
    const { loadPriceHistory } = setup({ state });
    await settled();
    expect(loadPriceHistory).toHaveBeenCalledWith("BX7");
    expect(screen.getByRole("region", { name: "BX-7 antes" })).toBeInTheDocument();
    expect(screen.queryByText("Empresa Cinco")).not.toBeInTheDocument();
    expect(screen.getByText("Este equipo no aparece en cotizaciones anteriores.")).toBeInTheDocument();
  });

  it("asks for a principal when there is none", async () => {
    setup({ state: stateFor({ computed: priced({ principal_line_no: null }) }) });
    await settled();
    expect(screen.getByText("Marca un equipo principal para ver sus precios anteriores.")).toBeInTheDocument();
  });
});
