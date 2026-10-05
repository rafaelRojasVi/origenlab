/**
 * Refusals as production sends them reach the operator as Spanish, a stale version can be
 * reloaded without losing what was typed, and every form turns its Idempotency-Key over exactly
 * when the API's receipt rules need it to. Every person, institution and address is invented.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import taxonomyJson from "../../../../api/src/origenlab_api/v2/equipment_taxonomy.json";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { REFUSAL_MESSAGES } from "../commandRefusal";
import { CampaignEditor } from "../marketing/CampaignEditor";
import type { EquipmentTaxonomy } from "../marketing/marketingTypes";
import { clearResourceCache } from "../useResource";
import type { OrganizationAuthoringResponse, PersonAuthoringResponse } from "./crmAuthoringApi";
import { NewPersonForm } from "./NewPersonForm";
import { OrgAuthoringSection } from "./OrgAuthoringSection";
import { PersonDrawer } from "./PersonDrawer";

const PERSON_ID = "0b000000-0000-4000-8000-00000000000b";
const ORG_ID = "0c000000-0000-4000-8000-00000000000c";
const STALE_TEXT = REFUSAL_MESSAGES.stale_version;

function withRole(role: string, node: ReactNode) {
  const session = {
    kind: "signed_in",
    method: "google_session",
    operator: { operatorId: "o1", email: "op@example.test", displayName: "Operadora", role },
    crmAuthoringEnabled: true,
  } as AuthSessionState;
  return <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>{node}</AuthSessionContext.Provider>;
}

/** The body `apps/api/src/origenlab_api/errors.py` sends for a refused command. */
function production(status: number, code: string, generic: string) {
  return {
    status,
    body: {
      error: { code: generic, message: "english text from the API", details: { code, message: "english text from the API" }, request_id: "req-1" },
    },
  };
}

/** The body the API sends for record_busy / service_busy / duplicate: the code on top. */
function productionTop(status: number, code: string) {
  return { status, body: { error: { code, message: "english text from the API", details: {}, request_id: "req-2" } } };
}

type Answer = { status: number; body: unknown } | "network";
type Post = { path: string; body: Record<string, unknown>; key: string | null };

/** GET answers come from `read`; POSTs take the queued answers in order (then 200 receipts). */
function stubApi(read: (path: string, reads: number) => unknown, answers: Answer[]) {
  const posts: Post[] = [];
  const gets: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      const reply = (status: number, b: unknown) =>
        Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
      if ((init?.method ?? "GET").toUpperCase() !== "GET") {
        posts.push({
          path: url.pathname,
          body: JSON.parse(String(init?.body ?? "null")),
          key: new Headers(init?.headers).get("Idempotency-Key"),
        });
        const next = answers.shift();
        if (next === "network") return Promise.reject(new TypeError("Failed to fetch"));
        if (next) return reply(next.status, next.body);
        return reply(200, { ok: true, replayed: false, idempotency_key: "k", command_receipt_id: "r", version: 9 });
      }
      gets.push(url.pathname);
      const body = read(url.pathname, gets.filter((g) => g === url.pathname).length);
      return body === undefined ? reply(404, { detail: "Not Found" }) : reply(200, body);
    }),
  );
  return { posts, gets };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearResourceCache();
});

function person(version: number, displayName: string): PersonAuthoringResponse {
  return {
    person: {
      id: PERSON_ID, display_name: displayName, given_name: null, family_name: null, title: null, status: "active",
      archived_at: null, archive_reason: null, confirmation: "confirmed", version, created_at: null, updated_at: null,
      merged_into_person_id: null,
    },
    contact_points: [],
    affiliations: [],
    notes: [],
    references: { campaign_recipients: 0, opportunity_participants: 0, quotes: 0, evidence_assertions: 0, notes: 0 },
    removal: { allowed: false, reasons: [] },
    authoring: { enabled: true, may_author: true, may_archive: false },
  };
}

