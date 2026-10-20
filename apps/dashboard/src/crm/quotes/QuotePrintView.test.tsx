/**
 * «Vista previa PDF»: the client's document. Every value is invented (the repository is public).
 * The internal values below are distinctive numbers so a leak into the DOM would be seen.
 */
import "@testing-library/jest-dom";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CatalogIndex } from "../catalog/catalogIndex";
import { isPrintable, printableQuote } from "./printableQuote";
import { computedFixture, economicsFixture, lineEconomics, workspaceFixture } from "./quoteFixtures";
import type { ComputedLine, QuoteStoredLine, QuoteWorkspace } from "./quoteApi";
import { IssuedView } from "./IssuedView";
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
    expect(sheet.getByText("Atentamente,")).toBeInTheDocument();
    expect(sheet.getByText("Tatiana Vivanco")).toBeInTheDocument();
    expect(sheet.getByText("Bioquímico · OrigenLab")).toBeInTheDocument();
    expect(sheet.getByText("www.origenlab.cl · Horario: 09:00-18:00")).toBeInTheDocument();
    expect(sheet.getByText("contacto@origenlab.cl · +56 9 6256 7816")).toBeInTheDocument();
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

describe("Vista previa PDF — «Economía de la cotización» stays internal", () => {
  // Distinctive amounts: a leak of any of them into the document would be seen.
  const economics = economicsFixture({
    sale_net_clp: "1300000", product_cost_clp: "713713", import_costs_clp: "424242",
    import_costs_split: { freight: "313131", agent: "55555", customs: "44444", transport: "11112", other: "0" },
    gross_margin_clp: "162045", gross_margin_pct: "0.1246",
  });

  it("printableQuote copies none of it, for a draft or an issued revision", () => {
    const draft = pricedDraft();
    draft.current = { ...draft.current!, economics, computed: { ...draft.current!.computed!, economics } };
    const issued = approved();
    issued.current = { ...issued.current!, economics };
    for (const ws of [draft, issued]) {
      const q = printableQuote(ws, null, new Date("2026-10-17T12:00:00Z"))!;
      const text = JSON.stringify(q);
      expect(text).not.toMatch(/economics|713713|424242|313131|55555|44444|11112|162045|0\.1246/);
    }
  });

  it("the print view shows none of it", () => {
    const ws = approved();
    ws.current = { ...ws.current!, economics };
    const dialog = show(ws);
    expectClientSafe(dialog);
    const t = dialog.textContent ?? "";
    expect(t).not.toMatch(/Economía|713[.,]?713|424[.,]?242|313[.,]?131|162[.,]?045|12,5 %|gastos de importación/i);
  });
  it("the cost strip under each line (`line_economics`) is copied nowhere and never printed", () => {
    const strip = [lineEconomics({ line_no: 1, cost_clp: "8181818", gain_clp: "3737373", product_cost_clp: "5151515", expenses_clp: "3030303" })];
    const draft = pricedDraft();
    draft.current = { ...draft.current!, computed: { ...draft.current!.computed!, line_economics: strip } };
    const issued = approved();
    issued.current = { ...issued.current!, line_economics: strip };
    for (const ws of [draft, issued]) {
      const q = printableQuote(ws, null, new Date("2026-10-19T12:00:00Z"))!;
      expect(JSON.stringify(q)).not.toMatch(/line_economics|8181818|3737373|5151515|3030303/);
      cleanup();
      const dialog = show(ws);
      expect(within(dialog).queryByTestId("cost-strip")).toBeNull();
      expect(dialog.textContent ?? "").not.toMatch(/Me cuesta|gano|Le cobro|8[.,]?181[.,]?818|3[.,]?737[.,]?373/);
    }
  });

  it("«Gastos ingresados» (the expenses as typed, their method and pesos) is copied nowhere and never printed", () => {
    const issued = approved();
    issued.current = {
      ...issued.current!,
      lines: [
        ...issued.current!.lines!,
        { ...stored(3, "logistics", "Agente de aduana — Agencia Secreta", "0", "0"), unit_cost: "6464646", cost_currency: "CLP", fx_rate: null, expense_method: "manual" },
        { ...stored(4, "logistics", "papeles secretos", "0", "0"), unit_cost: "7272727", cost_currency: "CLP", fx_rate: null, expense_method: "dhl_calc" },
      ],
    };
    const q = printableQuote(issued, null, new Date("2026-10-19T12:00:00Z"))!;
    expect(JSON.stringify(q)).not.toMatch(/Agencia Secreta|papeles secretos|6464646|7272727|dhl_calc|logistics/);
    const dialog = show(issued);
    expect(dialog.textContent ?? "").not.toMatch(/Gastos ingresados|Agencia Secreta|papeles secretos|6[.,]?464[.,]?646|7[.,]?272[.,]?727|DHL calculado|Perfil|Manual/);
  });

  it("prints «Datos del cliente» frozen at approval, and an address-only name once", () => {
    const ws = approved();
    const snap = ws.current!.party_snapshot as Record<string, unknown>;
    ws.current = { ...ws.current!, party_snapshot: {
      ...snap,
      recipient: { participant_id: "pa-1", name: "ana@ejemplo.cl", email: "ana@ejemplo.cl" },
      client: { contact_role: "Jefa de laboratorio", department: "Calidad", phone: "+56 9 1111 2222", address: "Calle Falsa 123", city: "Talca" },
    } };
    const el = show(ws);
    const t = el.textContent ?? "";
    expect(t).toContain("Jefa de laboratorio · Calidad");
    expect(t).toContain("+56 9 1111 2222");
    expect(t).toContain("Calle Falsa 123");
    expect(t).toContain("Talca");
    expect(t.split("ana@ejemplo.cl").length - 1).toBe(1);
  });

  it("prints a draft's typed details over the CRM's", () => {
    const base = workspaceFixture();
    const draft = { ...base.current!.draft!, recipient: { participant_id: "pa-1" },
      client: { contact_name: "Ana María Prueba", institution_name: "Universidad de Prueba S.A.", rut: "76.111.111-1" } };
    const ws = { ...base, current: { ...base.current!, draft } };
    const q = printableQuote(ws, null, new Date("2026-10-17T12:00:00Z"))!;
    expect(q.institution).toEqual({ name: "Universidad de Prueba S.A.", rut: "76.111.111-1" });
    if (q.recipient) expect(q.recipient.name).toBe("Ana María Prueba");
  });
});

