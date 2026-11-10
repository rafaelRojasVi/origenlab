import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
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
    { ...card("11111111-1111-4111-8111-111111111111", "Laboratorio Andino", "2026-03-28T12:00:00Z", "01020-26"), stage: "negotiating" },
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
    // The check panel is behind a toggle button — open it first.
    await waitFor(() => expect(screen.getByTestId("quote-last")).toHaveTextContent("01022-26"));
    fireEvent.click(screen.getByRole("button", { name: /ya existe/i }));
    const input = await screen.findByLabelText("¿Ya existe este número?");
    fireEvent.change(input, { target: { value: "1020" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/Ya usado: 01020-26 · Laboratorio Andino · CRM/);
    fireEvent.change(input, { target: { value: "01500-26" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/No está en el CRM ni en Drive/);
    fireEvent.change(input, { target: { value: "hola" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/Escribe un número/);
  });

  it("counts the numbers Gmail already shows as sent", async () => {
    const mail = { items: [{ quote_number: "CN01030", first_seen_at: "2026-03-30T14:00:00Z", messages: 2 }] };
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/drive": DRIVE, "/v2/workspace/mail-quote-numbers": mail });
    render(<OverviewPage navigate={() => undefined} />);
    await waitFor(() => expect(screen.getByTestId("quote-last")).toHaveTextContent("CN01030"));
    // Open the check panel.
    fireEvent.click(screen.getByRole("button", { name: /ya existe/i }));
    const input = await screen.findByLabelText("¿Ya existe este número?");
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
    await waitFor(() => expect(screen.getByTestId("quote-last")).toHaveTextContent("01022-26"));
    // The error note appears without opening the check panel.
    await waitFor(() => expect(screen.getByTestId("quote-number-box")).toHaveTextContent(/números de Gmail no se pudieron leer/));
    expect(screen.getByTestId("quote-next")).toHaveTextContent("01023-26");
    // Open the check panel to test the result text.
    fireEvent.click(screen.getByRole("button", { name: /ya existe/i }));
    const input = await screen.findByLabelText("¿Ya existe este número?");
    fireEvent.change(input, { target: { value: "01500-26" } });
    expect(screen.getByTestId("quote-check")).toHaveTextContent(/no se pudieron leer/);
    expect(screen.getByTestId("quote-check")).not.toHaveTextContent(/libre/i);
  });

  it("still answers from the CRM when the Drive archive cannot be read, and says so", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    await waitFor(() => expect(screen.getByTestId("quote-last")).toHaveTextContent("01020-26"));
    // The error note is always visible (no need to open the check panel).
    await waitFor(() => expect(screen.getByTestId("quote-number-box")).toHaveTextContent(/sin el archivo de Drive/i));
  });
});

describe("Resumen", () => {
  it("shows the day's dólar, euro and UF in pesos with their source (Hoy v3: tools bar, inline rates)", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    // FX loads lazily (after requestAnimationFrame); each currency has its own testid span.
    const usd = await screen.findByTestId("fx-USD");
    expect(usd).toHaveTextContent("$950,50");
    expect(screen.getByTestId("fx-EUR")).toHaveTextContent("$1.050,25");
    // Source attribution is in the moneda card footer.
    expect(screen.getByText(/Banco Central de Chile/)).toBeInTheDocument();
  });

  it("shows «no se pudo actualizar» when the figure is stale (Hoy v3: compact, no publication date per tile)", async () => {
    // NOW is Tuesday 31 March; the dollar and euro were last published on Monday 30.
    // Hoy v3 compact rates don't show «vigente hoy» or «publicado el lun» per tile.
    // The stale flag is shown as a warning in the footer when stale=true.
    const staleData = { ...FX, stale: true };
    respond({ "/v2/workspace/fx": staleData, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    await screen.findByTestId("fx-USD");
    expect(screen.getByText(/no se pudo actualizar/i)).toBeInTheDocument();
  });

  it("does not show stale warning when the figure is fresh", async () => {
    const today = { ...FX, rates: FX.rates.map((r) => ({ ...r, as_of: "2026-03-31" })) };
    respond({ "/v2/workspace/fx": today, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    await screen.findByTestId("fx-USD");
    expect(screen.queryByText(/no se pudo actualizar/i)).not.toBeInTheDocument();
  });

  it("shows today's date in Santiago (Hoy v3: quiet one-line header, no time)", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    const hero = await screen.findByTestId("hoy-mini-header");
    expect(hero).toHaveTextContent(/Martes 31 de marzo/); // only the first letter capitalised
    // Hoy v3 mini header shows the date only, not the time.
  });

  it("counts «N cosas para hoy» from the same queue as «Para hoy», not every open case", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    const header = await screen.findByTestId("hoy-mini-header");
    await waitFor(() => expect(header).toHaveTextContent(/cosas? para hoy/));
    const total = Number(/(\d+) cosas? para hoy/.exec(header.textContent ?? "")?.[1]);
    const todo = screen.queryByRole("button", { name: /^Todo\b/ });
    expect(total).toBe(todo ? Number(/\d+/.exec(todo.textContent ?? "")?.[0]) : 0);
  });

  it("converts an amount typed the Chilean way into pesos", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE });
    render(<OverviewPage navigate={() => undefined} />);
    // The converter is behind the [Convertir ▾] button — open it first.
    await screen.findByTestId("fx-USD");
    fireEvent.click(screen.getByRole("button", { name: /Convertir moneda/ }));
    const amount = await screen.findByLabelText("Monto");
    fireEvent.change(amount, { target: { value: "1.250" } });
    expect(screen.getByTestId("fx-result")).toHaveTextContent("$1.188.125");
    fireEvent.click(screen.getByRole("radio", { name: "EUR" }));
    expect(screen.getByTestId("fx-result")).toHaveTextContent("$1.312.813");
  });

  it("puts a decided case in the 3 · 7 · 10 · 15 rhythm, linking to its case and email, and counts the historical ones apart", async () => {
    const ANDINO = "11111111-1111-4111-8111-111111111111";
    respond({
      "/v2/workspace/fx": FX,
      "/v2/workspace/pipeline": PIPELINE,
      [`/v2/cockpit/opportunities/${ANDINO}/timeline`]: { opportunity_id: ANDINO, entries: [] },
      [`/v2/workspace/opportunities/${ANDINO}/notes`]: { opportunity_id: ANDINO, notes: [] },
      [`/v2/workspace/opportunities/${ANDINO}/threads`]: { opportunity_id: ANDINO, threads: [] },
    });
    const navigate = vi.fn();
    render(<OverviewPage navigate={navigate} />);
    // Hoy v3: quiet mini-header with date, no «HOY» title.
    expect(await screen.findByTestId("hoy-mini-header")).toHaveTextContent(/Martes 31 de marzo/);
    // Andino is 3 days old (sentAt "2026-03-28") → exactly the first-follow-up threshold.
    const paraTi = await screen.findByTestId("today-para-ti");
    const row = within(paraTi).getByTestId("today-followup-11111111-1111-4111-8111-111111111111");
    expect(row).toHaveTextContent("3");
    // The thread, opened as the shared mailbox — not as whichever account the browser lists first.
    // The follow-up's one button is «Escribir» (approved design 2026-10-15), still the shared-mailbox thread.
    expect(within(row).getByRole("link", { name: /Escribir en Gmail/ })).toHaveAttribute(
      "href",
      "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/gm1",
    );
    // The case opens in its drawer, over Hoy.
    fireEvent.click(within(row).getByRole("button", { name: "Laboratorio Andino" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Laboratorio Andino");
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" });
    // Valle is historical (undecided) → not in the queue.
    expect(within(paraTi).queryByText("Universidad del Valle")).not.toBeInTheDocument();
    // Hoy v3: HousekeepingLink removed — «N cosas por ordenar» no longer on Hoy.
    expect(screen.queryByTestId("today-housekeeping")).not.toBeInTheDocument();
    expect(screen.queryByTestId("today-stats")).not.toBeInTheDocument();
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
    const paraTi = screen.getByTestId("today-para-ti");
    expect(within(paraTi).getByText("Universidad del Valle")).toBeInTheDocument();
    expect(within(paraTi).getByRole("link", { name: /Responder en Gmail/ })).toHaveAttribute("href", "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/in1");
    // A case with an open task is planned: it does not appear separately as a follow-up.
    expect(screen.queryByTestId("today-followups")).not.toBeInTheDocument();
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

describe("Hoy · lecturas en paralelo", () => {
  it("fetches pipeline and FX on load; no person-suggestions (Hoy v3 removed it)", async () => {
    // Hoy v3: OverviewPage no longer pre-fetches /person-suggestions; TodayBody handles it via its own AuthoringPanel.
    let releasePipeline: (r: Response) => void = () => undefined;
    const held = new Promise<Response>((resolve) => { releasePipeline = resolve; });
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      calls.push(path);
      if (path === "/v2/workspace/pipeline") return held;
      return Promise.resolve(new Response("{}", { status: 404 }));
    }));
    render(<OverviewPage navigate={() => undefined} />);
    // Pipeline is fetched immediately.
    await waitFor(() => expect(calls.some((p) => p.endsWith("/pipeline"))).toBe(true));
    // Person-suggestions is no longer fetched by OverviewPage.
    expect(calls.some((p) => p.endsWith("/person-suggestions"))).toBe(false);
    releasePipeline(new Response(JSON.stringify(PIPELINE), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
});

describe("Hoy · orden", () => {
  it("shows the quote number and the rates in the tools bar above the queue (not buried at the bottom)", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/drive": DRIVE });
    render(<OverviewPage navigate={() => undefined} />);
    // Hoy v3: quote-number-box and FX are in the tools bar above the queue, not in the aside.
    const box = await screen.findByTestId("quote-number-box");
    expect(box).toBeInTheDocument();
    // FX loads lazily — wait for it.
    const usd = await screen.findByTestId("fx-USD");
    expect(usd).toBeInTheDocument();
    // Both are NOT inside the complementary aside (which only holds Pendientes + WebStats).
    const side = screen.getByRole("complementary");
    expect(side).not.toContainElement(box);
    expect(side).not.toContainElement(usd);
  });


  it("puts the tools bar above the queue — rates and quote number precede «Para hoy»", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/drive": DRIVE });
    render(<OverviewPage navigate={() => undefined} />);
    // Hoy v3: tools bar (rates, quote number) is above the unified queue.
    const heading = await screen.findByRole("heading", { level: 2, name: "Para hoy" });
    const usd = await screen.findByTestId("fx-USD");
    const quotes = await screen.findByTestId("quote-number-box");
    // Tools bar comes before the queue in the document.
    expect(heading.compareDocumentPosition(usd) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(heading.compareDocumentPosition(quotes) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });
});

describe("Hoy · housekeeping", () => {
  it("Hoy v3: housekeeping link removed — no «N cosas por ordenar» on Hoy", async () => {
    respond({
      "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/drive": DRIVE,
    });
    render(<OverviewPage navigate={() => undefined} />);
    await screen.findByTestId("hoy-mini-header");
    // «Correos sin caso» moved to Operaciones → not on «Hoy».
    expect(screen.queryByText("Correos sin caso")).not.toBeInTheDocument();
    // Hoy v3: HousekeepingLink removed — the «N cosas por ordenar» link no longer exists on Hoy.
    await screen.findByTestId("today-para-ti"); // wait for queue to render
    expect(screen.queryByTestId("today-housekeeping")).not.toBeInTheDocument();
  });
});


describe("Hoy · franja de metas y «Mi cola»", () => {
  function as(profileId: string, name: string): AuthSessionState {
    return {
      kind: "signed_in",
      method: "google_profile",
      operator: { operatorId: "op-1", email: "contacto@ejemplo.invalid", displayName: "Contacto", role: "sales" },
      caseCommandsEnabled: true,
      crmAuthoringEnabled: true,
      profile: { id: profileId, displayName: name, roleLabel: "Ventas", homeFilter: "llamadas" },
    };
  }
  const page = (s: AuthSessionState) => (
    <AuthSessionContext.Provider value={{ session: s, signOut: async () => true }}>
      <OverviewPage navigate={() => undefined} />
    </AuthSessionContext.Provider>
  );

  it("the goal strip sits between the greeting and today's queue", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/invoices": { items: [], summary: {} } });
    render(<OverviewPage navigate={() => undefined} />);
    const strip = await screen.findByTestId("goal-strip");
    // Hoy v3: quiet one-line header replaces HeroHeader.
    const hero = screen.getByTestId("hoy-mini-header");
    expect(hero.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Hoy v3: Seguimientos is now a button (filter), not a link.
    expect(within(strip).getByRole("button", { name: /Seguimiento/ })).toBeInTheDocument();
  });

  it("«Mi cola» / «Todo el equipo» is in TodayBody (not the header), one switch per page", async () => {
    respond({ "/v2/workspace/fx": FX, "/v2/workspace/pipeline": PIPELINE, "/v2/workspace/invoices": { items: [], summary: {} } });
    const { rerender } = render(page(as("p-ana", "Ana")));
    // Hoy v3: scope toggle is in TodayBody (today-scope), not in the mini-header.
    const scope = await screen.findByTestId("today-scope");
    fireEvent.click(within(scope).getByRole("button", { name: "Todo el equipo" }));
    expect(within(scope).getByRole("button", { name: "Todo el equipo" })).toHaveAttribute("aria-pressed", "true");
    rerender(page(as("p-beto", "Beto")));
    await screen.findByTestId("today-scope");
    expect(within(screen.getByTestId("today-scope")).getByRole("button", { name: /Mi cola/ })).toHaveAttribute("aria-pressed", "true");
    // Only one switch on the page.
    expect(screen.getAllByTestId("today-scope")).toHaveLength(1);
  });
});
