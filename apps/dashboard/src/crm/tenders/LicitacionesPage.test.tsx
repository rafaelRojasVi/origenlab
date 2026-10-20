/**
 * «Licitaciones»: the list, its filter chips with counts, the empty state, the row link and who sees
 * «Nueva licitación». Invented data only.
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

function stubApi(list: unknown = LIST) {
  const gets: string[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const u = new URL(raw, "http://localhost");
    gets.push(u.pathname + u.search);
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

function renderPage(role: "sales" | "admin" | "viewer" = "sales") {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: sessionAs(role), signOut: async () => true }}>
      <LicitacionesPage navigate={navigate} now={NOW} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
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
