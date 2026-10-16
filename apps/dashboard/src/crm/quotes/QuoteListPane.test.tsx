/**
 * «Todas»: the list of quotes. The API is answered by URL; every value is invented (this
 * repository is public).
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SEARCH_DEBOUNCE_MS, QuoteListPane } from "./QuoteListPane";
import type { QuoteListPage, QuoteListRow } from "./quoteApi";

const COUNTS = { draft: 3, approved: 5, sent: 41, void: 2, all: 51 };

function row(n: number, over: Partial<QuoteListRow> = {}): QuoteListRow {
  return {
    quote_id: `6bafddfa-0000-4000-8000-${String(n).padStart(12, "0")}`,
    quote_number: `${String(40 + n).padStart(5, "0")}-26`,
    label: `${String(40 + n).padStart(5, "0")}-26`,
    status: "draft",
    origin: "authored",
    opportunity: { id: `o-${n}`, title: `Solicitud de ejemplo ${n}`, closed: false },
    client: { organization_id: `i-${n}`, name: `Universidad Ejemplo ${n}` },
    items: { first: ["Balanza analítica", "Calibración"], count: 2 },
    grand_total: "2940000",
    currency: "CLP",
    updated_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    ...over,
  };
}

const page = (rows: QuoteListRow[], next: string | null = null, counts = COUNTS): QuoteListPage => ({ rows, counts, next_cursor: next });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

type Pending = { url: URL; resolve: (r: Response) => void };

/** The API, by URL: `answer` returns a page at once, or a Response; `hold` keeps requests open. */
function stubApi(answer: (url: URL) => QuoteListPage | Response | "hold" = () => page([row(1)])) {
  const urls: URL[] = [];
  const held: Pending[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(raw, "http://localhost");
      urls.push(url);
      const out = answer(url);
      if (out !== "hold") return Promise.resolve(out instanceof Response ? out : json(out));
      return new Promise<Response>((resolve, reject) => {
        held.push({ url, resolve });
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    }),
  );
  return { urls, held, last: () => urls[urls.length - 1] };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** With fake timers: let pending promises and renders settle, and move the clock on. */
const flush = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));
const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

/**
 * A transport that holds every request open and ignores the abort signal: a late answer reaches the
 * pane whatever it did, so only its own generation guard can keep the list right.
 */
function stubHeld() {
  const held: Pending[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(raw, "http://localhost");
      return new Promise<Response>((resolve) => held.push({ url, resolve }));
    }),
  );
  const find = (test: (u: URL) => boolean) => held.filter((h) => test(h.url));
  const answer = (h: Pending, p: QuoteListPage) => act(async () => h.resolve(json(p)));
  return { held, find, answer };
}

const rowsOf = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);

