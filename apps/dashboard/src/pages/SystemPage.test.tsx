import { render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardDataContext } from "../context/DashboardDataContext";
import type { ProcurementStatus } from "../api/institutionIntel/types";
import { SystemPage } from "./SystemPage";

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

const BASE_AUTOMATION_STATUS = {
  generated_at_utc: "2026-06-10T18:30:00+00:00",
  active_current_dir: "/hidden/active/current",
  active_current_dir_info: {
    redacted: true,
    basename: "current",
    kind: "directory",
  },
  path_redaction_applied: true,
  verdict: "healthy",
  daily_core: {
    exists: true,
    status: "success",
    returncode: 0,
    generated_at_utc: "2026-06-10T18:12:48+00:00",
    age_seconds: 1032,
    steps: 8,
  },
  mail_auto_refresh: {
    state_exists: true,
    paused: false,
    lock_live: false,
    dirty: false,
    pending: false,
    last_successful_refresh_at: "2026-06-10T18:12:48+00:00",
    last_seen_inbox_total: 403,
    last_seen_sent_total: 971,
    consecutive_failures: 0,
  },
  dashboard_auto_mirror: {
    state_exists: true,
    paused: false,
    lock_live: false,
    last_successful_mirror_at: "2026-06-10T18:18:33+00:00",
    last_mirrored_daily_core_generated_at: "2026-06-10T18:12:48+00:00",
    mirror_matches_daily_core: true,
    cooldown_seconds: 900,
    cooldown_remaining_seconds: 0,
    consecutive_failures: 0,
  },
  chilecompra_equipment_auto_refresh: {
    state_exists: false,
    lock_live: false,
    lock_age_seconds: null,
    freshness_age_seconds: null,
    next_run_due: null,
    consecutive_failures: 0,
  },
  cron: { note: "not inspected by API" },
  recommended_action: "none",
  warnings: [],
};

vi.mock("../api/operatorClient", () => ({
  fetchOperatorAutomationStatus: vi.fn(),
}));

import { fetchOperatorAutomationStatus } from "../api/operatorClient";

const mockFetchAutomation = vi.mocked(fetchOperatorAutomationStatus);

