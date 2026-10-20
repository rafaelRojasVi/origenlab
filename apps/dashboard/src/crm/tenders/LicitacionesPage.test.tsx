/**
 * «Licitaciones»: «En Mercado Público» (the default view: families, search, «Preparar», «Abrir»), then
 * «Mis licitaciones» — the list, its filter chips with counts, the empty state, the row link — and who
 * sees «Nueva licitación». Invented data only.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { LicitacionesPage } from "./LicitacionesPage";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const NOW = new Date("2026-10-19T15:00:00Z");
const ID1 = "11111111-1111-4111-8111-111111111111";
const ID2 = "22222222-2222-4222-8222-222222222222";

const row = (over: Record<string, unknown>) => ({
  id: ID1, version: 3, code: "1234567-12-LE26", title: "Compra de balanzas de ejemplo",
  institution: { id: "o1", name: "Hospital Ejemplo", confirmation: "confirmed" },
  closes_at: "2026-10-21T18:00:00Z", status: "preparando",
  amount: { neto: 1500000, currency: "CLP", source: "estimado" }, offer_quote_number: null,
  preparation: { read: null, fill: null }, ...over,
});
const LIST = {
  tenders: [
    row({}),
    row({ id: ID2, code: "7654321-1-L126", title: "Reactivos de ejemplo", institution: { id: "o2", name: "Liceo Ficticio", confirmation: "machine_proposed" },
      closes_at: "2026-10-10T18:00:00Z", status: "postulada", amount: { neto: 900000, currency: "CLP", source: "oferta" } }),
  ],
  counts: { preparando: 1, postulada: 1, adjudicada: 0, perdida: 2 },
};

const notice = (over: Record<string, unknown>) => ({
  id: "n1", code: "1111111-11-LE26", title: "Adquisición de balanzas analíticas", buyer: "Hospital Ejemplo Norte",
  region: "Región Ejemplo", closes_at: "2026-10-23T18:00:00Z", published_at: null, first_seen_at: "2026-10-19T12:00:00Z",
  estimated_amount: { amount: 2500000, currency: "CLP" }, tier: "catalogo",
  families: [{ key: "pesaje", label: "Balanzas y humedad" }],
  items: [{ name: "Balanzas", description: "Balanza analítica 0,1 mg", quantity: 2, unit: "Unidad", unspsc: "41111500" }],
  item_count: 4, detail: true, portal_url: "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idlicitacion=1111111-11-LE26",
  opportunity_id: null, ...over,
});
const DISCOVERED = {
  notices: [
    notice({}),
    notice({ id: "n2", code: "2222222-22-LE26", title: "Centrífuga clínica", buyer: "Liceo Ficticio", first_seen_at: "2026-10-01T12:00:00Z",
      families: [{ key: "centrifugas", label: "Centrífugas" }], items: [], item_count: 0, estimated_amount: null, opportunity_id: ID1 }),
    notice({ id: "n3", code: "3333333-33-LE26", title: "Equipos para laboratorio de química", buyer: null, detail: false,
      tier: "laboratorio", families: [], items: [], item_count: 0, estimated_amount: null }),
  ],
  counts: { catalogo: 2, laboratorio: 1 },
  families: [{ key: "pesaje", label: "Balanzas y humedad" }, { key: "centrifugas", label: "Centrífugas" }, { key: "osmometria", label: "Osmómetros" }],
  last_checked_at: "2026-10-19T14:45:00Z",
};

function stubApi(list: unknown = LIST, discovered: unknown = DISCOVERED) {
  const gets: string[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const u = new URL(raw, "http://localhost");
    gets.push(u.pathname + u.search);
    if (u.pathname === "/v2/tenders/discovered") return Promise.resolve(json(discovered));
    return Promise.resolve(u.pathname === "/v2/tenders" ? json(list) : json({}, 404));
  }));
  return gets;
}

function sessionAs(role: "sales" | "admin" | "viewer"): AuthSessionState {
  return {
    kind: "signed_in", method: "google_profile",
    operator: { operatorId: `op-${role}`, email: `${role}@example.cl`, displayName: role, role },
    caseCommandsEnabled: true, crmAuthoringEnabled: true,
  };
}

function renderPage(role: "sales" | "admin" | "viewer" = "sales", view: "mias" | "mercado" = "mias") {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: sessionAs(role), signOut: async () => true }}>
      <LicitacionesPage navigate={navigate} now={NOW} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
  if (view === "mias") fireEvent.click(screen.getByRole("button", { name: "Mis licitaciones" }));
  return navigate;
}

beforeEach(() => clearResourceCache());
afterEach(() => vi.unstubAllGlobals());

describe("LicitacionesPage", () => {
  it("shows the filter chips with their counts", async () => {
    stubApi();
    renderPage();
    await screen.findByText("Compra de balanzas de ejemplo");
    const chips = within(screen.getByRole("group", { name: "Filtrar por estado" }));
    expect(chips.getByRole("button", { name: "Preparando 1" })).toBeInTheDocument();
    expect(chips.getByRole("button", { name: "Postulada 1" })).toBeInTheDocument();
    expect(chips.getByRole("button", { name: "Adjudicada 0" })).toBeInTheDocument();
    expect(chips.getByRole("button", { name: "Perdida 2" })).toBeInTheDocument();
    expect(chips.getByRole("button", { name: "Todas 4" })).toBeInTheDocument();
  });

  it("shows code, title, institution, closing, amount and estado in a row", async () => {
    stubApi();
    renderPage();
    const open = await screen.findByTestId(`tender-row-${ID1}`);
    expect(open).toHaveTextContent("1234567-12-LE26");
    expect(open).toHaveTextContent("Compra de balanzas de ejemplo");
    expect(open).toHaveTextContent("Hospital Ejemplo");
    expect(open).toHaveTextContent("cierra en 2 días");
    expect(open).toHaveTextContent("$ 1.500.000");
    expect(open).toHaveTextContent("estimado");
    expect(open).toHaveTextContent("Preparando");
    expect(within(open).queryByText("por confirmar")).toBeNull();
    const closed = screen.getByTestId(`tender-row-${ID2}`);
    expect(closed).toHaveTextContent("cerró");
    expect(closed).not.toHaveTextContent("estimado");
    expect(within(closed).getByText("por confirmar")).toBeInTheDocument();
  });

  it("filters through the API and keeps the counts", async () => {
    const gets = stubApi();
    renderPage();
    await screen.findByText("Compra de balanzas de ejemplo");
    fireEvent.click(screen.getByRole("button", { name: "Postulada 1" }));
    await waitFor(() => expect(gets).toContain("/v2/tenders?status=postulada"));
    expect(screen.getByRole("button", { name: "Postulada 1" })).toHaveAttribute("aria-pressed", "true");
  });

  it("opens the tender's page from a row", async () => {
    stubApi();
    const navigate = renderPage();
    fireEvent.click(await screen.findByRole("link", { name: /Compra de balanzas de ejemplo/ }));
    expect(navigate).toHaveBeenCalledWith("licitaciones", ID1);
  });

  it("has an empty state that says how to start", async () => {
    stubApi({ tenders: [], counts: { preparando: 0, postulada: 0, adjudicada: 0, perdida: 0 } });
    renderPage();
    expect(await screen.findByText("Aún no hay licitaciones. Abre una con su código de Mercado Público.")).toBeInTheDocument();
  });

  it("«Nueva licitación» is for sales and admin, not a viewer", async () => {
    stubApi();
    renderPage("viewer");
    await screen.findByText("Compra de balanzas de ejemplo");
    expect(screen.queryByRole("button", { name: "Nueva licitación" })).toBeNull();
  });

  it("«Nueva licitación» opens the dialog for sales", async () => {
    stubApi();
    renderPage("sales");
    await screen.findByText("Compra de balanzas de ejemplo");
    fireEvent.click(screen.getByRole("button", { name: "Nueva licitación" }));
    expect(screen.getByLabelText("Código o enlace de Mercado Público")).toBeInTheDocument();
  });
});

describe("LicitacionesPage — En Mercado Público", () => {
  it("opens on «En Mercado Público» with the catalog matches, their families and items", async () => {
    stubApi();
    renderPage("sales", "mercado");
    expect(screen.getByRole("button", { name: "En Mercado Público" })).toHaveAttribute("aria-pressed", "true");
    const row = await screen.findByTestId("notice-row-1111111-11-LE26");
    expect(row).toHaveTextContent("Adquisición de balanzas analíticas");
    expect(row).toHaveTextContent("Hospital Ejemplo Norte · Región Ejemplo");
    expect(row).toHaveTextContent("Balanzas y humedad");
    expect(row).toHaveTextContent("2 unidad × Balanzas — Balanza analítica 0,1 mg");
    expect(row).toHaveTextContent("y 3 ítems más");
    expect(row).toHaveTextContent("cierra en 4 días");
    expect(row).toHaveTextContent("$ 2.500.000");
    expect(row).toHaveTextContent("nueva");
    expect(within(row).getByRole("link", { name: "Ver 1111111-11-LE26 en Mercado Público" })).toHaveAttribute("target", "_blank");
    expect(screen.getByTestId("notice-row-3333333-33-LE26")).toHaveTextContent("Institución por leer");
    expect(screen.getByTestId("notice-row-2222222-22-LE26")).not.toHaveTextContent("nueva");
  });

  it("filters by family and by text; a family with nothing open has no chip", async () => {
    stubApi();
    renderPage("sales", "mercado");
    await screen.findByTestId("notice-row-1111111-11-LE26");
    const chips = within(screen.getByRole("group", { name: "Filtrar por familia" }));
    expect(chips.getByRole("button", { name: "Todas 3" })).toBeInTheDocument();
    expect(chips.queryByRole("button", { name: /Osmómetros/ })).toBeNull();
    fireEvent.click(chips.getByRole("button", { name: "Centrífugas 1" }));
    expect(screen.queryByTestId("notice-row-1111111-11-LE26")).toBeNull();
    expect(screen.getByTestId("notice-row-2222222-22-LE26")).toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "Otros equipos de laboratorio 1" }));
    expect(screen.getByTestId("notice-row-3333333-33-LE26")).toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "Todas 3" }));
    fireEvent.change(screen.getByLabelText("Buscar licitación"), { target: { value: "liceo" } });
    expect(screen.getByTestId("notice-row-2222222-22-LE26")).toBeInTheDocument();
    expect(screen.queryByTestId("notice-row-1111111-11-LE26")).toBeNull();
  });

  it("«Preparar» opens «Nueva licitación» with the code; an opened one offers «Abrir»", async () => {
    stubApi();
    const navigate = renderPage("sales", "mercado");
    const row = await screen.findByTestId("notice-row-1111111-11-LE26");
    fireEvent.click(within(row).getByRole("button", { name: "Preparar" }));
    expect(screen.getByLabelText("Código o enlace de Mercado Público")).toHaveValue("1111111-11-LE26");
    const opened = screen.getByTestId("notice-row-2222222-22-LE26");
    expect(within(opened).queryByRole("button", { name: "Preparar" })).toBeNull();
    fireEvent.click(within(opened).getByRole("link", { name: "Abrir" }));
    expect(navigate).toHaveBeenCalledWith("licitaciones", ID1);
  });

  it("a viewer sees the notices but not «Preparar»", async () => {
    stubApi();
    renderPage("viewer", "mercado");
    const row = await screen.findByTestId("notice-row-1111111-11-LE26");
    expect(within(row).queryByRole("button", { name: "Preparar" })).toBeNull();
  });

  it("says when the search has not run yet", async () => {
    stubApi(LIST, { notices: [], counts: { catalogo: 0, laboratorio: 0 }, families: [], last_checked_at: null });
    renderPage("sales", "mercado");
    expect(await screen.findByText("La búsqueda en Mercado Público aún no ha corrido. La primera revisión llega en unos minutos.")).toBeInTheDocument();
  });
});
