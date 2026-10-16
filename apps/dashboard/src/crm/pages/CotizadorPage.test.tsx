/**
 * The Cotizador page reads one quote's workspace (`GET /v2/quotes/{id}`). Every value below is
 * invented. A viewer is refused by the API (403 `role_may_not_view_costs`); a missing quote is a 404.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
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
