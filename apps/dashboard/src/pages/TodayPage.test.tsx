import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EquipmentOpportunitiesResponse, WarmCasesResponse } from "../api/commercialTypes";
import type { TodayPanelData } from "../api/operatorTypes";
import { DashboardApp } from "./DashboardApp";

const panelSqlite: TodayPanelData = {
  health: {
    ok: true,
    service: "origenlab-api",
    mode: "operator-sqlite-readonly",
    backend: "sqlite",
    postgres_configured: false,
  },
  operator: {
    verdict: "READY",
    sqlite_path: "/tmp/emails.sqlite",
    campaign_mode: "default",
    operator_focus: "warm_cases",
    outbound_readiness: "ready",
    warnings: ["low volume this week"],
    daily_core_run: { exists: false },
  },
};

const warmPayload: WarmCasesResponse = {
  meta: {
    data_source: "sqlite",
    read_only: true,
    reduced_mode: false,
    count: 1,
    enrichment_available: false,
    note: "",
  },
  items: [
    {
      case_id: "gmail-contacto-1",
      last_email_id: 1,
      last_seen_at: "2026-05-19T10:00:00-04:00",
      account_name: "ACME",
      contact_email: "buyer@acme.cl",
      subject: "Quote follow-up",
      category: "client_reply",
      status: "open",
      next_action: "reply",
      equipment_signal: "balance",
      snippet: "preview text",
      gmail_url: null,
    },
  ],
};

const equipmentPayload: EquipmentOpportunitiesResponse = {
  meta: {
    data_source: "active_current_csv",
    read_only: true,
    count: 1,
    source_path: "/secret/path/queue.csv",
    campaign_mode: "equipment_first",
    reduced_mode: false,
    note: "",
  },
  items: [
    {
      priority_rank: 1,
      codigo_licitacion: "LP-99",
      buyer: "Hospital Regional",
      region: "RM",
      close_date: "15/06/2026",
      equipment_category: "incubator",
      item_description: "CO2 incubator",
      next_action: "monitor",
      safe_channel: "mercado_publico_bid",
      supplier_needed: "no",
      contact_status: "pending",
      contact_email: "procurement@hospital.cl",
      operator_note: "intel",
    },
  ],
};

vi.mock("../api/operatorClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/operatorClient")>();
  return {
    ...actual,
    fetchTodayPanel: vi.fn(),
    fetchWarmCases: vi.fn(),
    fetchEquipmentOpportunities: vi.fn(),
    fetchContactProfile: vi.fn(),
    getOperatorApiBaseUrl: vi.fn(() => ""),
  };
});

vi.mock("../api/mirrorCommercialClient", () => ({
  fetchCommercialDealsMirror: vi.fn(),
}));

vi.mock("../api/commercialOperationsClient", async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import("../api/commercialOperationsClient")
    >();

  return {
    ...actual,
    fetchCommercialWorkQueue: vi.fn(),
  };
});

vi.mock("../api/mirrorCatalogClient", () => ({
  fetchCatalogProductsMirror: vi.fn(),
}));

vi.mock("../api/mirrorLeadIntelClient", () => ({
  fetchLeadResearchSummaryMirror: vi.fn(),
}));

vi.mock("../api/institutionIntel/adapter", () => ({
  institutionIntelAdapter: {
    getCurrentOpportunities: vi.fn(async () => ({
      availability: { status: "available_empty" as const },
      pageInfo: { page: 1, pageSize: 15, totalItems: 0 },
    })),
  },
}));

vi.mock("../lib/logo/threeBodyCanvasRunner", () => ({
  startThreeBodyCanvas: vi.fn(() => () => {}),
}));