describe("a stale version in production", () => {
  it("shows the Spanish message, never the JSON, and «Cargar versión actual» keeps what was typed", async () => {
    // The first read is version 1; after the reload another operator's version 2 is current.
    const api = stubApi(
      (path, reads) => (path.endsWith(`/v2/workspace/people/${PERSON_ID}`) ? (reads === 1 ? person(1, "Persona Ficticia") : person(2, "Persona Cambiada")) : undefined),
      [production(409, "stale_version", "conflict")],
    );
    render(withRole("sales", <PersonDrawer personId={PERSON_ID} onClose={() => undefined} mayAuthor />));
    fireEvent.click(await screen.findByRole("button", { name: "Editar" }));
    const name = screen.getByDisplayValue("Persona Ficticia") as HTMLInputElement;
    fireEvent.change(name, { target: { value: "Nombre Tecleado" } });
    fireEvent.change(screen.getByPlaceholderText("Motivo"), { target: { value: "corrige el nombre" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(STALE_TEXT);
    expect(alert.textContent).not.toMatch(/[{}]|stale_version|conflict|http_409|english text/);

    fireEvent.click(within(alert).getByRole("button", { name: "Cargar versión actual" }));
    await waitFor(() => expect(api.gets.filter((g) => g.endsWith(PERSON_ID))).toHaveLength(2));
    await screen.findByText("Persona Cambiada"); // the drawer title: version 2 is on screen
    expect(screen.getByDisplayValue("Nombre Tecleado")).toBeInTheDocument();
    expect(screen.getByDisplayValue("corrige el nombre")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(api.posts).toHaveLength(2));
    expect(api.posts[1].body).toMatchObject({ expected_version: 2, display_name: "Nombre Tecleado", note: "corrige el nombre" });
    // A refusal settled the first attempt: the second is a new request with a new key.
    expect(api.posts[1].key).not.toBe(api.posts[0].key);
  });

  it("the bare `{detail}` envelope reads the same", async () => {
    stubApi(
      (path) => (path.endsWith(`/v2/workspace/people/${PERSON_ID}`) ? person(1, "Persona Ficticia") : undefined),
      [{ status: 409, body: { detail: { code: "stale_version", message: "the person was modified since you loaded it" } } }],
    );
    render(withRole("sales", <PersonDrawer personId={PERSON_ID} onClose={() => undefined} mayAuthor />));
    fireEvent.click(await screen.findByRole("button", { name: "Editar" }));
    fireEvent.change(screen.getByPlaceholderText("Motivo"), { target: { value: "nota" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(STALE_TEXT);
  });
});

describe("a create form's Idempotency-Key", () => {
  function fill() {
    fireEvent.change(screen.getByPlaceholderText("Nombre completo"), { target: { value: "Persona Ficticia" } });
    fireEvent.change(screen.getByPlaceholderText("Fuente o motivo"), { target: { value: "conocida en feria" } });
  }
  const submit = () => fireEvent.click(screen.getByRole("button", { name: "Crear contacto" }));

  it("is resent while the first attempt may still run, and turned over after a settling refusal", async () => {
    const api = stubApi(() => undefined, [
      "network",
      productionTop(503, "service_busy"),
      productionTop(409, "record_busy"),
      production(409, "command_in_progress", "conflict"),
      production(409, "contact_point_taken", "conflict"),
    ]);
    const done = vi.fn();
    render(<NewPersonForm onDone={done} onCancel={() => undefined} />);
    fill();

    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(REFUSAL_MESSAGES.network_error);
    submit();
    expect(await screen.findByText(REFUSAL_MESSAGES.service_busy)).toBeInTheDocument();
    submit();
    expect(await screen.findByText(REFUSAL_MESSAGES.record_busy)).toBeInTheDocument();
    expect(screen.getByRole("alert").textContent).not.toMatch(/[{}]|record_busy|english/);
    submit();
    expect(await screen.findByText(REFUSAL_MESSAGES.command_in_progress)).toBeInTheDocument();
    submit();
    expect(await screen.findByText(REFUSAL_MESSAGES.contact_point_taken)).toBeInTheDocument();
    submit();
    await waitFor(() => expect(done).toHaveBeenCalledTimes(1));

    const keys = api.posts.map((p) => p.key);
    expect(keys).toHaveLength(6);
    // No answer, a 503, a busy row, «still running»: the first attempt may yet commit, so the
    // same request is resent under the same key — a replay at worst, never a second person.
    expect(new Set(keys.slice(0, 5)).size).toBe(1);
    // A refusal that settles it (nothing was written): the next attempt is a new request.
    expect(keys[5]).not.toBe(keys[4]);
  });

  it("an edited body never reuses the key of an unanswered attempt", async () => {
    const api = stubApi(() => undefined, ["network"]);
    render(<NewPersonForm onDone={() => undefined} onCancel={() => undefined} />);
    fill();
    submit();
    await screen.findByRole("alert");
    fireEvent.change(screen.getByPlaceholderText("Nombre completo"), { target: { value: "Persona Corregida" } });
    submit();
    await waitFor(() => expect(api.posts).toHaveLength(2));
    expect(api.posts[1].key).not.toBe(api.posts[0].key);
  });
});

function organization(): OrganizationAuthoringResponse {
  return {
    organization: {
      id: ORG_ID, name: "Institución Ficticia", legal_name: null, kind: "company", status: "active",
      archived_at: null, archive_reason: null, confirmation: "confirmed", version: 3,
      merged_into_organization_id: null, created_at: null,
    },
    identifiers: [], domains: [], classifications: [], product_lines: [], contact_points: [], people: [], notes: [],
    references: { campaign_recipients: 0, opportunities: 0, quotes: 0, affiliations: 0, evidence_assertions: 0, catalog_products: 0 },
    removal: { allowed: false, reasons: [] },
    authoring: { enabled: true, may_author: true, may_archive: false },
    web_suggestions: null,
  };
}

describe("a section that stays open across commands", () => {
  it("takes a new key after each success (product lines)", async () => {
    const api = stubApi((path) => (path.endsWith(`/v2/workspace/organizations/${ORG_ID}/authoring`) ? organization() : undefined), []);
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    const link = async (note: string) => {
      const ika = await screen.findByText("IKA");
      fireEvent.click(within(ika.closest("div") as HTMLElement).getByRole("button", { name: "+" }));
      fireEvent.change(screen.getByPlaceholderText("Motivo"), { target: { value: note } });
      fireEvent.click(screen.getByRole("button", { name: "Vincular" }));
    };
    await link("distribuidor oficial");
    await waitFor(() => expect(api.posts).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Vincular" })).toBeNull());
    await link("otra nota");
    await waitFor(() => expect(api.posts).toHaveLength(2));
    expect(api.posts.map((p) => p.path)).toEqual([
      "/v2/commands/link-organization-product-line",
      "/v2/commands/link-organization-product-line",
    ]);
    expect(api.posts[1].key).not.toBe(api.posts[0].key);
  });
});

describe("a new campaign draft", () => {
  const taxonomy = taxonomyJson as EquipmentTaxonomy;
  const saved = {
    status: 200,
    body: {
      campaign_id: "a0000000-0000-4000-8000-0000000000d1", status: "draft", version: 1, saved_at: "2026-10-05T12:00:00Z",
      changed_fields: [], storage: { table: "outbound.campaign", database: "origenlab_test_abcd1234" }, replayed: false,
    },
  };

  it("keeps one key for the create until it succeeds, then saves under new keys", async () => {
    const api = stubApi(() => undefined, ["network", saved, production(409, "stale_version", "conflict")]);
    render(
      withRole(
        "sales",
        <CampaignEditor seed={{ stored: null }} taxonomy={taxonomy} draftsEnabled onSaved={() => undefined} onDuplicate={() => undefined} />,
      ),
    );
    fireEvent.change(screen.getByLabelText("Nombre interno"), { target: { value: "Campaña ficticia" } });
    fireEvent.change(screen.getByLabelText("Máximo de envíos"), { target: { value: "50" } });
    fireEvent.change(screen.getByLabelText("Días para recontactar"), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar borrador" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(REFUSAL_MESSAGES.network_error);

    // The copy changed before the retry: still the same intent, so still the same key — a create
    // whose answer was lost is never stored twice.
    fireEvent.change(screen.getByLabelText("Nombre interno"), { target: { value: "Campaña ficticia v2" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar borrador" }));
    await screen.findByText(/Guardado/);

    fireEvent.change(screen.getByLabelText("Nombre interno"), { target: { value: "Campaña ficticia v3" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Otro operador guardó este borrador mientras lo editabas.");
    expect(alert.textContent).not.toMatch(/[{}]|stale_version|english/);

    expect(api.posts.map((p) => p.path)).toEqual([
      "/v2/commands/create-campaign-draft",
      "/v2/commands/create-campaign-draft",
      "/v2/commands/save-campaign-draft",
    ]);
    expect(api.posts[1].key).toBe(api.posts[0].key);
    expect(api.posts[2].key).not.toBe(api.posts[0].key);
  });
});
