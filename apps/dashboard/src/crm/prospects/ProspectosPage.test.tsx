/**
 * «Prospectos»: new people only by default, best first, 25 at a time; the card's logo or initials,
 * its links (only those found, in a new tab); «Contactar» (with the institution choice when two
 * match), «Más tarde» (a future date) and «Descartar» (a reason, then «Deshacer»). Invented data only.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { ProspectosPage } from "./ProspectosPage";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const NOW = new Date("2026-10-23T15:00:00Z");
const ID1 = "11111111-1111-4111-8111-111111111111";
const ID2 = "22222222-2222-4222-8222-222222222222";
const ORG_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ORG_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

export const item = (over: Record<string, unknown> = {}) => ({
  id: ID1, person_name: "Ana Prueba", role: "Investigadora", organization_name: "Instituto Ficticio",
  unit: "Lab de Prueba", source: "fondequip", fund_label: "Fondequip 2025", amount_label: "$ 180 M",
  awarded_on: "2025-10-01", why_now: "Ganó un Fondequip para comprar una centrífuga", equipment: ["Centrífuga Ortoalresa"],
  email: "ana@example.test", email_source_url: "https://example.test/paper", email_source_year: 2024, phone: null,
  links: { website: "https://example.test", linkedin_person: null, linkedin_org: "https://www.linkedin.com/company/ficticio",
           orcid: null, scholar: null },
  has_logo: false, confidence: "alta", score: 90, state: "new", reasons: ["No está en el CRM ni en nuestros correos"],
  organization_candidates: [], latest_verdict: null, returns_today: false, acquired_at: "2026-10-23T09:00:00Z", ...over,
});
const COUNTS = { nuevos: 2, por_revisar: 2, contactados_mes: 1, vuelven_hoy: 0, omitidos_bajas: 3 };
const LIST = {
  counts: COUNTS,
  items: [item(), item({ id: ID2, person_name: "Bruno Ficticio", organization_name: "Laboratorio Ejemplo", has_logo: true,
                         email: null, email_source_url: null, phone: "+56 2 2000 0000", score: 40,
                         links: { website: null, linkedin_person: "https://www.linkedin.com/in/bruno", linkedin_org: null, orcid: null, scholar: null } })],
  has_more: true,
};

interface Call { method: string; path: string; body: Record<string, unknown> | null; key: string | null }

function stubApi(onPost: (path: string, body: Record<string, unknown>) => Response = () => json({ ok: true })) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const u = new URL(raw, "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    const headers = (init?.headers ?? {}) as Record<string, string>;
    calls.push({ method, path: u.pathname + u.search, body, key: headers["Idempotency-Key"] ?? null });
    if (method === "POST") return Promise.resolve(onPost(u.pathname, body ?? {}));
    if (u.pathname === "/v2/prospect-findings") {
      const offset = Number(u.searchParams.get("offset") ?? "0");
      return Promise.resolve(json(offset > 0 ? { counts: COUNTS, items: [item({ id: "33333333-3333-4333-8333-333333333333", person_name: "Clara Tercera" })], has_more: false } : LIST));
    }
    return Promise.resolve(json({}, 404));
  }));
  return calls;
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
      <ProspectosPage navigate={navigate} now={NOW} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
  return navigate;
}

beforeEach(() => clearResourceCache());
afterEach(() => vi.unstubAllGlobals());

describe("ProspectosPage", () => {
  it("muestra solo nuevos por defecto, ordenados, con «Ver más»", async () => {
    const calls = stubApi();
    renderPage();
    const cards = await screen.findAllByTestId(/^prospect-card-/);
    expect(cards.map((c) => c.dataset.testid)).toEqual([`prospect-card-${ID1}`, `prospect-card-${ID2}`]);
    expect(calls[0].path).toContain("view=nuevos");
    expect(screen.getByText("Gente nueva que podría comprarnos. Revisa, contacta o descarta.")).toBeInTheDocument();
    expect(screen.getByText("3 omitidos por bajas")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ver más" }));
    await screen.findByText("Clara Tercera");
    // The next page starts after the two the server sent.
    expect(calls.some((c) => c.path.includes("offset=2"))).toBe(true);
  });

  it("una tarjeta sin logo muestra iniciales", async () => {
    stubApi();
    renderPage();
    const first = await screen.findByTestId(`prospect-card-${ID1}`);
    expect(within(first).getByTestId("prospect-initials")).toHaveTextContent("IF");
    const second = screen.getByTestId(`prospect-card-${ID2}`);
    const img = within(second).getByTestId("prospect-logo") as HTMLImageElement;
    expect(img.src).toContain(`/v2/prospect-findings/${ID2}/logo`);
    expect(img).toHaveAttribute("alt", "");
  });

  it("enlaces solo aparecen cuando existen y abren en pestaña nueva", async () => {
    stubApi();
    renderPage();
    const first = await screen.findByTestId(`prospect-card-${ID1}`);
    const site = within(first).getByRole("link", { name: /Sitio web/ });
    expect(site).toHaveAttribute("href", "https://example.test");
    expect(site).toHaveAttribute("target", "_blank");
    expect(site.getAttribute("rel")).toContain("noopener");
    expect(within(first).getByRole("link", { name: /LinkedIn institución/ })).toBeInTheDocument();
    expect(within(first).queryByRole("link", { name: /LinkedIn persona/ })).toBeNull();
    expect(within(first).getByRole("link", { name: /fuente: paper 2024/ })).toHaveAttribute("href", "https://example.test/paper");
    const second = screen.getByTestId(`prospect-card-${ID2}`);
    expect(within(second).getByText("Sin correo — llamar")).toBeInTheDocument();
    expect(within(second).queryByRole("link", { name: /Sitio web/ })).toBeNull();
  });

  it("Descartar pide motivo y ofrece Deshacer", async () => {
    const calls = stubApi(() => json({ command: "review-prospect" }));
    renderPage();
    const first = await screen.findByTestId(`prospect-card-${ID1}`);
    fireEvent.click(within(first).getByRole("button", { name: "Descartar a Ana Prueba" }));
    const dialog = await screen.findByRole("dialog", { name: "Descartar a Ana Prueba" });
    const confirm = within(dialog).getByRole("button", { name: "Descartar" });
    expect(confirm).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "No es nuestro rubro" }));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/v2/commands/review-prospect");
    expect(post.body).toEqual({ finding_id: ID1, verdict: "descartar", reason: "No es nuestro rubro" });
    expect(post.key).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Deshacer" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "POST")[1].body).toEqual({ finding_id: ID1, verdict: "reabrir" });
  });

  it("Contactar con institución ambigua pide elegir y reintenta", async () => {
    let posts = 0;
    const calls = stubApi(() => {
      posts += 1;
      if (posts === 1) {
        return json({ error: { code: "conflict", message: "conflict", details: {
          code: "organization_ambiguous", message: "¿Cuál de estas instituciones es?",
          candidates: [{ id: ORG_A, name: "Instituto Ficticio" }, { id: ORG_B, name: "Instituto Ficticio (sede sur)" }] } } }, 409);
      }
      return json({ command: "contact-prospect", person_id: "p1", organization_id: ORG_B });
    });
    renderPage();
    const first = await screen.findByTestId(`prospect-card-${ID1}`);
    fireEvent.click(within(first).getByRole("button", { name: "Contactar a Ana Prueba" }));
    const dialog = await screen.findByRole("dialog", { name: "¿Cuál de estas instituciones es?" });
    fireEvent.click(within(dialog).getByRole("radio", { name: "Instituto Ficticio (sede sur)" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Agregar al CRM" }));
    await screen.findByText("Agregado al CRM");
    const sent = calls.filter((c) => c.method === "POST");
    expect(sent[0].body).toEqual({ finding_id: ID1 });
    expect(sent[1].body).toEqual({ finding_id: ID1, organization_id: ORG_B });
    expect(sent[1].key).not.toBe(sent[0].key);
  });

  it("Más tarde exige fecha futura", async () => {
    const calls = stubApi(() => json({ command: "review-prospect" }));
    renderPage();
    const first = await screen.findByTestId(`prospect-card-${ID1}`);
    fireEvent.click(within(first).getByRole("button", { name: "Más tarde: Ana Prueba" }));
    const dialog = await screen.findByRole("dialog", { name: "Volver a mostrar a Ana Prueba" });
    const date = within(dialog).getByLabelText("Fecha");
    fireEvent.change(date, { target: { value: "2026-10-20" } });
    expect(within(dialog).getByRole("button", { name: "Guardar" })).toBeDisabled();
    expect(within(dialog).getByText("Elige una fecha a partir de mañana.")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "En 1 semana" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ finding_id: ID1, verdict: "mas_tarde", remind_on: "2026-10-30" });
  });

  it("acciones accesibles por teclado con nombre visible", async () => {
    stubApi();
    renderPage();
    const first = await screen.findByTestId(`prospect-card-${ID1}`);
    for (const name of ["Contactar a Ana Prueba", "Más tarde: Ana Prueba", "Descartar a Ana Prueba"]) {
      const button = within(first).getByRole("button", { name });
      expect(button.tagName).toBe("BUTTON");
      expect(button).not.toHaveAttribute("tabindex", "-1");
      expect(button.textContent?.trim().length).toBeGreaterThan(0);
    }
    expect(within(first).getByText("Por qué ahora")).toBeInTheDocument();
  });

  it("un visor ve los prospectos pero no las acciones", async () => {
    stubApi();
    renderPage("viewer");
    const first = await screen.findByTestId(`prospect-card-${ID1}`);
    expect(within(first).queryByRole("button", { name: "Contactar a Ana Prueba" })).toBeNull();
  });
});