describe("QuoteListPane", () => {
  it("opens on «Borradores», says the counts in a line, and lists the rows with every column", async () => {
    const api = stubApi(() =>
      page([
        row(1, { items: { first: ["Balanza analítica", "Calibración"], count: 5 }, grand_total: "7350265" }),
        row(2, { status: "approved", client: null, opportunity: { id: "o-2", title: "Sonicador", closed: true } }),
      ]),
    );
    render(<QuoteListPane onOpen={vi.fn()} />);
    expect(await screen.findByText("3 borradores · 5 aprobadas sin enviar · 41 enviadas")).toBeInTheDocument();
    expect(api.urls[0].pathname).toBe("/v2/quotes");
    expect(api.urls[0].searchParams.get("status")).toBe("draft");
    expect(screen.getByRole("button", { name: "Borradores" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Número", "Cliente · caso", "Productos", "Estado", "Total", "Actualizada"]);
    const [first, second] = rowsOf();
    expect(first).toHaveTextContent("CN00041");
    expect(first).toHaveTextContent("Universidad Ejemplo 1");
    expect(first).toHaveTextContent("Solicitud de ejemplo 1");
    expect(first).toHaveTextContent("Balanza analítica, Calibración +3 más");
    expect(first).toHaveTextContent("Borrador");
    expect(first).toHaveTextContent("$ 7.350.265");
    expect(first).toHaveTextContent("hace 5 min");
    expect(second).toHaveTextContent("Sin institución");
    expect(second).toHaveTextContent("Aprobada");
    expect(second).toHaveTextContent("caso cerrado");
  });

  it("words one of each count in the singular, and leaves out what is zero", async () => {
    stubApi(() => page([row(1)], null, { draft: 1, approved: 0, sent: 1, void: 0, all: 2 }));
    render(<QuoteListPane onOpen={vi.fn()} />);
    expect(await screen.findByText("1 borrador · 1 enviada")).toBeInTheDocument();
  });

  it("says «N anuladas» when only voided quotes exist", async () => {
    stubApi(() => page([row(1, { status: "void" })], null, { draft: 0, approved: 0, sent: 0, void: 2, all: 2 }));
    render(<QuoteListPane onOpen={vi.fn()} />);
    expect(await screen.findByText("2 anuladas")).toBeInTheDocument();
  });

  it("asks for the filter chosen, with its rows replacing the old ones", async () => {
    const api = stubApi((url) => (url.searchParams.get("status") === "sent" ? page([row(7, { status: "sent" })]) : page([row(1)])));
    render(<QuoteListPane onOpen={vi.fn()} />);
    await screen.findByText("CN00041");
    for (const [name, status] of [["Aprobadas", "approved"], ["Enviadas", "sent"], ["Todas", "all"]] as const) {
      fireEvent.click(screen.getByRole("button", { name }));
      await waitFor(() => expect(api.last().searchParams.get("status")).toBe(status));
      expect(screen.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true");
    }
    fireEvent.click(screen.getByRole("button", { name: "Enviadas" }));
    expect(await screen.findByText("CN00047")).toBeInTheDocument();
    expect(screen.queryByText("CN00041")).not.toBeInTheDocument();
  });

  it(`waits ${SEARCH_DEBOUNCE_MS} ms after the last key to search, and starts again from the first page`, async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const api = stubApi((url) => (url.searchParams.get("cursor") ? page([row(2)]) : page([row(1)], "cursor-1")));
    render(<QuoteListPane onOpen={vi.fn()} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    await flush();
    expect(screen.getByText("CN00042")).toBeInTheDocument();
    const before = api.urls.length;
    const box = screen.getByRole("searchbox", { name: "Buscar" });
    fireEvent.change(box, { target: { value: "bal" } });
    await tick(200);
    fireEvent.change(box, { target: { value: "balanza" } });
    await tick(SEARCH_DEBOUNCE_MS - 1);
    expect(api.urls).toHaveLength(before);
    await tick(1);
    expect(api.urls).toHaveLength(before + 1);
    expect(api.last().searchParams.get("q")).toBe("balanza");
    expect(api.last().searchParams.has("cursor")).toBe(false);
    expect(screen.getByText("CN00041")).toBeInTheDocument();
    expect(screen.queryByText("CN00042")).not.toBeInTheDocument();
  });

  it("does not search after the pane is gone: unmounting clears the pending timer", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const api = stubApi();
    const { unmount } = render(<QuoteListPane onOpen={vi.fn()} />);
    await flush();
    const before = api.urls.length;
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar" }), { target: { value: "balanza" } });
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    await tick(SEARCH_DEBOUNCE_MS * 2);
    expect(api.urls).toHaveLength(before);
  });

  it("never lets an older answer overwrite a newer one", async () => {
    const api = stubApi(() => "hold");
    render(<QuoteListPane onOpen={vi.fn()} />);
    await waitFor(() => expect(api.held).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Enviadas" }));
    await waitFor(() => expect(api.held).toHaveLength(2));
    // The newer request answers first; then the older one (already abandoned) answers late.
    await act(async () => api.held[1].resolve(json(page([row(2, { status: "sent" })]))));
    await screen.findByText("CN00042");
    await act(async () => api.held[0].resolve(json(page([row(1)]))));
    expect(screen.getByText("CN00042")).toBeInTheDocument();
    expect(screen.queryByText("CN00041")).not.toBeInTheDocument();
  });

  it("drops an older search answer that arrives after a newer one, even when it was never aborted", async () => {
    // A transport that ignores the abort signal: the token alone must keep the answers in order.
    const held: Pending[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
        return new Promise<Response>((resolve) => held.push({ url, resolve }));
      }),
    );
    render(<QuoteListPane onOpen={vi.fn()} />);
    await waitFor(() => expect(held).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Todas" }));
    await waitFor(() => expect(held).toHaveLength(2));
    await act(async () => held[1].resolve(json(page([row(2)]))));
    await act(async () => held[0].resolve(json(page([row(1)]))));
    expect(screen.getByText("CN00042")).toBeInTheDocument();
    expect(screen.queryByText("CN00041")).not.toBeInTheDocument();
  });

  it("«Cargar más» appends the next page by its cursor, and goes away at the end", async () => {
    const api = stubApi((url) => {
      const cursor = url.searchParams.get("cursor");
      if (cursor === "c1") return page([row(3), row(4)], "c2");
      if (cursor === "c2") return page([row(5)]);
      return page([row(1), row(2)], "c1");
    });
    render(<QuoteListPane onOpen={vi.fn()} />);
    await screen.findByText("CN00041");
    fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    await screen.findByText("CN00044");
    expect(rowsOf()).toHaveLength(4);
    expect(api.last().searchParams.get("cursor")).toBe("c1");
    fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    await screen.findByText("CN00045");
    expect(rowsOf()).toHaveLength(5);
    expect(screen.queryByRole("button", { name: "Cargar más" })).not.toBeInTheDocument();
  });

  it("starts the list over when the API refuses its cursor, and says so when more cannot load", async () => {
    let refuse: "bad_cursor" | "down" = "bad_cursor";
    const api = stubApi((url) => {
      if (!url.searchParams.get("cursor")) return page([row(1)], "forged");
      return refuse === "bad_cursor"
        ? json({ error: { code: "validation_error", message: "that cursor is not one this list gave", details: { code: "bad_cursor", message: "that cursor is not one this list gave" }, request_id: "req-ejemplo" } }, 422)
        : json({}, 500);
    });
    render(<QuoteListPane onOpen={vi.fn()} />);
    await screen.findByText("CN00041");
    fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    await waitFor(() => expect(api.urls.filter((u) => !u.searchParams.has("cursor"))).toHaveLength(2));
    await screen.findByText("CN00041");
    expect(screen.getByRole("status")).toHaveTextContent("La lista cambió; se volvió a cargar desde el inicio.");
    refuse = "down";
    fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudieron cargar más cotizaciones");
    expect(screen.getByText("CN00041")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cargar más" })).toBeEnabled();
  });

  it("opens a row from its own button (a click on it opens once), named by number and client", async () => {
    stubApi(() => page([row(1), row(2, { status: "approved", client: null, opportunity: { id: "o-2", title: "Sonicador industrial", closed: false } })]));
    const onOpen = vi.fn();
    render(<QuoteListPane onOpen={onOpen} />);
    await screen.findByText("CN00041");
    const first = screen.getByRole("button", { name: "Abrir CN00041, Universidad Ejemplo 1" });
    // A real button: Enter and Space are its own, and it is the row's one tab stop.
    expect(first.tagName).toBe("BUTTON");
    expect(first).toHaveAttribute("type", "button");
    expect(rowsOf().every((r) => !r.hasAttribute("tabindex"))).toBe(true);
    fireEvent.click(first);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenLastCalledWith("6bafddfa-0000-4000-8000-000000000001", { label: "CN00041", client: "Universidad Ejemplo 1", status: "draft" });
    // A click anywhere on the row does the same.
    fireEvent.click(within(rowsOf()[1]).getByText("Aprobada"));
    expect(onOpen).toHaveBeenCalledTimes(2);
    expect(onOpen).toHaveBeenLastCalledWith("6bafddfa-0000-4000-8000-000000000002", { label: "CN00042", client: "Sonicador industrial", status: "approved" });
    expect(screen.getByRole("button", { name: "Abrir CN00042" })).toBeInTheDocument();
  });

  it("marks an imported quote «importada» and names a label outside the series as it is", async () => {
    stubApi(() => page([row(1, { origin: "historical_import", status: "sent", label: null, quote_number: "COT-1234/19" }), row(2)]));
    render(<QuoteListPane onOpen={vi.fn()} />);
    await screen.findByText("COT-1234/19");
    const [imported, authored] = rowsOf();
    expect(imported).toHaveTextContent("importada");
    expect(authored).not.toHaveTextContent("importada");
  });

  it("an imported quote shows what its read PDF said: its products and the neto, labelled; unread stays «—»", async () => {
    stubApi(() =>
      page([
        row(1, {
          origin: "historical_import", status: "sent", grand_total: null, net_total: "250000", amount_source: "pdf_read",
          items: { first: ["Centrífuga de ejemplo", "Rotor"], count: 4 },
        }),
        row(2, { origin: "historical_import", status: "sent", grand_total: null, net_total: null, amount_source: null, items: { first: [], count: 0 } }),
        row(3, { amount_source: "revision" }),
      ]),
    );
    render(<QuoteListPane onOpen={vi.fn()} />);
    await screen.findByText("Centrífuga de ejemplo, Rotor +2 más");
    const [read, unread, authored] = rowsOf();
    const cells = (r: HTMLElement) => within(r).getAllByRole("cell");
    // The neto as the API said it — no IVA added in the browser — and where it comes from.
    expect(cells(read)[4]).toHaveTextContent("$ 250.000");
    expect(cells(read)[4]).toHaveTextContent("neto leído del PDF");
    expect(cells(unread)[2]).toHaveTextContent("—");
    expect(cells(unread)[4]).toHaveTextContent("—");
    expect(cells(unread)[4]).not.toHaveTextContent("PDF");
    expect(cells(authored)[4]).toHaveTextContent("$ 2.940.000");
    expect(cells(authored)[4]).not.toHaveTextContent("PDF");
  });

  it("says the list is empty in the words of the filter, and of a search that found nothing", async () => {
    stubApi(() => page([], null, { draft: 0, approved: 0, sent: 0, void: 0, all: 0 }));
    render(<QuoteListPane onOpen={vi.fn()} />);
    expect(await screen.findByText("No hay borradores.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Enviadas" }));
    expect(await screen.findByText("Ninguna cotización enviada todavía.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar" }), { target: { value: "zzz" } });
    expect(await screen.findByText("Ninguna cotización coincide con «zzz».")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows a loading state, then an error that can be retried", async () => {
    let fail = true;
    const api = stubApi(() => (fail ? json({}, 500) : page([row(1)])));
    render(<QuoteListPane onOpen={vi.fn()} />);
    expect(screen.getByRole("status", { name: "Cargando" })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo cargar la lista de cotizaciones");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    await screen.findByText("CN00041");
    expect(api.urls).toHaveLength(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("explains a refusal for a role that may not quote", async () => {
    stubApi(() => json({ error: { code: "forbidden", message: "this role may not view quotes", details: { code: "role_may_not_view_costs", message: "this role may not view quotes" }, request_id: "req-ejemplo" } }, 403));
    render(<QuoteListPane onOpen={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Sólo ventas y administración pueden cotizar.");
  });

  it("reads nothing while it is hidden, and refreshes in place when shown again", async () => {
    let n = 1;
    const api = stubApi(() => page([row(n)]));
    const { rerender } = render(<QuoteListPane active={false} onOpen={vi.fn()} />);
    await new Promise((r) => setTimeout(r, 30));
    expect(api.urls).toHaveLength(0);
    rerender(<QuoteListPane active onOpen={vi.fn()} />);
    await screen.findByText("CN00041");
    rerender(<QuoteListPane active={false} onOpen={vi.fn()} />);
    n = 2;
    rerender(<QuoteListPane active onOpen={vi.fn()} />);
    // The old rows stay while the new answer comes.
    expect(screen.getByText("CN00041")).toBeInTheDocument();
    await screen.findByText("CN00042");
    expect(api.urls).toHaveLength(2);
  });

  it("has «Preferencias» and «Nueva cotización» at 44 px, off until they are wired, on when they are", async () => {
    stubApi();
    const onPreferences = vi.fn();
    const onNew = vi.fn();
    const { rerender } = render(<QuoteListPane onOpen={vi.fn()} />);
    await screen.findByText("CN00041");
    for (const name of ["Preferencias", "Nueva cotización"]) {
      const off = screen.getByRole("button", { name });
      expect(off).toBeDisabled();
      expect(off).not.toHaveAttribute("title");
      expect(off).toHaveAccessibleDescription("Llega en la siguiente entrega");
    }
    rerender(<QuoteListPane onOpen={vi.fn()} onPreferences={onPreferences} onNew={onNew} />);
    for (const name of ["Preferencias", "Nueva cotización"]) {
      expect(screen.getByRole("button", { name })).toBeEnabled();
      expect(screen.getByRole("button", { name }).className).toContain("min-h-[44px]");
      expect(screen.getByRole("button", { name })).not.toHaveAccessibleDescription("Llega en la siguiente entrega");
    }
    fireEvent.click(screen.getByRole("button", { name: "Preferencias" }));
    fireEvent.click(screen.getByRole("button", { name: "Nueva cotización" }));
    expect(onPreferences).toHaveBeenCalledOnce();
    expect(onNew).toHaveBeenCalledOnce();
  });

  it("«Nueva cotización» off with a reason says that reason, not «Llega en la siguiente entrega»", async () => {
    stubApi();
    render(<QuoteListPane onOpen={vi.fn()} onPreferences={vi.fn()} newOffReason="Crear cotizaciones está desactivado en este servidor." />);
    await screen.findByText("CN00041");
    const off = screen.getByRole("button", { name: "Nueva cotización" });
    expect(off).toBeDisabled();
    expect(off).toHaveAccessibleDescription("Crear cotizaciones está desactivado en este servidor.");
    expect(off).toHaveAttribute("title", "Crear cotizaciones está desactivado en este servidor.");
  });

  describe("late answers that must not land (a transport that ignores abort)", () => {
    it("a «Cargar más» page arriving after a filter change does not append", async () => {
      const api = stubHeld();
      render(<QuoteListPane onOpen={vi.fn()} />);
      await waitFor(() => expect(api.held).toHaveLength(1));
      await api.answer(api.held[0], page([row(1)], "c1"));
      fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
      await waitFor(() => expect(api.find((u) => u.searchParams.get("cursor") === "c1")).toHaveLength(1));
      fireEvent.click(screen.getByRole("button", { name: "Enviadas" }));
      await waitFor(() => expect(api.find((u) => u.searchParams.get("status") === "sent")).toHaveLength(1));
      await api.answer(api.find((u) => u.searchParams.get("status") === "sent")[0], page([row(5, { status: "sent" })]));
      await api.answer(api.find((u) => u.searchParams.get("cursor") === "c1")[0], page([row(2)]));
      expect(screen.getByText("CN00045")).toBeInTheDocument();
      expect(screen.queryByText("CN00042")).not.toBeInTheDocument();
      expect(rowsOf()).toHaveLength(1);
    });

    it("a «Cargar más» page arriving after a search change does not append", async () => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      const api = stubHeld();
      render(<QuoteListPane onOpen={vi.fn()} />);
      await flush();
      await api.answer(api.held[0], page([row(1)], "c1"));
      fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
      await flush();
      fireEvent.change(screen.getByRole("searchbox", { name: "Buscar" }), { target: { value: "sonicador" } });
      await tick(SEARCH_DEBOUNCE_MS);
      const searched = api.find((u) => u.searchParams.get("q") === "sonicador");
      expect(searched).toHaveLength(1);
      await api.answer(searched[0], page([row(6)]));
      await api.answer(api.find((u) => u.searchParams.get("cursor") === "c1")[0], page([row(2)]));
      expect(screen.getByText("CN00046")).toBeInTheDocument();
      expect(screen.queryByText("CN00042")).not.toBeInTheDocument();
    });

    it("a stale search answer arriving after the newer search's does not replace it", async () => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      const api = stubHeld();
      render(<QuoteListPane onOpen={vi.fn()} />);
      await flush();
      await api.answer(api.held[0], page([row(1)]));
      const box = screen.getByRole("searchbox", { name: "Buscar" });
      fireEvent.change(box, { target: { value: "bal" } });
      await tick(SEARCH_DEBOUNCE_MS);
      fireEvent.change(box, { target: { value: "balanza" } });
      await tick(SEARCH_DEBOUNCE_MS);
      const older = api.find((u) => u.searchParams.get("q") === "bal")[0];
      const newer = api.find((u) => u.searchParams.get("q") === "balanza")[0];
      expect(older && newer).toBeTruthy();
      await api.answer(newer, page([row(3)]));
      await api.answer(older, page([row(4)]));
      expect(screen.getByText("CN00043")).toBeInTheDocument();
      expect(screen.queryByText("CN00044")).not.toBeInTheDocument();
    });

    it("a refresh in place and a «Cargar más» that overlap: the old cursor's page never lands on the new first page", async () => {
      const api = stubHeld();
      const { rerender } = render(<QuoteListPane onOpen={vi.fn()} />);
      await waitFor(() => expect(api.held).toHaveLength(1));
      await api.answer(api.held[0], page([row(1)], "old"));
      // «Cargar más» is in flight when the pane is hidden and shown again (a refresh in place).
      fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
      await waitFor(() => expect(api.find((u) => u.searchParams.get("cursor") === "old")).toHaveLength(1));
      rerender(<QuoteListPane active={false} onOpen={vi.fn()} />);
      rerender(<QuoteListPane active onOpen={vi.fn()} />);
      await waitFor(() => expect(api.find((u) => !u.searchParams.has("cursor"))).toHaveLength(2));
      const refresh = api.find((u) => !u.searchParams.has("cursor"))[1];
      // «Cargar más» waits for the refresh.
      expect(screen.getByRole("button", { name: /Cargar más|Cargando/ })).toBeDisabled();
      await api.answer(refresh, page([row(7)], "new"));
      await api.answer(api.find((u) => u.searchParams.get("cursor") === "old")[0], page([row(2)]));
      expect(screen.getByText("CN00047")).toBeInTheDocument();
      expect(screen.queryByText("CN00042")).not.toBeInTheDocument();
      expect(screen.queryByText("CN00041")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Cargar más" })).toBeEnabled();
    });

    it("a page arriving while the pane is hidden does not append", async () => {
      const api = stubHeld();
      const { rerender } = render(<QuoteListPane onOpen={vi.fn()} />);
      await waitFor(() => expect(api.held).toHaveLength(1));
      await api.answer(api.held[0], page([row(1)], "c1"));
      fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
      await waitFor(() => expect(api.held).toHaveLength(2));
      rerender(<QuoteListPane active={false} onOpen={vi.fn()} />);
      await api.answer(api.held[1], page([row(2)]));
      rerender(<QuoteListPane active onOpen={vi.fn()} />);
      expect(screen.queryByText("CN00042")).not.toBeInTheDocument();
    });
  });

  it("aborts the page being appended when the filter changes and when the pane goes away", async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        const url = new URL(raw, "http://localhost");
        if (url.searchParams.get("cursor")) {
          signals.push(init!.signal!);
          return new Promise<Response>(() => {});
        }
        return Promise.resolve(json(page([row(1)], "c1")));
      }),
    );
    const { unmount } = render(<QuoteListPane onOpen={vi.fn()} />);
    await screen.findByText("CN00041");
    fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    await waitFor(() => expect(signals).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Enviadas" }));
    expect(signals[0].aborted).toBe(true);
    await screen.findByText("CN00041");
    fireEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[1].aborted).toBe(false);
    unmount();
    expect(signals[1].aborted).toBe(true);
  });

  it("says quietly that a refresh in place could not update, and keeps the rows", async () => {
    let down = false;
    stubApi(() => (down ? json({}, 500) : page([row(1)])));
    const { rerender } = render(<QuoteListPane onOpen={vi.fn()} />);
    await screen.findByText("CN00041");
    rerender(<QuoteListPane active={false} onOpen={vi.fn()} />);
    down = true;
    rerender(<QuoteListPane active onOpen={vi.fn()} />);
    expect(await screen.findByText(/No se pudo actualizar/)).toBeInTheDocument();
    expect(screen.getByText("CN00041")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
