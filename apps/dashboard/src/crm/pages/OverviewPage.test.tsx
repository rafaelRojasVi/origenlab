import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

// Andino is a decided case (in «Conversación»); Valle is still the historical import's trace.
const PIPELINE = {
  items: [
    { ...card("11111111-1111-4111-8111-111111111111", "Laboratorio Andino", "2026-03-20T12:00:00Z", "01020-26"), stage: "negotiating" },
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

// A case folder's name as filed on Drive (kept out of a `…key: "…"` literal, which the secret scanner
// reads as a credential).
const FOLDER_NAME = "CN01022-Persona Ejemplo – Agrícola Norte";

const DRIVE = {
  source: "archive_ledger",
  ledgers: ["run-1"],
  folders: [
    { folder_id: "f1", folder_url: null, case_key: FOLDER_NAME, documents: [],
      quote_numbers: ["01022-26"], in_crm: 0, organization_name: null },
  ],
  totals: { folders: 1, documents: 1, in_crm: 0, not_in_crm: 1 },
};

describe("Resumen · número de cotización", () => {
  it("shows the last number the system knows and the next one", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/drive": DRIVE });
    render(<OverviewPage navigate={() => undefined} />);
    const box = await screen.findByTestId("quote-number-box");
    await waitFor(() => expect(within(box).getByTestId("quote-last")).toHaveTextContent("01022-26"));
    expect(within(box).getByTestId("quote-last")).toHaveTextContent("Agrícola Norte · Drive");
    expect(within(box).getByTestId("quote-next")).toHaveTextContent("01023-26");
  });

  it("says a typed number is already used, and by whom", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/drive": DRIVE });
    render(<OverviewPage navigate={() => undefined} />);
    const input = await screen.findByLabelText("¿Ya existe este número?");
    await waitFor(() => expect(screen.getByTestId("quote-last")).toHaveTextContent("01022-26"));
    fireEvent.change(input, { target: { value: "1020" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/Ya usado: 01020-26 · Laboratorio Andino · CRM/);
    fireEvent.change(input, { target: { value: "01500-26" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/No está en el CRM ni en el archivo de Drive/);
    fireEvent.change(input, { target: { value: "hola" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/Escribe un número/);
  });

  it("counts the numbers Gmail already shows as sent", async () => {
    const mail = { items: [{ quote_number: "CN01030", first_seen_at: "2026-03-30T14:00:00Z", messages: 2 }] };
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/drive": DRIVE, "/v2/workspace/mail-quote-numbers": mail });
    render(<OverviewPage navigate={() => undefined} />);
    const input = await screen.findByLabelText("¿Ya existe este número?");
    await waitFor(() => expect(screen.getByTestId("quote-last")).toHaveTextContent("CN01030"));
    expect(screen.getByTestId("quote-last")).toHaveTextContent("Gmail");
    expect(screen.getByTestId("quote-next")).toHaveTextContent("01031-26");
    fireEvent.change(input, { target: { value: "1030" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/Ya usado: CN01030 · Gmail/);
    fireEvent.change(input, { target: { value: "01500-26" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/No está en el CRM, en Drive ni en Gmail/);
  });

  it("still answers from the CRM and Drive when Gmail numbers cannot be read, and never says free", async () => {
    respond({
      "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/drive": DRIVE,
      "/v2/workspace/mail-quote-numbers": new Response("{}", { status: 500 }),
    });
    render(<OverviewPage navigate={() => undefined} />);
    const input = await screen.findByLabelText("¿Ya existe este número?");
    await waitFor(() => expect(screen.getByTestId("quote-last")).toHaveTextContent("01022-26"));
    await waitFor(() => expect(screen.getByTestId("quote-number-box")).toHaveTextContent(/números de Gmail no se pudieron leer/));
    expect(screen.getByTestId("quote-next")).toHaveTextContent("01023-26");
    fireEvent.change(input, { target: { value: "01500-26" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/no se pudieron leer/);
    expect(screen.getByTestId("quote-check")).not.toHaveTextContent(/libre/i);
  });

  it("still answers from the CRM when the Drive archive cannot be read, and says so", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    await waitFor(() => expect(screen.getByTestId("quote-last")).toHaveTextContent("01020-26"));
    expect(screen.getByTestId("quote-number-box")).toHaveTextContent(/sin el archivo de Drive/);
  });
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

  it("says an older figure is still the one in force today, and when it was published", async () => {
    // NOW is Tuesday 31 March; the dollar and euro were last published on Monday 30.
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    const usd = await screen.findByTestId("fx-USD");
    expect(usd).toHaveTextContent(/vigente hoy/i);
    expect(usd).toHaveTextContent(/publicado el lun 30 mar/i);
    expect(screen.getByTestId("fx-weekend-note")).toHaveTextContent(/fines de semana ni feriados/);
  });

  it("says «publicado hoy» when the figure is today's", async () => {
    const today = { ...FX, rates: FX.rates.map((r) => ({ ...r, as_of: "2026-03-31" })) };
    respond({ "/v2/workspace/fx": today, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    expect(await screen.findByTestId("fx-USD")).toHaveTextContent(/publicado hoy/i);
    expect(screen.queryByTestId("fx-weekend-note")).toBeNull();
  });

  it("shows today's date and the time in Santiago", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    const clock = await screen.findByTestId("resumen-clock");
    expect(clock).toHaveTextContent(/Martes, 31 de marzo/); // only the first letter capitalised
    expect(clock).toHaveTextContent("12:00");
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

  it("puts a decided case in the 3 · 14 · 30 rhythm, linking to its case and email, and counts the historical ones apart", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    const navigate = vi.fn();
    render(<OverviewPage navigate={navigate} />);
    expect(await screen.findByRole("heading", { name: "Hoy" })).toBeInTheDocument();
    const followUps = await screen.findByTestId("today-followups");
    const first = within(followUps).getByTestId("today-rhythm-primero");
    expect(first).toHaveTextContent(/Primer seguimiento\s*1/);
    expect(first).toHaveTextContent("11días");
    // The thread, opened as the shared mailbox — not as whichever account the browser lists first.
    expect(within(first).getByRole("link", { name: /Responder en Gmail/ })).toHaveAttribute(
      "href",
      "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/gm1",
    );
    fireEvent.click(within(first).getByRole("button", { name: "Laboratorio Andino" }));
    expect(navigate).toHaveBeenCalledWith("oportunidades", "11111111-1111-4111-8111-111111111111");
    // Valle is not chased here: it is decided in bulk first.
    expect(within(followUps).queryByText("Universidad del Valle")).not.toBeInTheDocument();
    expect(screen.getByTestId("today-historical")).toHaveTextContent("1 caso por decidir");
    expect(within(screen.getByTestId("today-stats")).getByText("seguimientos").previousSibling).toHaveTextContent("1");
  });

  it("lists today's tasks and the clients who answered", async () => {
    const [andino, valle] = PIPELINE.items;
    respond({
      "/v2/workspace/fx": FX,
      "/v2/workspace/pipeline": {
        ...PIPELINE,
        items: [
          {
            ...andino,
            open_tasks: [{ task_id: "t-1", title: "Llamar por la balanza", due_at: "2026-03-30T12:00:00Z", version: 1, owner: "Ventas" }],
          },
          {
            ...valle,
            stage: "negotiating",
            last_contact: { outbound: null, inbound: { at: "2026-03-25T12:00:00Z", subject: "Re: cotización", url: "https://mail.google.com/mail/u/0/#all/in1" } },
          },
        ],
      },
    });
    render(<OverviewPage navigate={() => undefined} />);
    const task = await screen.findByTestId("today-task-t-1");
    expect(task).toHaveTextContent("Llamar por la balanza");
    expect(task).toHaveTextContent("Atrasada 1 día");
    const replies = screen.getByText("Te toca responder").closest("section") as HTMLElement;
    expect(within(replies).getByText("Universidad del Valle")).toBeInTheDocument();
    expect(within(replies).getByRole("link", { name: /Abrir respuesta/ })).toHaveAttribute("href", "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/in1");
    // A case with an open task is planned: it is not also a follow-up.
    expect(screen.getByTestId("today-followups")).toHaveTextContent("Ningún seguimiento pendiente.");
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
