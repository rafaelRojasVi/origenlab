/**
 * «Vista previa PDF»: the client's document. Every value is invented (the repository is public).
 * The internal values below are distinctive numbers so a leak into the DOM would be seen.
 */
import "@testing-library/jest-dom";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CatalogIndex } from "../catalog/catalogIndex";
import { isPrintable, printableQuote } from "./printableQuote";
import { computedFixture, workspaceFixture } from "./quoteFixtures";
import type { ComputedLine, QuoteStoredLine, QuoteWorkspace } from "./quoteApi";
import { QuotePrintView } from "./QuotePrintView";

const SECRET_COST = "987654";
const SUPPLIER = "s-proveedor-secreto";

function line(line_no: number, unit: string, total: string): ComputedLine {
  return {
    line_no, item_type: "equipment", product_id: null, unit_cost: SECRET_COST, cost_currency: "EUR",
    supplier_organization_id: SUPPLIER, cost_observation_id: null, imported: true, margin_mode: "markup",
    margin_pct: "0.4321", cost_clp_unit: "111222333", expense_to_line: "444555", unit_base: "666777",
    unit_price: unit, line_total: total, real_markup: "0.4321", gross_margin: "0.3012", margin_state: "in_range",
    is_principal: line_no === 1,
  };
}

/** A priced draft: two items, import expenses, costs and margins in the computed block. */
function pricedDraft(): QuoteWorkspace {
  const ws = workspaceFixture();
  const cur = ws.current!;
  return {
    ...ws,
    case: {
      ...ws.case,
      contacts: [{ participant_id: "pa-1", person_id: null, contact_point_id: null, name: "Ana Prueba", email: "ana@ejemplo.cl", source: "participant" }],
    },
    current: {
      ...cur,
      draft: {
        ...cur.draft!,
        items: [
          { ...cur.draft!.items[0], unit_cost: SECRET_COST, supplier_organization_id: SUPPLIER, line_note_es: "Con manual en español." },
          cur.draft!.items[1],
        ],
        recipient: { participant_id: "pa-1" },
        terms: { delivery_terms: "6 semanas", validity_days: 30, payment_terms: "30 días", terms_extra: [] },
      },
      computed: { ...computedFixture("1300000"), lines: [line(1, "1000000", "1000000"), line(2, "150000", "300000")] },
    },
  };
}

function stored(line_no: number, line_kind: string, description: string, unit: string, total: string): QuoteStoredLine {
  return {
    line_no, line_kind, description, product_id: line_no === 1 ? "p-1" : null, quantity: "2.000000", item_type: "equipment",
    cost_currency: "EUR", unit_cost: SECRET_COST, fx_rate: "1050.5", fx_as_of: "2026-10-01", fx_source: "bde",
    margin_mode: "markup", margin_pct: "0.4321", unit_price_qc: unit, line_total_qc: total, is_principal: line_no === 1,
    allocated_to_line_no: line_kind === "logistics" ? 1 : null, expense_method: line_kind === "logistics" ? "profile" : null,
    supplier_organization_id: SUPPLIER, cost_observation_id: null, line_note_es: line_no === 1 ? "Incluye instalación." : null,
  };
}

function approved(): QuoteWorkspace {
  const ws = workspaceFixture({
    status: "approved",
    label: "01272-26",
    subtotal: "2000000",
    tax_total: "380000",
    grand_total: "2380000",
    approved_at: "2026-10-16T13:00:00Z",
    valid_until: "2026-11-15",
    draft: undefined,
    computed: undefined,
    lines: [
      stored(1, "item", "Centrífuga de prueba", "1000000", "2000000"),
      stored(2, "logistics", "Flete aéreo internacional", "0", "0"),
    ],
    party_snapshot: {
      institution: { id: "i-1", name: "Universidad de Prueba", rut: "76.000.000-0" },
      recipient: { participant_id: "pa-1", name: "Ana Prueba", email: "ana@ejemplo.cl" },
      terms: {
        delivery_terms: "8 semanas", payment_terms: "Contado", warranty_terms_es: "12 meses", delivery_place_es: "Laboratorio del cliente",
        installation_terms_es: "Incluida", client_notes_es: "Precios en pesos chilenos.", validity_days: 30, terms_extra: [],
      },
      quote_number: "01272-26", revision_label: "01272-26", approved_by: "Rafa Prueba", approved_at: "2026-10-16", valid_until: "2026-11-15",
    },
  });
  return { ...ws, quote: { ...ws.quote, quote_number: "01272-26" } };
}

const index = (): Promise<CatalogIndex> =>
  Promise.resolve({ version: "v1", suppliers: [], items: [{ id: "p-1", model_number: "CF-100", name: "Centrífuga" } as never] });

function show(ws: QuoteWorkspace) {
  const q = printableQuote(ws, null, new Date("2026-10-17T12:00:00Z"))!;
  render(<QuotePrintView quote={q} onClose={vi.fn()} loadIndex={index} />);
  return screen.getByRole("dialog");
}

afterEach(() => {
  cleanup();
  document.title = "";
});

/** Nothing internal: no cost, recargo, margin, supplier, exchange rate or import expense. */
function expectClientSafe(el: HTMLElement) {
  const t = el.textContent ?? "";
  expect(t).not.toMatch(/987[.,]?654|111[.,]?222|444[.,]?555|666[.,]?777|43[,.]2|30[,.]1|1[.,]?050/);
  expect(t).not.toMatch(/costo|margen|recargo|proveedor|tipo de cambio|aviso|flete|desaduanaje|labdelivery/i);
  expect(el.innerHTML).not.toContain(SUPPLIER);
}

