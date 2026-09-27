import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OperatorApiError } from "../api/operatorClient";
import { splitAddress } from "./address";
import { crmHash, isCrmHash, parseCrmHash } from "./crmRoute";
import type { OpportunityCardData, PipelineResponse, WorkspaceOverview } from "./crmTypes";
import { PipelinePage } from "./pages/PipelinePage";
import { PeoplePage } from "./pages/PeoplePage";
import { AuthSessionContext } from "../context/AuthSessionContext";
import { byLatestSent, matchesQuery } from "./stage";
import { classifyError } from "./useResource";

// Every value below is invented; the repository is public.
const SHA = "a".repeat(64);

function card(over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  return {
    opportunity_id: "11111111-1111-4111-8111-111111111111",
    title: "Cotización 00001-26 — Institución Ejemplo",
    stage: "quoting",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    closed_at: null,
    close_reason: null,
    organization: { organization_id: "org-1", name: "Institución Ejemplo", confirmation: "confirmed" },
    other_organizations: [],
    contact: { source: "gmail_recipient", name: null, address: "Persona Ejemplo <persona@ejemplo.invalid>", others: 0 },
    quotes: [
      {
        quote_id: "q1",
        quote_number: "00001-26",
        number_origin: "printed_historical",
        revisions: [
          {
            revision_id: "r1",
            revision_no: 1,
            status: "sent",
            origin: "historical_import",
            sent_at: "2026-03-10T12:00:00Z",
            superseded_by_revision_no: null,
            is_active: true,
            document: { sha256: SHA, filename: "CN00001-Ejemplo.pdf" },
            gmail: { source_record_id: "s1", message_id: "gm1", thread_id: "th1", url: "https://mail.google.com/mail/u/0/#all/gm1", subject: "Cotización" },
            drive: {
              source: "archive_ledger",
              ledger: "run-1",
              document_sha256: SHA,
              file_id: "f1",
              file_url: "https://drive.google.com/file/d/f1/view",
              folder_id: "d1",
              folder_url: "https://drive.google.com/drive/folders/d1",
              case_key: "c1",
              quote_number: "00001-26",
              revision: 1,
              original_filename: "CN00001-Ejemplo.pdf",
              archive_status: "archived_verified",
            },
            quote_number: "00001-26",
          },
        ],
      },
    ],
    quote_numbers: ["00001-26"],
    revision_count: 1,
    latest_revision: null,
    drive_folder: { source: "archive_ledger", folder_id: "d1", url: "https://drive.google.com/drive/folders/d1" },
    attention: [{ code: "no_crm_contact", label: "Sin persona de contacto en el CRM", blocking: false }],
    status: "ok",
    next_action: { text: "Hacer seguimiento de 00001-26", source: "suggested", due_at: null },
    ...over,
  };
}

function withLatest(c: OpportunityCardData): OpportunityCardData {
  return { ...c, latest_revision: c.quotes[0]?.revisions[0] ?? null };
}

function respond(routes: Record<string, unknown>) {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, method: init?.method ?? "GET" });
      const path = new URL(url, "http://localhost").pathname;
      const hit = Object.entries(routes).find(([p]) => path === p);
      if (!hit) return Promise.resolve(new Response("{}", { status: 404 }));
      const [, body] = hit;
      if (body instanceof Response) return Promise.resolve(body);
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("crm routing", () => {
  it("recognises #/crm and parses section and uuid", () => {
    expect(isCrmHash("#/crm")).toBe(true);
    expect(isCrmHash("#/crm/drive")).toBe(true);
    expect(isCrmHash("#/crmx")).toBe(false);
    expect(isCrmHash("#/cotizaciones")).toBe(false);
    expect(parseCrmHash("#/crm")).toEqual({ section: "resumen", id: null });
    expect(parseCrmHash("#/crm/nope")).toEqual({ section: "resumen", id: null });
    const id = "11111111-1111-4111-8111-111111111111";
    expect(parseCrmHash(`#/crm/oportunidades/${id}`)).toEqual({ section: "oportunidades", id });
    // A non-uuid id is dropped, never interpolated into a request.
    expect(parseCrmHash("#/crm/oportunidades/../../v2/commands")).toEqual({ section: "oportunidades", id: null });
    expect(crmHash("drive")).toBe("#/crm/drive");
  });
});

describe("error classification", () => {
  it("keeps permission, unavailable and error apart", () => {
    expect(classifyError(new OperatorApiError("no operator credential", 401)).kind).toBe("permission");
    expect(classifyError(new OperatorApiError("forbidden", 403)).kind).toBe("permission");
    expect(classifyError(new OperatorApiError('{"error":"path_not_allowed"}', 403)).kind).toBe("unavailable");
    expect(classifyError(new OperatorApiError("Not Found", 404)).kind).toBe("unavailable");
    expect(classifyError(new OperatorApiError("boom", 500)).kind).toBe("error");
    expect(classifyError(new Error("network")).kind).toBe("error");
  });
});

