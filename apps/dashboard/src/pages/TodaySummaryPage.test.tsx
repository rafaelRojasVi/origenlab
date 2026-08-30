import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardDataContext } from "../context/DashboardDataContext";
import type { ProcurementStatus } from "../api/institutionIntel/types";
import { TodaySummaryPage } from "./TodaySummaryPage";

function procurementStatus(
  overrides: Partial<ProcurementStatus["meta"]> = {},
  queueValue: number | undefined = 4,
): ProcurementStatus {
  return {
    meta: {
      data_source: "institution_prospect_bundle",
      read_only: true,
      contract_version: "institution_prospect_contract_v4",
      supported_contract_version: true,
      reduced_mode: false,
      stale: false,
      canonical_reason: "institution_prospect_read_model",
      note: "",
      as_of_utc: "2026-08-30T00:12:01+00:00",
      not_persisted: true,
      contact_authorization: false,
      outreach_authorization: false,
      ...overrides,
    },
    operatorQueueSizes:
      queueValue === undefined ? {} : { current_opportunity_queue: queueValue },
    summaryOk: true,
  };
}

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
          procurementStatus: procurementStatus(),
          commercialDeals: null,
          catalogProducts: { total: 6 },
          leadResearchSummary: null,
          commercialWorkQueue: {
            open_tasks: [],
            review_opportunities: [],
            quote_followups: [],
          },
          commercialWorkQueueLoading: false,
          commercialWorkQueueError: null,
          mirrorBackend: false,
          loadPanel: async () => {},
          loadCommercialWorkQueue: async () => {},
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
    screen.getByText(/Colas priorizadas según correos, oportunidades accionables y señales comerciales cargadas/);
  });

  it("shows operator safety boundary once at the top", () => {
    renderToday();
    screen.getByText(/Este panel no envía correos ni aprueba contactos; las acciones comerciales se realizan dentro del ciclo de cada oportunidad/);
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

describe("TodaySummaryPage actionable-opportunity summary (W1 procurement status)", () => {
  it("shows the W1 current_opportunity_queue count, not any legacy equipment count", () => {
    // Deliberately distinct from any legacy equipment-feed count: proves the
    // summary is sourced from W1, not from the old GET /opportunities/equipment feed.
    renderToday({ procurementStatus: procurementStatus({}, 4) });
    screen.getByLabelText(/Oportunidades accionables: 4/);
    expect(screen.queryByText("Licitaciones / equipos")).toBeNull();
  });

  it("shows a healthy zero as a real 0, not N/D", () => {
    renderToday({ procurementStatus: procurementStatus({}, 0) });
    screen.getByLabelText(/Oportunidades accionables: 0/);
    expect(screen.queryByTestId("today-procurement-status-unavailable")).toBeNull();
  });

  it("shows unavailable warning and N/D KPI when reduced_mode is true", () => {
    renderToday({ procurementStatus: procurementStatus({ reduced_mode: true }, 4) });
    within(screen.getByTestId("today-procurement-status-unavailable")).getByText(
      "Fuente de oportunidades accionables no disponible",
    );
    screen.getByLabelText(/Oportunidades accionables: N\/D/);
    screen.getByText("Catálogo");
  });

  it("shows N/D and the unavailable banner once the request has finished with no usable status", () => {
    renderToday({ procurementStatus: null, procurementStatusLoading: false });
    screen.getByTestId("today-procurement-status-unavailable");
    screen.getByLabelText(/Oportunidades accionables: N\/D/);
  });

  it("shows a neutral loading placeholder, not N/D or the unavailable banner, while the initial request is in flight", () => {
    renderToday({ procurementStatus: null, procurementStatusLoading: true });
    expect(screen.queryByTestId("today-procurement-status-unavailable")).toBeNull();
    expect(
      screen.queryByText("Fuente de oportunidades accionables no disponible"),
    ).toBeNull();
    screen.getByLabelText(/Oportunidades accionables: …/);
  });

  it("keeps showing an already-loaded value while a background refresh is in flight", () => {
    renderToday({ procurementStatus: procurementStatus({}, 4), procurementStatusLoading: true });
    expect(screen.queryByTestId("today-procurement-status-unavailable")).toBeNull();
    screen.getByLabelText(/Oportunidades accionables: 4/);
  });

  it("shows N/D when summaryOk is false", () => {
    renderToday({
      procurementStatus: { ...procurementStatus({}, 4), summaryOk: false },
    });
    screen.getByTestId("today-procurement-status-unavailable");
    screen.getByLabelText(/Oportunidades accionables: N\/D/);
  });

  it("shows the numeric value AND a stale indication when meta.stale is true", () => {
    renderToday({ procurementStatus: procurementStatus({ stale: true }, 4) });
    within(screen.getByTestId("today-procurement-status-stale")).getByText(
      "Datos accionables desactualizados · revisar actualización W1",
    );
    screen.getByLabelText(/Oportunidades accionables: 4/);
    expect(screen.queryByTestId("today-procurement-status-unavailable")).toBeNull();
  });
});


describe("TodaySummaryPage commercial work queue", () => {
  it("shows overdue, today, review, and quote follow-up counts", () => {
    const now = new Date();

    const overdue = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate() - 1,
      12,
      0,
      0,
    ).toISOString();

    const dueToday = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate(),
      18,
      0,
      0,
    ).toISOString();

    const opportunityId =
      "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    function workTask(
      taskId: string,
      dueAt: string | null,
    ) {
      return {
        task: {
          task_id: taskId,
          opportunity_id: opportunityId,
          account_id: null,
          contact_id: null,
          title: taskId,
          status: "open" as const,
          priority: "normal" as const,
          due_at: dueAt,
          owner_key: null,
          version: 1,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          completed_at: null,
          created_at: now.toISOString(),
          updated_at: now.toISOString(),
        },
        contact_display_email:
          "buyer@example.cl",
        account_display_domain:
          "example.cl",
        canonical_stage: "quote_sent",
        machine_review_status:
          "needs_review",
      };
    }

    const opportunity = {
      opportunity_id: opportunityId,
      contact_display_email:
        "buyer@example.cl",
      account_display_domain:
        "example.cl",
      canonical_stage: "quote_sent",
      machine_review_status:
        "needs_review",
      confirmation_status: null,
      manual_stage: null,
      owner_key: null,
      operator_state_version: null,
    };

    renderToday({
      commercialWorkQueue: {
        open_tasks: [
          workTask(
            "task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            overdue,
          ),
          workTask(
            "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            dueToday,
          ),
          workTask(
            "task_cccccccccccccccccccccccccccccccc",
            null,
          ),
        ],
        review_opportunities: [
          opportunity,
          {
            ...opportunity,
            opportunity_id:
              "o_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          },
        ],
        quote_followups: [
          opportunity,
        ],
      },
    });

    screen.getByTestId(
      "today-commercial-work",
    );

    screen.getByLabelText(
      /Seguimientos vencidos: 1/,
    );

    screen.getByLabelText(
      /Para hoy: 1/,
    );

    screen.getByLabelText(
      /Revisión humana: 2/,
    );

    screen.getByLabelText(
      /Cotizaciones por seguir: 1/,
    );

    screen.getByText(
      "0 próximos · 1 sin fecha",
    );
  });
});
