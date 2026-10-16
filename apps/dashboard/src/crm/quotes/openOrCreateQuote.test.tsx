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

  it("with two open drafts, is the newest", () => {
    const older = quote("q-41", [rev()], "00041-26");
    const newer = quote("q-43", [rev()], "00043-26");
    const nextYear = quote("q-1-27", [rev()], "00001-27");
    expect(openDraftOf(card([newer, older]))?.quote_id).toBe("q-43");
    expect(openDraftOf(card([older, newer]))?.quote_id).toBe("q-43");
    expect(openDraftOf(card([older, nextYear, newer]))?.quote_id).toBe("q-1-27");
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

  it("a retry after an answer that may have run repeats the same command (same key)", async () => {
    const answers: (() => Response | Promise<Response>)[] = [
      () => Promise.reject(new TypeError("Failed to fetch")),
      () => new Response("<html>Gateway timeout</html>", { status: 504 }),
      () => new Response(JSON.stringify({ detail: { code: "internal", message: "boom" } }), { status: 502, headers: { "Content-Type": "application/json" } }),
      created,
    ];
    const keys: (string | null)[] = [];
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      keys.push(((init?.headers ?? {}) as Record<string, string>)["Idempotency-Key"] ?? null);
      return Promise.resolve(answers[keys.length - 1]());
    }));
    const go = vi.fn();
    const { result } = renderHook(() => useOpenOrCreateQuote(go));
    for (let i = 0; i < 4; i += 1) await act(() => result.current.run(card()));
    expect(keys).toHaveLength(4);
    expect(new Set(keys).size).toBe(1);
    expect(go).toHaveBeenCalledWith(NEW_QUOTE);
    // A later create for the same case (once that quote is gone) is a new command.
    stub(created);
    const voided = quote(NEW_QUOTE, [rev({ status: "void", is_active: false })]);
    await act(() => result.current.run(card([voided])));
    expect(vi.mocked(fetch).mock.calls).toHaveLength(1);
    const next = ((vi.mocked(fetch).mock.calls[0][1]?.headers ?? {}) as Record<string, string>)["Idempotency-Key"];
    expect(next).not.toBe(keys[0]);
  });

  it("two buttons for the same case cannot create at once", async () => {
    let release: (r: Response) => void = () => undefined;
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn(() => {
      posts += 1;
      return new Promise<Response>((resolve) => { release = resolve; });
    }));
    const goCard = vi.fn();
    const goDrawer = vi.fn();
    const cardButton = renderHook(() => useOpenOrCreateQuote(goCard)).result;
    const drawerButton = renderHook(() => useOpenOrCreateQuote(goDrawer)).result;
    let first: Promise<void> = Promise.resolve();
    act(() => { first = cardButton.current.run(card()); });
    await act(() => drawerButton.current.run(card()));
    expect(posts).toBe(1);
    await act(async () => { release(created()); await first; });
    expect(goCard).toHaveBeenCalledWith(NEW_QUOTE);
    expect(goDrawer).not.toHaveBeenCalled();
  });

  it("does not navigate once the button is gone", async () => {
    let release: (r: Response) => void = () => undefined;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { release = resolve; })));
    const go = vi.fn();
    const { result, unmount } = renderHook(() => useOpenOrCreateQuote(go));
    let pending: Promise<void> = Promise.resolve();
    act(() => { pending = result.current.run(card()); });
    unmount();
    await act(async () => { release(created()); await pending; });
    expect(go).not.toHaveBeenCalled();
    // The quote was made, though: the next «Cotizar» opens it.
    expect(quoteToOpen(card())).toBe(NEW_QUOTE);
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

  it("keeps the key while the API says the same create is still running", async () => {
    for (const code of ["command_in_progress", "idempotency_conflict"]) {
      forgetCreatedQuotes();
      const answers = [() => refused(409, code, "still running"), created];
      const keys: (string | null)[] = [];
      vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        keys.push(((init?.headers ?? {}) as Record<string, string>)["Idempotency-Key"] ?? null);
        return Promise.resolve(answers[keys.length - 1]());
      }));
      const go = vi.fn();
      const { result, unmount } = renderHook(() => useOpenOrCreateQuote(go));
      await act(() => result.current.run(card()));
      await act(() => result.current.run(card()));
      expect(keys).toHaveLength(2);
      expect(keys[1]).toBe(keys[0]);
      expect(go).toHaveBeenCalledWith(NEW_QUOTE);
      unmount();
    }
  });

  it("says an English refusal in Spanish, never the API's words", async () => {
    stub(() => refused(409, "quote_numbering_exhausted", "numbering exhausted"));
    const { result } = renderHook(() => useOpenOrCreateQuote(vi.fn()));
    render(<Toaster />);
    await act(() => result.current.run(card()));
    expect(await screen.findByText("La numeración de cotizaciones se agotó; configura un nuevo inicio.")).toBeInTheDocument();
    expect(screen.queryByText(/numbering exhausted/)).not.toBeInTheDocument();
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