describe("Vista previa PDF — a tender's offer", () => {
  const tenderOf = (bidder: { legal_name: string; rut: string } | null) => ({ code: "1234-56-LE26", bidder });
  const BIDDER = { legal_name: "Oferente Ejemplo", rut: "11.111.111-1" };

  /** The approved fixture with two item lines (neto 1.000.005 and 999.995: IVA 190.000,95 and 189.999,05 round half up). */
  function tenderQuote(bidder: typeof BIDDER | null): QuoteWorkspace {
    const ws = approved();
    return {
      ...ws,
      tender: tenderOf(bidder),
      current: {
        ...ws.current!,
        subtotal: "2000000", tax_total: "380000", grand_total: "2380000",
        lines: [stored(1, "item", "Centrífuga de prueba", "1000005", "1000005"), stored(2, "item", "Balanza de prueba", "999995", "999995")],
      },
    };
  }

  it("prints the bidder, the tender code and the IVA and total of every line", () => {
    const dialog = show(tenderQuote(BIDDER));
    const sheet = within(dialog);
    expect(sheet.getByText("OrigenLab")).toBeInTheDocument();
    expect(sheet.getByText("Oferente: Oferente Ejemplo · RUT 11.111.111-1")).toBeInTheDocument();
    expect(sheet.getByText("Licitación 1234-56-LE26")).toBeInTheDocument();
    const table = within(sheet.getByRole("table"));
    for (const h of ["Neto", "IVA 19 %", "Total"]) expect(table.getByRole("columnheader", { name: h })).toBeInTheDocument();
    const rows = table.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("$ 1.000.005");
    expect(rows[1]).toHaveTextContent("$ 190.001"); // 190.000,95 half up
    expect(rows[1]).toHaveTextContent("$ 1.190.006");
    expect(rows[2]).toHaveTextContent("$ 189.999"); // 189.999,05
    expect(rows[2]).toHaveTextContent("$ 1.189.994");
    // The totals are the revision's own.
    const totals = within(sheet.getByLabelText("Totales"));
    expect(totals.getByText("Neto").nextSibling).toHaveTextContent("$ 2.000.000");
    expect(totals.getByText("IVA 19 %").nextSibling).toHaveTextContent("$ 380.000");
    expect(totals.getByText("Total CLP").nextSibling).toHaveTextContent("$ 2.380.000");
    expectClientSafe(dialog);
  });

  it("a tender without a bidder, or a quote without a tender, prints as any quote", () => {
    const plain = show(approved()).innerHTML;
    cleanup();
    expect(show(tenderQuote(null)).textContent).not.toMatch(/Oferente|Licitación/);
    cleanup();
    expect(show({ ...approved(), tender: null }).innerHTML).toBe(plain);
  });

  it("an offer approved with no recipient prints and shows no «Atención» or «Para» line", () => {
    const ws = tenderQuote(BIDDER);
    ws.current = { ...ws.current!, party_snapshot: { ...ws.current!.party_snapshot!, recipient: null } };
    const sheet = show(ws);
    expect(within(sheet).getByText("Universidad de Prueba")).toBeInTheDocument();
    expect(sheet.textContent).not.toMatch(/Atención/);
    cleanup();
    render(<IssuedView workspace={ws} />);
    expect(screen.getByText("Universidad de Prueba")).toBeInTheDocument();
    expect(screen.queryByText("Para")).toBeNull();
  });

  it("a non-tender quote prints exactly as before (snapshot)", () => {
    const el = show(approved());
    expect(el.querySelector("article")!.innerHTML).toMatchSnapshot();
    expect(el.textContent).not.toMatch(/Oferente|Licitación/);
  });

  it("per-line IVA and total add up to the stored totals when naive rounding is off by one", () => {
    const ws = tenderQuote(BIDDER);
    // 3 x 1.003 neto: 190,57 each rounds to 191 (573); the stored IVA of 3.009 is 572.
    ws.current = {
      ...ws.current!,
      subtotal: "3009", tax_total: "572", grand_total: "3581",
      lines: [1, 2, 3].map((n) => stored(n, "item", `Equipo ${n}`, "1003", "1003")),
    };
    const q = printableQuote(ws, null, new Date("2026-10-17T12:00:00Z"))!;
    expect(q.lines.map((l) => l.iva)).toEqual(["191", "191", "190"]);
    expect(q.lines.reduce((a, l) => a + Number(l.iva), 0)).toBe(572);
    expect(q.lines.reduce((a, l) => a + Number(l.grossTotal), 0)).toBe(3581);
  });

  it("keeps the lines as computed when the stored neto is not the sum of the lines", () => {
    const ws = tenderQuote(BIDDER);
    const q = printableQuote(ws, null, new Date("2026-10-17T12:00:00Z"))!; // lines sum 2.000.000, naive IVA 380.000
    expect(q.lines.map((l) => l.iva)).toEqual(["190001", "189999"]);
    ws.current = { ...ws.current!, subtotal: "2000100", tax_total: "380019" };
    expect(printableQuote(ws, null, new Date("2026-10-17T12:00:00Z"))!.lines.map((l) => l.iva)).toEqual(["190001", "189999"]);
  });
});
