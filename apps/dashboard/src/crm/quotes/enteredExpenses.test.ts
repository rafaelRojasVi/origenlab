/** «Gastos ingresados»: the issued revision's expense lines as typed. Every value is invented. */
import { describe, expect, it } from "vitest";
import { enteredExpenses, roundedPesos } from "./enteredExpenses";
import type { QuoteStoredLine } from "./quoteApi";
import { storedLineFixture as storedLine } from "./quoteFixtures";

const expense = (line_no: number, description: string, unit_cost: string, over: Partial<QuoteStoredLine> = {}) =>
  storedLine({
    line_no, line_kind: "logistics", description, unit_cost, item_type: null, margin_mode: "none", margin_pct: null,
    unit_price_qc: "0", line_total_qc: "0", is_principal: false, allocated_to_line_no: 1, expense_method: "manual", ...over,
  });

describe("enteredExpenses", () => {
  it("lists only the expense lines, in line order, with the type of a «Tipo — Detalle» label apart", () => {
    const out = enteredExpenses([
      expense(3, "Agente de aduana — Agencia Ejemplo", "120000"),
      storedLine({ line_no: 1 }),
      expense(2, "Flete", "300000", { expense_method: "profile" }),
    ]);
    expect(out.rows.map((r) => [r.lineNo, r.text, r.tag, r.method])).toEqual([
      [2, "Flete", null, "Perfil"],
      [3, "Agencia Ejemplo", "Agente de aduana", "Manual"],
    ]);
    expect(out.totalClp).toBe("420000");
  });

  it("keeps an old free label as typed and names an old DHL calculation", () => {
    const out = enteredExpenses([
      expense(2, "papeles de prueba", "15000"),
      expense(3, "Courier ejemplo", "80000", { expense_method: "dhl_calc" }),
    ]);
    expect(out.rows.map((r) => [r.text, r.tag, r.method])).toEqual([
      ["papeles de prueba", null, "Manual"],
      ["Courier ejemplo", null, "DHL calculado"],
    ]);
  });

  it("puts a foreign expense in pesos at its stored rate, rounded half up as the engine does", () => {
    const out = enteredExpenses([
      expense(2, "Flete — Aéreo", "150.5", { cost_currency: "USD", fx_rate: "950.1" }),
      expense(3, "Otro", "10", { cost_currency: "EUR", fx_rate: "1000.05" }),
    ]);
    // 150.5 × 950.1 = 142990.05 → 142990; 10 × 1000.05 = 10000.5 → 10001.
    expect(out.rows.map((r) => [r.currency, r.amount, r.amountClp])).toEqual([
      ["USD", "150.5", "142990"],
      ["EUR", "10", "10001"],
    ]);
    expect(out.totalClp).toBe("152991");
  });

  it("prices a currency at the first line's rate for it, as the engine's input does", () => {
    const out = enteredExpenses([
      storedLine({ line_no: 1, cost_currency: "USD", unit_cost: "100", fx_rate: "900" }),
      expense(2, "Flete", "10", { cost_currency: "USD", fx_rate: "999" }),
    ]);
    expect(out.rows[0].amountClp).toBe("9000");
  });

  it("has no total when a rate is missing, and none at all without expense lines", () => {
    expect(enteredExpenses([expense(2, "Flete", "10", { cost_currency: "USD", fx_rate: null })]).totalClp).toBeNull();
    expect(enteredExpenses([storedLine({})])).toEqual({ rows: [], totalClp: "0" });
    expect(enteredExpenses(undefined).rows).toEqual([]);
  });

  it("counts fee lines too, and a missing amount as zero", () => {
    const out = enteredExpenses([expense(2, "Otro — Trámite", "5000", { line_kind: "fee" }), expense(3, "Flete", null as unknown as string)]);
    expect(out.rows.map((r) => r.amountClp)).toEqual(["5000", "0"]);
    expect(out.totalClp).toBe("5000");
  });
});

describe("roundedPesos", () => {
  it("multiplies decimal strings exactly and rounds half away from zero", () => {
    expect(roundedPesos("0.1", "0.2")).toBe("0");
    expect(roundedPesos("2.5", "1")).toBe("3");
    expect(roundedPesos("-2.5", "1")).toBe("-3");
    expect(roundedPesos("1234567.891", "1050.123456")).toBe("1296448700");
    expect(roundedPesos("abc", "1")).toBeNull();
  });
});
