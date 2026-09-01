import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WarmCasesResponse } from "../api/commercialTypes";
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
    warnings: [],
    daily_core_run: { exists: false },
  },
};

const warmPayload: WarmCasesResponse = {
  meta: {
    data_source: "sqlite",
    read_only: true,
    reduced_mode: false,
    count: 4,
    enrichment_available: false,
    note: "",
  },
  items: [
    {
      case_id: "client-1",
      last_email_id: 1,
      last_seen_at: "2026-05-19T10:00:00-04:00",
      account_name: "ACME",
      contact_email: "buyer@acme.cl",
      subject: "Quote follow-up",
      category: "client_opportunity",
      status: "open",
      next_action: "reply",
      equipment_signal: "balance",
      snippet: "preview text",
      gmail_url: null,
    },
    {
      case_id: "supplier-1",
      last_email_id: 2,
      last_seen_at: "2026-05-18T10:00:00-04:00",
      account_name: "IKA",
      contact_email: "beatriz.bonon@ika.net.br",
      subject: "RE: price response",
      category: "supplier_quote_received",
      status: "waiting",
      next_action: "wait",
      equipment_signal: "",
      snippet: "supplier preview",
      gmail_url: null,
    },
    {
      case_id: "pay-1",
      last_email_id: 3,
      last_seen_at: "2026-05-17T10:00:00-04:00",
      account_name: "Banco",
      contact_email: "serviciodetransferencias@bancochile.cl",
      subject: "FACTURA 6",
      category: "payment_admin",
      status: "open",
      next_action: "review",
      equipment_signal: "",
      snippet: "payment preview",
      gmail_url: null,
    },
    {
      case_id: "dhl-1",
      last_email_id: 4,
      last_seen_at: "2026-05-16T10:00:00-04:00",
      account_name: "DHL",
      contact_email: "monica.silva@dhl.com",
      subject: "PROPUESTA COMERCIAL DHL",
      category: "logistics_admin",
      status: "open",
      next_action: "review",
      equipment_signal: "",
      snippet: "logistics preview",
      gmail_url: null,
    },
  ],
};

vi.mock("../api/operatorClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/operatorClient")>();
  return {
    ...actual,
    fetchTodayPanel: vi.fn(),
    fetchWarmCases: vi.fn(),
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

vi.mock("../lib/logo/threeBodyCanvasRunner", () => ({
  startThreeBodyCanvas: vi.fn(() => () => {}),
}));

vi.mock("../api/institutionIntel/adapter", () => ({
  institutionIntelAdapter: {
    getCurrentOpportunities: vi.fn(async () => ({
      availability: { status: "available_empty" as const },
      pageInfo: { page: 1, pageSize: 15, totalItems: 0 },
    })),
    getProcurementStatus: vi.fn(async () => ({
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
      },
      operatorQueueSizes: { current_opportunity_queue: 1 },
      summaryOk: true,
    })),
  },
}));

import { fetchTodayPanel, fetchWarmCases } from "../api/operatorClient";
import { fetchCommercialDealsMirror } from "../api/mirrorCommercialClient";
import { fetchCommercialWorkQueue } from "../api/commercialOperationsClient";
import { fetchCatalogProductsMirror } from "../api/mirrorCatalogClient";
import { catalogListFixture } from "../test/fixtures/catalogMirrorFixtures";

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
  vi.mocked(fetchCatalogProductsMirror).mockResolvedValue(catalogListFixture());
  vi.mocked(fetchCommercialDealsMirror).mockResolvedValue({
    table_available: true,
    read_only: true,
    data_source: "postgres_mirror",
    total: 1,
    limit: 20,
    items: [
      {
        client_org_name: "CEAF",
        supplier_org_name: "SERVA",
        deal_status: "logistics_pending",
        margin_status: "needs_review",
        reconciliation_status: "reconciled",
        freight_status: "pending",
        client_sale_net_clp: 1_260_000,
        client_sale_gross_clp: 1_499_400,
        client_payment_received_clp: 1_499_400,
        supplier_invoice_total_decimal: "363.00",
        supplier_amount_paid_decimal: "218.00",
        margin_net_clp: null,
        margin_pct: null,
        margin_blockers: [],
        updated_at: "2026-05-22T12:00:00+00:00",
        product_lines: [],
      },
    ],
  });
}

async function navigateTo(label: string) {
  const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
  fireEvent.click(within(nav).getByRole("link", { name: label }));
  await waitFor(() => {
    expect(screen.getByRole("heading", { level: 1, name: label })).toBeTruthy();
  });
}

