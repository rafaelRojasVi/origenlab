/**
 * A manual expense's «Tipo»: chosen in the editor and stored in its label («Flete — DHL envío»),
 * so «Economía de la cotización» splits it by what the operator chose. An old free label shows the
 * type the API's guesser reads in it (the same vectors the API's test checks). Invented labels.
 */
import { describe, expect, it } from "vitest";
import { encodeExpenseLabel, guessExpenseKind, parseExpenseLabel } from "./expenseKind";
import vectors from "./expenseKindVectors.json";

describe("expense kind", () => {
  it("encodes the chosen type and an optional detail", () => {
    expect(encodeExpenseLabel("freight", "DHL envío")).toBe("Flete — DHL envío");
    expect(encodeExpenseLabel("agent", "  Agente Ficticio ")).toBe("Agente de aduana — Agente Ficticio");
    expect(encodeExpenseLabel("customs", "")).toBe("Derechos y aduana");
    expect(encodeExpenseLabel("other", "   ")).toBe("Otro");
  });

  it("parses a typed label back to its type and detail", () => {
    expect(parseExpenseLabel("Flete — DHL envío")).toEqual({ kind: "freight", detail: "DHL envío", typed: true });
    expect(parseExpenseLabel("Transporte local")).toEqual({ kind: "transport", detail: "", typed: true });
    expect(parseExpenseLabel("Otro — flete de embalaje")).toEqual({ kind: "other", detail: "flete de embalaje", typed: true });
  });

  it("reads an old free label with the guesser: the label becomes the detail", () => {
    expect(parseExpenseLabel("DHL Envio")).toEqual({ kind: "freight", detail: "DHL Envio", typed: false });
    expect(parseExpenseLabel("Persona Ficticia")).toEqual({ kind: "other", detail: "Persona Ficticia", typed: false });
  });

  it("a new row has no type yet", () => {
    expect(parseExpenseLabel("")).toEqual({ kind: null, detail: "", typed: false });
  });

  it("guesses as the API does (`quoting.expenses.expense_category`)", () => {
    for (const [label, kind] of vectors as [string, string][]) expect([label, guessExpenseKind(label)]).toEqual([label, kind]);
  });

  it("cuts a long old detail with «…» so the label still fits 120 characters", () => {
    const old = "Detalle ficticio muy largo ".repeat(5).trim(); // 134 characters
    const label = encodeExpenseLabel("agent", old);
    expect(label).toHaveLength(120);
    expect(label.startsWith("Agente de aduana — Detalle ficticio")).toBe(true);
    expect(label.endsWith("…")).toBe(true);
    const fits = "x".repeat(120 - "Flete — ".length);
    expect(encodeExpenseLabel("freight", fits)).toBe(`Flete — ${fits}`);
  });
});
