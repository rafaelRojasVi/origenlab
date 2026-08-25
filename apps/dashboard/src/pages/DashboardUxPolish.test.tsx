import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EquipmentOpportunitiesResponse, WarmCasesResponse } from "../api/commercialTypes";
import type { TodayPanelData } from "../api/operatorTypes";
import { DashboardApp } from "./DashboardApp";

const panel: TodayPanelData = {
  health: {
    ok: true,
    service: "origenlab-api",
    mode: "operator-sqlite-readonly",
    backend: "sqlite",
    postgres_configured: false,
  },
  operator: {
    verdict: "READY",
    sqlite_path: "/hidden/sqlite.db",
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
    count: 5,
    enrichment_available: false,
    note: "",
  },
  items: [
    {
      case_id: "c1",
      last_email_id: 1,
      last_seen_at: "2026-05-19T10:00:00-04:00",
      account_name: "ACME",
      contact_email: "buyer@acme.cl",
      subject: "Quote",
      category: "client_opportunity",
      status: "open",
      next_action: "reply",
      equipment_signal: "",
      snippet: "preview",
      gmail_url: "https://mail.google.com/secret",
    },
    {
      case_id: "s1",
      last_email_id: 2,
      last_seen_at: "2026-05-18T10:00:00-04:00",
      account_name: "SERVA",
      contact_email: "sales@serva.de",
      subject: "SERVA thread",
      category: "supplier_followup",
      status: "waiting",
      next_action: "wait",
      equipment_signal: "",
      snippet: "serva preview",
      gmail_url: null,
    },
    {
      case_id: "s2",
      last_email_id: 3,
      last_seen_at: "2026-05-17T10:00:00-04:00",
      account_name: "IKA",
      contact_email: "beatriz.bonon@ika.net.br",
      subject: "IKA quote",
      category: "supplier_quote_received",
      status: "waiting",
      next_action: "wait",
      equipment_signal: "",
      snippet: "ika preview",
      gmail_url: null,
    },
    {
      case_id: "p1",
      last_email_id: 4,
      last_seen_at: "2026-05-16T10:00:00-04:00",
      account_name: "Banco",
      contact_email: "pay@bancochile.cl",
      subject: "FACTURA 6",
      category: "payment_admin",
      status: "open",
      next_action: "payment_admin",
      equipment_signal: "",
      snippet: "cuenta 1234567890",
      gmail_url: null,
    },
    {
      case_id: "d1",
      last_email_id: 5,
      last_seen_at: "2026-05-15T10:00:00-04:00",
      account_name: "DHL",
      contact_email: "ops@dhl.com",
      subject: "DHL import",
      category: "logistics_admin",
      status: "open",
      next_action: "vendor_logistics",
      equipment_signal: "",
      snippet: "logistics preview",
      gmail_url: null,
    },
  ],
};

const equipmentPayload: EquipmentOpportunitiesResponse = {
  meta: {
    data_source: "active_current_csv",
    read_only: true,
    count: 1,
    source_path: "/secret/path.csv",
    campaign_mode: null,
    reduced_mode: false,
    note: "",
  },
  items: [],
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

vi.mock("../lib/logo/threeBodyCanvasRunner", () => ({
  startThreeBodyCanvas: vi.fn(() => () => {}),
}));

import { fetchEquipmentOpportunities, fetchTodayPanel, fetchWarmCases } from "../api/operatorClient";
import { fetchCommercialDealsMirror } from "../api/mirrorCommercialClient";
import { fetchCommercialWorkQueue } from "../api/commercialOperationsClient";

function mockAll() {
  vi.mocked(
    fetchCommercialWorkQueue,
  ).mockResolvedValue({
    open_tasks: [],
    review_opportunities: [],
    quote_followups: [],
  });
  vi.mocked(fetchTodayPanel).mockResolvedValue(panel);
  vi.mocked(fetchWarmCases).mockResolvedValue(warmPayload);
  vi.mocked(fetchEquipmentOpportunities).mockResolvedValue(equipmentPayload);
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
        deal_status: "open",
        margin_status: "needs_review",
        reconciliation_status: "pending",
        freight_status: "pending",
        client_sale_net_clp: 1_000_000,
        client_sale_gross_clp: 1_200_000,
        client_payment_received_clp: 1_200_000,
        supplier_invoice_total_decimal: "100.00",
        supplier_amount_paid_decimal: "50.00",
        margin_net_clp: null,
        margin_pct: null,
        margin_blockers: ["fx_rate_missing"],
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

describe("Dashboard UX polish (Phase 7B.3 ES)", () => {
  beforeEach(() => {
    window.location.hash = "#/";
    mockAll();
  });

  afterEach(() => {
    window.location.hash = "";
    vi.clearAllMocks();
  });

  it("mantiene marca solo en sidebar sin logo duplicado en header", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    const staticLogo = screen.getByTestId("origenlab-logo-static");
    expect(staticLogo.closest("aside")).toBeTruthy();
    expect(screen.queryByTestId("origenlab-logo-animated")).toBeNull();
    expect(staticLogo.querySelector("canvas")).toBeNull();
    expect(screen.getByText("Panel operador")).toBeTruthy();
    expect(screen.getByTestId("operator-center-chip").textContent).toMatch(/Centro operador/i);
    expect(screen.getByTestId("sidebar-collapse-toggle").closest("aside")).toBeTruthy();
  });

  it("navegación en español", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    expect(within(nav).getByRole("link", { name: "Hoy" })).toBeTruthy();
    expect(within(nav).getByRole("link", { name: "Bandeja de revisión" })).toBeTruthy();
    expect(within(nav).getByRole("link", { name: "Negocios" })).toBeTruthy();
    expect(screen.queryByText(/client_opportunity/)).toBeNull();
  });

  it("tarjetas de Hoy en español navegan", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));

    fireEvent.click(
      screen.getByRole("button", { name: /Proveedores pendientes:/i }),
    );
    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: "Proveedores" })).toBeTruthy();
    });
  });

  it("proveedores agrupados y filas al hacer clic", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    await navigateTo("Proveedores");

    fireEvent.click(screen.getByRole("button", { name: /SERVA, 1 caso en espejo/i }));
    await waitFor(() => {
      screen.getByText("sales@serva.de");
    });
    expect(screen.queryByText("supplier_quote_received")).toBeNull();
  });

  it("página Negocios muestra tarjeta CEAF × SERVA y bloqueos", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    await navigateTo("Negocios");

    const card = screen.getByTestId("deal-highlight-card");
    expect(within(card).getByText(/CEAF/)).toBeTruthy();
    expect(within(card).getByText(/Bloqueos de margen/i)).toBeTruthy();
    expect(screen.queryByText(/1234567890/)).toBeNull();
  });

  it("pagos y logística en dos secciones", async () => {
    render(<DashboardApp />);
    await waitFor(() => screen.getByTestId("operator-verdict-chip"));
    await navigateTo("Pagos y logística");

    screen.getByRole("heading", { name: "Pagos" });
    screen.getByRole("heading", { name: "Logística" });
    screen.getByText("pay@bancochile.cl");
    screen.getByText("ops@dhl.com");
  });
});
