/** A prospect's full profile opens from a direct link (`#/crm/prospectos/<id>`). Invented data only. */
import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { ProspectosPage } from "./ProspectosPage";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const ID = "44444444-4444-4444-8444-444444444444";

const DETAIL = {
  id: ID, person_name: "Diana Detalle", role: "Directora", organization_name: "Instituto Ficticio del Sur", unit: "Lab Sur",
  source: "fondecyt", fund_label: "Fondecyt Regular 2025", amount_label: "$ 110 M", awarded_on: "2025-01-27",
  why_now: "Proyecto nuevo de cuatro años", equipment: ["Sonicador Hielscher", "Balanza OHAUS"],
  email: "diana@example.test", email_source_url: "https://example.test/paper2", email_source_year: 2025, phone: null,
  links: { website: "https://example.test/sur", linkedin_person: null, linkedin_org: null, orcid: "https://orcid.org/0000-0000-0000-0000", scholar: null },
  has_logo: false, confidence: "media", score: 70, state: "new",
  reasons: ["No está en el CRM ni en nuestros correos"], organization_candidates: [], latest_verdict: null,
  returns_today: false, acquired_at: "2026-10-23T09:00:00Z", project_title: "Un proyecto ficticio",
  sources: [["fondo", "https://example.test/fondo2"]], history: [],
};

function session(): AuthSessionState {
  return { kind: "signed_in", method: "google_profile",
    operator: { operatorId: "op", email: "sales@example.cl", displayName: "sales", role: "sales" },
    caseCommandsEnabled: true, crmAuthoringEnabled: true };
}

beforeEach(() => clearResourceCache());
afterEach(() => vi.unstubAllGlobals());

describe("ProspectDetail", () => {
  it("el detalle se abre por enlace directo #/crm/prospectos/<id>", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const u = new URL(raw, "http://localhost");
      if (u.pathname === `/v2/prospect-findings/${ID}`) return Promise.resolve(json(DETAIL));
      if (u.pathname === "/v2/prospect-findings") {
        return Promise.resolve(json({ counts: { nuevos: 0, por_revisar: 0, contactados_mes: 0, vuelven_hoy: 0, omitidos_bajas: 0 }, items: [], has_more: false }));
      }
      return Promise.resolve(json({}, 404));
    }));
    render(
      <AuthSessionContext.Provider value={{ session: session(), signOut: async () => true }}>
        <ProspectosPage id={ID} navigate={vi.fn()} now={new Date("2026-10-23T15:00:00Z")} />
        <Toaster />
      </AuthSessionContext.Provider>,
    );
    const drawer = await screen.findByRole("dialog", { name: "Diana Detalle" });
    for (const heading of ["Persona", "Institución y laboratorio", "Proyecto y fondos", "Cómo contactar",
      "Equipos sugeridos", "Por qué es nuevo", "Fuentes", "Historial"]) {
      expect(within(drawer).getByRole("heading", { name: heading })).toBeInTheDocument();
    }
    expect(within(drawer).getByText("Un proyecto ficticio")).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: /ORCID/ })).toHaveAttribute("href", "https://orcid.org/0000-0000-0000-0000");
    expect(within(drawer).getByRole("link", { name: /example.test\/fondo2/ })).toBeInTheDocument();
    expect(screen.getByText("Hoy no hay nuevos. La búsqueda diaria llega pronto; mientras tanto revisa Más tarde.")).toBeInTheDocument();
  });
});
