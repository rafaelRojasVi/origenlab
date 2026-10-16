/**
 * The Cotizador page reads one quote's workspace (`GET /v2/quotes/{id}`). Every value below is
 * invented. A viewer is refused by the API (403 `role_may_not_view_costs`); a missing quote is a 404.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearCatalogIndexMemory } from "../catalog/catalogIndex";
import { computedFixture, workspaceFixture } from "../quotes/quoteFixtures";
import { clearResourceCache } from "../useResource";
import { CotizadorPage } from "./CotizadorPage";

const QUOTE_ID = "6bafddfa-0000-4000-8000-0000000000d1";
const CASE_ID = "6bafddfa-0000-4000-8000-0000000000d2";

function workspace(over: { status?: string; label?: string | null; revision_no?: number; title?: string | null } = {}) {
  return {
    quote: { id: QUOTE_ID, quote_number: "00042-26", opportunity_id: CASE_ID },
    case: {
      id: CASE_ID,
      title: over.title === undefined ? "Universidad Ejemplo — agitador" : over.title,
      stage: "quoting",
      closed: false,
      institution: { id: "i1", name: "Universidad Ejemplo", rut: null },
      participants: [],
      contacts: [],
      lo_que_pidio: null,
    },
    revisions: [],
    current: {
      id: "r1",
      revision_no: over.revision_no ?? 1,
      status: over.status ?? "draft",
      origin: "authored",
      version: 1,
      label: over.label === undefined ? "00042-26" : over.label,
    },
    suggestions: { mentioned: [], kits: [] },
    price_history: null,
  };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stub(answer: () => Response) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      urls.push(new URL(raw, "http://localhost").pathname);
      return Promise.resolve(answer());
    }),
  );
  return urls;
}

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: `op-${role}`, email: "ventas@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

function renderPage(role = "sales", quoteId: string | null = QUOTE_ID) {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <CotizadorPage quoteId={quoteId} navigate={navigate} />
    </AuthSessionContext.Provider>,
  );
  return navigate;
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
  clearCatalogIndexMemory();
  sessionStorage.clear();
});

describe("Cotizador page", () => {
  it("shows a skeleton while the workspace is read", () => {
    stub(() => json(workspace()));
    renderPage();
    expect(screen.getByRole("status", { name: "Cargando" })).toBeInTheDocument();
  });

  it("shows the number, the status and the way back to the case", async () => {
    const urls = stub(() => json(workspace()));
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Cotización CN00042" })).toBeInTheDocument();
    expect(urls).toEqual([`/v2/quotes/${QUOTE_ID}`]);
    expect(screen.getByText("Borrador")).toBeInTheDocument();
    const back = screen.getByRole("link", { name: /Universidad Ejemplo — agitador/ });
    expect(back).toHaveAttribute("href", `#/crm/oportunidades/${CASE_ID}`);
    expect(back.textContent).toMatch(/^←/);
    expect(screen.getByTestId("cotizador-actions")).toBeEmptyDOMElement();
  });

  it("names a later revision with its letter and labels each status", async () => {
    const cases: [string, string][] = [
      ["draft", "Borrador"],
      ["approved", "Aprobada"],
      ["sent", "Enviada"],
      ["void", "Anulada"],
    ];
    for (const [status, text] of cases) {
      stub(() => json(workspace({ status, label: "00042A-26", revision_no: 2 })));
      const { unmount } = render(
        <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
          <CotizadorPage quoteId={QUOTE_ID} navigate={vi.fn()} />
        </AuthSessionContext.Provider>,
      );
      expect(await screen.findByRole("heading", { level: 1, name: "Cotización CN00042A" })).toBeInTheDocument();
      expect(screen.getByText(text)).toBeInTheDocument();
      unmount();
      clearResourceCache();
    }
  });

  it("falls back to the institution when the case has no title", async () => {
    stub(() => json(workspace({ title: null })));
    renderPage();
    expect(await screen.findByRole("link", { name: /Universidad Ejemplo/ })).toBeInTheDocument();
  });

  it("tells a viewer only sales and administration can quote", async () => {
    stub(() => json({ detail: { code: "role_may_not_view_costs", message: "no" } }, 403));
    renderPage("viewer");
    expect(await screen.findByText("Sólo ventas y administración pueden cotizar.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("says the quote does not exist on a 404, with a way back", async () => {
    stub(() => json({ detail: "Not Found" }, 404));
    renderPage();
    expect(await screen.findByText("Esta cotización no existe.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Volver a Oportunidades" })).toHaveAttribute("href", "#/crm/oportunidades");
  });

  it("offers a retry on a server error", async () => {
    let calls = 0;
    stub(() => (++calls === 1 ? json({ detail: "boom" }, 500) : json(workspace())));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Reintentar" }));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "Cotización CN00042" })).toBeInTheDocument());
    expect(calls).toBe(2);
  });

  it("makes no request without a quote id", () => {
    const urls = stub(() => json(workspace()));
    renderPage("sales", null);
    expect(screen.getByText("Esta cotización no existe.")).toBeInTheDocument();
    expect(urls).toEqual([]);
  });
});

/** An authored draft the editor can change; the API answers by method and path. */
function editableWorkspace() {
  const ws = workspaceFixture();
  return {
    ...ws,
    quote: { ...ws.quote, id: QUOTE_ID, opportunity_id: CASE_ID },
    case: {
      ...ws.case,
      id: CASE_ID,
      contacts: [{ participant_id: "pa-1", person_id: null, contact_point_id: null, name: "Ana Prueba", email: "ana@ejemplo.cl", source: "participant" }],
      lo_que_pidio: "Un equipo de prueba.",
    },
  };
}

