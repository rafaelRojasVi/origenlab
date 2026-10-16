import "@testing-library/jest-dom";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OpportunityCardData, QuoteCard, RevisionCard } from "../crmTypes";
import { Toaster } from "../ui";
import {
  forgetCreatedQuotes,
  isAuthoredQuote,
  openDraftOf,
  quoteToOpen,
  useOpenOrCreateQuote,
} from "./openOrCreateQuote";

// Every value below is invented; the repository is public.
const CASE_ID = "11111111-1111-4111-8111-111111111111";
const NEW_QUOTE = "99999999-9999-4999-8999-999999999999";

function rev(over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: "r-1", revision_no: 1, status: "draft", origin: "authored", sent_at: null,
    superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null,
    quote_number: "00042-26", ...over,
  };
}

function quote(id: string, revisions: RevisionCard[], number = "00042-26"): QuoteCard {
  return { quote_id: id, quote_number: number, number_origin: "minted", revisions };
}

function card(quotes: QuoteCard[] = []): OpportunityCardData {
  return {
    opportunity_id: CASE_ID, title: "Solicitud — Equipo ficticio", stage: "qualifying", version: 1, created_at: null,
    updated_at: null, closed_at: null, close_reason: null, organization: null, other_organizations: [], contact: null,
    quotes, quote_numbers: quotes.map((q) => q.quote_number), revision_count: 0, latest_revision: null,
    drive_folder: null, attention: [], status: "ok", next_action: { text: "", source: "suggested", due_at: null },
  } as OpportunityCardData;
}

interface Call {
  path: string;
  method: string;
  key: string | null;
  body: unknown;
}

function stub(answer: () => Response): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const headers = (init?.headers ?? {}) as Record<string, string>;
      calls.push({
        path: new URL(url, "http://localhost").pathname,
        method: init?.method ?? "GET",
        key: headers["Idempotency-Key"] ?? null,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return Promise.resolve(answer());
    }),
  );
  return calls;
}

const created = () =>
  new Response(JSON.stringify({ ok: true, quote_id: NEW_QUOTE, quote_number: "00042-26", revision_id: "r-new", version: 1 }), {
    status: 200, headers: { "Content-Type": "application/json" },
  });

const refused = (status: number, code: string, message: string) =>
  new Response(JSON.stringify({ detail: { code, message } }), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.unstubAllGlobals();
  forgetCreatedQuotes();
});

describe("which quote «Cotizar» opens", () => {
  it("is the quote whose newest revision is an authored draft or one in review", () => {
    const draft = quote("q-draft", [rev({ revision_no: 1, status: "approved", is_active: false }), rev({ revision_id: "r-2", revision_no: 2, status: "draft" })]);
    expect(openDraftOf(card([draft]))?.quote_id).toBe("q-draft");
    const review = quote("q-review", [rev({ status: "in_review" })]);
    expect(openDraftOf(card([review]))?.quote_id).toBe("q-review");
  });

  it("is none when the newest revision is issued, void or historical", () => {
    const issued = quote("q-sent", [rev({ revision_no: 1, status: "draft", is_active: false }), rev({ revision_id: "r-2", revision_no: 2, status: "sent" })]);
    const voided = quote("q-void", [rev({ status: "void", is_active: false })]);
    const historical = quote("q-hist", [rev({ status: "draft", origin: "historical_import" })]);
    expect(openDraftOf(card([issued, voided, historical]))).toBeNull();
    expect(quoteToOpen(card([issued, voided, historical]))).toBeNull();
  });

  it("knows a quote of this app from a historical one", () => {
    expect(isAuthoredQuote(quote("a", [rev({ status: "sent" })]))).toBe(true);
    expect(isAuthoredQuote(quote("h", [rev({ origin: "historical_import", status: "sent" })]))).toBe(false);
    expect(isAuthoredQuote(quote("n", [rev({ origin: null, status: "sent" })]))).toBe(false);
  });
});

