/**
 * CRM authoring: role-gate, viewer no-write, admin create flows, stale_version,
 * ConfirmDialog guard, references panel, candidate confirm/reject.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { PeoplePage } from "../pages/PeoplePage";
import { OrganizationsPage } from "../pages/OrganizationsPage";
import { ProvidersPage } from "../pages/ProvidersPage";
import { PipelinePage } from "../pages/PipelinePage";
import { mayAuthorCrm, isAdmin } from "./authoring";
import { OrgAuthoringSection } from "./OrgAuthoringSection";
import type { OrganizationAuthoringResponse } from "./crmAuthoringApi";

// ── helpers ──────────────────────────────────────────────────────────────────

function sessionFor(role: string | null): AuthSessionState {
  return role === null
    ? ({ kind: "loading" } as AuthSessionState)
    : ({
        kind: "signed_in",
        method: "google_session",
        operator: { operatorId: "o1", email: "op@example.test", displayName: "Operador de prueba", role },
      } as AuthSessionState);
}

function withRole(role: string | null, node: ReactNode) {
  return (
    <AuthSessionContext.Provider value={{ session: sessionFor(role), signOut: async () => true }}>
      {node}
    </AuthSessionContext.Provider>
  );
}

const writes = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET" && c.method !== "HEAD");

/** Click every enabled button inside root (skip navigation buttons). */
function clickEverything(root: HTMLElement) {
  for (const b of within(root).queryAllByRole("button")) {
    if (/Volver|Cerrar/.test(b.textContent ?? "") || (b as HTMLButtonElement).disabled) continue;
    fireEvent.click(b);
  }
}

function expectNoWriteAffordance() {
  const WRITE_LABELS = [
    "Nuevo contacto",
    "Nueva organización",
    "Agregar proveedor",
    "Agregar nota",
    "Editar",
    "Archivar",
    "Restaurar",
    "Fusionar",
    "Confirmar…",
    "Rechazar…",
    "Agregar",
    "Vincular",
    "Desvincular",
    "Crear contacto",
    "Guardar",
  ];
  const offending = screen.queryAllByRole("button").filter((b) => {
    const text = (b.textContent ?? "").trim();
    return WRITE_LABELS.some((label) => text === label || text.startsWith(label));
  });
  expect(offending.map((b) => b.textContent?.trim())).toEqual([]);
  expect(document.querySelector("form")).toBeNull();
}