describe("helpers", () => {
  it("splits a display name from an address without inventing one", () => {
    expect(splitAddress("Persona Ejemplo <p@ejemplo.invalid>")).toEqual({ display: "Persona Ejemplo", email: "p@ejemplo.invalid" });
    expect(splitAddress("p@ejemplo.invalid")).toEqual({ display: null, email: "p@ejemplo.invalid" });
  });

  it("searches institution, quote number and contact", () => {
    const c = card();
    expect(matchesQuery(c, "00001")).toBe(true);
    expect(matchesQuery(c, "ejemplo.invalid")).toBe(true);
    expect(matchesQuery(c, "otra cosa")).toBe(false);
  });

  it("orders by latest sent revision, cases without one last", () => {
    const a = withLatest(card());
    const b = card({ opportunity_id: "b", latest_revision: null, title: "B" });
    expect([b, a].sort(byLatestSent)[0]).toBe(a);
  });
});

describe("PipelinePage", () => {
  it("renders a card with quote, contact provenance, Drive and Gmail links, and opens the drawer", async () => {
    const pipeline: PipelineResponse = {
      items: [
        withLatest(card()),
        withLatest(
          card({
            opportunity_id: "22222222-2222-4222-8222-222222222222",
            organization: { organization_id: "org-2", name: "Otra Institución", confirmation: "machine_proposed" },
            status: "blocked",
            attention: [{ code: "canonical_undetermined", label: "Hay más de una revisión vigente", blocking: true }],
            next_action: { text: "Anular o reemplazar la revisión duplicada", source: "suggested", due_at: null },
          }),
        ),
      ],
      total: 2,
      drive_configured: true,
    };
    const calls = respond({ "/v2/workspace/pipeline": pipeline });
    render(<PipelinePage />);
    const first = await screen.findByTestId("opportunity-card-11111111-1111-4111-8111-111111111111");
    expect(within(first).getByText("00001-26")).toBeInTheDocument();
    expect(within(first).getByText(/Persona Ejemplo/)).toBeInTheDocument();
    expect(within(first).getByText(/destinatario/)).toBeInTheDocument();
    expect(within(first).getByLabelText(/Abrir carpeta de Drive/)).toHaveAttribute("href", "https://drive.google.com/drive/folders/d1");
    expect(within(first).getByLabelText(/Abrir correo de Gmail/)).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#all/gm1");

    // Blocked filter.
    fireEvent.click(screen.getByRole("button", { name: /^Bloqueadas/ }));
    expect(screen.queryByTestId("opportunity-card-11111111-1111-4111-8111-111111111111")).not.toBeInTheDocument();
    expect(screen.getByText("Hay más de una revisión vigente")).toBeInTheDocument();

    // Drawer: revision history, links, and every write action disabled.
    fireEvent.click(screen.getByRole("button", { name: /Otra Institución/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Institución sin confirmar")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Abrir PDF r1 en Drive")).toHaveAttribute("href", "https://drive.google.com/file/d/f1/view");
    for (const name of ["Avanzar etapa", "Registrar seguimiento", "Nueva revisión"]) {
      expect(within(dialog).getByRole("button", { name })).toBeDisabled();
    }
    expect(screen.getByRole("button", { name: "Nueva oportunidad" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });

  it("opens the deep-linked opportunity's drawer", async () => {
    respond({ "/v2/workspace/pipeline": { items: [withLatest(card())], total: 1, drive_configured: true } });
    render(<PipelinePage initialOpportunityId="11111111-1111-4111-8111-111111111111" />);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("shows the permission state on 401, not an empty list", async () => {
    respond({ "/v2/workspace/pipeline": new Response("no operator credential", { status: 401 }) });
    render(<PipelinePage />);
    expect(await screen.findByText("Sin permiso para ver esta sección")).toBeInTheDocument();
  });

  it("shows the error state with a retry on 500", async () => {
    respond({ "/v2/workspace/pipeline": new Response("statement timeout", { status: 500 }) });
    render(<PipelinePage />);
    expect(await screen.findByText("No se pudo leer el CRM")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });

  it("distinguishes an empty CRM", async () => {
    respond({ "/v2/workspace/pipeline": { items: [], total: 0, drive_configured: true } });
    render(<PipelinePage />);
    expect(await screen.findByText("Sin oportunidades en el CRM")).toBeInTheDocument();
  });
});

describe("PeoplePage", () => {
  it("says persons are not imported, and lists Gmail recipients as evidence, not people", async () => {
    const overview: Partial<WorkspaceOverview> = {
      entities: [{ key: "persons", count: 0, provenance: "not_imported", note: "…" }],
    };
    respond({
      "/v2/workspace/overview": overview,
      "/v2/workspace/pipeline": { items: [withLatest(card())], total: 1, drive_configured: true },
      "/v2/contacts": { items: [], total: 0, limit: 30, offset: 0 },
    });
    render(<PeoplePage navigate={() => undefined} />);
    expect(await screen.findByText("El CRM no tiene personas registradas")).toBeInTheDocument();
    expect(await screen.findByText("Destinatarios de cotizaciones")).toBeInTheDocument();
    expect(screen.getByText("persona@ejemplo.invalid")).toBeInTheDocument();
    expect(screen.queryByTestId("people-redaction-note")).toBeNull();
  });

  it("tells a viewer that the API masked the addresses, and renders the masked form as given", async () => {
    const overview: Partial<WorkspaceOverview> = {
      entities: [{ key: "persons", count: 0, provenance: "not_imported", note: "…" }],
    };
    respond({
      "/v2/workspace/overview": overview,
      "/v2/workspace/pipeline": {
        items: [withLatest(card({ contact: { source: "gmail_recipient", name: null, address: "Persona Ejemplo <***@ejemplo.invalid>", others: 0 } }))],
        total: 1,
        drive_configured: true,
      },
      "/v2/contacts": { items: [], total: 0, limit: 30, offset: 0 },
    });
    render(
      <AuthSessionContext.Provider
        value={{
          session: {
            kind: "signed_in",
            method: "google_session",
            operator: { operatorId: "op-1", email: "lectora@ejemplo.invalid", displayName: "Lectora", role: "viewer" },
          },
          signOut: async () => undefined,
        }}
      >
        <PeoplePage navigate={() => undefined} />
      </AuthSessionContext.Provider>,
    );
    expect(await screen.findByTestId("people-redaction-note")).toBeInTheDocument();
    expect(await screen.findByText("***@ejemplo.invalid")).toBeInTheDocument();
    expect(screen.queryByText("persona@ejemplo.invalid")).toBeNull();
  });

  it("offers a viewer a name search, not an address search the API would refuse", async () => {
    respond({
      "/v2/workspace/overview": { entities: [{ key: "persons", count: 0, provenance: "not_imported", note: "…" }] },
      "/v2/workspace/pipeline": { items: [], total: 0, drive_configured: true },
      "/v2/contacts": { items: [], total: 0, limit: 30, offset: 0 },
    });
    render(
      <AuthSessionContext.Provider
        value={{
          session: {
            kind: "signed_in",
            method: "google_session",
            operator: { operatorId: "op-1", email: "lectora@ejemplo.invalid", displayName: "Lectora", role: "viewer" },
          },
          signOut: async () => undefined,
        }}
      >
        <PeoplePage navigate={() => undefined} />
      </AuthSessionContext.Provider>,
    );
    const box = (await screen.findByLabelText("Buscar por nombre")) as HTMLInputElement;
    expect(box.placeholder).toBe("Nombre de persona o institución…");
    expect(screen.queryByLabelText("Buscar direcciones")).toBeNull();
    expect(screen.queryByPlaceholderText(/direcci/i)).toBeNull();
  });

  it("keeps the address search for sales, whose query does reach addresses", async () => {
    respond({
      "/v2/workspace/overview": { entities: [{ key: "persons", count: 0, provenance: "not_imported", note: "…" }] },
      "/v2/workspace/pipeline": { items: [], total: 0, drive_configured: true },
      "/v2/contacts": { items: [], total: 0, limit: 30, offset: 0 },
    });
    render(
      <AuthSessionContext.Provider
        value={{
          session: {
            kind: "signed_in",
            method: "google_session",
            operator: { operatorId: "op-2", email: "ventas@ejemplo.invalid", displayName: "Ventas", role: "sales" },
          },
          signOut: async () => undefined,
        }}
      >
        <PeoplePage navigate={() => undefined} />
      </AuthSessionContext.Provider>,
    );
    expect(await screen.findByLabelText("Buscar direcciones")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Dirección o dominio…")).toBeInTheDocument();
  });
});

describe("no write path in the CRM workspace", () => {
  it("never issues a non-GET request or references a command route", () => {
    const sources = import.meta.glob(["./**/*.ts", "./**/*.tsx", "!./**/*.test.tsx"], {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;
    expect(Object.keys(sources).length).toBeGreaterThan(5);
    for (const [f, src] of Object.entries(sources)) {
      expect(src, f).not.toMatch(/method:\s*["'](POST|PUT|PATCH|DELETE)/i);
      expect(src, f).not.toMatch(/\/v2\/commands/);
      expect(src, f).not.toMatch(/\/operations\//);
    }
  });
});
