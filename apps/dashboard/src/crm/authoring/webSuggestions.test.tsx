/**
 * «Confirmar institución» and «Sugerencias de la web» on the institution card. The API is a stub
 * that records every command; every institution, RUT and domain is invented.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearResourceCache } from "../useResource";
import type { OrganizationAuthoringResponse, OrgWebSuggestion } from "./crmAuthoringApi";
import { OrgAuthoringSection } from "./OrgAuthoringSection";
import { applyAllPlan, rutKey, suggestionRows } from "./webSuggestions";

const ORG_ID = "0c000000-0000-4000-8000-00000000000c";

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

function suggestion(over: Partial<OrgWebSuggestion> = {}): OrgWebSuggestion {
  return {
    display_name: "Universidad Ficticia del Sur",
    legal_name: "Corporación Universidad Ficticia del Sur",
    rut: "12345678-5",
    rut_source: "official",
    website: "https://www.ficticia.example",
    email_domain: "ficticia.example",
    type: "universidad",
    city: "Ciudad Inventada",
    region: null,
    confidence: "high",
    sources: [{ url: "https://www.ficticia.example/transparencia", shows: "RUT y razón social" }],
    notes: null,
    ...over,
  };
}

function fixture(
  over: Partial<OrganizationAuthoringResponse["organization"]> = {},
  web: OrgWebSuggestion | null = suggestion(),
): OrganizationAuthoringResponse {
  return {
    organization: {
      id: ORG_ID, name: "Ficticiasur", legal_name: null, kind: "unknown", status: "active",
      archived_at: null, archive_reason: null, confirmation: "machine_proposed", version: 3,
      merged_into_organization_id: null, created_at: "2026-09-01T00:00:00+00:00", ...over,
    },
    identifiers: [],
    domains: [],
    classifications: [],
    product_lines: [],
    contact_points: [],
    people: [],
    notes: [],
    references: { campaign_recipients: 0, opportunities: 1, quotes: 1, affiliations: 0, evidence_assertions: 0, catalog_products: 0 },
    removal: { allowed: false, reasons: [] },
    authoring: { enabled: true, may_author: true, may_archive: false },
    web_suggestions: web,
  };
}

type Answer = { status: number; body: unknown };

/** GETs answer `card`; POSTs answer the queued answers in order, then receipts with version + 1. */
function stubApi(card: OrganizationAuthoringResponse, answers: Answer[] = []) {
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  let version = card.organization.version;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      const reply = (status: number, b: unknown) =>
        Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
      if ((init?.method ?? "GET").toUpperCase() !== "GET") {
        posts.push({ path: url.pathname, body: JSON.parse(String(init?.body ?? "null")) });
        const next = answers.shift();
        if (next) return reply(next.status, next.body);
        version += 1;
        return reply(200, { ok: true, replayed: false, idempotency_key: "key", command_receipt_id: "rcpt", version });
      }
      if (url.pathname.endsWith(`/v2/workspace/organizations/${ORG_ID}/authoring`)) return reply(200, card);
      return reply(404, {});
    }),
  );
  return posts;
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("the suggestion rows", () => {
  it("one RUT has one spelling", () => {
    expect(rutKey("12.345.678-5")).toBe("12345678-5");
    expect(rutKey(" 012345678 k")).toBe("12345678-K");
    expect(rutKey("")).toBeNull();
  });

  it("marks what the CRM already holds as applied, whatever the RUT's spelling", () => {
    const card = fixture({ name: "Universidad Ficticia del Sur" });
    card.identifiers = [{ id: "i1", scheme: "rut", value_norm: "12.345.678-5", removed_at: null, remove_reason: null }];
    card.domains = [{ id: "d1", domain_norm: "ficticia.example", scope: "shared", removed_at: null, remove_reason: null }];
    const rows = suggestionRows(card, suggestion());
    expect(rows.filter((r) => r.applied).map((r) => r.field)).toEqual(["name", "rut", "domain"]);
    expect(applyAllPlan(rows, suggestion()).map((r) => r.field)).toEqual(["legal_name", "kind"]);
  });

  it("«Aplicar todo» never takes a directory RUT, and is not offered below high confidence", () => {
    const rows = suggestionRows(fixture(), suggestion({ rut_source: "directory" }));
    expect(applyAllPlan(rows, suggestion({ rut_source: "directory" })).map((r) => r.field)).toEqual([
      "name", "legal_name", "kind", "domain",
    ]);
    expect(applyAllPlan(rows, suggestion({ confidence: "medium" }))).toEqual([]);
  });
});

