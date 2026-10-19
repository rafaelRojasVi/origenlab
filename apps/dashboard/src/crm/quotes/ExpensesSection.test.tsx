/**
 * «Gastos de importación»: how the expenses are worked out (Perfil / Manual), one card per expense
 * the API computed, and the manual ones edited by hand — each with its «Tipo» chosen, stored in its
 * label («Flete — DHL envío») so the economics split never guesses. «DHL calculado» is no longer
 * offered; a draft saved with it keeps its lines and says so. Every value is invented;
 * the amounts on screen are the API's `computed`, only formatted.
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { CatalogIndex } from "../catalog/catalogIndex";
import { draftProblems } from "./draftEditor";
import { ExpensesSection } from "./ExpensesSection";
import type { Computed, QuoteDraft } from "./quoteApi";
import { computedFixture, draftFixture } from "./quoteFixtures";

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
  ],
};

function draft(over: Partial<QuoteDraft> = {}): QuoteDraft {
  const d = draftFixture();
  return { ...d, items: d.items.map((i, n) => ({ ...i, uid: `u${n + 1}` })), ...over };
}

function priced(over: Partial<Computed> = {}): Computed {
  return {
    ...computedFixture(),
    expenses: [
      { line_no: 3, label: "Flete", amount: "620000", currency: "CLP", method: "profile", allocated_to_line_no: 1, calc_inputs: { parameter: "profile_freight_clp" } },
      { line_no: 4, label: "Agente de aduana", amount: "180000", currency: "CLP", method: "profile", allocated_to_line_no: 1 },
    ],
    ...over,
  };
}

async function setup({
  initial = draft(),
  computed = priced(),
  disabled = false,
  stale = false,
}: { initial?: QuoteDraft; computed?: Computed | null; disabled?: boolean; stale?: boolean } = {}) {
  const latest = { draft: initial };
  const edit = vi.fn();
  function Harness() {
    const [d, setD] = useState(initial);
    latest.draft = d;
    return (
      <ExpensesSection
        draft={d}
        computed={computed}
        computedKeys={["u1", "u2"]}
        stale={stale}
        invalid={draftProblems(d)}
        disabled={disabled}
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

const method = (name: string) => within(screen.getByRole("group", { name: "Cómo calcular los gastos" })).getByRole("button", { name });

describe("ExpensesSection — derived expenses", () => {
  it("shows one card per computed expense, with its origin, and says where they go", async () => {
    await setup();
    expect(screen.getByRole("heading", { level: 2, name: "Gastos de importación" })).toBeInTheDocument();
    expect(method("Perfil")).toHaveAttribute("aria-pressed", "true");
    const cards = screen.getAllByRole("listitem");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("Flete");
    expect(cards[0]).toHaveTextContent("$ 620.000");
    expect(cards[0]).toHaveTextContent("perfil Plantilla");
    expect(screen.getByText("Se suman al equipo principal (AG-100). Al cliente no se muestran como línea.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("says the expenses are spread by cost when the allocation is proportional", async () => {
    await setup({ initial: draft({ allocation_mode: "proportional_cost" }) });
    expect(screen.getByText("Se reparten según el costo de cada línea.")).toBeInTheDocument();
  });

  it("offers only Perfil and Manual", async () => {
    await setup();
    const group = screen.getByRole("group", { name: "Cómo calcular los gastos" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Perfil", "Manual"]);
    expect(screen.queryByText(/DHL calculado/)).not.toBeInTheDocument();
  });

  it("switches the method on click, with no manual expenses", async () => {
    const { latest, edit } = await setup({ initial: draft({ expense_method: "manual", manual_expenses: [] }), computed: priced({ expenses: [] }) });
    fireEvent.click(method("Perfil"));
    expect(edit).toHaveBeenCalledTimes(1);
    expect(latest.draft).toMatchObject({ expense_method: "profile", manual_expenses: [] });
    expect(method("Perfil")).toHaveAttribute("aria-pressed", "true");
  });

  describe("a draft saved with «DHL calculado»", () => {
    const dhl = () =>
      setup({
        initial: draft({ expense_method: "dhl_calc" }),
        computed: priced({
          expenses: [
            { line_no: 3, label: "Flete DHL (Proveedor Uno)", amount: "410000", currency: "CLP", method: "dhl_calc", allocated_to_line_no: 1, calc_inputs: { chargeable_kg: "12.5", zone: "3" } },
            { line_no: 4, label: "Derechos", amount: "90000", currency: "CLP", method: "dhl_calc", allocated_to_line_no: 1, calc_inputs: { duty_rate: "0.06", origin_country: "DE" } },
          ],
        }),
      });

    it("opens unchanged, with its lines and a note, and neither method pressed", async () => {
      const { latest, edit } = await dhl();
      expect(edit).not.toHaveBeenCalled();
      expect(latest.draft.expense_method).toBe("dhl_calc");
      expect(screen.getByRole("note")).toHaveTextContent(
        "Este borrador usa «DHL calculado», que ya no se ofrece. Sus gastos siguen como estaban; para cambiarlos, elige Perfil o «Editar montos».",
      );
      const cards = screen.getAllByRole("listitem");
      expect(cards[0]).toHaveTextContent("DHL · 12,5 kg · zona 3");
      expect(cards[1]).toHaveTextContent("DHL · 6,0 % · origen DE");
      expect(method("Perfil")).toHaveAttribute("aria-pressed", "false");
      expect(method("Manual")).toHaveAttribute("aria-pressed", "false");
    });

    it("«Editar montos» turns it into Manual with the same lines, typed by what they are", async () => {
      const { latest } = await dhl();
      fireEvent.click(screen.getByRole("button", { name: "Editar montos" }));
      expect(latest.draft.expense_method).toBe("manual");
      expect(latest.draft.manual_expenses.map(({ label, amount }) => [label, amount])).toEqual([
        ["Flete DHL (Proveedor Uno)", "410000"],
        ["Derechos", "90000"],
      ]);
      expect(screen.getByLabelText("Tipo del gasto 1")).toHaveValue("freight");
      expect(screen.getByLabelText("Tipo del gasto 2")).toHaveValue("customs");
      expect(screen.queryByRole("note")).not.toBeInTheDocument();
    });

    it("still says when it fell back to the profile", async () => {
      await setup({
        initial: draft({ expense_method: "dhl_calc" }),
        computed: priced({ warnings: ["dhl_fallback:dhl_weight_missing", "profile_parameter_missing:profile_customs_clp"] }),
      });
      expect(screen.getByText("Faltan pesos o datos: se usó el perfil de la Plantilla.")).toBeInTheDocument();
      expect(screen.getByText("Falta el parámetro profile_customs_clp del perfil.")).toBeInTheDocument();
    });
  });

  it("copies nothing while the amounts shown are for an older draft", async () => {
    const { latest } = await setup({ stale: true });
    const button = screen.getByRole("button", { name: "Editar montos" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Espera el cálculo");
    fireEvent.click(method("Manual"));
    expect(latest.draft).toMatchObject({ expense_method: "manual", manual_expenses: [] });
  });

  it("«Editar montos» copies the computed expenses into manual ones (B5)", async () => {
    const { latest } = await setup();
    fireEvent.click(screen.getByRole("button", { name: "Editar montos" }));
    expect(latest.draft.expense_method).toBe("manual");
    expect(latest.draft.manual_expenses).toEqual([
      { label: "Flete", amount: "620000", currency: "CLP" },
      { label: "Agente de aduana", amount: "180000", currency: "CLP" },
    ]);
    expect(screen.getByLabelText("Tipo del gasto 1")).toHaveValue("freight");
    expect(screen.getByLabelText("Detalle del gasto 1")).toHaveValue("");
    expect(screen.getByLabelText("Tipo del gasto 2")).toHaveValue("agent");
  });
});

describe("ExpensesSection — manual expenses", () => {
  const manual = () =>
    draft({
      expense_method: "manual",
      manual_expenses: [
        { uid: "e1", label: "Flete aéreo", amount: "300000", currency: "CLP" },
        { uid: "e2", label: "Seguro", amount: "50", currency: "USD" },
      ],
    });

  it("each expense is a chosen type, an optional detail and an amount, stored as «Tipo — Detalle»", async () => {
    const { latest } = await setup({ initial: manual(), computed: priced({ expenses: [] }) });
    expect(screen.queryByRole("button", { name: "Editar montos" })).not.toBeInTheDocument();
    const types = within(screen.getByLabelText("Tipo del gasto 1")).getAllByRole("option").map((o) => o.textContent);
    expect(types).toEqual(["Elige el tipo", "Flete", "Agente de aduana", "Derechos y aduana", "Transporte local", "Otro"]);
    fireEvent.change(screen.getByLabelText("Detalle del gasto 1"), { target: { value: "DHL envío " } });
    // A trailing space while typing stays in the box, not in what is stored.
    expect(screen.getByLabelText("Detalle del gasto 1")).toHaveValue("DHL envío ");
    fireEvent.change(screen.getByLabelText("Tipo del gasto 2"), { target: { value: "agent" } });
    fireEvent.change(screen.getByLabelText("Detalle del gasto 2"), { target: { value: "Agente Ficticio" } });
    const amount = screen.getByLabelText("Monto del gasto 1");
    fireEvent.focus(amount);
    fireEvent.change(amount, { target: { value: "1.250.000" } });
    fireEvent.change(screen.getByLabelText("Moneda del gasto 2"), { target: { value: "EUR" } });
    expect(latest.draft.manual_expenses).toEqual([
      { uid: "e1", label: "Flete — DHL envío", amount: "1250000", currency: "CLP" },
      { uid: "e2", label: "Agente de aduana — Agente Ficticio", amount: "50", currency: "EUR" },
    ]);
  });

  it("an old free label shows the type read in it and the label as its detail, unchanged until edited", async () => {
    const { latest, edit } = await setup({
      initial: draft({
        expense_method: "manual",
        manual_expenses: [
          { uid: "e1", label: "DHL Envio", amount: "350000", currency: "CLP" },
          { uid: "e2", label: "Persona Ficticia", amount: "80000", currency: "CLP" },
        ],
      }),
      computed: priced({ expenses: [] }),
    });
    expect(screen.getByLabelText("Tipo del gasto 1")).toHaveValue("freight");
    expect(screen.getByLabelText("Detalle del gasto 1")).toHaveValue("DHL Envio");
    expect(screen.getByLabelText("Tipo del gasto 2")).toHaveValue("other");
    expect(edit).not.toHaveBeenCalled();
    // Choosing the type is what fixes it: an agent, not «Otros gastos».
    fireEvent.change(screen.getByLabelText("Tipo del gasto 2"), { target: { value: "agent" } });
    expect(latest.draft.manual_expenses[1].label).toBe("Agente de aduana — Persona Ficticia");
    expect(latest.draft.manual_expenses[0].label).toBe("DHL Envio");
  });

  it("adds and removes an expense; a new row asks for its type first, and only once left", async () => {
    const { latest } = await setup({ initial: manual(), computed: priced({ expenses: [] }) });
    fireEvent.click(screen.getByRole("button", { name: "+ Agregar gasto" }));
    expect(latest.draft.manual_expenses).toHaveLength(3);
    const third = screen.getByLabelText("Tipo del gasto 3").closest("li") as HTMLElement;
    expect(screen.getByLabelText("Tipo del gasto 3")).toHaveValue("");
    expect(screen.getByLabelText("Detalle del gasto 3")).toBeDisabled();
    expect(within(third).queryByText(/Elige/, { selector: "p" })).not.toBeInTheDocument();
    expect(draftProblems(latest.draft).length).toBeGreaterThan(0);
    fireEvent.blur(screen.getByLabelText("Tipo del gasto 3"));
    expect(within(third).getByText("Elige el tipo de gasto.")).toBeInTheDocument();
    expect(within(third).queryByText("Escribe el monto del gasto.")).not.toBeInTheDocument();
    const amount = screen.getByLabelText("Monto del gasto 3");
    fireEvent.focus(amount);
    fireEvent.blur(amount);
    expect(within(third).getByText("Escribe el monto del gasto.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Tipo del gasto 3"), { target: { value: "transport" } });
    expect(latest.draft.manual_expenses[2].label).toBe("Transporte local");
    expect(screen.getByLabelText("Detalle del gasto 3")).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Quitar el gasto 1" }));
    expect(latest.draft.manual_expenses.map((e) => e.label)).toEqual(["Seguro", "Transporte local"]);
  });

  it("edits an amount without the API's trailing zeros", async () => {
    await setup({
      initial: draft({
        expense_method: "manual",
        manual_expenses: [
          { uid: "e1", label: "Flete", amount: "300000.000000", currency: "CLP" },
          { uid: "e2", label: "Seguro", amount: "150.50", currency: "USD" },
        ],
      }),
      computed: priced({ expenses: [] }),
    });
    const first = screen.getByLabelText("Monto del gasto 1");
    expect(first).toHaveValue("$ 300.000");
    fireEvent.focus(first);
    expect(first).toHaveValue("300000");
    const second = screen.getByLabelText("Monto del gasto 2");
    fireEvent.focus(second);
    expect(second).toHaveValue("150,5");
  });

  it("going back to a derived method asks first, then drops the manual expenses", async () => {
    const { latest } = await setup({ initial: manual(), computed: priced({ expenses: [] }) });
    expect(screen.getByText(/Si vuelves a Perfil, estos montos se descartan/)).toBeInTheDocument();
    fireEvent.click(method("Perfil"));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText("Se borrarán los gastos escritos a mano.")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(latest.draft.expense_method).toBe("manual");
    expect(latest.draft.manual_expenses).toHaveLength(2);
    fireEvent.click(method("Perfil"));
    const again = screen.getByRole("alertdialog");
    fireEvent.click(within(again).getByRole("checkbox"));
    fireEvent.click(within(again).getByRole("button", { name: "Borrar y cambiar" }));
    expect(latest.draft).toMatchObject({ expense_method: "profile", manual_expenses: [] });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("leaves Manual without asking when no expense was written", async () => {
    const { latest } = await setup({ initial: draft({ expense_method: "manual", manual_expenses: [] }), computed: priced({ expenses: [] }) });
    fireEvent.click(method("Perfil"));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(latest.draft.expense_method).toBe("profile");
  });

  it("is read-only when the draft cannot be edited", async () => {
    await setup({ initial: manual(), computed: priced({ expenses: [] }), disabled: true });
    expect(screen.getByLabelText("Tipo del gasto 1")).toBeDisabled();
    expect(screen.getByLabelText("Detalle del gasto 1")).toBeDisabled();
    expect(method("Perfil")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "+ Agregar gasto" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quitar el gasto 1" })).not.toBeInTheDocument();
  });
});