const CATALOG = { version: "v1", fields: ["id", "model_number", "name"], suppliers: [], items: [] };

function stubApi(onSave: (call: number) => Response) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let saves = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(raw, "http://localhost").pathname;
      const method = init?.method ?? "GET";
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (path === "/v2/catalog/index") return Promise.resolve(json(CATALOG));
      if (path === "/v2/quotes/preview") return Promise.resolve(json({ computed: computedFixture("200000") }));
      if (path === "/v2/commands/save-quote-draft") return Promise.resolve(onSave(++saves));
      return Promise.resolve(json(editableWorkspace()));
    }),
  );
  return calls;
}

describe("Cotizador page — the draft editor", () => {
  it("shows the client and the products of an authored draft, saved", async () => {
    stubApi(() => json({}));
    renderPage();
    expect(await screen.findByRole("heading", { level: 2, name: "Productos" })).toBeInTheDocument();
    expect(screen.getByText("Institución de prueba")).toBeInTheDocument();
    expect(screen.getByText("Un equipo de prueba.")).toBeInTheDocument();
    expect(screen.getByLabelText("Descripción de la línea 2")).toHaveValue("Servicio de prueba");
    expect(screen.getByText("guardado")).toBeInTheDocument();
  });

  it("saves an edit by itself and says so", async () => {
    const calls = stubApi(() => json({ revision_id: "r-1", version: 4, computed: computedFixture("200000") }));
    renderPage();
    const qty = await screen.findByLabelText("Cantidad de la línea 1");
    fireEvent.focus(qty);
    fireEvent.change(qty, { target: { value: "3" } });
    expect(screen.getByText("sin guardar")).toBeInTheDocument();
    expect(await screen.findByText(/guardado automáticamente hace \d+ s/, undefined, { timeout: 3000 })).toBeInTheDocument();
    const save = calls.find((c) => c.path === "/v2/commands/save-quote-draft");
    expect(save?.body).toMatchObject({ revision_id: "r-1", expected_version: 3 });
    expect((save?.body as { draft: { items: { quantity: string }[] } }).draft.items[0].quantity).toBe("3");
  });

  it("offers a retry when the save fails", async () => {
    stubApi((n) => (n === 1 ? json({ detail: "boom" }, 500) : json({ revision_id: "r-1", version: 4, computed: computedFixture() })));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "2" } });
    fireEvent.click(await screen.findByRole("button", { name: "Reintentar" }, { timeout: 3000 }));
    expect(await screen.findByText(/guardado automáticamente/)).toBeInTheDocument();
  });

  it("stops on someone else's save and reloads on «Ver cambios»", async () => {
    const calls = stubApi(() => json({ detail: { code: "stale_version", message: "otra versión" } }, 409));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "2" } });
    expect(await screen.findByText("Otra persona cambió este borrador.", undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByLabelText("Cantidad de la línea 1")).toBeDisabled();
    const reads = calls.filter((c) => c.path === `/v2/quotes/${QUOTE_ID}`).length;
    fireEvent.click(screen.getByRole("button", { name: "Ver cambios" }));
    await waitFor(() => expect(screen.queryByText("Otra persona cambió este borrador.")).not.toBeInTheDocument());
    expect(calls.filter((c) => c.path === `/v2/quotes/${QUOTE_ID}`).length).toBe(reads + 1);
    expect(screen.getByLabelText("Cantidad de la línea 1")).toBeEnabled();
  });

  it("hides costs and margins in «Vista cliente»", async () => {
    stubApi(() => json({}));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Vista cliente" }));
    expect(screen.queryByRole("columnheader", { name: "Costo unit." })).not.toBeInTheDocument();
  });
});
