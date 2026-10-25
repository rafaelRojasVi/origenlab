/**
 * «Editar prospecto» and «¿Ya lo tenemos?» (owner 2026-10-25): only the changed fields are sent, a
 * new correo needs where it was found and is checked against the whole system as it is typed, and
 * the page's search answers from everywhere. Invented data only.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { changesOf } from "./EditProspectDrawer";
import { ProspectosPage } from "./ProspectosPage";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const NOW = new Date("2026-10-23T15:00:00Z");
const item = (over: Record<string, unknown> = {}) => ({
  id: "11111111-1111-4111-8111-111111111111", person_name: "Ana Prueba", role: "Investigadora",
  organization_name: "Instituto Ficticio", unit: "Lab de Prueba", source: "fondequip", fund_label: "Fondequip 2025",
  amount_label: "$ 180 M", awarded_on: "2025-10-01", why_now: "Ganó un fondo", equipment: ["Centrífuga"],
  email: "ana@example.test", email_source_url: "https://example.test/paper", email_source_year: 2024, phone: null,
  links: { website: null, linkedin_person: null, linkedin_org: null, orcid: null, scholar: null },
  has_logo: false, confidence: "alta", score: 90, state: "new", reasons: [], organization_candidates: [],
  latest_verdict: null, returns_today: false, acquired_at: "2026-10-23T09:00:00Z", ...over,
});
const NO_EMAIL = item({ email: null, email_source_url: null, email_source_year: null, phone: "+56 2 2000 0000" });
const FOUND = {
  query: "", kind: "email", found: true, people: [{ person_id: "p-1", display_name: "Ana Prueba", email: "ana@nueva.example.test", organization_name: "Instituto Ficticio" }],
  organizations: [], addresses: [], mail: { messages: 3, last_at: "2026-09-01T12:00:00Z" }, controls: [], prospects: [],
};
const NOT_FOUND = { ...FOUND, found: false, people: [], mail: null };

interface Call { method: string; path: string; body: Record<string, unknown> | null }

function stubApi(lookup: unknown = NOT_FOUND) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const u = new URL(raw, "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, path: u.pathname + u.search, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (method === "POST") return Promise.resolve(json({ ok: true }));
    if (u.pathname === "/v2/prospect-findings") {
      return Promise.resolve(json({ counts: { nuevos: 1, por_revisar: 1, contactados_mes: 0, vuelven_hoy: 0, omitidos_bajas: 0 },
                                   items: [NO_EMAIL], has_more: false }));
    }
    if (u.pathname === "/v2/workspace/lookup") return Promise.resolve(json({ ...(lookup as object), query: u.searchParams.get("q") }));
    return Promise.resolve(json({}, 404));
  }));
  return calls;
}

function renderPage() {
  const session: AuthSessionState = {
    kind: "signed_in", method: "google_profile", operator: { operatorId: "op", email: "s@example.cl", displayName: "Ventas", role: "sales" },
    caseCommandsEnabled: true, crmAuthoringEnabled: true,
  };
  render(
    <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
      <ProspectosPage navigate={vi.fn()} now={NOW} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
}

beforeEach(() => clearResourceCache());
afterEach(() => vi.unstubAllGlobals());

describe("Editar prospecto", () => {
  it("sends only what changed; a blank clears; equipment is a list", () => {
    const before = { person_name: "Ana", role: "Investigadora", organization_name: "Instituto", unit: "Lab", email: "",
      email_source_note: "", phone: "", why_now: "Fondo", equipment: "Centrífuga", fund_label: "", amount_label: "",
      website: "", linkedin_person: "", linkedin_org: "", orcid: "", scholar: "" };
    expect(changesOf(before, { ...before })).toEqual({});
    expect(changesOf(before, { ...before, role: "  Directora ", unit: " ", equipment: "Centrífuga, Balanza" })).toEqual({
      role: "Directora", unit: null, equipment: ["Centrífuga", "Balanza"] });
    expect(changesOf({ ...before, email: "a@x.cl" }, { ...before, email: "A@X.cl" })).toEqual({});
  });

  it("adds a found correo with where it came from, after checking whether we already have it", async () => {
    const calls = stubApi(FOUND);
    renderPage();
    const card = await screen.findByTestId(/^prospect-card-/);
    fireEvent.click(within(card).getByRole("button", { name: /Editar a Ana Prueba/ }));
    const panel = await screen.findByRole("dialog");
    fireEvent.change(within(panel).getByLabelText("Correo"), { target: { value: "ana@nueva.example.test" } });
    expect(await within(panel).findByText(/En el CRM: Ana Prueba/, {}, { timeout: 2000 })).toBeInTheDocument();
    expect(within(panel).getByText(/Nos escribimos: 3 correos/)).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Guardar" }));
    expect(await within(panel).findByText("Di dónde encontraste el correo.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.change(within(panel).getByLabelText(/Dónde lo encontraste/), { target: { value: "sitio del laboratorio" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/v2/commands/edit-prospect");
    expect(post.body).toEqual({ finding_id: NO_EMAIL.id, changes: { email: "ana@nueva.example.test", email_source_note: "sitio del laboratorio" } });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("¿Ya lo tenemos?", () => {
  it("answers a correo from the whole system, and says so when it is nowhere", async () => {
    stubApi(FOUND);
    renderPage();
    await screen.findByTestId(/^prospect-card-/);
    fireEvent.change(screen.getByTestId("system-search"), { target: { value: "ana@nueva.example.test" } });
    const results = await screen.findByTestId("lookup-results", {}, { timeout: 2000 });
    expect(within(results).getByRole("link", { name: "Ana Prueba" })).toHaveAttribute("href", expect.stringContaining("p-1"));
    expect(results).toHaveTextContent("3 correos");
    fireEvent.click(screen.getByRole("button", { name: "Limpiar búsqueda" }));
    expect(screen.queryByTestId("lookup-results")).toBeNull();
  });

  it("says plainly when nothing matched", async () => {
    stubApi(NOT_FOUND);
    renderPage();
    await screen.findByTestId(/^prospect-card-/);
    fireEvent.change(screen.getByTestId("system-search"), { target: { value: "nadie@ninguno.example.test" } });
    expect(await screen.findByTestId("lookup-empty", {}, { timeout: 2000 })).toHaveTextContent("no aparece");
  });
});