/** Minimal fetch stub for list pages */
function stub() {
  const calls: { path: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      calls.push({ path: url.pathname, method: (init?.method ?? "GET").toUpperCase() });
      const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
      const p = url.pathname;

      if (p.endsWith("/auth/session")) return json({ authenticated: false, google_login_enabled: true, workspace_domain: null });
      if (p.endsWith("/v2/workspace/overview")) return json({ entities: [], opportunities_by_stage: {}, organizations_by_confirmation: {}, contact_points_linked: { organization: 0, person: 0 }, assertions: [], drive_archive: { configured: false, documents: 0, revisions_with_drive_file: 0, revisions_total: 0 } });
      if (p.endsWith("/v2/workspace/pipeline")) return json({ items: [], drive_configured: false });
      if (p.endsWith("/v2/workspace/providers")) return json({ directory: [], on_cases: [], candidates: [{ assertion_id: "a0000000-0000-4000-8000-000000000001", domain: "hielscher.com", trade_name: "Hielscher", review: { state: "unresolved", decided_at: null, note: null } }], authoring: { enabled: true } });
      if (p.includes("/v2/contacts")) return json({ items: [], total: 0 });
      if (p.endsWith("/v2/workspace/organizations")) return json({ items: [], total: 0, facets: { customers: 0, suppliers: 0, all: 0 } });
      if (p.endsWith("/v2/workspace/equipment-interests")) return json({ by_contact_point: [], by_address_ref: [], by_organization: [] });
      if (init?.method && init.method.toUpperCase() !== "GET") {
        return json({ ok: true, replayed: false, idempotency_key: "key", command_receipt_id: "rcpt", version: 2 });
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ── unit: mayAuthorCrm, isAdmin ───────────────────────────────────────────────

describe("mayAuthorCrm", () => {
  it.each([
    ["sales", true],
    ["admin", true],
    ["viewer", false],
    ["auditor", false],
    ["", false],
  ])("signed-in %s → %s", (role, expected) => {
    expect(mayAuthorCrm(sessionFor(role))).toBe(expected);
  });

  it.each(["loading", "signed_out", "unconfigured", "error"])("session %s → false", (kind) => {
    expect(mayAuthorCrm({ kind } as AuthSessionState)).toBe(false);
  });
});

describe("isAdmin", () => {
  it("admin → true", () => expect(isAdmin(sessionFor("admin"))).toBe(true));
  it("sales → false", () => expect(isAdmin(sessionFor("sales"))).toBe(false));
  it("viewer → false", () => expect(isAdmin(sessionFor("viewer"))).toBe(false));
});

// ── viewer / unknown role / no session: no write affordance on list pages ─────

describe.each([
  ["a viewer", "viewer"],
  ["an unknown role", "auditor"],
  ["no session", null],
])("For %s: no write affordance anywhere", (_who, role) => {
  it("PeoplePage shows no write buttons and makes no writes", async () => {
    const calls = stub();
    render(withRole(role, <PeoplePage navigate={() => undefined} />));
    await waitFor(() => expect(screen.queryByRole("status", { name: "Cargando" })).toBeNull(), { timeout: 2000 });
    await new Promise((r) => setTimeout(r, 50));
    expectNoWriteAffordance();
    clickEverything(document.body);
    expect(writes(calls)).toEqual([]);
  });

  it("OrganizationsPage shows no write buttons and makes no writes", async () => {
    const calls = stub();
    render(withRole(role, <OrganizationsPage navigate={() => undefined} />));
    await waitFor(() => expect(screen.queryByRole("status", { name: "Cargando" })).toBeNull(), { timeout: 2000 });
    await new Promise((r) => setTimeout(r, 50));
    expectNoWriteAffordance();
    clickEverything(document.body);
    expect(writes(calls)).toEqual([]);
  });

  it("ProvidersPage shows no write buttons and makes no writes", async () => {
    const calls = stub();
    render(withRole(role, <ProvidersPage />));
    await waitFor(() => expect(screen.queryByRole("status", { name: "Cargando" })).toBeNull(), { timeout: 2000 });
    await new Promise((r) => setTimeout(r, 50));
    expectNoWriteAffordance();
    clickEverything(document.body);
    expect(writes(calls)).toEqual([]);
  });

  it("PipelinePage OpportunityDrawer shows no write buttons and makes no writes", async () => {
    const calls = stub();
    render(withRole(role, <PipelinePage />));
    await waitFor(() => expect(screen.queryByRole("status", { name: "Cargando" })).toBeNull(), { timeout: 2000 });
    await new Promise((r) => setTimeout(r, 50));
    expectNoWriteAffordance();
    clickEverything(document.body);
    expect(writes(calls)).toEqual([]);
  });
});

// ── sales/admin: write affordances present ────────────────────────────────────

describe.each(["sales", "admin"])("For %s: write affordances present", (role) => {
  it("PeoplePage shows «Nuevo contacto»", async () => {
    stub();
    render(withRole(role, <PeoplePage navigate={() => undefined} />));
    expect(await screen.findByRole("button", { name: "Nuevo contacto" })).toBeInTheDocument();
  });

  it("OrganizationsPage shows «Nueva organización»", async () => {
    stub();
    render(withRole(role, <OrganizationsPage navigate={() => undefined} />));
    expect(await screen.findByRole("button", { name: "Nueva organización" })).toBeInTheDocument();
  });

  it("ProvidersPage shows «Agregar proveedor»", async () => {
    stub();
    render(withRole(role, <ProvidersPage />));
    expect(await screen.findByRole("button", { name: "Agregar proveedor" })).toBeInTheDocument();
  });
});

// ── ConfirmDialog: blocks submit until checkbox + reason ──────────────────────

describe("ConfirmDialog", () => {
  it("submit is disabled until checkbox checked and reason filled", async () => {
    const { ConfirmDialog } = await import("../ui");
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        title="Archivar"
        lines={["Se archivará el elemento."]}
        requireReason
        onConfirm={onConfirm}
        onCancel={() => undefined}
      />,
    );
    const submitBtn = screen.getByRole("button", { name: "Confirmar" });
    expect(submitBtn).toBeDisabled();
    fireEvent.click(submitBtn);
    expect(onConfirm).not.toHaveBeenCalled();

    // Fill reason but don't check checkbox.
    const textarea = screen.getByRole("textbox", { name: /Motivo/ });
    fireEvent.change(textarea, { target: { value: "motivo de prueba" } });
    expect(submitBtn).toBeDisabled();

    // Check checkbox without reason.
    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);
    // Now both filled.
    expect(submitBtn).not.toBeDisabled();
    fireEvent.click(submitBtn);
    expect(onConfirm).toHaveBeenCalledWith("motivo de prueba");
  });

  it("submit is disabled without checkbox even when reason not required", async () => {
    const { ConfirmDialog } = await import("../ui");
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        title="Confirmar"
        lines={["Acción sin motivo requerido."]}
        requireReason={false}
        onConfirm={onConfirm}
        onCancel={() => undefined}
      />,
    );
    const submitBtn = screen.getByRole("button", { name: "Confirmar" });
    expect(submitBtn).toBeDisabled();
    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);
    expect(submitBtn).not.toBeDisabled();
  });
});