import {
  fetchContactProfile,
  fetchEquipmentOpportunities,
  fetchTodayPanel,
  fetchWarmCases,
} from "../api/operatorClient";
import { fetchCommercialDealsMirror } from "../api/mirrorCommercialClient";
import { fetchCommercialWorkQueue } from "../api/commercialOperationsClient";
import { fetchCatalogProductsMirror } from "../api/mirrorCatalogClient";
import { fetchLeadResearchSummaryMirror } from "../api/mirrorLeadIntelClient";
import { catalogListFixture } from "../test/fixtures/catalogMirrorFixtures";
import { leadSummaryFixture } from "../test/fixtures/leadIntelFixtures";
import { waitForDashboardReady } from "../test/renderDashboardApp";

describe("DashboardApp (legacy TodayPage tests)", () => {
  beforeEach(() => {
    vi.stubEnv("MODE", "development");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "");
    window.location.hash = "#/";
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.clearAllMocks();
    window.location.hash = "";
  });

  function mockAllOk() {
  vi.mocked(
    fetchCommercialWorkQueue,
  ).mockResolvedValue({
    open_tasks: [],
    review_opportunities: [],
    quote_followups: [],
  });
    vi.mocked(fetchTodayPanel).mockResolvedValue(panelSqlite);
    vi.mocked(fetchWarmCases).mockResolvedValue(warmPayload);
    vi.mocked(fetchEquipmentOpportunities).mockResolvedValue(equipmentPayload);
    vi.mocked(fetchCatalogProductsMirror).mockResolvedValue(catalogListFixture());
    vi.mocked(fetchLeadResearchSummaryMirror).mockResolvedValue(leadSummaryFixture());
    vi.mocked(fetchCommercialDealsMirror).mockResolvedValue({
      table_available: true,
      read_only: true,
      data_source: "postgres_mirror",
      total: 0,
      limit: 20,
      items: [],
    });
  }

  it("shows legacy :8000 dev warning when env points at wrong port", async () => {
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "http://127.0.0.1:8000");
    mockAllOk();
    render(<DashboardApp />);
    await screen.findByText(/Configuración local incorrecta/);
    await waitForDashboardReady();
  });

  it("does not expose sqlite paths or raw body fields", async () => {
    mockAllOk();
    vi.mocked(fetchTodayPanel).mockResolvedValue({
      ...panelSqlite,
      operator: {
        ...panelSqlite.operator,
        daily_core_run: {
          exists: true,
          loaded: true,
          status: "success",
          returncode: 0,
          step_count: 7,
          send_approval: false,
          postgres_mirror: "not included",
          path: "/secret/active/current/daily_core_run_manifest.json",
        },
      },
    });
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    expect(screen.queryByText(/\/tmp\/emails\.sqlite/)).toBeNull();
    expect(screen.queryByText(/body_preview/)).toBeNull();

    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    fireEvent.click(within(nav).getByRole("link", { name: "Sistema" }));
    await waitFor(() => screen.getByText("Última ejecución daily-core"));
    expect(screen.queryByText(/daily_core_run_manifest\.json/)).toBeNull();
    expect(screen.queryByText(/\/secret\/active/)).toBeNull();

    fireEvent.click(within(nav).getByRole("link", { name: "Licitaciones / equipos" }));
    await waitFor(() => screen.getByText("Oportunidades accionables"));
    expect(screen.queryByText(/\/secret\/path/)).toBeNull();
  });

  it("shows daily-core section when no run is registered", async () => {
    mockAllOk();
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    screen.getByText(/Este panel no envía correos ni aprueba contactos; las acciones comerciales se realizan dentro del ciclo de cada oportunidad/);

    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    fireEvent.click(within(nav).getByRole("link", { name: "Sistema" }));
    await waitFor(() => screen.getByText("Última ejecución daily-core"));
    screen.getByText("Sin ejecución registrada todavía.");
    expect(screen.queryByText(/No aprueba envíos/)).toBeNull();
  });

  it("shows valid daily-core run summary", async () => {
    mockAllOk();
    vi.mocked(fetchTodayPanel).mockResolvedValue({
      ...panelSqlite,
      operator: {
        ...panelSqlite.operator,
        daily_core_run: {
          exists: true,
          loaded: true,
          workflow: "daily-core",
          status: "success",
          returncode: 0,
          step_count: 7,
          send_approval: false,
          postgres_mirror: "not included",
          generated_at_utc: "2026-06-05T12:00:00+00:00",
          path: "/hidden/daily_core_run_manifest.json",
        },
      },
    });
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    fireEvent.click(within(nav).getByRole("link", { name: "Sistema" }));
    await waitFor(() => screen.getByText("Última ejecución daily-core"));
    const note = screen.getByTestId("daily-core-run-note");
    within(note).getByText("success");
    within(note).getByText("7");
    within(note).getByText("0");
    within(note).getByText("2026-06-05T12:00:00+00:00");
    within(note).getByText("not included");
    within(note).getByText("No");
    expect(within(note).queryByText(/No aprueba envíos/)).toBeNull();
    expect(screen.queryByText(/\/hidden\/daily_core/)).toBeNull();
  });

  it("shows readable warning when daily-core manifest has parse error", async () => {
    mockAllOk();
    vi.mocked(fetchTodayPanel).mockResolvedValue({
      ...panelSqlite,
      operator: {
        ...panelSqlite.operator,
        daily_core_run: {
          exists: true,
          loaded: false,
          parse_error: true,
          path: "/hidden/daily_core_run_manifest.json",
        },
      },
    });
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    fireEvent.click(within(nav).getByRole("link", { name: "Sistema" }));
    await waitFor(() => screen.getByText("Manifest no legible; revisar status en CLI."));
    expect(screen.queryByText(/\/hidden\/daily_core/)).toBeNull();
  });

  it("shows postgres mirror labels when backend is postgres", async () => {
    vi.mocked(fetchTodayPanel).mockResolvedValue({
      ...panelSqlite,
      health: {
        ...panelSqlite.health,
        backend: "postgres",
        mode: "operator-postgres-readonly",
        postgres_configured: true,
      },
      operator: { ...panelSqlite.operator, verdict: "CAUTION" },
    });
    vi.mocked(fetchWarmCases).mockResolvedValue({
      ...warmPayload,
      meta: { ...warmPayload.meta, data_source: "postgres_mirror" },
    });
    vi.mocked(fetchEquipmentOpportunities).mockResolvedValue({
      ...equipmentPayload,
      meta: { ...equipmentPayload.meta, data_source: "postgres_mirror" },
    });
    mockAllOk();
    vi.mocked(fetchTodayPanel).mockResolvedValue({
      ...panelSqlite,
      health: {
        ...panelSqlite.health,
        backend: "postgres",
        mode: "operator-postgres-readonly",
        postgres_configured: true,
      },
      operator: { ...panelSqlite.operator, verdict: "CAUTION" },
    });

    render(<DashboardApp />);

    await waitFor(() => {
      screen.getByText("Espejo Postgres");
    });
  });

  it("opens read-only contact profile from inbox warm case email", async () => {
    mockAllOk();
    vi.mocked(fetchContactProfile).mockResolvedValue({
      meta: { data_source: "sqlite", read_only: true, reduced_mode: false, note: "" },
      contact: {
        email: "buyer@acme.cl",
        normalized_email: "buyer@acme.cl",
        name: "Buyer",
        domain: "acme.cl",
        organization_name: "ACME",
        organization_domain: "acme.cl",
        last_seen_at: null,
        first_seen_at: null,
        message_count: 1,
      },
      outreach: {
        state: "open",
        last_contacted_at: null,
        source: null,
        notes: null,
        do_not_repeat: false,
        suppressed_email: false,
        suppressed_domain: false,
      },
      sent_history: { sent_count: 0, latest_sent_at: null, latest_subject: null },
      warnings: [],
    });

    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    fireEvent.click(within(nav).getByRole("link", { name: "Bandeja de revisión" }));
    await waitFor(() => screen.getByText("buyer@acme.cl"));

    fireEvent.click(screen.getByRole("button", { name: "buyer@acme.cl" }));

    await waitFor(() => {
      screen.getByText("Perfil de contacto · solo lectura");
      screen.getByText("Buyer");
    });
  });
});
