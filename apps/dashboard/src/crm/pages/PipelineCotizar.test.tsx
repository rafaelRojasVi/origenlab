import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData, QuoteCard, RevisionCard } from "../crmTypes";
import { forgetCreatedQuotes } from "../quotes/openOrCreateQuote";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { PipelinePage } from "./PipelinePage";

// Every value below is invented; the repository is public.
const CASE_ID = "11111111-1111-4111-8111-111111111111";
const NEW_QUOTE = "99999999-9999-4999-8999-999999999999";
const DRAFT_QUOTE = "88888888-8888-4888-8888-888888888888";

function rev(over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: "r-1", revision_no: 1, status: "sent", origin: "historical_import", sent_at: "2026-09-20T12:00:00Z",
    superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null,
    quote_number: "00041-26", ...over,
  };
}

const historical: QuoteCard = { quote_id: "q-hist", quote_number: "00041-26", number_origin: "printed_historical", revisions: [rev()] };
const draft: QuoteCard = {
  quote_id: DRAFT_QUOTE, quote_number: "00042-26", number_origin: "minted",
  revisions: [rev({ revision_id: "r-d", status: "draft", origin: "authored", sent_at: null, quote_number: "00042-26" })],
};

function card(over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  return {
    opportunity_id: CASE_ID, title: "Solicitud — Equipo ficticio", stage: "qualifying", version: 1,
    created_at: "2026-10-10T12:00:00Z", updated_at: null, closed_at: null, close_reason: null,
    organization: { organization_id: "org-1", name: "Universidad Ficticia", confirmation: "confirmed", version: 1 },
    other_organizations: [], contact: null, quotes: [], quote_numbers: [], revision_count: 0, latest_revision: null,
    drive_folder: null, attention: [], status: "ok", next_action: { text: "Cotizar", source: "suggested", due_at: null },
    ...over,
  } as OpportunityCardData;
}

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

function stub(items: OpportunityCardData[]): { path: string; method: string }[] {
  const calls: { path: string; method: string }[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    calls.push({ path, method: init?.method ?? "GET" });
    const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
    if (path === "/v2/workspace/pipeline") return json({ items, total: items.length, drive_configured: true });
    if (path === "/v2/commands/create-quote") {
      return json({ ok: true, quote_id: NEW_QUOTE, quote_number: "00043-26", revision_id: "r-new", version: 1 });
    }
    return Promise.resolve(new Response("{}", { status: 404 }));
  }));
  return calls;
}

function renderPipeline(role: string, initialOpportunityId: string | null = null) {
  vi.stubGlobal("scrollTo", vi.fn()); // the Cotizador's address scrolls to the top; jsdom has no layout
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <PipelinePage initialOpportunityId={initialOpportunityId} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
}

const posts = (calls: { path: string; method: string }[]) => calls.filter((c) => c.method === "POST").map((c) => c.path);

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
  forgetCreatedQuotes();
  window.location.hash = "";
});