// ── create-person flow sends correct body + Idempotency-Key ───────────────────

describe("create-person command", () => {
  it("admin: NewPersonForm sends create-person with Idempotency-Key on submit and replays same key on retry", async () => {
    const calls: { path: string; method: string; key: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
        const body = init?.body ? JSON.parse(init.body as string) : null;
        const key = (init?.headers as Record<string, string>)?.["Idempotency-Key"] ?? "";
        calls.push({ path: url.pathname, method: (init?.method ?? "GET").toUpperCase(), key, body });
        const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
        const p = url.pathname;
        if (p.endsWith("/auth/session")) return json({ authenticated: false });
        if (p.endsWith("/v2/workspace/overview")) return json({ entities: [], opportunities_by_stage: {}, organizations_by_confirmation: {}, contact_points_linked: { organization: 0, person: 0 }, assertions: [], drive_archive: { configured: false, documents: 0, revisions_with_drive_file: 0, revisions_total: 0 } });
        if (p.endsWith("/v2/workspace/pipeline")) return json({ items: [], drive_configured: false });
        if (p.includes("/v2/contacts")) return json({ items: [], total: 0 });
        if (p.endsWith("/v2/workspace/equipment-interests")) return json({ by_contact_point: [], by_address_ref: [], by_organization: [] });
        if (p.endsWith("/v2/commands/create-person")) {
          return json({ ok: true, replayed: false, idempotency_key: key, command_receipt_id: "rcpt1", version: 1 });
        }
        return Promise.resolve(new Response("{}", { status: 404 }));
      }),
    );

    render(withRole("admin", <PeoplePage navigate={() => undefined} />));
    const newBtn = await screen.findByRole("button", { name: "Nuevo contacto" });
    fireEvent.click(newBtn);

    const dialog = await screen.findByRole("dialog");
    const nameInput = within(dialog).getByLabelText(/Nombre para mostrar/);
    fireEvent.change(nameInput, { target: { value: "Test User" } });
    const noteInput = within(dialog).getByLabelText(/Nota de registro/);
    fireEvent.change(noteInput, { target: { value: "importado desde prueba" } });
    const createBtn = within(dialog).getByRole("button", { name: "Crear contacto" });
    fireEvent.click(createBtn);

    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/v2/commands/create-person"))).toBe(true));
    const postCall = calls.find((c) => c.path.endsWith("/v2/commands/create-person"))!;
    expect(postCall.method).toBe("POST");
    expect(postCall.key).toBeTruthy();
    expect((postCall.body as { display_name: string }).display_name).toBe("Test User");
    expect((postCall.body as { note: string }).note).toBe("importado desde prueba");
  });
});

// ── responsive: w-full on inputs ─────────────────────────────────────────────

