import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LicitacionIntelCard } from "./LicitacionIntelCard";

describe("LicitacionIntelCard", () => {
  it("renders eligibility, term facts with state, item budget, and recognition delta", async () => {
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("Abierto / público");
    });

    // Term facts show both value and honesty state.
    screen.getByText("Presupuesto");
    expect(screen.getAllByText("Confirmado").length).toBeGreaterThan(0);
    screen.getByText("Duración contrato");
    screen.getByText("Calculado");

    // Per-item budget: found values and the honest not-itemized case, same section.
    screen.getByText("Centrífuga refrigerada");
    screen.getByText("Balanza analítica");
    screen.getByText(/Suma coincide con el total/);
    screen.getByText("No desglosado — incluido en el total");

    // Recognition delta: added category, evidence visible by default (high confidence).
    screen.getByText("Anexo agregó equipo");
    screen.getByText(/\+ Balanza analítica/i);
  });

  it("shows a not-available state for a tender with no intel yet", async () => {
    render(<LicitacionIntelCard tenderCode="unknown-tender" />);
    await waitFor(() => {
      screen.getByText(/Inteligencia de anexos no disponible/);
    });
  });
});
