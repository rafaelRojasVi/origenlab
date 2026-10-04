import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OpportunityCardData, RevisionCard } from "../crmTypes";
import type { FxResponse } from "../fx";
import { OverviewPage } from "./OverviewPage";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-03-31T15:00:00Z");
const SHA = "a".repeat(64);

const FX: FxResponse = {
  source: "mindicador.cl",
  source_label: "Banco Central de Chile, vía mindicador.cl",
  source_url: "https://mindicador.cl",
  rates: [
    { code: "USD", label: "Dólar observado", clp: 950.5, as_of: "2026-03-30" },
    { code: "EUR", label: "Euro", clp: 1050.25, as_of: "2026-03-30" },
    { code: "UF", label: "UF", clp: 40000, as_of: "2026-03-31" },
  ],
  fetched_at: "2026-03-31T14:00:00+00:00",
  stale: false,
};

function revision(sentAt: string, quoteNumber: string): RevisionCard {
  return {
    revision_id: `r-${quoteNumber}`,
    revision_no: 1,
    status: "sent",
    origin: "historical_import",
    sent_at: sentAt,
    superseded_by_revision_no: null,
    is_active: true,
    document: { sha256: SHA, filename: `CN${quoteNumber}.pdf` },
    gmail: { source_record_id: "s1", message_id: "gm1", thread_id: "th1", url: "https://mail.google.com/mail/u/0/#all/gm1", subject: "Cotización" },
    drive: {
      source: "archive_ledger",
      ledger: "run-1",
      document_sha256: SHA,
      file_id: "file-1",
      file_url: "https://drive.google.com/file/d/file-1/view",
      folder_id: "folder-1",
      folder_url: "https://drive.google.com/drive/folders/folder-1",
      case_key: "case-1",
      quote_number: quoteNumber,
      revision: 1,
      original_filename: `CN${quoteNumber}.pdf`,
      archive_status: "archived_verified",
    },
    quote_number: quoteNumber,
  };
}

function card(id: string, org: string, sentAt: string, quoteNumber: string): OpportunityCardData {
  const latest = revision(sentAt, quoteNumber);
  return {
    opportunity_id: id,
    title: `Cotización ${quoteNumber}`,
    stage: "quoting",
    created_at: null,
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: { organization_id: `org-${id}`, name: org, confirmation: "confirmed" },
    other_organizations: [],
    contact: { source: "gmail_recipient", name: "Persona Ejemplo", address: "persona@ejemplo.invalid", others: 0 },
    quotes: [{ quote_id: `q-${id}`, quote_number: quoteNumber, number_origin: "printed_historical", revisions: [latest] }],
    quote_numbers: [quoteNumber],
    revision_count: 1,
    latest_revision: latest,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "", source: "suggested", due_at: null },
  };
}

const PIPELINE = {
  items: [
    card("11111111-1111-4111-8111-111111111111", "Laboratorio Andino", "2026-03-20T12:00:00Z", "01020-26"),
    card("22222222-2222-4222-8222-222222222222", "Universidad del Valle", "2026-02-10T12:00:00Z", "01001-26"),
  ],
  total: 2,
  drive_configured: true,
};

function respond(routes: Record<string, unknown>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      calls.push(path);
      const body = routes[path];
      if (body === undefined) return Promise.resolve(new Response("{}", { status: 404 }));
      if (body instanceof Response) return Promise.resolve(body);
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }),
  );
  return calls;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Resumen", () => {
  it("shows the day's dólar, euro and UF in pesos with their date and source", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    const usd = await screen.findByTestId("fx-USD");
    expect(within(usd).getByText("Dólar observado")).toBeInTheDocument();
    expect(usd).toHaveTextContent("$950,50");
    expect(screen.getByTestId("fx-EUR")).toHaveTextContent("$1.050,25");
    expect(screen.getByText(/Banco Central de Chile, vía mindicador\.cl/)).toBeInTheDocument();
  });

  it("converts an amount typed the Chilean way into pesos", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    const amount = await screen.findByLabelText("Monto");
    fireEvent.change(amount, { target: { value: "1.250" } });
    expect(screen.getByTestId("fx-result")).toHaveTextContent("$1.188.125");
    fireEvent.click(screen.getByRole("radio", { name: "EUR" }));
    expect(screen.getByTestId("fx-result")).toHaveTextContent("$1.312.813");
  });

  it("groups open quotes by days since sent, each row linking to its case, PDF and email", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    const navigate = vi.fn();
    render(<OverviewPage navigate={navigate} />);
    const followUp = await screen.findByTestId("followups-follow_up");
    expect(within(followUp).getByText("Laboratorio Andino")).toBeInTheDocument();
    expect(within(followUp).getByText("01020-26")).toBeInTheDocument();
    expect(within(followUp).getByText("hace 11 días")).toBeInTheDocument();
    expect(within(followUp).getByRole("link", { name: /PDF/ })).toHaveAttribute("href", "https://drive.google.com/file/d/file-1/view");
    expect(within(followUp).getByRole("link", { name: /Correo/ })).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#all/gm1");
    fireEvent.click(within(followUp).getByRole("button", { name: /Laboratorio Andino/ }));
    expect(navigate).toHaveBeenCalledWith("oportunidades", "11111111-1111-4111-8111-111111111111");

    expect(within(screen.getByTestId("followups-older")).getByText("Universidad del Valle")).toBeInTheDocument();
  });

  it("says plainly when nothing was sent this week and how far the data reaches", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    expect(await screen.findByTestId("followups-this_week")).toHaveTextContent(/Ninguna cotización enviada en los últimos 7 días/);
    expect(screen.getByTestId("data-freshness")).toHaveTextContent(/Datos hasta el 20.+2026/);
    expect(screen.getByTestId("data-freshness")).toHaveTextContent("2 casos con cotización abierta");
  });

  it("keeps the follow-ups when the exchange rate cannot be read", async () => {
    respond({ "/v2/workspace/fx": new Response("{}", { status: 503 }), "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    expect(await screen.findByText("Laboratorio Andino")).toBeInTheDocument();
    expect(await screen.findByText(/Tipo de cambio no disponible/)).toBeInTheDocument();
  });

  it("marks figures kept from an earlier refresh", async () => {
    respond({ "/v2/workspace/fx": { ...FX, stale: true }, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    expect(await screen.findByText(/no se pudo actualizar/i)).toBeInTheDocument();
  });

  it("does not ask for the data-health counts, the slowest read", async () => {
    const calls = respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    await screen.findByText("Laboratorio Andino");
    expect(calls).not.toContain("/v2/workspace/overview");
  });
});