describe("responsive layout", () => {
  it("NewPersonForm inputs have w-full class", async () => {
    const { NewPersonForm } = await import("./NewPersonForm");
    render(<NewPersonForm onDone={() => undefined} onCancel={() => undefined} />);
    const inputs = document.querySelectorAll("input[type=text], input:not([type]), select, textarea");
    for (const input of inputs) {
      const cls = (input as HTMLElement).className;
      expect(cls, `input ${(input as HTMLInputElement).id || (input as HTMLInputElement).name} should have w-full`).toMatch(/\bw-full\b/);
    }
  });

  it("NewOrganizationForm inputs have w-full class", async () => {
    const { NewOrganizationForm } = await import("./NewOrganizationForm");
    render(<NewOrganizationForm onDone={() => undefined} onCancel={() => undefined} />);
    const inputs = document.querySelectorAll("input[type=text], input:not([type]), select, textarea");
    for (const input of inputs) {
      const cls = (input as HTMLElement).className;
      expect(cls).toMatch(/\bw-full\b/);
    }
  });

  it("no element uses a fixed width > 360px", async () => {
    const { NewPersonForm } = await import("./NewPersonForm");
    render(<NewPersonForm onDone={() => undefined} onCancel={() => undefined} />);
    const allElements = document.querySelectorAll("[style]");
    for (const el of allElements) {
      const style = (el as HTMLElement).style;
      const width = style.width;
      if (width && width.endsWith("px")) {
        const px = parseFloat(width);
        expect(px, `element has fixed width ${width}`).toBeLessThanOrEqual(360);
      }
    }
  });
});

// ── stale_version: shows reload message ───────────────────────────────────────

describe("stale_version handling", () => {
  it("stale_version 409 shows reload message in NoteForm", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          return new Response(
            JSON.stringify({ detail: { code: "stale_version", message: "Version mismatch" } }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("{}", { status: 404 });
      }),
    );
    const { NoteForm } = await import("./NoteList");
    render(
      <NoteForm
        mode="add"
        subjectKind="opportunity"
        subjectId="00000000-0000-4000-8000-000000000001"
        onDone={() => undefined}
        onCancel={() => undefined}
      />,
    );
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "una nota de prueba" } });
    const btn = screen.getByRole("button", { name: "Agregar" });
    fireEvent.click(btn);
    expect(await screen.findByText(/Otro operador modificó este registro/)).toBeInTheDocument();
  });
});

// ── candidates: confirm/reject send correct bodies ────────────────────────────

describe("supplier candidate confirm / reject", () => {
  it("ProvidersPage with unresolved candidate shows Confirmar… and Rechazar…", async () => {
    stub();
    render(withRole("admin", <ProvidersPage />));
    // Open candidates section.
    const summary = await screen.findByText("Candidatos por revisar");
    fireEvent.click(summary);
    await waitFor(() => expect(screen.queryByText("Hielscher")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Confirmar…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rechazar…" })).toBeInTheDocument();
  });

  it("reject-supplier-candidate: dialog blocks until checkbox checked + note filled", async () => {
    stub();
    render(withRole("admin", <ProvidersPage />));
    const summary = await screen.findByText("Candidatos por revisar");
    fireEvent.click(summary);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Rechazar…" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Rechazar…" }));
    const dialog = await screen.findByRole("alertdialog");
    const rejectBtn = within(dialog).getByRole("button", { name: "Rechazar" });
    expect(rejectBtn).toBeDisabled();
    const noteInput = within(dialog).getByLabelText(/Motivo/);
    fireEvent.change(noteInput, { target: { value: "no es proveedor real" } });
    expect(rejectBtn).toBeDisabled(); // still blocked: no checkbox
    const checkbox = within(dialog).getByRole("checkbox");
    fireEvent.click(checkbox);
    expect(rejectBtn).not.toBeDisabled();
  });
});


// ── organization domains: soft-removed rows and their explicit restore ────────

const ORG_ID = "0a000000-0000-4000-8000-00000000000a";

