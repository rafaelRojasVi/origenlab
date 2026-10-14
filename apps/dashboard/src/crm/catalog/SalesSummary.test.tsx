import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PriceHistory, QuotedPrice } from "./catalogApi";
import { SalesSummary } from "./SalesSummary";

// Every value is invented.
type Outcome = NonNullable<QuotedPrice["case"]>["outcome"];
const line = (date: string, total: string, number: string, outcome: Outcome | null,
              institution = "Universidad Ficticia"): QuotedPrice => ({
  date, line_total: total, qty: "1", unit_price: total, currency: "CLP", check_status: "verified", client_type: null,
  document_number: number,
  case: outcome ? { id: `case-${number}`, institution, outcome } : null,
});

const HISTORY: PriceHistory = {
  model_key: "SALES1",
  median_last_5: "1200000.0000",
  items: [
    line("2026-06-10", "1200000.0000", "09103-26", null),
    line("2026-05-10", "1300000.0000", "09102-26", "open", "Laboratorio Ficticio"),
    line("2026-04-10", "1000000.0000", "09101-26", "won"),
  ],
  summary: {
    times_quoted: 3, times_won: 1, currency: "CLP", min: "1000000.0000", max: "1300000.0000",
    median: "1200000.0000", last: { date: "2026-06-10", price: "1200000.0000" }, won_prices: ["1000000.0000"],
    institutions: 2,
  },
};

describe("SalesSummary", () => {
  it("leads with the four numbers that matter", () => {
    render(<SalesSummary history={HISTORY} />);
    const stats = screen.getByTestId("sales-stats");
    expect(stats).toHaveTextContent("Cotizado3 veces");
    expect(stats).toHaveTextContent("Vendido1 vez");
    expect(stats).toHaveTextContent("$1.000.000");
    expect(stats).toHaveTextContent("Último precio$1.200.000");
    expect(stats).toHaveTextContent("$1.000.000 – $1.300.000");
  });

  it("charts each quoted price, the won one filled and named, with a legend", () => {
    render(<SalesSummary history={HISTORY} />);
    const chart = screen.getByRole("img", { name: /Precio cotizado en el tiempo/ });
    expect(within(chart).getAllByTestId("price-dot")).toHaveLength(3);
    expect(within(chart).getByTestId("price-dot-won")).toBeInTheDocument();
    const legend = chart.closest("figure")!.querySelector("figcaption")!;
    expect(legend).toHaveTextContent("Vendida");
    expect(legend).toHaveTextContent("Cotizada");
  });

  it("lists every quote with its institution, a link to its case and the outcome in words", () => {
    render(<SalesSummary history={HISTORY} />);
    const list = screen.getByTestId("quoted-history");
    const won = within(list).getByText(/09101-26/).closest("li")!;
    expect(won).toHaveTextContent("Vendida");
    expect(within(won).getByRole("link", { name: "Universidad Ficticia" })).toHaveAttribute("href", "#/crm/oportunidades/case-09101-26");
    expect(within(list).getByText(/09102-26/).closest("li")).toHaveTextContent("Abierta");
    expect(within(list).getByText(/09103-26/).closest("li")).toHaveTextContent("Sin caso");
  });

  it("says so plainly when the model was never quoted", () => {
    render(<SalesSummary history={{ model_key: "X", items: [], median_last_5: null,
      summary: { times_quoted: 0, times_won: 0, currency: null, min: null, max: null, median: null, last: null,
        won_prices: [], institutions: 0 } }} />);
    expect(screen.getByText("Este modelo no aparece en cotizaciones anteriores.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });
});

describe("SalesSummary without a summary (older API)", () => {
  it("still lists the quotes", () => {
    render(<SalesSummary history={{ ...HISTORY, summary: undefined }} />);
    expect(screen.queryByTestId("sales-stats")).toBeNull();
    expect(within(screen.getByTestId("quoted-history")).getAllByRole("listitem")).toHaveLength(3);
  });
});
