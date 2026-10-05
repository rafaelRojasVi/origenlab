/**
 * «Personas sugeridas» on the Personas page and on the institution card: one create-person per
 * click, the suggestion gone on the next read, «Ocultar» local to this browser and storing no
 * address. Every name and address is invented (`example.invalid`).
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { PeoplePage } from "../pages/PeoplePage";
import { clearResourceCache } from "../useResource";
import type { OrganizationAuthoringResponse, PersonSuggestion } from "./crmAuthoringApi";
import { HIDDEN_SUGGESTIONS_KEY } from "./hiddenSuggestions";
import { OrgAuthoringSection } from "./OrgAuthoringSection";

const UNI = "0a000000-0000-4000-8000-00000000000a";
const ANA: PersonSuggestion = {
  suggestion_ref: "ref-ana",
  email: "ana.perez@ficticia.example.invalid",
  display_name: "Ana Pérez Soto",
  name_source: "recipient",
  organization_id: UNI,
  organization_name: "Universidad Ficticia",
  quotes: 2,
  last_sent_at: "2026-04-01T10:00:00+00:00",
  existing_contact_point: "unattributed",
};
const GIS: PersonSuggestion = {
  ...ANA,
  suggestion_ref: "ref-gis",
  email: "minventada@labficticio.example.invalid",
  display_name: "Marta Inventada",
  name_source: "filename",
  organization_id: null,
  organization_name: null,
  quotes: 1,
  existing_contact_point: null,
};

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_session",
    operator: { operatorId: "o1", email: "op@example.test", displayName: "Operadora", role },
    crmAuthoringEnabled: true,
  } as AuthSessionState;
}

function withRole(role: string, node: ReactNode) {
  return (
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>{node}</AuthSessionContext.Provider>
  );
}

function card(): OrganizationAuthoringResponse {
  return {
    organization: {
      id: UNI, name: "Universidad Ficticia", legal_name: null, kind: "universidad", status: "active",
      archived_at: null, archive_reason: null, confirmation: "confirmed", version: 2,
      merged_into_organization_id: null, created_at: null,
    },
    identifiers: [], domains: [], classifications: [], product_lines: [], contact_points: [], people: [], notes: [],
    references: { campaign_recipients: 0, opportunities: 1, quotes: 2, affiliations: 0, evidence_assertions: 0, catalog_products: 0 },
    removal: { allowed: false, reasons: [] },
    authoring: { enabled: true, may_author: true, may_archive: false },
    web_suggestions: null,
    person_suggestions: [ANA],
  };
}

/** Suggestions are served until a create-person for that address succeeds. */
function stubApi() {
  const calls: { path: string; method: string; body: unknown }[] = [];
  let suggestions = [ANA, GIS];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ path: url.pathname, method, body });
      const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
      if (method !== "GET") {
        suggestions = suggestions.filter((s) => s.email !== body.email);
        return json({ ok: true, replayed: false, idempotency_key: "key", command_receipt_id: "rcpt", person_id: "p1", version: 1 });
      }
      const p = url.pathname;
      if (p.endsWith("/v2/workspace/person-suggestions")) return json({ items: suggestions, total: suggestions.length });
      if (p.endsWith(`/v2/workspace/organizations/${UNI}/authoring`)) return json(card());
      if (p.endsWith("/v2/workspace/overview")) return json({ entities: [], opportunities_by_stage: {}, organizations_by_confirmation: {}, contact_points_linked: { organization: 0, person: 0 }, assertions: [], drive_archive: { configured: false, documents: 0, revisions_with_drive_file: 0, revisions_total: 0 } });
      if (p.endsWith("/v2/workspace/pipeline")) return json({ items: [], drive_configured: false });
      if (p.includes("/v2/contacts")) return json({ items: [], total: 0 });
      if (p.endsWith("/v2/workspace/equipment-interests")) return json({ by_contact_point: [], by_address_ref: [], by_organization: [] });
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
  return calls;
}

