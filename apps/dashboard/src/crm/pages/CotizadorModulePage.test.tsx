/**
 * The Cotizador module: many quotes open as tabs. Routed for real (`useShellRoute` over the
 * address), with the API answered by path. Every value below is invented: this repository is public.
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearCatalogIndexMemory } from "../catalog/catalogIndex";
import { crmHash } from "../crmRoute";
import { computedFixture, workspaceFixture } from "../quotes/quoteFixtures";
import { forgetTabEditors } from "../quotes/tabEditors";
import { MAX_TABS, tabsStorageKey } from "../quotes/tabStore";
import { SAVE_DELAY_MS } from "../quotes/useQuoteDraft";
import { useShellRoute } from "../shellRoute";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { CotizadorModulePage } from "./CotizadorModulePage";

const Q = (n: number) => `6bafddfa-0000-4000-8000-${String(n).padStart(12, "0")}`;
const nOf = (quoteId: string) => Number(quoteId.slice(-12));
const OP = "op-sales";

function workspace(n: number) {
  const ws = workspaceFixture({ id: `r-${n}`, label: `${String(40 + n).padStart(5, "0")}-26` });
  return {
    ...ws,
    quote: { ...ws.quote, id: Q(n), quote_number: `${String(40 + n).padStart(5, "0")}-26`, opportunity_id: `o-${n}` },
    case: { ...ws.case, id: `o-${n}`, institution: { id: `i-${n}`, name: `Universidad Ejemplo ${n}`, rut: null } },
  };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const CLIENT = {
  id: "aaaaaaaa-0000-4000-8000-000000000009", name: "Universidad Ejemplo 9", kind: "university", domains: [],
  open_cases: [{ id: "o-9", title: "Balanza analítica", stage: "qualifying", requested_at: null }],
};
const CATALOG = { version: "v1", fields: ["id", "model_number", "name"], suppliers: [], items: [] };

const PARAMETERS = {
  current: { default_markup_equipment: { id: "p-1", value: "0.40", valid_from: "2026-10-01T15:00:00Z", set_by: null, reason: "Prueba" } },
  history: [],
};

type Call = { method: string; path: string; body: { revision_id?: string } | null };

function stubApi(
  onSave: (revisionId: string, call: number) => Response | Promise<Response> = () => json({ revision_id: "r", version: 4, computed: computedFixture() }),
  missing: number[] = [],
) {
  const calls: Call[] = [];
  let saves = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(raw, "http://localhost").pathname;
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ method: init?.method ?? "GET", path, body });
      if (path === "/v2/catalog/index") return Promise.resolve(json(CATALOG));
      if (path === "/v2/catalog/parameters") return Promise.resolve(json(PARAMETERS));
      if (path === "/v2/quotes/preview") return Promise.resolve(json({ computed: computedFixture("200000") }));
      if (path === "/v2/commands/save-quote-draft") return Promise.resolve(onSave(body.revision_id, ++saves));
      if (path === "/v2/quotes/clients") return Promise.resolve(json({ organizations: [CLIENT], people: [] }));
      if (path === "/v2/commands/create-quote")
        return Promise.resolve(json({ ok: true, quote_id: Q(9), quote_number: "00049-26", revision_id: "r-9", version: 1, opportunity_id: "o-9" }));
      const m = /^\/v2\/quotes\/([0-9a-f-]{36})$/.exec(path);
      if (m && missing.includes(nOf(m[1]))) return Promise.resolve(json({ detail: { code: "quote_not_found", message: "no" } }, 404));
      if (m) return Promise.resolve(json(workspace(nOf(m[1]))));
      return Promise.resolve(json({}, 404));
    }),
  );
  const reads = (n: number) => calls.filter((c) => c.method === "GET" && c.path === `/v2/quotes/${Q(n)}`).length;
  const savesOf = (n: number) => calls.filter((c) => c.path === "/v2/commands/save-quote-draft" && c.body?.revision_id === `r-${n}`).length;
  return { calls, reads, savesOf };
}

const session: AuthSessionState = {
  kind: "signed_in",
  method: "google_profile",
  operator: { operatorId: OP, email: "ventas@example.cl", displayName: "Ventas", role: "sales" },
  caseCommandsEnabled: true,
  crmAuthoringEnabled: true,
};

/** The module only while the address is the module's, as in the shell; anything else is «Hoy». */
function Routed() {
  const route = useShellRoute();
  if (route.section !== "cotizador") return <p>Fuera del módulo</p>;
  return (
    <CotizadorModulePage
      quoteId={route.section === "cotizador" ? route.id : null}
      pane={route.pane}
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
const tabOf = (label: string) => within(tablist()).getByRole("tab", { name: new RegExp(label) });
const shown = () => screen.getByRole("tabpanel");
/** The tab shown changes asynchronously (the address first): look again until it is the quote. */
async function showsQuote(label: string) {
  await waitFor(() => expect(within(shown()).getByRole("heading", { level: 1, name: `Cotización ${label}` })).toBeInTheDocument());
}
async function shownField(label: string) {
  return waitFor(() => within(shown()).getByLabelText(label));
}

function seed(ns: number[], active: number | null) {
  localStorage.setItem(
    tabsStorageKey(OP),
    JSON.stringify({
      v: 1,
      tabs: ns.map((n) => ({ id: Q(n), meta: { label: `CN000${40 + n}`, client: `Guardado ${n}`, status: "draft" } })),
      active: active === null ? null : Q(active),
    }),
  );
}

function fakeTimers() {
  vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout"] });
}
async function autosave() {
  await act(async () => {
    vi.advanceTimersByTime(SAVE_DELAY_MS + 50);
  });
}

beforeEach(() => {
  window.location.hash = "";
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearResourceCache();
  clearCatalogIndexMemory();
  forgetTabEditors();
  localStorage.clear();
  sessionStorage.clear();
});

describe("Cotizador module — tabs", () => {
  it("opens the address's quote as a tab after «Todas», and names it once its workspace loads", async () => {
    const api = stubApi();
    renderModule(`#/crm/cotizador/${Q(1)}`);
    expect(within(tablist()).getAllByRole("tab")[0]).toHaveTextContent("Todas");
    await showsQuote("CN00041");
    const tab = tabOf("CN00041");
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(tab).toHaveTextContent("Universidad Ejemplo 1");
    expect(tab).toHaveTextContent("Borrador");
    expect(within(tab).getByTestId("cotizador-tab-dot")).toHaveAttribute("data-status", "draft");
    expect(api.reads(1)).toBe(1);
    expect(JSON.parse(localStorage.getItem(tabsStorageKey(OP))!).tabs[0].meta).toEqual({
      label: "CN00041",
      client: "Universidad Ejemplo 1",
      status: "draft",
    });
  });

  it("restores the tabs on reload, reading only the tab shown; another reads the first time it is shown, then stays mounted", async () => {
    seed([1, 2, 3], 2);
    const api = stubApi();
    renderModule("#/crm/cotizador");
    await waitFor(() => expect(window.location.hash).toBe(`#/crm/cotizador/${Q(2)}`));
    await showsQuote("CN00042");
    expect(quoteTabs()).toHaveLength(3);
    // Restored tabs say what was stored until they are shown.
    expect(tabOf("CN00043")).toHaveTextContent("Guardado 3");
    expect(api.reads(1) + api.reads(3)).toBe(0);
    expect(api.reads(2)).toBe(1);

    fireEvent.click(tabOf("CN00043"));
    await showsQuote("CN00043");
    await waitFor(() => expect(tabOf("CN00043")).toHaveTextContent("Universidad Ejemplo 3"));
    fireEvent.click(tabOf("CN00042"));
    await waitFor(() => expect(tabOf("CN00042")).toHaveAttribute("aria-selected", "true"));
    expect(within(shown()).getByRole("heading", { level: 1, name: "Cotización CN00042" })).toBeInTheDocument();
    // Both stay in the page; neither is read again.
    expect(document.getElementById(`cotizador-panel-${Q(3)}`)).not.toBeNull();
    expect(document.getElementById(`cotizador-panel-${Q(3)}`)).not.toBeVisible();
    expect(api.reads(2)).toBe(1);
    expect(api.reads(3)).toBe(1);
    expect(api.reads(1)).toBe(0);
  });

  it("never opens the same quote twice: opening it again shows its tab", async () => {
    stubApi();
    renderModule(`#/crm/cotizador/${Q(1)}`);
    await showsQuote("CN00041");
    act(() => void (window.location.hash = `#/crm/cotizador/${Q(2)}`));
    await showsQuote("CN00042");
    act(() => void (window.location.hash = `#/crm/cotizador/${Q(1).toUpperCase()}`));
    await waitFor(() => expect(tabOf("CN00041")).toHaveAttribute("aria-selected", "true"));
    expect(quoteTabs()).toHaveLength(2);
  });

  it("«Todas» shows the list pane", async () => {
    stubApi();
    renderModule(`#/crm/cotizador/${Q(1)}`);
    await showsQuote("CN00041");
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Todas" }));
    await waitFor(() => expect(within(shown()).getByRole("heading", { level: 1, name: "Cotizaciones" })).toBeInTheDocument());
    expect(window.location.hash).toBe("#/crm/cotizador");
  });

  it("«+ Nueva» opens «Nueva cotización»; the quote it creates opens as a tab, in the address", async () => {
    const api = stubApi();
    renderModule("#/crm/cotizador");
    fireEvent.click(screen.getByRole("button", { name: "+ Nueva" }));
    const dialog = screen.getByRole("dialog", { name: "Nueva cotización" });
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Cliente" }), { target: { value: "Universidad" } });
    fireEvent.click(await within(dialog).findByRole("option", { name: /Universidad Ejemplo 9/ }));
    expect(within(dialog).getByRole("radio", { name: /Balanza analítica/ })).toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "Crear y abrir" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Nueva cotización" })).not.toBeInTheDocument());
    await waitFor(() => expect(tabOf("CN00049")).toHaveAttribute("aria-selected", "true"));
    expect(tabOf("CN00049")).toHaveTextContent("Universidad Ejemplo 9");
    await waitFor(() => expect(window.location.hash).toBe(`#/crm/cotizador/${Q(9)}`));
    await showsQuote("CN00049");
    expect(api.calls.find((c) => c.path === "/v2/commands/create-quote")?.body).toEqual({
      client: { organization_id: CLIENT.id },
      opportunity_id: "o-9",
    });
  });

  it("the list's «Nueva cotización» opens the same dialog, and Esc closes it", async () => {
    stubApi();
    renderModule("#/crm/cotizador");
    fireEvent.click(await screen.findByRole("button", { name: "Nueva cotización" }));
    expect(screen.getByRole("dialog", { name: "Nueva cotización" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Nueva cotización" })).not.toBeInTheDocument();
  });

  it("«+ Nueva» is off, and says why, when the commands are off", async () => {
    stubApi();
    window.location.hash = "#/crm/cotizador";
    render(
      <AuthSessionContext.Provider value={{ session: { ...session, caseCommandsEnabled: false }, signOut: async () => true }}>
        <Routed />
      </AuthSessionContext.Provider>,
    );
    expect(screen.getByRole("button", { name: "+ Nueva" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "+ Nueva" })).toHaveAccessibleDescription("Crear cotizaciones está desactivado en este servidor.");
    // The list's «Nueva cotización» gives the same reason, not «Llega en la siguiente entrega».
    const listNew = screen.getByRole("button", { name: "Nueva cotización" });
    expect(listNew).toBeDisabled();
    expect(listNew).toHaveAccessibleDescription("Crear cotizaciones está desactivado en este servidor.");
  });

  it(`opens at most ${MAX_TABS}: a ninth closes the oldest tab with nothing pending`, async () => {
    seed([1, 2, 3, 4, 5, 6, 7, 8], 8);
    stubApi();
    renderModule(`#/crm/cotizador/${Q(9)}`);
    await showsQuote("CN00049");
    const labels = quoteTabs().map((t) => t.textContent ?? "");
    expect(labels).toHaveLength(MAX_TABS);
    expect(labels.some((l) => l.includes("CN00041"))).toBe(false);
    expect(labels[MAX_TABS - 1]).toContain("CN00049");
  });

  it("moves between tabs with the arrows, and Supr closes the focused one", async () => {
    seed([1, 2], 1);
    stubApi();
    renderModule(`#/crm/cotizador/${Q(1)}`);
    await showsQuote("CN00041");
    const todas = within(tablist()).getByRole("tab", { name: "Todas" });
    expect(tabOf("CN00041")).toHaveAttribute("tabindex", "0");
    expect(todas).toHaveAttribute("tabindex", "-1");
    todas.focus();
    fireEvent.keyDown(todas, { key: "ArrowRight" });
    expect(tabOf("CN00041")).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(tabOf("CN00042")).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(todas).toHaveFocus();
    fireEvent.keyDown(todas, { key: "ArrowLeft" });
    fireEvent.keyDown(document.activeElement!, { key: "Delete" });
    await waitFor(() => expect(quoteTabs()).toHaveLength(1));
    expect(within(tablist()).queryByRole("tab", { name: /CN00042/ })).not.toBeInTheDocument();
  });
});

describe("Cotizador module — panes keep their edits", () => {
  it("an edit survives a switch, and its autosave runs while the tab is hidden", async () => {
    fakeTimers();
    const api = stubApi();
    renderModule(`#/crm/cotizador/${Q(1)}`);
    fireEvent.change(await shownField("Cantidad de la línea 1"), { target: { value: "3" } });
    act(() => void (window.location.hash = `#/crm/cotizador/${Q(2)}`));
    await showsQuote("CN00042");
    await autosave();
    await waitFor(() => expect(api.savesOf(1)).toBe(1));
    fireEvent.click(tabOf("CN00041"));
    await waitFor(() => expect(within(shown()).getByLabelText("Cantidad de la línea 1")).toHaveValue("3"));
    expect(api.reads(1)).toBe(1);
  });

  it("closing a tab sends its pending save first", async () => {
    const api = stubApi();
    renderModule(`#/crm/cotizador/${Q(1)}`);
    fireEvent.change(await shownField("Cantidad de la línea 1"), { target: { value: "5" } });
    expect(api.savesOf(1)).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Cerrar CN00041" }));
    await waitFor(() => expect(quoteTabs()).toHaveLength(0));
    expect(api.savesOf(1)).toBe(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(window.location.hash).toBe("#/crm/cotizador"));
    expect(JSON.parse(localStorage.getItem(tabsStorageKey(OP))!).tabs).toEqual([]);
  });

  it("asks «No se pudo guardar. ¿Cerrar igual?» when that save fails, and keeps the tab unless told yes", async () => {
    const api = stubApi(() => json({ detail: "boom" }, 500));
    renderModule(`#/crm/cotizador/${Q(1)}`);
    fireEvent.change(await shownField("Cantidad de la línea 1"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Cerrar CN00041" }));
    const dialog = await screen.findByRole("dialog", { name: "No se pudo guardar. ¿Cerrar igual?" });
    expect(api.savesOf(1)).toBe(1);
    fireEvent.click(within(dialog).getByRole("button", { name: "Seguir editando" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(quoteTabs()).toHaveLength(1);
    expect(within(shown()).getByLabelText("Cantidad de la línea 1")).toHaveValue("5");

    fireEvent.click(screen.getByRole("button", { name: "Cerrar CN00041" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cerrar igual" }));
    await waitFor(() => expect(quoteTabs()).toHaveLength(0));
  });

  it("a quote frozen by another tab's approval never freezes another quote open beside it", async () => {
    fakeTimers();
    const api = stubApi((revisionId) =>
      revisionId === "r-1"
        ? json({ detail: { code: "not_a_draft", message: "only a draft revision is edited" } }, 409)
        : json({ revision_id: revisionId, version: 4, computed: computedFixture() }),
    );
    renderModule(`#/crm/cotizador/${Q(2)}`);
    await showsQuote("CN00042");
    act(() => void (window.location.hash = `#/crm/cotizador/${Q(1)}`));
    await showsQuote("CN00041");
    fireEvent.change(await shownField("Cantidad de la línea 1"), { target: { value: "2" } });
    await autosave();
    expect(await within(shown()).findByText("Esta cotización cambió en otra pestaña (se aprobó o anuló).")).toBeInTheDocument();
    expect(within(shown()).getByLabelText("Cantidad de la línea 1")).toBeDisabled();

    fireEvent.click(tabOf("CN00042"));
    await waitFor(() => expect(within(shown()).getByRole("heading", { level: 1, name: "Cotización CN00042" })).toBeInTheDocument());
    expect(within(shown()).queryByText(/cambió en otra pestaña/)).not.toBeInTheDocument();
    const qty = within(shown()).getByLabelText("Cantidad de la línea 1");
    expect(qty).toBeEnabled();
    fireEvent.change(qty, { target: { value: "4" } });
    await autosave();
    await waitFor(() => expect(api.savesOf(2)).toBe(1));
    expect(await within(shown()).findByText(/guardado automáticamente/)).toBeInTheDocument();
  });
});

describe("Cotizador module — fix round 1", () => {
  it("«/» focuses the catalog search of the quote shown, not of a hidden one mounted first (I1)", async () => {
    stubApi();
    renderModule(`#/crm/cotizador/${Q(1)}`);
    await showsQuote("CN00041");
    act(() => void (window.location.hash = `#/crm/cotizador/${Q(2)}`));
    await showsQuote("CN00042");
    const search = await shownField("Agregar del catálogo");
    fireEvent.keyDown(document.body, { key: "/" });
    expect(search).toHaveFocus();
  });

  it("two closes whose saves both fail ask one after the other, and both can close (I2)", async () => {
    stubApi(() => json({ detail: "boom" }, 500));
    renderModule(`#/crm/cotizador/${Q(1)}`);
    fireEvent.change(await shownField("Cantidad de la línea 1"), { target: { value: "5" } });
    act(() => void (window.location.hash = `#/crm/cotizador/${Q(2)}`));
    await showsQuote("CN00042");
    fireEvent.change(await shownField("Cantidad de la línea 1"), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Cerrar CN00041" }));
    fireEvent.click(screen.getByRole("button", { name: "Cerrar CN00042" }));
    const first = await screen.findByRole("dialog", { name: "No se pudo guardar. ¿Cerrar igual?" });
    await waitFor(() => expect(first).toHaveTextContent("CN00041"));
    fireEvent.click(within(first).getByRole("button", { name: "Cerrar igual" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("CN00042"));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cerrar igual" }));
    await waitFor(() => expect(quoteTabs()).toHaveLength(0));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a close that finishes after the operator left the module does not pull the address back (I3)", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const api = stubApi(() => gate.then(() => json({ revision_id: "r-1", version: 4, computed: computedFixture() })));
    renderModule(`#/crm/cotizador/${Q(1)}`);
    fireEvent.change(await shownField("Cantidad de la línea 1"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Cerrar CN00041" }));
    await waitFor(() => expect(api.savesOf(1)).toBe(1));
    act(() => void (window.location.hash = "#/crm/resumen"));
    await screen.findByText("Fuera del módulo");
    await act(async () => {
      release();
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(window.location.hash).toBe("#/crm/resumen");
  });

  it("no second warning after «Cerrar igual» on an incomplete draft (M1)", async () => {
    stubApi();
    renderModule(`#/crm/cotizador/${Q(1)}`);
    fireEvent.change(await shownField("Cantidad de la línea 1"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Cerrar CN00041" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cerrar igual" }));
    await waitFor(() => expect(quoteTabs()).toHaveLength(0));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(screen.queryByText(/cotización CN00041 no se guardaron: el borrador estaba incompleto/)).not.toBeInTheDocument();
  });

  it("after a close the focus goes to the tab now selected, then to «Todas» (M2); the × is outside the tablist (M3)", async () => {
    seed([1, 2], 1);
    stubApi();
    renderModule(`#/crm/cotizador/${Q(1)}`);
    await showsQuote("CN00041");
    expect(within(tablist()).queryByRole("button", { name: /Cerrar/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cerrar CN00041" }));
    await waitFor(() => expect(tabOf("CN00042")).toHaveFocus());
    fireEvent.keyDown(tabOf("CN00042"), { key: "Delete" });
    await waitFor(() => expect(within(tablist()).getByRole("tab", { name: "Todas" })).toHaveFocus());
  });

  it("a restored tab whose quote is gone says so, and can be closed (M6)", async () => {
    seed([7], 7);
    stubApi(undefined, [7]);
    renderModule(`#/crm/cotizador/${Q(7)}`);
    expect(await within(shown()).findByText("Esta cotización no existe.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cerrar CN00047" }));
    await waitFor(() => expect(quoteTabs()).toHaveLength(0));
  });

  it("the old one-quote address lands on the quote's tab (M6)", async () => {
    stubApi();
    renderModule(`#/crm/cotizar/${Q(1)}`);
    await waitFor(() => expect(window.location.hash).toBe(`#/crm/cotizador/${Q(1)}`));
    await showsQuote("CN00041");
    expect(quoteTabs()).toHaveLength(1);
  });
});

describe("Cotizador module — «Preferencias»", () => {
  const prefsHeading = () => screen.queryByRole("heading", { level: 1, name: "Preferencias del Cotizador" });

  it("#/crm/cotizador/preferencias shows the fixed pane: no quote is read, no tab is opened", async () => {
    const api = stubApi();
    renderModule("#/crm/cotizador/preferencias");
    expect(await screen.findByRole("heading", { level: 1, name: "Preferencias del Cotizador" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Equipos")).toHaveValue("40");
    expect(quoteTabs()).toHaveLength(0);
    expect(api.calls.some((c) => c.path.startsWith("/v2/quotes/") && c.path !== "/v2/quotes/clients")).toBe(false);
    expect(screen.getByRole("button", { name: "Preferencias" })).toHaveAttribute("aria-current", "page");
    expect(window.location.hash).toBe("#/crm/cotizador/preferencias");
  });

  it("the header's and the list's «Preferencias» open it; «Todas» goes back to the list", async () => {
    stubApi();
    renderModule("#/crm/cotizador");
    // The list's own button (in the «Todas» panel).
    const listButton = await within(screen.getByRole("tabpanel")).findByRole("button", { name: "Preferencias" });
    expect(listButton).toBeEnabled();
    fireEvent.click(listButton);
    await waitFor(() => expect(window.location.hash).toBe("#/crm/cotizador/preferencias"));
    expect(await screen.findByRole("heading", { level: 1, name: "Preferencias del Cotizador" })).toBeInTheDocument();
    expect(within(tablist()).getByRole("tab", { name: "Todas" })).toHaveAttribute("aria-selected", "false");

    fireEvent.click(within(tablist()).getByRole("tab", { name: "Todas" }));
    await waitFor(() => expect(window.location.hash).toBe("#/crm/cotizador"));
    await waitFor(() => expect(prefsHeading()).toBeNull());
    expect(screen.getByRole("heading", { level: 1, name: "Cotizaciones" })).toBeVisible();

    // The header's button, beside «+ Nueva».
    const headerButton = screen.getAllByRole("button", { name: "Preferencias" }).find((b) => b.getAttribute("data-module-preferences") !== null)!;
    expect(headerButton.className).toContain("min-h-[44px]");
    fireEvent.click(headerButton);
    await waitFor(() => expect(window.location.hash).toBe("#/crm/cotizador/preferencias"));
  });

  it("opened directly with restored tabs, it stays on «Preferencias»; a quote tab is one click away", async () => {
    seed([1, 2], 2);
    const api = stubApi();
    renderModule("#/crm/cotizador/preferencias");
    expect(await screen.findByRole("heading", { level: 1, name: "Preferencias del Cotizador" })).toBeInTheDocument();
    await act(async () => {
      for (let i = 0; i < 5; i++) await Promise.resolve();
    });
    expect(window.location.hash).toBe("#/crm/cotizador/preferencias");
    expect(quoteTabs()).toHaveLength(2);
    // The restored tab is not read behind the pane.
    expect(api.reads(1) + api.reads(2)).toBe(0);
    fireEvent.click(tabOf("CN00041"));
    await showsQuote("CN00041");
    expect(prefsHeading()).toBeNull();
  });
});