describe("the request card's «Cotizar»", () => {
  it("sales: creates the quote and opens the Cotizador", async () => {
    const calls = stub([card()]);
    renderPipeline("sales");
    fireEvent.click(await screen.findByRole("button", { name: "Tarjetas" }));
    const c = await screen.findByTestId(`opportunity-card-${CASE_ID}`);
    fireEvent.click(within(c).getByRole("button", { name: "Cotizar" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/crm/cotizador/${NEW_QUOTE}`));
    expect(posts(calls)).toEqual(["/v2/commands/create-quote"]);
  });

  it("is a ≥44 px button that says «Creando…» while it works", async () => {
    let release: (r: Response) => void = () => undefined;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/v2/commands/create-quote") return new Promise<Response>((resolve) => { release = resolve; });
      if (path === "/v2/workspace/pipeline") {
        return Promise.resolve(new Response(JSON.stringify({ items: [card()], total: 1, drive_configured: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }));
    renderPipeline("sales");
    fireEvent.click(await screen.findByRole("button", { name: "Tarjetas" }));
    const c = await screen.findByTestId(`opportunity-card-${CASE_ID}`);
    const button = within(c).getByRole("button", { name: "Cotizar" });
    expect(button.className).toContain("min-h-[44px]");
    fireEvent.click(button);
    expect(await within(c).findByRole("button", { name: "Creando…" })).toBeDisabled();
    release(new Response(JSON.stringify({ ok: true, quote_id: NEW_QUOTE, quote_number: "00043-26", revision_id: "r", version: 1 }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await waitFor(() => expect(window.location.hash).toBe(`#/crm/cotizador/${NEW_QUOTE}`));
  });

  it("a case whose quote is still a draft stays «Por cotizar»: «Continuar cotización» opens it, no POST", async () => {
    const draftRev = draft.revisions[0];
    const calls = stub([card({ quotes: [draft], quote_numbers: ["00042-26"], latest_revision: draftRev })]);
    renderPipeline("sales");
    fireEvent.click(await screen.findByRole("button", { name: "Tarjetas" }));
    const c = await screen.findByTestId(`opportunity-card-${CASE_ID}`);
    expect(within(c).getByTestId("por-cotizar-chip")).toBeInTheDocument();
    fireEvent.click(within(c).getByRole("button", { name: "Continuar cotización" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/crm/cotizador/${DRAFT_QUOTE}`));
    expect(posts(calls)).toEqual([]);
  });

  it("a viewer opens the case, as before", async () => {
    const calls = stub([card()]);
    renderPipeline("viewer");
    fireEvent.click(await screen.findByRole("button", { name: "Tarjetas" }));
    const c = await screen.findByTestId(`opportunity-card-${CASE_ID}`);
    fireEvent.click(within(c).getByRole("button", { name: "Cotizar" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(posts(calls)).toEqual([]);
    expect(window.location.hash).toBe("");
  });
});

describe("the case drawer", () => {
  it("«Nueva cotización» creates a quote when the case has no open draft", async () => {
    const calls = stub([card({ stage: "quoting", quotes: [historical], quote_numbers: ["00041-26"] })]);
    renderPipeline("admin", CASE_ID);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: "Abrir borrador" })).not.toBeInTheDocument();
    // A historical quote is not the Cotizador's.
    expect(within(dialog).queryByRole("link", { name: /Abrir en Cotizador/ })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Nueva cotización" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/crm/cotizador/${NEW_QUOTE}`));
    expect(posts(calls)).toEqual(["/v2/commands/create-quote"]);
  });

  it("with an open draft it reads «Abrir borrador» and opens it; each authored quote opens in the Cotizador", async () => {
    const calls = stub([card({ stage: "quoting", quotes: [historical, draft], quote_numbers: ["00041-26", "00042-26"] })]);
    renderPipeline("sales", CASE_ID);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: "Nueva cotización" })).not.toBeInTheDocument();
    const links = within(dialog).getAllByRole("link", { name: /Abrir en Cotizador/ });
    expect(links.map((a) => a.getAttribute("href"))).toEqual([`#/crm/cotizador/${DRAFT_QUOTE}`]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Abrir borrador" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/crm/cotizador/${DRAFT_QUOTE}`));
    expect(posts(calls)).toEqual([]);
  });

  it("a closed case offers neither «Nueva cotización» nor «Abrir borrador»", async () => {
    stub([card({ stage: "lost", closed_at: "2026-10-12T12:00:00Z", quotes: [historical, draft], quote_numbers: ["00041-26", "00042-26"] })]);
    renderPipeline("sales", CASE_ID);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: /Nueva cotización|Abrir borrador/ })).not.toBeInTheDocument();
    // Its quotes still open in the Cotizador, to read.
    expect(within(dialog).getByRole("link", { name: /Abrir en Cotizador/ })).toHaveAttribute("href", `#/crm/cotizador/${DRAFT_QUOTE}`);
  });

  it("a viewer gets neither", async () => {
    stub([card({ stage: "quoting", quotes: [historical, draft], quote_numbers: ["00041-26", "00042-26"] })]);
    renderPipeline("viewer", CASE_ID);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: /Nueva cotización|Abrir borrador/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: /Abrir en Cotizador/ })).not.toBeInTheDocument();
  });
});
