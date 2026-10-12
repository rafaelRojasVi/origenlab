import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { QuoteReadingsResponse } from "../crmTypes";

// Every value below is invented; the repository is public.
const RESPONSE: QuoteReadingsResponse = {
  items: [
    { quote_number: "00999-26", revision_no: 1, sent_at: "2026-10-08T12:00:00Z", code: "net_mismatch",
      reader_version: "quote_read:v1", recorded_at: "2026-10-12T10:00:00Z", opportunity_id: "o-1", case_title: "Caso ficticio" },
    { quote_number: "00998-26", revision_no: 2, sent_at: null, code: "not_origenlab_quote",
      reader_version: "quote_read:v1", recorded_at: "2026-10-12T09:00:00Z", opportunity_id: "o-2", case_title: null },
  ],
};

vi.mock("../crmApi", () => ({ fetchQuoteReadings: vi.fn(async () => RESPONSE) }));

describe("Cotizaciones no leídas", () => {
  it("lists each refused quotation with its reason in Spanish and a link to its case", async () => {
    const { QuoteReadingsPanel } = await import("./QuoteReadingsPanel");
    render(<QuoteReadingsPanel />);
    const rows = await screen.findAllByTestId("quote-reading-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("00999-26 r1");
    expect(rows[0]).toHaveTextContent("El neto no cuadra con las líneas");
    expect(within(rows[0]).getByRole("link", { name: /Caso ficticio/ })).toHaveAttribute("href", "#/crm/oportunidades/o-1");
    expect(rows[1]).toHaveTextContent("No parece una cotización de OrigenLab");
  });
});