const posts = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method !== "GET");

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearResourceCache();
});

describe("Personas page", () => {
  it("«Crear persona» is one create-person with the name, the address and the institution; the suggestion then goes", async () => {
    const calls = stubApi();
    render(withRole("sales", <PeoplePage navigate={() => undefined} />));
    const list = await screen.findByRole("list", { name: "Personas sugeridas" });
    const row = within(list).getByText("Ana Pérez Soto").closest("li")!;
    fireEvent.click(within(row).getByRole("button", { name: "Crear persona" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({
      path: "/v2/commands/create-person",
      body: { display_name: "Ana Pérez Soto", email: "ana.perez@ficticia.example.invalid", organization_id: UNI },
    });
    await waitFor(() => expect(screen.queryByText("Ana Pérez Soto")).toBeNull());
    expect(screen.getByText("Marta Inventada")).toBeInTheDocument();
  });

  it("a suggestion without an institution creates the person without one", async () => {
    const calls = stubApi();
    render(withRole("admin", <PeoplePage navigate={() => undefined} />));
    const list = await screen.findByRole("list", { name: "Personas sugeridas" });
    expect(within(list).getByText("nombre del PDF")).toBeInTheDocument();
    const row = within(list).getByText("Marta Inventada").closest("li")!;
    fireEvent.click(within(row).getByRole("button", { name: "Crear persona" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toMatchObject({ display_name: "Marta Inventada", organization_id: null });
  });

  it("«Ocultar» hides it in this browser only: no request, and only the opaque ref is stored", async () => {
    const calls = stubApi();
    const { unmount } = render(withRole("sales", <PeoplePage navigate={() => undefined} />));
    const list = await screen.findByRole("list", { name: "Personas sugeridas" });
    expect(screen.getByText(/sólo las oculta en este navegador/)).toBeInTheDocument();
    const row = within(list).getByText("Ana Pérez Soto").closest("li")!;
    fireEvent.click(within(row).getByRole("button", { name: "Ocultar" }));
    expect(screen.queryByText("Ana Pérez Soto")).toBeNull();
    expect(posts(calls)).toEqual([]);
    const stored = window.localStorage.getItem(HIDDEN_SUGGESTIONS_KEY) ?? "";
    expect(JSON.parse(stored)).toEqual(["ref-ana"]);
    expect(stored).not.toContain("@");
    unmount();
    render(withRole("sales", <PeoplePage navigate={() => undefined} />));
    await screen.findByText("Marta Inventada");
    expect(screen.queryByText("Ana Pérez Soto")).toBeNull();
  });

  it("still works when the browser refuses storage", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    stubApi();
    render(withRole("sales", <PeoplePage navigate={() => undefined} />));
    const list = await screen.findByRole("list", { name: "Personas sugeridas" });
    fireEvent.click(within(within(list).getByText("Ana Pérez Soto").closest("li")!).getByRole("button", { name: "Ocultar" }));
    expect(screen.queryByText("Ana Pérez Soto")).toBeNull();
  });

  it("a viewer sees the suggestions but no «Crear persona»", async () => {
    const calls = stubApi();
    render(withRole("viewer", <PeoplePage navigate={() => undefined} />));
    await screen.findByRole("list", { name: "Personas sugeridas" });
    expect(screen.queryByRole("button", { name: "Crear persona" })).toBeNull();
    expect(posts(calls)).toEqual([]);
  });
});

describe("institution card", () => {
  it("lists the card's suggested people and creates one linked to this institution", async () => {
    const calls = stubApi();
    render(withRole("sales", <OrgAuthoringSection organizationId={UNI} mayAuthor admin={false} />));
    expect(await screen.findByText("Personas sugeridas (1)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Crear persona" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]).toMatchObject({ path: "/v2/commands/create-person", body: { email: ANA.email, organization_id: UNI } });
  });
});
