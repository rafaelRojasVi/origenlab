import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProspectosPage } from "./ProspectosPage";
import {
  lead5mSameDomainDetailFixture,
  leadBiorenDetailFixture,
  leadBlockedDetailFixture,
  leadListFixture,
  leadNetNewDetailFixture,
  leadGmailHistoricoDetailFixture,
  leadSummaryFixture,
  leadTenderDetailFixture,
} from "../test/fixtures/leadIntelFixtures";

vi.mock("../api/mirrorLeadIntelClient", () => ({
  fetchLeadProspectsMirror: vi.fn(),
  fetchLeadProspectDetailMirror: vi.fn(),
  fetchLeadResearchSummaryMirror: vi.fn(),
}));

import {
  fetchLeadProspectDetailMirror,
  fetchLeadProspectsMirror,
  fetchLeadResearchSummaryMirror,
} from "../api/mirrorLeadIntelClient";
import { OperatorApiError } from "../api/operatorClient";

describe("ProspectosPage", () => {
  beforeEach(() => {
    vi.mocked(fetchLeadResearchSummaryMirror).mockResolvedValue(leadSummaryFixture());
    vi.mocked(fetchLeadProspectsMirror).mockResolvedValue(leadListFixture());
    vi.mocked(fetchLeadProspectDetailMirror).mockImplementation(async (key: string) => {
      if (key === "blocked") return leadBlockedDetailFixture();
      if (key === "same" || key === "5m") return lead5mSameDomainDetailFixture();
      if (key === "tender") return leadTenderDetailFixture();
      if (key === "bioren") return leadBiorenDetailFixture();
      if (key === "acme") return leadNetNewDetailFixture();
      if (key === "gmail-hist") return leadGmailHistoricoDetailFixture();
      return leadBlockedDetailFixture();
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows humanized Spanish copy when mirror Postgres is unavailable", async () => {
    const raw = '{"detail":"Postgres audit requested but no Postgres URL resolved"}';
    vi.mocked(fetchLeadProspectsMirror).mockRejectedValue(new OperatorApiError(raw, 503));
    render(<ProspectosPage />);
    await waitFor(() => {
      screen.getByText(/El espejo Postgres no está configurado en este entorno/);
    });
    screen.getByText("Ver detalle técnico");
    expect(screen.getByText("Ver detalle técnico").closest("details")?.hasAttribute("open")).toBe(
      false,
    );
    expect(screen.queryByRole("button", { name: /Enviar/i })).toBeNull();
  });

  it("shows footer with loaded vs API total and numbered pagination", async () => {
    const list = leadListFixture();
    vi.mocked(fetchLeadProspectsMirror).mockResolvedValue({
      ...list,
      total: 71,
    });
    render(<ProspectosPage />);
    await waitFor(() => {
      const footer = screen.getByTestId("prospectos-table-footer");
      expect(footer.textContent).toContain("cargados");
      expect(footer.textContent).toContain("API total 71");
      expect(footer.textContent).toContain("solo lectura");
    });
    expect(screen.getByTestId("prospectos-table-footer").textContent).toMatch(
      /más resultados que los cargados/,
    );
    expect(screen.getByTestId("table-pagination-bar")).toBeTruthy();
  });

  it("renders KPIs and table without send buttons", async () => {
    render(<ProspectosPage />);
    await waitFor(() => {
      const kpis = screen.getByTestId("prospectos-kpis");
      expect(within(kpis).getByText("Listos para contactar")).toBeTruthy();
      expect(within(kpis).getByText("Ya contactados")).toBeTruthy();
      expect(screen.getByText("Acme Labs")).toBeTruthy();
    });
    expect(screen.getByTestId("prospectos-page")).toBeTruthy();
    expect(screen.getByTestId("prospectos-origin-filter")).toBeTruthy();
    expect(screen.getByText("Nuevo seguro")).toBeTruthy();
    expect(screen.getAllByText("No contactar").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /enviar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /compose/i })).toBeNull();
  });

  it("shows disclaimer with space after OrigenLab before Revisión", async () => {
    render(<ProspectosPage />);
    await waitFor(() => {
      expect(screen.getByText(/historial OrigenLab\. Revisión humana/i)).toBeTruthy();
    });
    expect(screen.queryByText(/OrigenLab\.Revisión/i)).toBeNull();
  });

  it("blocked prospect shows No contactar decision and no message preview", async () => {
    render(<ProspectosPage />);
    await waitFor(() => expect(screen.getByText("Blocked Co")).toBeTruthy());
    fireEvent.click(screen.getByText("Blocked Co"));
    await waitFor(() => {
      expect(screen.getByTestId("prospect-decision-banner").textContent).toMatch(/No contactar/i);
    });
    expect(screen.queryByTestId("prospect-message-preview")).toBeNull();
  });

  it("BIOREN research_only shows buscar contacto and no email draft", async () => {
    vi.mocked(fetchLeadProspectsMirror).mockResolvedValue({
      ...leadListFixture(),
      items: [
        {
          ...leadListFixture().items[0],
          prospect_key: "bioren",
          organization_name: "BIOREN-UFRO",
          email: null,
          classification: "research_only_contact_needed",
          status: "research_needed",
        },
      ],
    });
    render(<ProspectosPage />);
    await waitFor(() => expect(screen.getByText("BIOREN-UFRO")).toBeTruthy());
    expect(screen.getByText("Sin email — investigar contacto")).toBeTruthy();
    fireEvent.click(screen.getByText("BIOREN-UFRO"));
    await waitFor(() => {
      expect(screen.getByTestId("prospect-decision-banner").textContent).toMatch(
        /buscar contacto directo/i,
      );
      expect(screen.getByText(/No hay email directo disponible/i)).toBeTruthy();
      expect(screen.getByText(/buscar responsable de laboratorio/i)).toBeTruthy();
    });
    expect(screen.queryByText(/Estimados\/as equipo de BIOREN/i)).toBeNull();
    expect(screen.queryByTestId("prospect-message-preview")).toBeNull();
    expect(screen.getAllByTestId("prospect-evidence-link")[0]?.textContent).toMatch(/Abrir evidencia/);
    expect(screen.queryByText(/https:\/\/www\.ufro\.cl/i)).toBeNull();
  });

  it("empty-email same_domain shows contactado por dominio not falta email", async () => {
    vi.mocked(fetchLeadProspectsMirror).mockResolvedValue({
      ...leadListFixture(),
      items: [
        {
          ...leadListFixture().items[0],
          prospect_key: "inia",
          organization_name: "INIA La Platina",
          email: null,
          domain: "inia.cl",
          classification: "same_domain_contacted_review",
          status: "same_domain_review",
        },
      ],
    });
    render(<ProspectosPage />);
    await waitFor(() => expect(screen.getByText("INIA La Platina")).toBeTruthy());
    expect(screen.getAllByText("Revisar historial").length).toBeGreaterThan(0);
    expect(screen.getByText(/Contactado por dominio — sin email en fila/i)).toBeTruthy();
    const row = screen.getByText("INIA La Platina").closest("tr");
    expect(row?.textContent).not.toMatch(/Falta email/);
  });

  it("5M same_domain shows revisar historial and follow-up wording not cold email", async () => {
    vi.mocked(fetchLeadProspectsMirror).mockResolvedValue({
      ...leadListFixture(),
      items: [
        {
          ...leadListFixture().items[0],
          prospect_key: "5m",
          organization_name: "5M S.A.",
          classification: "same_domain_contacted_review",
          status: "same_domain_review",
          buyer_type: "laboratorio_acuicola",
        },
      ],
    });
    render(<ProspectosPage />);
    await waitFor(() => expect(screen.getByText("5M S.A.")).toBeTruthy());
    expect(screen.getAllByText("Revisar historial").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText("5M S.A."));
    await waitFor(() => {
      expect(screen.getByTestId("prospect-decision-banner").textContent).toMatch(
        /revisar historial antes de escribir/i,
      );
      expect(screen.getByText(/Borrador de seguimiento, no correo frío/i)).toBeTruthy();
      expect(screen.getByTestId("prospect-message-preview").textContent).toMatch(
        /anteriormente habíamos enviado/i,
      );
    });
    expect(screen.getByTestId("prospect-message-preview").textContent).not.toMatch(/breve llamada/i);
    expect(screen.getByTestId("prospect-buyer-type-label").textContent).toMatch(/Laboratorio acuícola/);
    expect(screen.getByText("SERNAPESCA")).toBeTruthy();
  });

  it("net_new_safe shows message preview with softer ask", async () => {
    render(<ProspectosPage />);
    await waitFor(() => expect(screen.getByText("Acme Labs")).toBeTruthy());
    fireEvent.click(screen.getByText("Acme Labs"));
    await waitFor(() => {
      expect(screen.getByTestId("prospect-message-preview")).toBeTruthy();
      expect(screen.getByTestId("prospect-message-preview").textContent).toMatch(
        /reposición, compra o cotización referencial/i,
      );
    });
    expect(screen.getByTestId("prospect-message-preview").textContent).not.toMatch(/breve llamada/i);
  });

  it("translates risk flags in drawer chips", async () => {
    render(<ProspectosPage />);
    await waitFor(() => expect(screen.getByText("Acme Labs")).toBeTruthy());
    fireEvent.click(screen.getByText("Acme Labs"));
    await waitFor(() => {
      const chips = screen.getByTestId("prospect-risk-chips");
      expect(within(chips).getByText("Nuevo según investigación")).toBeTruthy();
    });
    expect(screen.queryByText("lead_status=net_new_candidate")).toBeNull();
  });

  it("shows origin chip for gmail historico row", async () => {
    render(<ProspectosPage />);
    await waitFor(() => expect(screen.getByText("Gmail Hist Co")).toBeTruthy());
    expect(screen.getAllByTestId("prospect-origin-chip").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText("Gmail Hist Co"));
    await waitFor(() => {
      expect(screen.getByTestId("prospect-gmail-history")).toBeTruthy();
      expect(screen.getByTestId("prospect-source-type-label").textContent).toMatch(/Gmail histórico/i);
      expect(screen.getByTestId("prospect-message-preview")).toBeTruthy();
    });
  });

  it("origin filter gmail_historico calls API with source_type", async () => {
    render(<ProspectosPage />);
    await waitFor(() => expect(screen.getByTestId("prospectos-origin-filter")).toBeTruthy());
    fireEvent.change(screen.getByTestId("prospectos-origin-filter"), {
      target: { value: "gmail_historico" },
    });
    await waitFor(() => {
      expect(vi.mocked(fetchLeadProspectsMirror)).toHaveBeenCalledWith(
        expect.objectContaining({ source_type: "gmail_historico" }),
      );
    });
  });

  it("public tender shows tender instruction not cold email", async () => {
    vi.mocked(fetchLeadProspectsMirror).mockResolvedValue({
      ...leadListFixture(),
      items: [
        {
          ...leadListFixture().items[0],
          prospect_key: "tender",
          organization_name: "Hospital Demo",
          email: null,
          classification: "public_tender_review",
          status: "public_tender_review",
        },
      ],
    });
    render(<ProspectosPage />);
    await waitFor(() => expect(screen.getByText("Hospital Demo")).toBeTruthy());
    fireEvent.click(screen.getByText("Hospital Demo"));
    await waitFor(() => {
      expect(screen.getByTestId("prospect-message-section").textContent).toMatch(/equivalencia técnica/i);
    });
    expect(screen.queryByTestId("prospect-message-preview")).toBeNull();
  });
});