function wrap(ui: ReactNode, overrides: Record<string, unknown> = {}) {
  return (
    <DashboardDataContext.Provider
      value={
        {
          data: {
            health: { ok: true, service: "origenlab-api", mode: "operator-sqlite-readonly", backend: "sqlite" },
            operator: { verdict: "READY", outbound_readiness: "ready", warnings: [] },
          },
          warm: { items: [], meta: null },
          procurementStatus: procurementStatus(),
          commercialDeals: null,
          panelLoading: false,
          panelError: null,
          catalogProducts: null,
          leadResearchSummary: null,
          mirrorBackend: false,
          loadPanel: async () => {},
          setContactEmail: () => {},
          ...overrides,
        } as never
      }
    >
      {ui}
    </DashboardDataContext.Provider>
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("SystemPage", () => {
  beforeEach(() => {
    mockFetchAutomation.mockResolvedValue(BASE_AUTOMATION_STATUS as never);
  });

  it("explains full archive vs canonical Gmail subset", async () => {
    render(wrap(<SystemPage />));
    await waitFor(() => {
      screen.getByText("Estado de automatización");
    });
    screen.getByText(/216\s*000/);
    screen.getByText(/1\s*100/);
    screen.getByText(/contacto@origenlab\.cl/);
    expect(screen.getByText(/subconjunto canónico/i)).toBeTruthy();
    expect(screen.getByText(/todo el archivo histórico/i)).toBeTruthy();
  });

  it("renders operator automation section with safety note", async () => {
    render(wrap(<SystemPage />));
    screen.getByTestId("system-automation-section");
    screen.getByRole("heading", { name: "Automatización operador" });
    screen.getByText(/no ejecuta refresh, mirror ni envíos/i);
    await waitFor(() => {
      screen.getByText("Estado de automatización");
    });
  });

  it("renders ChileCompra automation details on System page", async () => {
    mockFetchAutomation.mockResolvedValue({
      ...BASE_AUTOMATION_STATUS,
      chilecompra_equipment_auto_refresh: {
        state_exists: true,
        lock_live: false,
        lock_age_seconds: null,
        last_result: "refreshed",
        last_successful_refresh_at: "2026-06-10T17:12:48+00:00",
        last_successful_publish_at: "2026-06-10T17:41:00+00:00",
        next_recommended_run_at: "2026-06-10T20:41:00+00:00",
        freshness_age_seconds: 4620,
        next_run_due: false,
        consecutive_failures: 0,
        detail_requests: 4,
        detail_cache_hits: 2,
        detail_error_count: 0,
        published_rows: 7,
      },
      cron: {
        chilecompra_entry_present: true,
        chilecompra_uses_tracked_script: true,
      },
    } as never);
    render(wrap(<SystemPage />));
    await waitFor(() => {
      screen.getByTestId("chilecompra-automation-section");
    });
    screen.getByText("Actualizado");
    screen.getByText("4 / 2 / 0");
  });

  it("renders Estado del sistema with daily-core status", () => {
    render(wrap(<SystemPage />));
    const systemStatus = screen.getByTestId("system-status-section");
    within(systemStatus).getByText("Última ejecución daily-core");
    within(systemStatus).getByText("Sin ejecución registrada todavía.");
    expect(within(systemStatus).queryByText(/No aprueba envíos/)).toBeNull();
  });

  it("shows empty Atención state when there are no warnings", () => {
    render(wrap(<SystemPage />));
    screen.getByText("Sin advertencias por ahora.");
  });

  it("humanizes stale Postgres mirror warning in Atención section", () => {
    render(
      wrap(<SystemPage />, {
        data: {
          health: { ok: true, service: "origenlab-api", mode: "operator-sqlite-readonly", backend: "sqlite" },
          operator: {
            verdict: "READY",
            outbound_readiness: "ready",
            warnings: ["Postgres mirror last sync older than 24h (2026-06-01T00:00:00Z)."],
          },
        },
      }),
    );
    screen.getByText(
      "El espejo Postgres no se ha sincronizado en más de 24h. Los datos pueden estar atrasados.",
    );
  });

  it("humanizes Global MAX outlier warning in Atención section", () => {
    render(
      wrap(<SystemPage />, {
        data: {
          health: { ok: true, service: "origenlab-api", mode: "operator-sqlite-readonly", backend: "sqlite" },
          operator: {
            verdict: "READY",
            outbound_readiness: "ready",
            warnings: [
              "Global MAX(date_iso) outlier (2033-06-09T15:09:53+01:00) — prefer 2026-filtered freshness.",
            ],
          },
        },
      }),
    );
    screen.getByText(
      "Hay una fecha futura anómala en el archivo histórico. Para frescura diaria se usa la fecha filtrada de 2026.",
    );
    expect(screen.queryByText(/Global MAX\(date_iso\)/)).toBeNull();
  });

  it("humanizes FastLab not_contacted warning in Atención section", () => {
    render(
      wrap(<SystemPage />, {
        data: {
          health: { ok: true, service: "origenlab-api", mode: "operator-sqlite-readonly", backend: "sqlite" },
          operator: {
            verdict: "READY",
            outbound_readiness: "ready",
            warnings: [
              "FastLab (contacto@fastlab.cl): corrected to not_contacted; no Gmail Sent evidence; future outreach requires deliberate manual review.",
            ],
          },
        },
      }),
    );
    screen.getByText(
      "FastLab quedó marcado como no contactado porque no hay evidencia en Gmail Enviados. Revisar manualmente antes de contactar.",
    );
    expect(screen.queryByText(/corrected to not_contacted/i)).toBeNull();
  });

  it("shows prior-history warning near Atención when same_domain_review threshold is met", () => {
    render(
      wrap(<SystemPage />, {
        leadResearchSummary: {
          table_available: true,
          total: 10,
          review_count: 10,
          blocked_count: 0,
          net_new_safe: 2,
          gmail_historico: 0,
          followup_antiguo: 0,
          caso_activo: 0,
          public_tender_review: 0,
          same_domain_review: 3,
          research_needed: 0,
          data_source: "postgres_mirror",
          read_only: true,
          disclaimer: "",
        },
      }),
    );
    screen.getByTestId("system-prospect-prior-history-warning");
    screen.getByText(/Hay prospectos con historial previo/);
  });

  it("shows the W1 current_opportunity_queue count as Oportunidades accionables", () => {
    render(wrap(<SystemPage />, { procurementStatus: procurementStatus({}, 4) }));
    const activitySection = screen.getByRole("heading", { name: "Actividad del sistema" }).closest("section")!;
    within(activitySection).getByText("Oportunidades accionables");
    within(activitySection).getByText("4");
    expect(within(activitySection).queryByText("Licitaciones")).toBeNull();
  });

  it("shows a healthy zero as a real 0", () => {
    render(wrap(<SystemPage />, { procurementStatus: procurementStatus({}, 0) }));
    const tile = screen.getByText("Oportunidades accionables").closest("div")!;
    within(tile).getByText("0");
  });

  it("shows N/D and an unavailable hint when reduced_mode is true", () => {
    render(wrap(<SystemPage />, { procurementStatus: procurementStatus({ reduced_mode: true }, 4) }));
    const activitySection = screen.getByRole("heading", { name: "Actividad del sistema" }).closest("section")!;
    within(activitySection).getByText("N/D");
    within(activitySection).getByText("Fuente de oportunidades accionables no disponible");
  });

  it("shows the numeric value and a stale hint when meta.stale is true", () => {
    render(wrap(<SystemPage />, { procurementStatus: procurementStatus({ stale: true }, 4) }));
    const activitySection = screen.getByRole("heading", { name: "Actividad del sistema" }).closest("section")!;
    within(activitySection).getByText("4");
    within(activitySection).getByText("Datos accionables desactualizados · revisar actualización W1");
  });

  it("shows a neutral loading placeholder, not N/D or an unavailable hint, while the initial request is in flight", () => {
    render(
      wrap(<SystemPage />, { procurementStatus: null, procurementStatusLoading: true }),
    );
    const tile = screen.getByText("Oportunidades accionables").closest("div")!;
    within(tile).getByText("…");
    expect(within(tile).queryByText("N/D")).toBeNull();
    expect(
      within(tile).queryByText("Fuente de oportunidades accionables no disponible"),
    ).toBeNull();
  });

  it("shows N/D and an unavailable hint once the request has finished with no usable status", () => {
    render(
      wrap(<SystemPage />, { procurementStatus: null, procurementStatusLoading: false }),
    );
    const tile = screen.getByText("Oportunidades accionables").closest("div")!;
    within(tile).getByText("N/D");
    within(tile).getByText("Fuente de oportunidades accionables no disponible");
  });

  it("keeps showing an already-loaded value while a background refresh is in flight", () => {
    render(
      wrap(<SystemPage />, {
        procurementStatus: procurementStatus({}, 4),
        procurementStatusLoading: true,
      }),
    );
    const tile = screen.getByText("Oportunidades accionables").closest("div")!;
    within(tile).getByText("4");
    expect(within(tile).queryByText("N/D")).toBeNull();
  });
});
