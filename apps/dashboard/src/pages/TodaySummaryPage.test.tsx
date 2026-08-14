import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardDataContext } from "../context/DashboardDataContext";
import { TodaySummaryPage } from "./TodaySummaryPage";

const BASE_PANEL = {
  health: {
    ok: true,
    service: "origenlab-api",
    mode: "operator-sqlite-readonly",
    backend: "sqlite" as const,
    postgres_configured: false,
  },
  operator: {
    verdict: "READY",
    sqlite_path: "/hidden/emails.sqlite",
    campaign_mode: null,
    operator_focus: null,
    outbound_readiness: "ready",
    warnings: [] as string[],
    daily_core_run: { exists: false },
  },
};

function renderToday(overrides: Record<string, unknown> = {}) {
  return render(
    <DashboardDataContext.Provider
      value={
        {
          data: BASE_PANEL,
          panelLoading: false,
          panelError: null,
          warm: { items: [], meta: null },
          equipment: { items: [], meta: null },
          commercialDeals: null,
          catalogProducts: { total: 6 },
          leadResearchSummary: null,
          mirrorBackend: false,
          loadPanel: async () => {},
          setContactEmail: () => {},
          ...overrides,
        } as never
      }
    >
      <TodaySummaryPage />
    </DashboardDataContext.Provider>,
  );
}

describe("TodaySummaryPage operator landing layout", () => {
  it("shows Qué revisar hoy once and Colas prioritarias as queue section title", () => {
    renderToday();
    expect(screen.getAllByText("Qué revisar hoy")).toHaveLength(1);
    screen.getByRole("heading", { level: 2, name: "Colas prioritarias" });
    screen.getByText(/Colas priorizadas según correos, oportunidades de equipos y señales comerciales cargadas/);
  });

  it("shows read-only safety note once at the top", () => {
    renderToday();
    screen.getByText(/Solo lectura: este panel no envía correos ni aprueba contactos/);
    expect(screen.queryByText(/No aprueba envíos/)).toBeNull();
  });

  it("does not render forbidden action button labels", () => {
    renderToday();
    expect(screen.queryByRole("button", { name: /Enviar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Aplicar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Ejecutar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Validar stack/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Run$/i })).toBeNull();
    expect(screen.getAllByText("Ver sección →").length).toBeGreaterThan(0);
  });

});

describe("TodaySummaryPage prospect review card", () => {
  it("renders Prospectos en revisión with review_count as the main value", () => {
    renderToday({
      leadResearchSummary: {
        table_available: true,
        total: 71,
        review_count: 71,
        blocked_count: 1,
        net_new_safe: 0,
        gmail_historico: 5,
        followup_antiguo: 0,
        caso_activo: 0,
        public_tender_review: 2,
        same_domain_review: 1,
        research_needed: 3,
        data_source: "postgres_mirror",
        read_only: true,
        disclaimer: "",
      },
    });

    screen.getByText("Prospectos en revisión");
    expect(screen.queryByText("Prospectos seguros")).toBeNull();
    screen.getByLabelText(/Prospectos en revisión: 71/);
    expect(screen.queryByLabelText(/Prospectos en revisión: 0/)).toBeNull();
  });

  it("shows net_new_safe in the hint when review_count is present", () => {
    renderToday({
      leadResearchSummary: {
        table_available: true,
        total: 71,
        review_count: 71,
        blocked_count: 1,
        net_new_safe: 0,
        gmail_historico: 5,
        followup_antiguo: 0,
        caso_activo: 0,
        public_tender_review: 2,
        same_domain_review: 1,
        research_needed: 3,
        data_source: "postgres_mirror",
        read_only: true,
        disclaimer: "",
      },
    });

    screen.getByText("0 nuevos seguros · revisar historial antes de contactar");
  });

  it("shows missing-summary hint when leadResearchSummary is null", () => {
    renderToday({ leadResearchSummary: null });
    screen.getByText("Sin resumen de prospectos cargado");
    screen.getByLabelText(/Prospectos en revisión: 0/);
  });
});

describe("TodaySummaryPage equipment feed warning", () => {
  it("shows unavailable warning and N/D KPI when equipment reduced_mode", () => {
    render(
      <DashboardDataContext.Provider
        value={
          {
            data: {
              health: {
                ok: true,
                service: "origenlab-api",
                mode: "operator-sqlite-readonly",
                backend: "sqlite",
              },
              operator: { verdict: "READY", outbound_readiness: "ready", warnings: [] },
            },
            panelLoading: false,
            panelError: null,
            warm: { items: [], meta: null },
            equipment: {
              items: [],
              meta: {
                reduced_mode: true,
                count: 0,
                data_source: "active_current_csv",
                read_only: true,
                note: "missing queue",
                campaign_mode: "equipment_first",
              },
            },
            commercialDeals: {
              items: [],
              table_available: true,
              total: 0,
              limit: 20,
              read_only: true,
              data_source: "postgres_mirror",
            },
            catalogProducts: { total: 6 },
            mirrorBackend: false,
            loadPanel: async () => {},
            setContactEmail: () => {},
          } as never
        }
      >
        <TodaySummaryPage />
      </DashboardDataContext.Provider>,
    );

    screen.getByTestId("today-equipment-feed-unavailable");
    screen.getByText("Fuente de licitaciones no disponible");
    screen.getByLabelText(/Licitaciones \/ equipos: N\/D/);
    screen.getByText("Catálogo");
  });
});