describe("DashboardApp shell (Phase 7B.1)", () => {
  beforeEach(() => {
    vi.stubEnv("MODE", "development");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "");
    window.location.hash = "#/";
    mockAllOk();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.clearAllMocks();
    window.location.hash = "";
  });

  it("sidebar renders exactly the flat V2 IA, in order, with no legacy sections", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    const links = within(nav).getAllByRole("link").map((el) => el.textContent);

    expect(links).toEqual([
      "Inicio",
      "Ventas",
      "Cotizaciones",
      "Clientes",
      "Licitaciones",
      "Proveedores",
      "Pagos y logística",
      "Catálogo",
      "Sistema",
    ]);

    for (const removedLabel of ["Bandeja de revisión", "Negocios", "Prospectos"]) {
      expect(within(nav).queryByRole("link", { name: removedLabel })).toBeNull();
    }
  });

  it("marks active nav item with aria-current", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    expect(within(nav).getByRole("link", { name: "Inicio" }).getAttribute("aria-current")).toBe(
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Sistema" }).getAttribute("aria-current")).toBe(
      null,
    );
  });

  it("can collapse and expand sidebar from top toggle", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    const sidebar = screen.getByTestId("dashboard-sidebar");
    const toggle = screen.getByTestId("sidebar-collapse-toggle");
    expect(sidebar.getAttribute("data-collapsed")).toBe("false");
    expect(toggle.closest("aside")).toBeTruthy();
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(toggle);
    expect(sidebar.getAttribute("data-collapsed")).toBe("true");
    expect(screen.getByTestId("sidebar-collapse-toggle").getAttribute("aria-expanded")).toBe(
      "false",
    );

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));
    expect(sidebar.getAttribute("data-collapsed")).toBe("false");
  });

  it("does not render duplicate OrigenLab branding in header and sidebar", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    expect(screen.queryByTestId("origenlab-logo-animated")).toBeNull();
    expect(screen.getAllByTestId("origenlab-logo-static")).toHaveLength(1);
    expect(screen.getByTestId("origenlab-logo-static").closest("aside")).toBeTruthy();
    expect(screen.getByTestId("operator-center-chip")).toBeTruthy();
  });

  it("does not introduce send or write action buttons in shell", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    expect(screen.queryByRole("button", { name: /Enviar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Aplicar/i })).toBeNull();
    screen.getByTestId("read-only-chip");
  });

  it("Today summary renders queue count cards", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    expect(screen.getAllByText("Qué revisar hoy").length).toBeGreaterThan(0);
    screen.getByText("Clientes por responder");
    screen.getByText("Proveedores pendientes");
    screen.getByText("Negocios en curso");
    screen.getByLabelText(/Oportunidades accionables:/);
    screen.getByLabelText(/Catálogo:/);
    expect(screen.queryByText(/Casos tibios \/ Warm cases/)).toBeNull();
  });

  it("Catalog page renders product table", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    await navigateTo("Catálogo");
    await waitFor(() => {
      expect(screen.getByTestId("catalog-table-footer").textContent).toMatch(/de 9/);
      screen.getByText("CRTOP Lab Reactor OLT-HP-5L");
    });
  });

  it("Bandeja de revisión is still reachable by deep link even though it's hidden from nav", async () => {
    window.location.hash = "#/inbox";
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    await waitFor(() => {
      screen.getByText("buyer@acme.cl");
    });

    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    expect(within(nav).queryByRole("link", { name: "Bandeja de revisión" })).toBeNull();
  });

  it("Suppliers page excludes client opportunities", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    await navigateTo("Proveedores");
    await waitFor(() => {
      expect(screen.getByTestId("suppliers-workspace")).toBeTruthy();
      expect(screen.getByTestId("supplier-detail-title").textContent).toBe("IKA");
    });
    fireEvent.click(screen.getByRole("button", { name: /IKA, 1 caso en espejo/i }));
    await waitFor(() => {
      screen.getByText("beatriz.bonon@ika.net.br");
    });
    expect(screen.queryByText("buyer@acme.cl")).toBeNull();
  });

  it("Payments & logistics excludes supplier and client rows", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    await navigateTo("Pagos y logística");
    await waitFor(() => {
      screen.getByText("serviciodetransferencias@bancochile.cl");
      screen.getByText("monica.silva@dhl.com");
    });
    expect(screen.queryByText("buyer@acme.cl")).toBeNull();
    expect(screen.queryByText("beatriz.bonon@ika.net.br")).toBeNull();
  });

  it("Negocios is still reachable by deep link even though it's hidden from nav", async () => {
    window.location.hash = "#/deals";
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    await waitFor(() => {
      screen.getByText("Negocios comerciales");
      screen.getByText("CEAF");
      screen.getByText("SERVA");
    });

    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    expect(within(nav).queryByRole("link", { name: "Negocios" })).toBeNull();
  });

  it("global Refresh button reloads data", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    vi.mocked(fetchTodayPanel).mockClear();

    const refreshButton = await screen.findByRole("button", { name: "Actualizar" });
    fireEvent.click(refreshButton);

    await waitFor(() => {
      expect(fetchTodayPanel).toHaveBeenCalled();
    });
  });
});