describe("Vista previa PDF — an approved revision", () => {
  it("prints the number, client, lines, totals, conditions and who approved it", async () => {
    const dialog = show(approved());
    const sheet = within(dialog);
    expect(sheet.getByText("CN01272")).toBeInTheDocument();
    expect(sheet.getByText("16 de octubre de 2026")).toBeInTheDocument();
    expect(sheet.getByText("Válida hasta el 15 de noviembre de 2026")).toBeInTheDocument();
    expect(sheet.getByText("Universidad de Prueba")).toBeInTheDocument();
    expect(sheet.getByText("RUT 76.000.000-0")).toBeInTheDocument();
    expect(sheet.getByText("Ana Prueba")).toBeInTheDocument();
    expect(sheet.getByText("ana@ejemplo.cl")).toBeInTheDocument();
    const rows = within(sheet.getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(2); // header + one item
    expect(rows[1]).toHaveTextContent("Centrífuga de prueba");
    expect(rows[1]).toHaveTextContent("Incluye instalación.");
    expect(rows[1]).toHaveTextContent(/\b2\b/);
    expect(rows[1]).toHaveTextContent("$ 1.000.000");
    expect(rows[1]).toHaveTextContent("$ 2.000.000");
    expect(await sheet.findByText("CF-100")).toBeInTheDocument();
    const totals = within(sheet.getByLabelText("Totales"));
    expect(totals.getByText("Neto").nextSibling).toHaveTextContent("$ 2.000.000");
    expect(totals.getByText("IVA 19 %").nextSibling).toHaveTextContent("$ 380.000");
    expect(totals.getByText("Total CLP").nextSibling).toHaveTextContent("$ 2.380.000");
    const terms = within(sheet.getByLabelText("Condiciones"));
    for (const [label, value] of [
      ["Plazo de entrega", "8 semanas"],
      ["Validez", "30 días"],
      ["Forma de pago", "Contado"],
      ["Garantía", "12 meses"],
      ["Lugar de entrega", "Laboratorio del cliente"],
      ["Instalación", "Incluida"],
    ]) {
      expect(terms.getByText(label).nextSibling).toHaveTextContent(value);
    }
    expect(sheet.getByText("Precios en pesos chilenos.")).toBeInTheDocument();
    // The paper is signed by the issuer; who approved stays in the dashboard.
    expect(sheet.queryByText("Aprobada por Rafa Prueba")).toBeNull();
    expect(sheet.getByText("Atte.,")).toBeInTheDocument();
    expect(sheet.getByText("TATIANA VIVANCO")).toBeInTheDocument();
    expect(sheet.getByText("BIOQUÍMICO")).toBeInTheDocument();
    expect(sheet.getByText("Horario: 09:00-18:00")).toBeInTheDocument();
    expect(sheet.getByText("contacto@origenlab.cl")).toBeInTheDocument();
    expect(sheet.getByText("www.origenlab.cl")).toBeInTheDocument();
    expect(sheet.queryByText(/BORRADOR/)).not.toBeInTheDocument();
    expect(sheet.getByRole("button", { name: "Imprimir / Guardar PDF" })).toBeEnabled();
    expect(document.title).toBe("Cotización CN01272 — Universidad de Prueba");
  });

  it("never prints the logistics lines, costs, margins or suppliers", () => {
    const dialog = show(approved());
    expect(dialog.textContent).not.toContain("Flete aéreo internacional");
    expectClientSafe(dialog);
  });

  it("prints nothing for a missing RUT and only the conditions that are set", () => {
    const ws = approved();
    const snap = ws.current!.party_snapshot as { institution: { rut: string | null }; terms: Record<string, unknown> };
    snap.institution.rut = null;
    snap.terms.warranty_terms_es = null;
    const sheet = within(show(ws));
    expect(sheet.queryByText(/RUT/)).not.toBeInTheDocument();
    expect(sheet.queryByText("Garantía")).not.toBeInTheDocument();
  });
});

describe("Vista previa PDF — a draft", () => {
  it("prints the live draft marked BORRADOR, with the API's prices and totals only", () => {
    const dialog = show(pricedDraft());
    const sheet = within(dialog);
    expect(sheet.getByText("BORRADOR · sin aprobar")).toBeInTheDocument();
    expect(sheet.getByText("BORRADOR")).toBeInTheDocument(); // the watermark
    expect(sheet.queryByText(/Aprobada por/)).not.toBeInTheDocument();
    expect(sheet.getByText("Ana Prueba")).toBeInTheDocument();
    const rows = within(sheet.getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("Equipo de prueba A");
    expect(rows[1]).toHaveTextContent("Con manual en español.");
    expect(rows[2]).toHaveTextContent("$ 300.000");
    const totals = within(sheet.getByLabelText("Totales"));
    expect(totals.getByText("Neto").nextSibling).toHaveTextContent("$ 1.300.000");
    expect(totals.getByText("Total CLP").nextSibling).toHaveTextContent("$ 119.000");
    expect(sheet.getByText("6 semanas")).toBeInTheDocument();
    expect(document.title).toBe("Cotización CN00042 — Institución de prueba (borrador)");
    expectClientSafe(dialog);
  });

  it("is printable for a draft, an approved and a sent revision, not a void one", () => {
    expect(isPrintable(pricedDraft())).toBe(true);
    expect(isPrintable(approved())).toBe(true);
    expect(isPrintable(workspaceFixture({ status: "sent", draft: undefined }))).toBe(true);
    expect(isPrintable(workspaceFixture({ status: "void", draft: undefined }))).toBe(false);
  });
});
