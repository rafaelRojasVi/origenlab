import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MOCK_TALCA } from "../../api/institutionIntel/mockData";
import { InstitutionProfileCard } from "./InstitutionProfileCard";

vi.mock("../../api/institutionIntel/adapter", () => ({
  institutionIntelAdapter: {
    getInstitutionProfile: vi.fn(async (institutionId: string) =>
      institutionId === "inst-talca-01" ? MOCK_TALCA : null,
    ),
  },
}));

describe("InstitutionProfileCard", () => {
  it("renders identity, three independent axes, and equipment history", async () => {
    render(<InstitutionProfileCard institutionId="inst-talca-01" />);

    await waitFor(() => {
      screen.getByText("Hospital Regional de Talca");
    });

    // Three axes must render as three separate labeled cards, never one blended score.
    screen.getByText("Fuerza de prospecto");
    screen.getByText("Urgencia de oportunidad");
    screen.getByText("Contacto listo");
    expect(screen.getAllByText("Alta").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Baja")).toBeTruthy();

    // Equipment history, including the annex-sourced entry and its evidence trail.
    screen.getByText("Balanza analítica");
    screen.getByText("Detectado por anexo");
    expect(screen.getAllByText(/Ver texto real de licitaciones/).length).toBeGreaterThanOrEqual(1);

    // Contact overlay: counts + next action, no fuzzy-matched named contacts implied as real.
    screen.getByText(/Acción recomendada: Investigar nuevo contacto/);
  });

  it("shows a not-found state for an unknown institution id", async () => {
    render(<InstitutionProfileCard institutionId="does-not-exist" />);
    await waitFor(() => {
      screen.getByText("Institución no encontrada.");
    });
  });

  it("labels mock-only contacts as preview, not a real join", async () => {
    render(<InstitutionProfileCard institutionId="inst-talca-01" />);
    await waitFor(() => {
      screen.getByTestId("mock-only-contacts");
    });
    screen.getByText(/Vista previa — solo mock, no un join real/);
    screen.getByText(/sin coincidencia difusa de dominio\/nombre/);
  });
});
