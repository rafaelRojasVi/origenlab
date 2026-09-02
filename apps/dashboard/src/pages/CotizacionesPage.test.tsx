import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CotizacionesPage } from "./CotizacionesPage";
import * as client from "../api/customerQuoteClient";
import * as opsClient from "../api/commercialOperationsClient";
import {
  drivePendingQuoteItemFixture,
  globalQuoteItemFixture,
} from "../test/fixtures/customerQuoteFixtures";

vi.mock("../api/customerQuoteClient");
vi.mock("../api/commercialOperationsClient");

describe("CotizacionesPage", () => {
  beforeEach(() => {
    vi.mocked(opsClient.fetchSalesOpportunities).mockReset().mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchCustomerQuotesGlobal).mockReset();
    vi.mocked(client.fetchDrivePendingQuotes).mockReset().mockResolvedValue({
      meta: { count: 0 },
      items: [],
    });
    vi.mocked(client.fetchCustomerQuote).mockReset();
    vi.mocked(client.createCustomerQuote).mockReset();
    vi.mocked(opsClient.createManualSalesOpportunity).mockReset();
  });

  it("renders the durable global queue, not a placeholder", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByText("01183-26"));
    screen.getByText("CEAF");
    expect(screen.queryByText(/próximamente/)).toBeNull();
    expect(screen.queryByText(/vive dentro de su oportunidad/)).toBeNull();
  });

  it("shows an honest empty state when there are no quotes yet", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));
  });

  it("filters the visible rows by search text against quote/document number and customer", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("01183-26"));

    const search = screen.getByRole("searchbox", { name: /buscar/i });
    fireEvent.change(search, { target: { value: "no-match" } });
    await waitFor(() => screen.getByText("Sin resultados para estos filtros"));
  });

  it("does not render KPI cards above the queue — it is a work list, not a report", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("01183-26"));

    expect(screen.queryAllByRole("table")).toHaveLength(1);
  });

  it("opens the detail drawer on row click and shows the quote number", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: globalQuoteItemFixture().quote });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("01183-26"));

    fireEvent.click(screen.getByRole("button", { name: /01183-26/ }));
    await waitFor(() => screen.getByRole("dialog"));
  });

  it("'Ver en Ventas' from the drawer passes the exact opportunity id through", async () => {
    const fixture = globalQuoteItemFixture();
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [fixture],
    });
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: fixture.quote });
    const onOpenVentas = vi.fn();

    render(<CotizacionesPage onOpenVentas={onOpenVentas} />);
    await waitFor(() => screen.getByText("01183-26"));

    fireEvent.click(screen.getByRole("button", { name: /01183-26/ }));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: "Ver en Ventas" }));

    expect(onOpenVentas).toHaveBeenCalledWith(fixture.quote.sales_opportunity_id);
  });

  it("Nueva Cotización opens the create dialog, which allocates nothing on open", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));

    fireEvent.click(screen.getByRole("button", { name: "Nueva Cotización" }));
    await waitFor(() => screen.getByRole("dialog"));
    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(client.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("a successful create closes the dialog, opens the real created quote's drawer (no placeholder), and background-refetches the queue", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(opsClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [
        {
          sales_opportunity_id: "sales_" + "c".repeat(32),
          source_kind: "manual",
          source_opportunity_id: "sales_" + "c".repeat(32),
          account_id: null,
          primary_contact_id: null,
          organization_id: null,
          primary_crm_contact_id: null,
          title: "Reactor CEAF",
          stage: "quoting",
          owner_key: "op@origenlab.cl",
          version: 1,
          created_by: "op@origenlab.cl",
          updated_by: "op@origenlab.cl",
          created_at: "2026-08-30T10:00:00Z",
          updated_at: "2026-08-30T10:00:00Z",
          stage_updated_at: "2026-08-30T10:00:00Z",
          contact_display_email: null,
          account_display_domain: null,
          organization_display_name: "CEAF",
          contact_display_name: "Tatiana Rojas",
          contact_primary_email: "tatiana@ceaf.cl",
          open_task_count: 0,
          next_task_id: null,
          next_task_title: null,
          next_task_due_at: null,
        },
      ],
    });
    const createdQuote = { ...globalQuoteItemFixture().quote, quote_number: "01186-26", sales_opportunity_id: "sales_" + "c".repeat(32) };
    vi.mocked(client.createCustomerQuote).mockResolvedValue(createdQuote);
    vi.mocked(client.fetchCustomerQuote).mockResolvedValue({ item: createdQuote });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));

    fireEvent.click(screen.getByRole("button", { name: "Nueva Cotización" }));
    await waitFor(() => screen.getByText("Reactor CEAF"));
    fireEvent.click(screen.getByRole("button", { name: /Reactor CEAF/ }));
    fireEvent.click(screen.getByRole("button", { name: /Crear cotización/ }));

    await waitFor(() => {
      expect(screen.queryByTestId("nueva-cotizacion-dialog")).toBeNull();
    });
    await waitFor(() => screen.getByText("01186-26"));
    expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledTimes(2);
  });

  it("shows the 3 live Drive Pendientes rows instead of the empty state when the CRM list is empty", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 3 },
      items: [
        drivePendingQuoteItemFixture({ folder_id: "f1", folder_name: "CN01191-ICN Chile", document_identifier: "CN01191" }),
        drivePendingQuoteItemFixture({
          folder_id: "f2",
          folder_name: "CN01190-Prof. Dr. Juan Matos Lale – Universidad Autónoma- UP400St",
          document_identifier: "CN01190",
        }),
        drivePendingQuoteItemFixture({ folder_id: "f3", folder_name: "CN1185 — Gustavo Zúñiga - UP200St", document_identifier: "CN1185" }),
      ],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByText("CN01191-ICN Chile"));
    expect(screen.queryByText("Aún no hay cotizaciones")).toBeNull();
    expect(screen.getAllByText("Pendiente en Drive")).toHaveLength(3);
  });

  it("the empty state only fires when both the CRM list and the Drive-pending list are empty", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));
  });

  it("excludes a Drive folder already owned by a durable CRM quote (server-side dedup)", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [globalQuoteItemFixture()],
    });
    // The server excludes CRM-owned folders; the client renders exactly
    // what it's given, so a Drive-only fixture is simply absent here.
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);

    await waitFor(() => screen.getByText("01183-26"));
    expect(screen.queryByText("Pendiente en Drive")).toBeNull();
  });

  it("'Recargar cotizaciones' refreshes both the CRM list and the Drive-pending list", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 0 },
      items: [],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("Aún no hay cotizaciones"));

    fireEvent.click(screen.getByRole("button", { name: /recargar cotizaciones/i }));

    await waitFor(() => {
      expect(client.fetchCustomerQuotesGlobal).toHaveBeenCalledTimes(2);
      expect(client.fetchDrivePendingQuotes).toHaveBeenCalledTimes(2);
    });
  });

  it("clicking a Drive-only row's 'Abrir carpeta' never opens the CRM quote drawer", async () => {
    vi.mocked(client.fetchCustomerQuotesGlobal).mockResolvedValue({
      meta: { count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(client.fetchDrivePendingQuotes).mockResolvedValue({
      meta: { count: 1 },
      items: [drivePendingQuoteItemFixture()],
    });

    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    await waitFor(() => screen.getByText("Pendiente en Drive"));

    fireEvent.click(screen.getByRole("link", { name: "Abrir carpeta" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
