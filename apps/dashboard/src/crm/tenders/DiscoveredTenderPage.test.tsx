/**
 * One Mercado Público notice's page: header with «Abrir en Mercado Público», the stat line, every
 * date, every item with what matched, the documents' link, the facts; «Preparar oferta» for sales,
 * «Abrir la licitación» once a case exists, nothing to prepare for a viewer. Invented data only.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearResourceCache } from "../useResource";
import { DiscoveredTenderPage } from "./DiscoveredTenderPage";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const NOW = new Date("2026-10-20T15:00:00Z");
const NOTICE = "44444444-4444-4444-8444-444444444444";
const CASE = "55555555-5555-4555-8555-555555555555";
const PORTAL = "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idlicitacion=4444444-44-LE26";

const page = (over: Record<string, unknown> = {}) => ({
  id: NOTICE, code: "4444444-44-LE26", title: "Adquisición de termociclador de ejemplo", buyer: "Hospital Ejemplo Sur",
  region: "Región Ejemplo", closes_at: "2026-10-21T18:00:00Z", published_at: "2026-10-10T12:00:00Z", first_seen_at: "2026-10-19T12:00:00Z",
  estimated_amount: { amount: 9000000, currency: "CLP" }, tier: "catalogo",
  families: [{ key: "catalogo", label: "Otros de tu catálogo" }], terms: ["tx900", "termociclador"],
  items: [
    { name: "Termocicladores", description: "TX900 o similar", quantity: 1, unit: "Unidad", unspsc: "41106200", category: "Equipos de laboratorio",
      families: [], terms: ["tx900", "termocicladores"], lab_equipment: true },
    { name: "Mesa antivibración", description: null, quantity: 2, unit: null, unspsc: null, category: null, families: [], terms: [], lab_equipment: false },
  ],
  item_count: 2, detail: true, portal_url: PORTAL, opportunity_id: null, description: "Compra de termociclador para biología molecular",
  dates: [
    { key: "FechaPublicacion", label: "Publicación", at: "2026-10-10T12:00:00Z" },
    { key: "FechaFinal", label: "Preguntas hasta", at: "2026-10-14T21:00:00Z" },
    { key: "FechaCierre", label: "Cierre de ofertas", at: "2026-10-21T18:00:00Z" },
    { key: "FechaEstimadaAdjudicacion", label: "Adjudicación estimada", at: "2026-11-20T03:00:00Z" },
  ],
  facts: [{ label: "Tipo", value: "LE" }, { label: "Duración del contrato", value: "12 meses" }],
  source_status: "Publicada", open: true, ...over,
});

function stub(body: unknown) {
  const gets: string[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const u = new URL(raw, "http://localhost");
    gets.push(u.pathname);
    return Promise.resolve(u.pathname === `/v2/tenders/discovered/${NOTICE}` ? json(body) : json({}, 404));
  }));
  return gets;
}

function sessionAs(role: "sales" | "viewer"): AuthSessionState {
  return { kind: "signed_in", method: "google_profile", operator: { operatorId: `op-${role}`, email: `${role}@example.cl`, displayName: role, role },
    caseCommandsEnabled: true, crmAuthoringEnabled: true };
}

function renderPage(role: "sales" | "viewer" = "sales") {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: sessionAs(role), signOut: async () => true }}>
      <DiscoveredTenderPage noticeId={NOTICE} navigate={navigate} now={NOW} />
    </AuthSessionContext.Provider>,
  );
  return navigate;
}

beforeEach(() => clearResourceCache());
afterEach(() => vi.unstubAllGlobals());

describe("DiscoveredTenderPage", () => {
  it("shows the header, the Mercado Público link, the stat line and the facts", async () => {
    const gets = stub(page());
    renderPage();
    expect(await screen.findByRole("heading", { name: "Adquisición de termociclador de ejemplo" })).toBeInTheDocument();
    expect(gets).toContain(`/v2/tenders/discovered/${NOTICE}`);
    const links = screen.getAllByRole("link", { name: "Abrir 4444444-44-LE26 en Mercado Público" });
    expect(links).toHaveLength(2); // the header and «Documentos»
    for (const link of links) {
      expect(link).toHaveAttribute("href", PORTAL);
      expect(link).toHaveAttribute("target", "_blank");
    }
    expect(screen.getByText("Hospital Ejemplo Sur · Región Ejemplo")).toBeInTheDocument();
    expect(screen.getByText(/cierra mañana/)).toBeInTheDocument();
    expect(screen.getByText("1 de 2")).toBeInTheDocument();
    expect(screen.getByText("Duración del contrato").nextSibling).toHaveTextContent("12 meses");
    expect(screen.getByText("Compra de termociclador para biología molecular")).toBeInTheDocument();
  });

  it("puts every date on the track and marks the next one", async () => {
    stub(page());
    renderPage();
    const track = (await screen.findByText("Publicación")).closest("ol") as HTMLElement;
    expect(within(track).getAllByRole("listitem").map((li) => li.querySelector("span.text-xs")?.textContent)).toEqual([
      "Publicación", "Preguntas hasta", "Cierre de ofertas", "Adjudicación estimada"]);
    expect(within(track).getByText("Cierre de ofertas").closest("li")).toHaveAttribute("aria-current", "step");
  });

  it("lists every item with what it matched", async () => {
    stub(page());
    renderPage();
    const table = (await screen.findByText("Termocicladores")).closest("table") as HTMLElement;
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("tx900")).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("TX900 o similar");
    expect(rows[1]).toHaveTextContent("Mesa antivibración");
    expect(rows[1]).toHaveTextContent("—");
  });

  it("«Preparar oferta» opens «Nueva licitación» with the code; a viewer has no such button", async () => {
    stub(page());
    renderPage("sales");
    fireEvent.click(await screen.findByRole("button", { name: "Preparar oferta" }));
    expect(screen.getByLabelText("Código o enlace de Mercado Público")).toHaveValue("4444444-44-LE26");
  });

  it("a viewer sees the page but cannot prepare", async () => {
    stub(page());
    renderPage("viewer");
    await screen.findByRole("heading", { name: "Adquisición de termociclador de ejemplo" });
    expect(screen.queryByRole("button", { name: "Preparar oferta" })).toBeNull();
  });

  it("a notice with a case offers «Abrir la licitación» instead", async () => {
    stub(page({ opportunity_id: CASE }));
    const navigate = renderPage();
    fireEvent.click(await screen.findByRole("link", { name: "Abrir la licitación" }));
    expect(navigate).toHaveBeenCalledWith("licitaciones", CASE);
    expect(screen.queryByRole("button", { name: "Preparar oferta" })).toBeNull();
  });
});
