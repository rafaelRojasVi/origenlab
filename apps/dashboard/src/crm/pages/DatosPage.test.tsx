import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceOverview } from "../crmTypes";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { DatosTab } from "../crmRoute";
import { DatosPage } from "./DatosPage";

// Every value below is invented; the repository is public.
const OVERVIEW: WorkspaceOverview = {
  entities: [
    { key: "opportunities", count: 12, provenance: "imported", note: "Casos importados." },
    { key: "drive_links_in_crm", count: 30, provenance: "imported", note: "Registros de archivo verificados en Supabase." },
  ],
  opportunities_by_stage: { quoting: 12 },
  organizations_by_confirmation: { confirmed: 3 },
  contact_points_linked: { organization: 0, person: 0 },
  assertions: [{ kind: "contact_address", resolution: "unresolved", count: 7 }],
  drive_archive: { configured: true, documents: 30, revisions_with_drive_file: 14, revisions_total: 14 },
};

function respond(routes: Record<string, unknown>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      calls.push(path);
      const body = routes[path];
      if (body === undefined) return Promise.resolve(new Response("{}", { status: 404 }));
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

function asRole(role: "admin" | "sales"): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_session",
    operator: { operatorId: "op-1", email: "op@ejemplo.invalid", displayName: "Operadora", role },
  } as AuthSessionState;
}

function renderDatos(role: "admin" | "sales" = "admin", tab?: DatosTab) {
  return render(
    <AuthSessionContext.Provider value={{ session: asRole(role), signOut: async () => true }}>
      <DatosPage navigate={() => undefined} tab={tab} />
    </AuthSessionContext.Provider>,
  );
}

describe("Datos · sólo administración", () => {
  const ROUTES = {
    "/v2/cockpit/work-queue": { items: [], total: 0, limit: 200, offset: 0 },
    "/v2/workspace/review": { archived_not_in_crm: [], open_assertions: [], ambiguous_organizations: [], drive_configured: true },
  };

  it("an admin reaches every technical panel that was in Revisión, plus suppliers and the Drive archive", async () => {
    respond(ROUTES);
    renderDatos("admin");
    const group = await screen.findByRole("group", { name: "Sección" });
    const labels = Array.from(group.querySelectorAll("button")).map((b) => b.textContent ?? "");
    for (const name of ["Bloqueos técnicos", "No importadas", "Evidencia", "Estado de los datos", "Acciones automáticas", "Proveedores", "Archivo Drive"]) {
      expect(labels.some((l) => l.startsWith(name))).toBe(true);
    }
    expect(labels.some((l) => l.startsWith("Correos"))).toBe(false); // email suggestions live on «Hoy»
  });

  it("a sales user sees only «Sólo administración» and nothing is read", () => {
    const calls = respond(ROUTES);
    renderDatos("sales");
    expect(screen.getByText("Sólo administración")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Sección" })).not.toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("opens the tab an old link asked for", async () => {
    respond({ ...ROUTES, "/v2/workspace/drive": { configured: false, folders: [], totals: {} } });
    renderDatos("admin", "drive");
    expect(await screen.findByRole("button", { name: /Archivo Drive/, pressed: true })).toBeInTheDocument();
  });
});

describe("Datos → Estado de los datos", () => {
  const ROUTES = {
    "/v2/cockpit/work-queue": { items: [], total: 0, limit: 200, offset: 0 },
    "/v2/workspace/review": { archived_not_in_crm: [], open_assertions: [], ambiguous_organizations: [], drive_configured: true },
    "/v2/workspace/overview": OVERVIEW,
  };

  it("reads the data-health counts only when the tab is opened", async () => {
    const calls = respond(ROUTES);
    renderDatos();
    await screen.findByText("Sin bloqueos técnicos");
    expect(calls).not.toContain("/v2/workspace/overview");

    fireEvent.click(screen.getByRole("button", { name: /Estado de los datos/ }));
    expect(await screen.findByText("Estado real de los datos")).toBeInTheDocument();
    expect(calls).toContain("/v2/workspace/overview");
  });

  it("offers no button that can never be pressed", async () => {
    respond(ROUTES);
    renderDatos();
    fireEvent.click(await screen.findByRole("button", { name: /No importadas/ }));
    await waitFor(() => expect(screen.queryByText(/Cargando/)).not.toBeInTheDocument());
    expect(screen.queryByText("Importación histórica no disponible")).not.toBeInTheDocument();
  });

  it("says where each Drive figure comes from", async () => {
    respond(ROUTES);
    renderDatos();
    fireEvent.click(await screen.findByRole("button", { name: /Estado de los datos/ }));
    expect(await screen.findByText("14/14")).toBeInTheDocument();
    expect(screen.getByText(/actualizado desde Supabase/)).toBeInTheDocument();
    expect(screen.getByText("Archivos Drive registrados en Supabase")).toBeInTheDocument();
    expect(screen.queryByText(/evidence\.source_record|crm\.\*/)).not.toBeInTheDocument();
  });
});

describe("Datos → Bloqueos técnicos", () => {
  it("shows the queue's true count for a kind when the page holds only part of it", async () => {
    const evidence = [0, 1].map((i) => ({
      kind: "pending_evidence", reason: "r", next_action: "n",
      subject_ids: { source_record_id: `00000000-0000-4000-8000-00000000000${i}` }, age_days: 1, label: "gmail_message",
    }));
    respond({
      "/v2/cockpit/work-queue": { items: evidence, total: 412, counts: { pending_evidence: 412 }, limit: 200, offset: 0 },
      "/v2/workspace/review": { archived_not_in_crm: [], open_assertions: [], ambiguous_organizations: [], drive_configured: true },
    });
    renderDatos();
    expect(await screen.findByText("· 412")).toBeInTheDocument();
    expect(screen.getByText(/se muestran los 2 más antiguos/)).toBeInTheDocument();
  });

  it("counts only the blocking kinds in the tab badge, not the Gmail evidence", async () => {
    const item = (kind: string, i: number) => ({
      kind, reason: "r", next_action: "n", subject_ids: { id: `00000000-0000-4000-8000-00000000000${i}` },
      age_days: 1, label: null,
    });
    respond({
      "/v2/cockpit/work-queue": {
        items: [item("shared_printed_number", 1), item("case_without_institution", 2), item("pending_evidence", 3)],
        total: 414, counts: { shared_printed_number: 1, case_without_institution: 1, pending_evidence: 412 },
        limit: 200, offset: 0,
      },
      "/v2/workspace/review": { archived_not_in_crm: [], open_assertions: [], ambiguous_organizations: [], drive_configured: true },
    });
    renderDatos();
    const tab = await screen.findByRole("button", { name: /Bloqueos técnicos/ });
    await screen.findByText("Número impreso compartido");
    expect(tab).toHaveTextContent(/Bloqueos técnicos\s*·?\s*2$/);
    expect(tab).not.toHaveTextContent("414");
  });
});
