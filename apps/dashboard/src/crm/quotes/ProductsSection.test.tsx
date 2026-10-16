/**
 * «Productos»: catalog search, suggestions, the line table and «Vista cliente». Every value is
 * invented. The numbers on screen are the API's `computed`, only formatted here.
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { CatalogIndex } from "../catalog/catalogIndex";
import type { IndexItem } from "../catalog/catalogSearch";
import { draftProblems } from "./draftEditor";
import type { Computed, ComputedLine, QuoteDraft, QuoteWorkspace } from "./quoteApi";
import { computedFixture, workspaceFixture } from "./quoteFixtures";
import { ProductsSection } from "./ProductsSection";

function item(over: Partial<IndexItem> & Pick<IndexItem, "id" | "model_number" | "name">): IndexItem {
  return {
    model_key: over.model_number.replace(/[\s\-_./]/g, "").toUpperCase(),
    category_es: null,
    kind: "equipment",
    manufacturer: "Fabricante Uno",
    supplier_ids: ["s-1"],
    image_id: null,
    has_datasheet: false,
    name_en: null,
    ...over,
  };
}

const INDEX: CatalogIndex = {
  version: "v1",
  suppliers: [{ id: "s-1", display_name: "Proveedor Uno", products: 4, by_kind: {} }],
  items: [
    item({ id: "p-1", model_number: "AG-100", name: "Agitador de prueba" }),
    item({ id: "p-2", model_number: "AG-100X", name: "Agitador de prueba grande" }),
    item({ id: "p-3", model_number: "SN-22", name: "Sonda de prueba", kind: "accessory" }),
    item({ id: "p-4", model_number: "CF-9", name: "Celda de prueba", kind: "accessory" }),
  ],
};

function line(over: Partial<ComputedLine> & Pick<ComputedLine, "line_no">): ComputedLine {
  return {
    item_type: "equipment",
    product_id: null,
    unit_cost: null,
    cost_currency: null,
    imported: false,
    margin_mode: "markup",
    margin_pct: null,
    cost_clp_unit: null,
    expense_to_line: "0",
    unit_base: null,
    unit_price: null,
    line_total: null,
    real_markup: null,
    gross_margin: null,
    margin_state: "no_cost",
    is_principal: false,
    ...over,
  };
}

function computed(): Computed {
  return {
    ...computedFixture(),
    principal_line_no: 1,
    lines: [
      line({
        line_no: 1,
        product_id: "p-1",
        unit_cost: "6900",
        cost_currency: "EUR",
        imported: true,
        margin_pct: "0.4",
        cost_clp_unit: "7500000",
        expense_to_line: "800000",
        unit_base: "8300000",
        unit_price: "11620000",
        line_total: "11620000",
        real_markup: "0.4",
        gross_margin: "0.2857",
        margin_state: "on_target",
        is_principal: true,
      }),
      line({ line_no: 2, item_type: "service", margin_pct: "0.3", margin_state: "no_cost" }),
    ],
  };
}

function draft(): QuoteDraft {
  return {
    items: [
      { uid: "u1", product_id: "p-1", description: "Agitador de prueba", quantity: "1", item_type: "equipment", margin_mode: "markup", margin_pct: "0.4", is_principal: false },
      { uid: "u2", product_id: null, description: "Servicio de prueba", quantity: "2", item_type: null, margin_mode: "markup", margin_pct: null, is_principal: false },
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

function ws(suggestions: QuoteWorkspace["suggestions"] = { mentioned: [], kits: [] }): QuoteWorkspace {
  return { ...workspaceFixture(), suggestions };
}

async function setup({
  workspace = ws(),
  initial = draft(),
  priced = computed(),
  keys = ["u1", "u2"],
  stale = false,
  clientView = false,
}: { workspace?: QuoteWorkspace; initial?: QuoteDraft; priced?: Computed | null; keys?: string[]; stale?: boolean; clientView?: boolean } = {}) {
  const latest: { draft: QuoteDraft; setPriced: (c: Computed | null) => void } = { draft: initial, setPriced: () => undefined };
  const edit = vi.fn();
  function Harness() {
    const [d, setD] = useState(initial);
    const [view, setView] = useState(clientView);
    const [shown, setShown] = useState(priced);
    latest.draft = d;
    latest.setPriced = setShown;
    return (
      <ProductsSection
        workspace={workspace}
        draft={d}
        computed={shown}
        computedKeys={keys}
        stale={stale}
        invalid={draftProblems(d)}
        disabled={false}
        clientView={view}
        onClientViewChange={setView}
        loadIndex={() => Promise.resolve(INDEX)}
        edit={(fn) => {
          edit(fn);
          setD((prev) => fn(prev));
        }}
      />
    );
  }
  render(<Harness />);
  await act(async () => {
    await Promise.resolve();
  });
  return { latest, edit };
}

const search = () => screen.getByRole("combobox", { name: "Agregar del catálogo" });

describe("ProductsSection — adding products", () => {
  it("finds the exact code first and adds it with Enter, with its product id and name", async () => {
    const { latest } = await setup();
    fireEvent.change(search(), { target: { value: "ag-100" } });
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options[0]).toHaveTextContent("AG-100");
    expect(options[0]).toHaveTextContent("Agitador de prueba");
    expect(options[0]).toHaveTextContent("Fabricante Uno");
    expect(options[1]).toHaveTextContent("AG-100X");
    fireEvent.keyDown(search(), { key: "Enter" });
    expect(latest.draft.items).toHaveLength(3);
    expect(latest.draft.items[2]).toMatchObject({ product_id: "p-1", description: "Agitador de prueba", quantity: "1", margin_mode: "markup" });
    expect(search()).toHaveValue("");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("adds the result clicked, and the arrow keys move through the results", async () => {
    const { latest } = await setup();
    fireEvent.change(search(), { target: { value: "AG-100" } });
    fireEvent.keyDown(search(), { key: "ArrowDown" });
    fireEvent.keyDown(search(), { key: "Enter" });
    expect(latest.draft.items[2].product_id).toBe("p-2");
    fireEvent.change(search(), { target: { value: "sonda" } });
    fireEvent.mouseDown(screen.getByRole("option", { name: /SN-22/ }));
    expect(latest.draft.items[3]).toMatchObject({ product_id: "p-3", description: "Sonda de prueba", item_type: "accessory" });
  });

  it("focuses the search with «/» when no field has the focus", async () => {
    await setup();
    fireEvent.keyDown(document.body, { key: "/" });
    expect(search()).toHaveFocus();
  });

  it("offers what usually goes with the principal and what the email mentioned, once each", async () => {
    const { latest } = await setup({
      workspace: ws({
        kits: [
          { model_key: "SN22", product_id: "p-3", name: "Sonda de prueba", share: "0.6", documents: 5 },
          { model_key: "XX1", product_id: null, name: "Sin catálogo", share: "0.4", documents: 2 },
        ],
        mentioned: [
          { product_id: "p-4", model: "CF-9", name: "Celda de prueba", model_key: "CF9" },
          { product_id: "p-1", model: "AG-100", name: "Agitador de prueba", model_key: "AG100" },
        ],
      }),
    });
    expect(screen.getByText("Suele ir con AG-100:")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Sin catálogo" })).not.toBeInTheDocument();
    // Already on the quote: not offered again.
    expect(screen.queryByRole("button", { name: "+ Agitador de prueba" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Sonda de prueba" }));
    expect(latest.draft.items[2]).toMatchObject({ product_id: "p-3", description: "Sonda de prueba", item_type: "accessory" });
    expect(screen.queryByRole("button", { name: "+ Sonda de prueba" })).not.toBeInTheDocument();
    expect(screen.getByText("Mencionado en el correo:")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Celda de prueba" }));
    expect(latest.draft.items[3]).toMatchObject({ product_id: "p-4", description: "Celda de prueba" });
  });

  it("adds a free line, focuses its description and says it needs one", async () => {
    const { latest } = await setup();
    fireEvent.click(screen.getByRole("button", { name: "+ Línea libre (sin catálogo)" }));
    expect(latest.draft.items[2]).toMatchObject({ product_id: null, description: "" });
    const desc = screen.getByLabelText("Descripción de la línea 3");
    expect(desc).toHaveFocus();
    expect(screen.getByText("Escribe la descripción de la línea 3.")).toBeInTheDocument();
    fireEvent.change(desc, { target: { value: "Instalación de prueba" } });
    expect(latest.draft.items[2].description).toBe("Instalación de prueba");
    expect(screen.queryByText("Escribe la descripción de la línea 3.")).not.toBeInTheDocument();
  });
});

describe("ProductsSection — the table", () => {
  it("prints the API's numbers: cost, cost in pesos with expenses, price, total and margin", async () => {
    await setup();
    const row = screen.getByRole("row", { name: /Agitador de prueba/ });
    expect(within(row).getByText("€ 6.900,00")).toBeInTheDocument();
    expect(within(row).getByText("$ 8.300.000 con gastos")).toBeInTheDocument();
    expect(within(row).getByLabelText("Precio unitario de la línea 1")).toHaveValue("$ 11.620.000");
    expect(within(row).getByText("$ 11.620.000")).toBeInTheDocument();
    expect(within(row).getByText("28,6 %")).toBeInTheDocument();
    expect(within(row).getByText("En rango")).toBeInTheDocument();
    expect(within(row).getByText("Equipo principal · absorbe los gastos")).toBeInTheDocument();
    expect(within(row).getByText("AG-100")).toBeInTheDocument();
    expect(within(row).getByText(/Proveedor Uno/)).toBeInTheDocument();
    const free = screen.getByRole("row", { name: /Servicio de prueba/ });
    expect(within(free).getByText("Sin costo")).toBeInTheDocument();
  });

  it("names every margin state in words", async () => {
    const states: [string, string][] = [
      ["loss", "Pérdida"],
      ["below_target", "Bajo"],
      ["on_target", "En rango"],
      ["above_target", "Sobre"],
      ["no_cost", "Sin costo"],
    ];
    for (const [state, text] of states) {
      const c = computed();
      c.lines[0] = { ...c.lines[0], margin_state: state };
      const { unmount } = render(
        <ProductsSection
          workspace={ws()}
          draft={draft()}
          computed={c}
          computedKeys={["u1", "u2"]}
          stale={false}
          invalid={[]}
          disabled={false}
          clientView={false}
          onClientViewChange={() => undefined}
          loadIndex={() => Promise.resolve(INDEX)}
          edit={() => undefined}
        />,
      );
      expect(within(screen.getByRole("row", { name: /Agitador de prueba/ })).getByText(text)).toBeInTheDocument();
      unmount();
    }
  });

  it("shows the stored recargo and sends a typed one as a ratio", async () => {
    const { latest, edit } = await setup();
    const markup = screen.getByLabelText("Recargo de la línea 1");
    expect(markup).toHaveValue("40");
    fireEvent.focus(markup);
    fireEvent.change(markup, { target: { value: "45,5" } });
    expect(edit).toHaveBeenCalled();
    expect(latest.draft.items[0]).toMatchObject({ margin_mode: "markup", margin_pct: "0.455" });
    expect(markup).toHaveValue("45,5");
  });

  it("shows the type's default recargo when the line has none", async () => {
    await setup();
    expect(screen.getByLabelText("Recargo de la línea 2")).toHaveAttribute("placeholder", "30");
  });

  it("typing a price prices the line by hand, and «volver a recargo» undoes it", async () => {
    const { latest } = await setup();
    const price = screen.getByLabelText("Precio unitario de la línea 1");
    fireEvent.focus(price);
    expect(price).toHaveValue("11620000");
    fireEvent.change(price, { target: { value: "1.500.000" } });
    expect(latest.draft.items[0]).toMatchObject({ margin_mode: "none", typed_unit_price: "1500000", margin_pct: "0.4" });
    fireEvent.blur(price);
    expect(screen.getByText("por precio")).toBeInTheDocument();
    expect(screen.queryByLabelText("Recargo de la línea 1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "volver a recargo" }));
    expect(latest.draft.items[0]).toMatchObject({ margin_mode: "markup", typed_unit_price: null, margin_pct: "0.4" });
    expect(screen.getByLabelText("Recargo de la línea 1")).toHaveValue("40");
  });

  it("edits the quantity with a decimal comma", async () => {
    const { latest } = await setup();
    const qty = screen.getByLabelText("Cantidad de la línea 2");
    fireEvent.focus(qty);
    fireEvent.change(qty, { target: { value: "2,5" } });
    expect(latest.draft.items[1].quantity).toBe("2.5");
  });

  it("makes a line the principal from its menu", async () => {
    const { latest } = await setup();
    fireEvent.click(screen.getByRole("button", { name: "Más acciones de la línea 2" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Hacer principal" }));
    expect(latest.draft.principal_rule).toBe("manual");
    expect(latest.draft.items.map((i) => i.is_principal)).toEqual([false, true]);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("removes a line from its menu", async () => {
    const { latest } = await setup();
    fireEvent.click(screen.getByRole("button", { name: "Más acciones de la línea 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Quitar" }));
    expect(latest.draft.items).toHaveLength(1);
    expect(latest.draft.items[0].description).toBe("Servicio de prueba");
  });

  it("writes a note for the client on a line", async () => {
    const { latest } = await setup();
    fireEvent.click(screen.getByRole("button", { name: "Más acciones de la línea 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Nota para el cliente" }));
    const note = screen.getByLabelText("Nota para el cliente, línea 1 (sale en el PDF)");
    expect(note).toHaveFocus();
    fireEvent.change(note, { target: { value: "Incluye capacitación de prueba." } });
    expect(latest.draft.items[0].line_note_es).toBe("Incluye capacitación de prueba.");
  });

  it("«Vista cliente» hides costs, recargo and margin", async () => {
    await setup();
    expect(screen.getByRole("columnheader", { name: "Costo unit." })).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Vista cliente" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    for (const name of ["Costo unit.", "Recargo", "Margen"]) {
      expect(screen.queryByRole("columnheader", { name })).not.toBeInTheDocument();
    }
    expect(screen.getByRole("columnheader", { name: "Precio unit." })).toBeInTheDocument();
    expect(screen.queryByText("€ 6.900,00")).not.toBeInTheDocument();
    expect(screen.queryByText("28,6 %")).not.toBeInTheDocument();
    expect(screen.queryByText("En rango")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Recargo de la línea 1")).not.toBeInTheDocument();
  });

  it("shows no number for a line the API has not priced yet", async () => {
    await setup({ priced: null });
    const row = screen.getByRole("row", { name: /Agitador de prueba/ });
    expect(within(row).queryByText(/\$/)).not.toBeInTheDocument();
  });

  it("says when the quote has no lines yet", async () => {
    await setup({ initial: { ...draft(), items: [] }, priced: { ...computed(), lines: [] } });
    expect(screen.getByText(/Busca un producto del catálogo/)).toBeInTheDocument();
  });
});

describe("ProductsSection — each row keeps its own numbers", () => {
  it("after a line above is removed, the next row shows its own prices, not the removed one's", async () => {
    await setup();
    fireEvent.click(screen.getByRole("button", { name: "Más acciones de la línea 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Quitar" }));
    const row = screen.getByRole("row", { name: /Servicio de prueba/ });
    expect(within(row).queryByText("$ 11.620.000")).not.toBeInTheDocument();
    expect(within(row).queryByText("En rango")).not.toBeInTheDocument();
    expect(within(row).getByText("Sin costo")).toBeInTheDocument();
  });

  it("two lines of the same product each keep their price", async () => {
    const c = computed();
    c.lines = [
      { ...c.lines[0], unit_price: "100", line_total: "100" },
      { ...c.lines[0], line_no: 2, unit_price: "200", line_total: "200", is_principal: false },
    ];
    const same = draft();
    same.items = [same.items[0], { ...same.items[0], uid: "u2" }];
    await setup({ initial: same, priced: c });
    fireEvent.click(screen.getByRole("button", { name: "Más acciones de la línea 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Quitar" }));
    expect(screen.getByLabelText("Precio unitario de la línea 1")).toHaveValue("$ 200");
  });

  it("dims the numbers and marks them busy while the prices are for an older draft", async () => {
    await setup({ stale: true });
    const row = screen.getByRole("row", { name: /Agitador de prueba/ });
    expect(within(row).getByText("$ 11.620.000").closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");
  });

  it("finds the principal by the draft the API priced", async () => {
    const c = computed();
    c.lines = [{ ...c.lines[0], product_id: null, line_no: 1, is_principal: true }, { ...c.lines[1], line_no: 2, is_principal: false }];
    await setup({
      priced: c,
      keys: ["u2", "u1"],
      workspace: ws({ kits: [{ model_key: "SN22", product_id: "p-3", name: "Sonda de prueba", share: "0.6", documents: 5 }], mentioned: [] }),
    });
    expect(screen.getByText("Suele ir con Servicio de prueba:")).toBeInTheDocument();
    const free = screen.getByRole("row", { name: /Servicio de prueba/ });
    expect(within(free).getByText("Equipo principal · absorbe los gastos")).toBeInTheDocument();
    expect(within(free).queryByText("Sin costo")).not.toBeInTheDocument();
  });

  it("a row's open note moves with the row", async () => {
    await setup();
    fireEvent.click(screen.getByRole("button", { name: "Más acciones de la línea 2" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Nota para el cliente" }));
    fireEvent.click(screen.getByRole("button", { name: "Más acciones de la línea 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Quitar" }));
    expect(screen.getByLabelText("Nota para el cliente, línea 1 (sale en el PDF)")).toBeInTheDocument();
  });
});

describe("ProductsSection — costs typed by hand", () => {
  it("a free line takes a cost and its currency", async () => {
    const { latest } = await setup();
    fireEvent.change(screen.getByLabelText("Moneda del costo de la línea 2"), { target: { value: "EUR" } });
    expect(latest.draft.items[1].cost_currency ?? null).toBeNull();
    const cost = screen.getByLabelText("Costo unitario de la línea 2");
    fireEvent.focus(cost);
    fireEvent.change(cost, { target: { value: "980,5" } });
    expect(latest.draft.items[1]).toMatchObject({ unit_cost: "980.5", cost_currency: "EUR", cost_observation_id: null });
    fireEvent.change(screen.getByLabelText("Moneda del costo de la línea 2"), { target: { value: "USD" } });
    expect(latest.draft.items[1].cost_currency).toBe("USD");
    fireEvent.change(cost, { target: { value: "" } });
    expect(latest.draft.items[1]).toMatchObject({ unit_cost: null, cost_currency: null });
  });

  it("a catalog line the API found no cost for takes one", async () => {
    const c = computed();
    c.lines[0] = { ...c.lines[0], unit_cost: null, cost_currency: null, cost_clp_unit: null, margin_state: "no_cost" };
    const { latest } = await setup({ priced: c });
    const cost = screen.getByLabelText("Costo unitario de la línea 1");
    fireEvent.change(cost, { target: { value: "1.200" } });
    expect(latest.draft.items[0]).toMatchObject({ unit_cost: "1200", cost_currency: "CLP" });
  });

  it("a catalog line with a known cost keeps it until «cambiar costo», and «usar costo del catálogo» goes back", async () => {
    const { latest } = await setup();
    expect(screen.queryByLabelText("Costo unitario de la línea 1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "cambiar costo" }));
    expect(screen.getByLabelText("Moneda del costo de la línea 1")).toHaveValue("EUR");
    const cost = screen.getByLabelText("Costo unitario de la línea 1");
    fireEvent.change(cost, { target: { value: "7000" } });
    expect(latest.draft.items[0]).toMatchObject({ unit_cost: "7000", cost_currency: "EUR", cost_observation_id: null });
    fireEvent.click(screen.getByRole("button", { name: "usar costo del catálogo" }));
    expect(latest.draft.items[0]).toMatchObject({ unit_cost: null, cost_currency: null });
    expect(screen.queryByLabelText("Costo unitario de la línea 1")).not.toBeInTheDocument();
  });
});

describe("ProductsSection — details", () => {
  it("«Vista cliente» also hides the supplier and the principal badge", async () => {
    await setup({ clientView: true });
    const row = screen.getByRole("row", { name: /Agitador de prueba/ });
    expect(within(row).getByText("AG-100")).toBeInTheDocument();
    expect(within(row).queryByText(/Proveedor Uno/)).not.toBeInTheDocument();
    expect(within(row).queryByText("Equipo principal · absorbe los gastos")).not.toBeInTheDocument();
  });

  it("asks about «1.000» as a quantity instead of guessing", async () => {
    const { latest } = await setup();
    const qty = screen.getByLabelText("Cantidad de la línea 1");
    fireEvent.focus(qty);
    fireEvent.change(qty, { target: { value: "1.000" } });
    expect(screen.getByText("Usa coma para decimales (1,5) — ¿quisiste decir 1000?")).toBeInTheDocument();
    expect(draftProblems(latest.draft)).toHaveLength(1);
    // At rest it stays as typed, not re-read as «1».
    fireEvent.blur(qty);
    expect(qty).toHaveValue("1.000");
    fireEvent.focus(qty);
    fireEvent.change(qty, { target: { value: "1000" } });
    expect(latest.draft.items[0].quantity).toBe("1000");
    expect(draftProblems(latest.draft)).toEqual([]);
  });
});

describe("ProductsSection — a cost being cleared stays editable", () => {
  it("clearing a typed cost on a catalog line keeps its inputs, even once the API priced it", async () => {
    const c = computed();
    c.lines[0] = { ...c.lines[0], unit_cost: null, cost_currency: null, cost_clp_unit: null, margin_state: "no_cost" };
    const { latest } = await setup({ priced: c });
    const cost = screen.getByLabelText("Costo unitario de la línea 1");
    fireEvent.focus(cost);
    fireEvent.change(cost, { target: { value: "1200" } });
    // The preview answers with the typed cost.
    act(() => latest.setPriced(computed()));
    fireEvent.change(screen.getByLabelText("Costo unitario de la línea 1"), { target: { value: "" } });
    expect(latest.draft.items[0]).toMatchObject({ unit_cost: null, cost_currency: null });
    expect(screen.getByLabelText("Costo unitario de la línea 1")).toBeInTheDocument();
  });

  it("a price that cannot be read stays as typed", async () => {
    await setup();
    const price = screen.getByLabelText("Precio unitario de la línea 1");
    fireEvent.focus(price);
    fireEvent.change(price, { target: { value: "mil" } });
    fireEvent.blur(price);
    expect(price).toHaveValue("mil");
    expect(screen.getByText(/El precio debe ser un número/)).toBeInTheDocument();
  });
});