describe("«Confirmar institución»", () => {
  it("sales confirms a proposed institution with its loaded version", async () => {
    const posts = stubApi(fixture({}, null));
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    fireEvent.click(await screen.findByRole("button", { name: "Confirmar institución" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ path: "/v2/commands/confirm-organization-record", body: { organization_id: ORG_ID, expected_version: 3 } });
  });

  it("a confirmed institution says who and when, with no button", async () => {
    stubApi(fixture({ confirmation: "confirmed", confirmed_by_name: "Operadora Uno", confirmed_at: "2026-10-04T15:00:00+00:00" }, null));
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    const bar = await screen.findByTestId("org-confirmation");
    expect(within(bar).getByText("Confirmada")).toBeInTheDocument();
    expect(bar).toHaveTextContent("por Operadora Uno el");
    expect(screen.queryByRole("button", { name: "Confirmar institución" })).toBeNull();
  });

  it("without authoring there is no confirm and no apply", async () => {
    const posts = stubApi(fixture());
    render(withRole("viewer", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor={false} admin={false} />));
    expect(await screen.findByText("Sugerencias de la web")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirmar institución" })).toBeNull();
    expect(screen.queryAllByRole("button", { name: /^Aplicar/ })).toEqual([]);
    expect(posts).toEqual([]);
  });
});

describe("«Sugerencias de la web»", () => {
  it("«Aplicar» on the RUT adds identifier scheme rut with the loaded version and the source in the note", async () => {
    const posts = stubApi(fixture());
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    fireEvent.click(await screen.findByRole("button", { name: "Aplicar RUT" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/v2/commands/add-organization-identifier");
    expect(posts[0].body).toMatchObject({ organization_id: ORG_ID, expected_version: 3, scheme: "rut", value: "12345678-5" });
    expect(String(posts[0].body.note)).toContain("https://www.ficticia.example/transparencia");
  });

  it.each([
    ["Nombre", "/v2/commands/update-organization", { name: "Universidad Ficticia del Sur" }],
    ["Razón social", "/v2/commands/update-organization", { legal_name: "Corporación Universidad Ficticia del Sur" }],
    ["Tipo", "/v2/commands/update-organization", { kind: "universidad" }],
    ["Dominio de correo", "/v2/commands/add-organization-domain", { domain: "ficticia.example" }],
  ])("«Aplicar» on %s calls %s with only that field", async (label, path, field) => {
    const posts = stubApi(fixture());
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    fireEvent.click(await screen.findByRole("button", { name: `Aplicar ${label}` }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe(path);
    expect(posts[0].body).toEqual({ organization_id: ORG_ID, expected_version: 3, note: expect.any(String), ...field });
  });

  it("a directory RUT asks for an SII check first", async () => {
    stubApi(fixture({}, suggestion({ rut_source: "directory" })));
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    expect(await screen.findByText("verificar en SII antes de aplicar")).toBeInTheDocument();
  });

  it("a field the CRM already holds shows «ya aplicado» instead of «Aplicar»", async () => {
    stubApi(fixture({ name: "Universidad Ficticia del Sur" }));
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    const list = await screen.findByRole("list", { name: "Sugerencias de la web" });
    expect(within(list).getByText("ya aplicado")).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: "Aplicar Nombre" })).toBeNull();
  });

  it("«Aplicar todo y confirmar» applies in order, each from the version the last one returned, then confirms", async () => {
    const posts = stubApi(fixture());
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    fireEvent.click(await screen.findByRole("button", { name: "Aplicar todo y confirmar" }));
    await waitFor(() => expect(posts).toHaveLength(6));
    expect(posts.map((p) => [p.path.replace("/v2/commands/", ""), p.body.expected_version])).toEqual([
      ["update-organization", 3],
      ["update-organization", 4],
      ["update-organization", 5],
      ["add-organization-identifier", 6],
      ["add-organization-domain", 7],
      ["confirm-organization-record", 8],
    ]);
    expect(await screen.findByText("Sugerencias aplicadas e institución confirmada.")).toBeInTheDocument();
  });

  it("stops at the first refusal, names it, keeps what was applied and does not confirm", async () => {
    const posts = stubApi(fixture(), [
      { status: 200, body: { ok: true, replayed: false, idempotency_key: "key", command_receipt_id: "r1", version: 4 } },
      { status: 200, body: { ok: true, replayed: false, idempotency_key: "key", command_receipt_id: "r2", version: 5 } },
      { status: 200, body: { ok: true, replayed: false, idempotency_key: "key", command_receipt_id: "r3", version: 6 } },
      { status: 409, body: { detail: { code: "identifier_taken", message: "that identifier is recorded on another record" } } },
    ]);
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    fireEvent.click(await screen.findByRole("button", { name: "Aplicar todo y confirmar" }));
    expect(
      await screen.findByText("Se detuvo en RUT: Ese RUT ya está registrado en otra institución. Ya aplicado: Nombre, Razón social, Tipo."),
    ).toBeInTheDocument();
    expect(posts.map((p) => p.path)).not.toContain("/v2/commands/add-organization-domain");
    expect(posts.map((p) => p.path)).not.toContain("/v2/commands/confirm-organization-record");
  });

  it("a version that moved under the operator is said plainly, and nothing else is sent", async () => {
    const posts = stubApi(fixture(), [
      { status: 409, body: { detail: { code: "stale_version", message: "organization was modified since you loaded it" } } },
    ]);
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    fireEvent.click(await screen.findByRole("button", { name: "Aplicar Tipo" }));
    expect(await screen.findByText("Tipo: Otro operador modificó esta institución; recarga y vuelve a intentar.")).toBeInTheDocument();
    expect(posts).toHaveLength(1);
  });
});
