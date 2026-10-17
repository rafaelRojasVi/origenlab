/**
 * «Rentabilidad»: the period, the summary with its coverage note, the months and the quotes.
 * Every value is invented (the repository is public); every number on screen is the API's.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { EconomicsReport } from "./quoteApi";
import { economicsReportFixture } from "./quoteFixtures";
import { RentabilidadPage, SCOPE_NOTE, coverageNote, presetPeriod } from "./RentabilidadPage";

const session: AuthSessionState = {
  kind: "signed_in",
  method: "google_profile",
  operator: { operatorId: "op-1", email: "ventas@ejemplo.cl", displayName: "Ventas Prueba", role: "sales" },
  caseCommandsEnabled: true,
  crmAuthoringEnabled: true,
};

const NOW = () => new Date("2026-10-17T15:00:00Z");

function setup(report: EconomicsReport = economicsReportFixture()) {
  const load = vi.fn(() => Promise.resolve(report));
  const onOpen = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
      <RentabilidadPage onOpen={onOpen} load={load} now={NOW} />
    </AuthSessionContext.Provider>,
  );
  return { load, onOpen };
}

describe("«Rentabilidad»", () => {
  it("reads this year by default and shows the summary with the coverage note", async () => {
    const { load } = setup();
    expect(load).toHaveBeenCalledWith({ from: "2026-01", to: "2026-12" });
    const summary = within(await screen.findByRole("region", { name: "Resumen del período" }));
    for (const [label, value] of [
      ["Ventas netas", "$ 3.702.000"],
      ["Costo productos", "$ 1.630.000"],
      ["Gastos de importación", "$ 830.000"],
      ["Margen bruto", "$ 742.000"],
    ]) {
      expect(summary.getByText(label).nextSibling).toHaveTextContent(value);
    }
    expect(summary.getByText("28,5 % de la venta con costo completo")).toBeInTheDocument();
    const note = within(summary.getByRole("list", { name: "Cobertura del margen" }));
    expect(note.getByText(/sólo sobre 1 de 2 cotizaciones.*\$ 2\.602\.000 de \$ 3\.702\.000 en ventas \(70,3 %\)/)).toBeInTheDocument();
    expect(note.getByText("1 cotización tiene líneas sin costo y no entra en el margen.")).toBeInTheDocument();
    expect(note.getByText("1 borrador ($ 2.602.000) se lista pero no se suma.")).toBeInTheDocument();
    expect(note.getByText(SCOPE_NOTE)).toBeInTheDocument();
  });

  it("lists the months and the quotes; the margin says its band in words, not only in colour", async () => {
    setup();
    const months = within(await screen.findByRole("region", { name: "Por mes" }));
    const [, oct] = months.getAllByRole("row");
    expect(oct).toHaveTextContent(/octubre de 2026/i);
    expect(oct).toHaveTextContent("sobre 1 de 2");
    const quotes = within(screen.getByRole("region", { name: "Cotizaciones" }));
    expect(quotes.getByText("Objetivo de margen 25–45 % (Preferencias).")).toBeInTheDocument();
    const rows = quotes.getAllByRole("row");
    expect(rows).toHaveLength(4); // header + three quotes
    expect(rows[1]).toHaveTextContent("CN00041");
    expect(rows[1]).toHaveTextContent("Universidad Ejemplo 1");
    expect(rows[1]).toHaveTextContent("$ 2.602.000");
    expect(rows[1]).toHaveTextContent("$ 742.000");
    expect(within(rows[1]).getByText("28,5 %")).toHaveClass("text-good");
    expect(rows[1]).toHaveTextContent("En rango");
    expect(rows[1]).toHaveTextContent("Completa");
    // A line without cost: no margin, never a 0.
    expect(rows[2]).toHaveTextContent("Falta el costo de 1 línea");
    const cells = within(rows[2]).getAllByRole("cell");
    expect(cells[6]).toHaveTextContent(/^—$/); // Margen $
    expect(cells[7]).toHaveTextContent(/^—$/); // Margen %
    expect(within(rows[3]).getByText("Borrador")).toBeInTheDocument();
  });

  it("a row opens its import-cost split and «Abrir cotización» opens the quote's tab", async () => {
    const { onOpen } = setup();
    const toggle = await screen.findByRole("button", { name: /CN00041/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle.className).toContain("min-h-[44px]");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const detail = document.getElementById(toggle.getAttribute("aria-controls")!)!;
    const split = within(within(detail).getByLabelText("Gastos de importación por tipo"));
    expect(split.getByText("Flete").nextSibling).toHaveTextContent("$ 500.000");
    expect(split.getByText("Agente de aduana").nextSibling).toHaveTextContent("$ 120.000");
    expect(split.getByText("Derechos y aduana").nextSibling).toHaveTextContent("$ 180.000");
    expect(split.getByText("Transporte local").nextSibling).toHaveTextContent("$ 30.000");
    expect(split.queryByText("Otros gastos")).toBeNull(); // nothing of that kind
    const open = within(detail).getByRole("button", { name: "Abrir cotización" });
    expect(open.className).toContain("min-h-[44px]");
    fireEvent.click(open);
    expect(onOpen).toHaveBeenCalledWith("6bafddfa-0000-4000-8000-000000000001", {
      label: "CN00041",
      client: "Universidad Ejemplo 1",
      status: "approved",
    });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("the period buttons read their months; a custom range is checked before it is read", async () => {
    const { load } = setup();
    await screen.findByRole("region", { name: "Resumen del período" });
    fireEvent.click(screen.getByRole("button", { name: "Este mes" }));
    await waitFor(() => expect(load).toHaveBeenLastCalledWith({ from: "2026-10", to: "2026-10" }));
    fireEvent.click(screen.getByRole("button", { name: "Últimos 12 meses" }));
    await waitFor(() => expect(load).toHaveBeenLastCalledWith({ from: "2025-11", to: "2026-10" }));
    fireEvent.click(screen.getByRole("button", { name: "Otro período" }));
    expect(screen.getByRole("button", { name: "Otro período" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2026-06" } });
    fireEvent.change(screen.getByLabelText("Hasta"), { target: { value: "2026-03" } });
    fireEvent.click(screen.getByRole("button", { name: "Ver período" }));
    expect(screen.getByRole("alert")).toHaveTextContent("no puede ser anterior");
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2020-01" } });
    fireEvent.change(screen.getByLabelText("Hasta"), { target: { value: "2026-03" } });
    fireEvent.click(screen.getByRole("button", { name: "Ver período" }));
    expect(screen.getByRole("alert")).toHaveTextContent("como máximo 36 meses");
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2026-06" } });
    const calls = load.mock.calls.length;
    fireEvent.change(screen.getByLabelText("Hasta"), { target: { value: "2026-08" } });
    fireEvent.click(screen.getByRole("button", { name: "Ver período" }));
    await waitFor(() => expect(load).toHaveBeenLastCalledWith({ from: "2026-06", to: "2026-08" }));
    expect(load.mock.calls.length).toBe(calls + 1);
  });

  it("an empty period says so, with the scope note", async () => {
    const empty = economicsReportFixture();
    setup({ ...empty, rows: [], months: [], totals: { ...empty.totals, quotes: 0, drafts: { quotes: 0, sale_net_clp: "0" } } });
    expect(await screen.findByText("No hay cotizaciones en este período.")).toBeInTheDocument();
    expect(screen.getAllByText(SCOPE_NOTE).length).toBeGreaterThan(0);
  });
});

describe("coverageNote and presetPeriod", () => {
  it("says the margin covers every quote when all are costed", () => {
    const t = economicsReportFixture().totals;
    const all = { ...t, coverage: { ...t.coverage, full: { quotes: 2, sale_net_clp: t.sale_net_clp } }, drafts: { quotes: 0, sale_net_clp: "0" } };
    expect(coverageNote(all)).toEqual(["El margen cubre las 2 cotizaciones: todas tienen el costo de cada línea.", SCOPE_NOTE]);
  });

  it("this year, this month and the last twelve months, in Santiago", () => {
    // 02:00 UTC on 1 January is still 31 December in Santiago.
    const newYear = new Date("2027-01-01T02:00:00Z");
    expect(presetPeriod("year", newYear)).toEqual({ from: "2026-01", to: "2026-12" });
    expect(presetPeriod("month", newYear)).toEqual({ from: "2026-12", to: "2026-12" });
    expect(presetPeriod("last12", NOW())).toEqual({ from: "2025-11", to: "2026-10" });
  });
});