describe("useOpenOrCreateQuote", () => {
  it("creates a quote with its own Idempotency-Key, then opens it", async () => {
    const calls = stub(created);
    const go = vi.fn();
    const { result } = renderHook(() => useOpenOrCreateQuote(go));
    await act(() => result.current.run(card()));
    expect(calls).toEqual([
      { path: "/v2/commands/create-quote", method: "POST", key: expect.any(String), body: { opportunity_id: CASE_ID } },
    ]);
    expect(go).toHaveBeenCalledWith(NEW_QUOTE);
    expect(result.current.busy).toBe(false);
  });

  it("opens the open draft without creating anything", async () => {
    const calls = stub(created);
    const go = vi.fn();
    const { result } = renderHook(() => useOpenOrCreateQuote(go));
    await act(() => result.current.run(card([quote("q-draft", [rev()])])));
    expect(calls).toEqual([]);
    expect(go).toHaveBeenCalledWith("q-draft");
  });

  it("a double click creates one quote", async () => {
    let release: (r: Response) => void = () => undefined;
    const calls: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(() => {
      calls.push(1);
      return new Promise<Response>((resolve) => { release = resolve; });
    }));
    const go = vi.fn();
    function Harness() {
      const { busy, run } = useOpenOrCreateQuote(go);
      return <button type="button" aria-busy={busy} onClick={() => void run(card())}>Cotizar</button>;
    }
    render(<Harness />);
    const button = screen.getByRole("button", { name: "Cotizar" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(button).toHaveAttribute("aria-busy", "true"));
    expect(calls).toHaveLength(1);
    await act(async () => release(created()));
    await waitFor(() => expect(go).toHaveBeenCalledWith(NEW_QUOTE));
    expect(go).toHaveBeenCalledTimes(1);
  });

  it("each click is its own command: a retry after a refusal carries a new key", async () => {
    const calls = stub(() => refused(409, "quote_number_taken", "the number 00042-26 was just taken; try again"));
    const { result } = renderHook(() => useOpenOrCreateQuote(vi.fn()));
    await act(() => result.current.run(card()));
    await act(() => result.current.run(card()));
    expect(calls).toHaveLength(2);
    expect(calls[0].key).not.toBe(calls[1].key);
  });

  it("a page still showing the case before its new quote opens that quote instead of creating another", async () => {
    const calls = stub(created);
    const go = vi.fn();
    const { result } = renderHook(() => useOpenOrCreateQuote(go));
    await act(() => result.current.run(card()));
    await act(() => result.current.run(card()));
    expect(calls).toHaveLength(1);
    expect(go).toHaveBeenNthCalledWith(2, NEW_QUOTE);
    // Once the page shows that quote, its own state decides (here: voided, so a new one).
    const voided = quote(NEW_QUOTE, [rev({ status: "void", is_active: false })]);
    expect(quoteToOpen(card([voided]))).toBeNull();
  });

  it("says the API's own Spanish reason when it refuses", async () => {
    stub(() => refused(409, "quote_numbering_not_configured", "Falta configurar la numeración de cotizaciones (parámetro quote_number_seed)."));
    const go = vi.fn();
    const { result } = renderHook(() => useOpenOrCreateQuote(go));
    render(<Toaster />);
    await act(() => result.current.run(card()));
    expect(await screen.findByText(/^Falta configurar la numeración de cotizaciones/)).toBeInTheDocument();
    expect(go).not.toHaveBeenCalled();
  });

  it("says a closed case in Spanish, and a body that is not the API's in general words", async () => {
    stub(() => refused(409, "case_closed", "this case is closed; a closed case gets no new quote"));
    const { result } = renderHook(() => useOpenOrCreateQuote(vi.fn()));
    render(<Toaster />);
    await act(() => result.current.run(card()));
    expect(await screen.findByText("Este caso está cerrado; no recibe cotizaciones nuevas.")).toBeInTheDocument();

    vi.unstubAllGlobals();
    stub(() => new Response("<html>Bad gateway</html>", { status: 502 }));
    await act(() => result.current.run(card()));
    expect(await screen.findByText("No se pudo crear la cotización.")).toBeInTheDocument();
  });
});
