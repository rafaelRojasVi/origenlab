/**
 * «Todas» inside the Cotizador module: a row opens its quote as a tab. Routed for real; the API is
 * answered by path. Every value below is invented: this repository is public.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearCatalogIndexMemory } from "../catalog/catalogIndex";
import { crmHash } from "../crmRoute";
import type { QuoteListRow } from "../quotes/quoteApi";
import { computedFixture, workspaceFixture } from "../quotes/quoteFixtures";
import { forgetTabEditors } from "../quotes/tabEditors";
import { tabsStorageKey } from "../quotes/tabStore";
import { useShellRoute } from "../shellRoute";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { CotizadorModulePage } from "./CotizadorModulePage";

const Q = (n: number) => `6bafddfa-0000-4000-8000-${String(n).padStart(12, "0")}`;
const nOf = (quoteId: string) => Number(quoteId.slice(-12));
const OP = "op-sales";

function listRow(n: number, over: Partial<QuoteListRow> = {}): QuoteListRow {
  return {
    quote_id: Q(n),
    quote_number: `${String(40 + n).padStart(5, "0")}-26`,
    label: `${String(40 + n).padStart(5, "0")}-26`,
    status: "draft",
    origin: "authored",
    opportunity: { id: `o-${n}`, title: `Solicitud de ejemplo ${n}`, closed: false },
    client: { organization_id: `i-${n}`, name: `Universidad Ejemplo ${n}` },
    items: { first: ["Balanza analítica"], count: 1 },
    grand_total: "2940000",
    currency: "CLP",
    updated_at: new Date().toISOString(),
    ...over,
  };
}

const ROWS = [listRow(1), listRow(2, { status: "sent", origin: "historical_import" })];

function workspace(n: number) {
  const historical = n === 2;
  const ws = workspaceFixture(historical ? { id: `r-${n}`, status: "sent", origin: "historical_import", draft: undefined, computed: undefined } : { id: `r-${n}`, label: `${String(40 + n).padStart(5, "0")}-26` });
  return {
    ...ws,
    quote: { ...ws.quote, id: Q(n), quote_number: `${String(40 + n).padStart(5, "0")}-26`, opportunity_id: `o-${n}` },
    case: { ...ws.case, id: `o-${n}`, institution: { id: `i-${n}`, name: `Universidad Ejemplo ${n}`, rut: null } },
  };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stubApi() {
  const reads: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(raw, "http://localhost").pathname;
      if (path === "/v2/quotes") return Promise.resolve(json({ rows: ROWS, counts: { draft: 1, approved: 0, sent: 1, void: 0, all: 2 }, next_cursor: null }));
      if (path === "/v2/catalog/index") return Promise.resolve(json({ version: "v1", fields: ["id", "model_number", "name"], suppliers: [], items: [] }));
      if (path === "/v2/quotes/preview") return Promise.resolve(json({ computed: computedFixture("200000") }));
      const m = /^\/v2\/quotes\/([0-9a-f-]{36})$/.exec(path);
      if (m) {
        reads.push(m[1]);
        return Promise.resolve(json(workspace(nOf(m[1]))));
      }
      return Promise.resolve(json({}, 404));
    }),
  );
  return { reads };
}

const session: AuthSessionState = {
  kind: "signed_in",
  method: "google_profile",
  operator: { operatorId: OP, email: "ventas@example.cl", displayName: "Ventas", role: "sales" },
  caseCommandsEnabled: true,
  crmAuthoringEnabled: true,
};

function Routed() {
  const route = useShellRoute();
  return (
    <CotizadorModulePage
      quoteId={route.section === "cotizador" ? route.id : null}
      navigate={(section, id) => {
        window.location.hash = crmHash(section, id);
      }}
    />
  );
}

function renderModule(hash: string) {
  window.location.hash = hash;
  return render(
    <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
      <Routed />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
}

const tablist = () => screen.getByRole("tablist", { name: "Cotizaciones abiertas" });
const quoteTabs = () => within(tablist()).getAllByRole("tab").slice(1);

beforeEach(() => {
  window.location.hash = "";
});
afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
  clearCatalogIndexMemory();
  forgetTabEditors();
  localStorage.clear();
  sessionStorage.clear();
});

describe("Cotizador module — «Todas»", () => {
  it("lists the quotes, and a row opens its tab named from the row, at the quote's address", async () => {
    const api = stubApi();
    renderModule("#/crm/cotizador");
    const list = await screen.findByRole("table");
    fireEvent.click(within(list).getByText("CN00041"));
    await waitFor(() => expect(quoteTabs()).toHaveLength(1));
    expect(quoteTabs()[0]).toHaveAttribute("aria-selected", "true");
    expect(quoteTabs()[0]).toHaveTextContent("CN00041");
    expect(quoteTabs()[0]).toHaveTextContent("Universidad Ejemplo 1");
    await waitFor(() => expect(window.location.hash).toBe(`#/crm/cotizador/${Q(1)}`));
    expect(JSON.parse(localStorage.getItem(tabsStorageKey(OP)) ?? "{}").tabs[0].id).toBe(Q(1));
    await waitFor(() => expect(api.reads).toContain(Q(1)));
  });

  it("shows a quote already open instead of opening it twice", async () => {
    stubApi();
    renderModule("#/crm/cotizador");
    await screen.findByRole("table");
    for (let i = 0; i < 2; i++) {
      fireEvent.click(within(await screen.findByRole("tabpanel", { hidden: true, name: "Todas" })).getByText("CN00041"));
      await waitFor(() => expect(quoteTabs()).toHaveLength(1));
      fireEvent.click(within(tablist()).getByRole("tab", { name: "Todas" }));
      await waitFor(() => expect(window.location.hash).toBe("#/crm/cotizador"));
    }
    expect(quoteTabs()).toHaveLength(1);
  });

  it("opens an imported quote with nothing to edit", async () => {
    stubApi();
    renderModule("#/crm/cotizador");
    const list = await screen.findByRole("table");
    expect(within(list).getByText("importada")).toBeInTheDocument();
    fireEvent.click(within(list).getByText("CN00042"));
    await waitFor(() => expect(quoteTabs()[0]).toHaveTextContent("CN00042"));
    const panel = await screen.findByRole("tabpanel", { name: /CN00042/ });
    await waitFor(() => expect(within(panel).getByRole("heading", { level: 1, name: "Cotización CN00042" })).toBeInTheDocument());
    expect(await within(panel).findByText("Esta revisión no tiene líneas guardadas.")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: /Aprobar/ })).not.toBeInTheDocument();
    expect(within(panel).queryByLabelText(/Cantidad/)).not.toBeInTheDocument();
  });
});