function orgAuthoringFixture(): OrganizationAuthoringResponse {
  return {
    organization: {
      id: ORG_ID, name: "Instituto Demo", legal_name: null, kind: "customer", status: "active",
      archived_at: null, archive_reason: null, confirmation: "confirmed", version: 3,
      merged_into_organization_id: null, created_at: "2026-09-01T00:00:00+00:00",
    },
    identifiers: [],
    domains: [
      { id: "d-live", domain_norm: "demo.test", scope: "shared", removed_at: null, remove_reason: null },
      { id: "d-removed", domain_norm: "old-demo.test", scope: "exclusive", removed_at: "2026-09-20T12:00:00+00:00", remove_reason: "cambió de dominio" },
    ],
    classifications: [],
    product_lines: [],
    contact_points: [],
    people: [],
    notes: [],
    references: { campaign_recipients: 0, opportunities: 0, quotes: 0, affiliations: 0, evidence_assertions: 0, catalog_products: 0 },
    removal: { allowed: false, reasons: [] },
    authoring: { enabled: true, may_author: true, may_archive: false },
  };
}

function stubOrgAuthoring() {
  const posts: { path: string; body: unknown; idempotencyKey: string | null }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
      const method = (init?.method ?? "GET").toUpperCase();
      if (method !== "GET") {
        const headers = new Headers(init?.headers);
        posts.push({ path: url.pathname, body: JSON.parse(String(init?.body ?? "null")), idempotencyKey: headers.get("Idempotency-Key") });
        return json({ ok: true, replayed: false, idempotency_key: "key", command_receipt_id: "rcpt", version: 4, domain_id: "d-removed", restored: true });
      }
      if (url.pathname.endsWith(`/v2/workspace/organizations/${ORG_ID}/authoring`)) return json(orgAuthoringFixture());
      if (url.pathname.endsWith("/auth/session")) return json({ authenticated: false, google_login_enabled: true, workspace_domain: null });
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
  return posts;
}

describe("organization domain restore", () => {
  it("lists the soft-removed domain apart, with its reason, and only the live one under «Dominios»", async () => {
    stubOrgAuthoring();
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    expect(await screen.findByText("Dominios (1)")).toBeInTheDocument();
    expect(screen.getByText("demo.test")).toBeInTheDocument();
    const removedList = screen.getByRole("list", { name: "Dominios eliminados" });
    expect(within(removedList).getByText("old-demo.test")).toBeInTheDocument();
    expect(within(removedList).getByText(/cambió de dominio/)).toBeInTheDocument();
    // The live domain can be removed; the removed one can only be restored.
    expect(within(removedList).queryByRole("button", { name: "Eliminar" })).toBeNull();
    expect(within(removedList).getByRole("button", { name: "Restaurar" })).toBeInTheDocument();
  });

  it("restores through /v2/commands/restore-organization-domain with the row id, the loaded version and a reason", async () => {
    const posts = stubOrgAuthoring();
    render(withRole("sales", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor admin={false} />));
    const removedList = await screen.findByRole("list", { name: "Dominios eliminados" });
    fireEvent.click(within(removedList).getByRole("button", { name: "Restaurar" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Restaurar dominio")).toBeInTheDocument();
    expect(within(dialog).getByText(/old-demo\.test/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Restaurar" });
    expect(confirm).toBeDisabled();
    expect(posts).toEqual([]);

    fireEvent.change(within(dialog).getByLabelText(/Motivo/), { target: { value: "vuelven a usarlo" } });
    expect(confirm).toBeDisabled(); // the reason alone is not enough: the consequences must be acknowledged
    fireEvent.click(within(dialog).getByRole("checkbox"));
    expect(confirm).not.toBeDisabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/v2/commands/restore-organization-domain");
    expect(posts[0].body).toEqual({ organization_id: ORG_ID, expected_version: 3, domain_id: "d-removed", note: "vuelven a usarlo" });
    expect(posts[0].idempotencyKey).toBeTruthy();
  });

  it("a viewer sees the removed domain but no «Restaurar»", async () => {
    const posts = stubOrgAuthoring();
    render(withRole("viewer", <OrgAuthoringSection organizationId={ORG_ID} mayAuthor={false} admin={false} />));
    const removedList = await screen.findByRole("list", { name: "Dominios eliminados" });
    expect(within(removedList).getByText("old-demo.test")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restaurar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Eliminar" })).toBeNull();
    expect(posts).toEqual([]);
  });
});
