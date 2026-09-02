import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CotizacionesPage } from "./CotizacionesPage";
import * as client from "../api/customerQuoteClient";
import { globalQuoteItemFixture } from "../test/fixtures/customerQuoteFixtures";

vi.mock("../api/customerQuoteClient");

describe("CotizacionesPage", () => {
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
});
