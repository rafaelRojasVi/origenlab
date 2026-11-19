import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData, PipelineResponse, RevisionCard } from "../crmTypes";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { SeguimientoPage } from "./SeguimientoPage";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-18T15:00:00Z"); // 12:00 in Santiago
const A = "aaaaaaaa-0000-4000-8000-000000000001";
const B = "bbbbbbbb-0000-4000-8000-000000000002";
const C = "cccccccc-0000-4000-8000-000000000003";

const PROFILES = [
  { id: "op-rafa", display_name: "Rafa", role_label: "Ventas", key: "rafael" },
  { id: "op-tati", display_name: "Tati", role_label: "Ventas", key: "tati" },
  { id: "op-karla", display_name: "Karla", role_label: "Ventas", key: "karla" },
];

const session: AuthSessionState = {
  kind: "signed_in",
  method: "google_profile",
  operator: { operatorId: "op-shared", email: "ventas@example.cl", displayName: "Ventas", role: "sales" },
  profile: { id: "op-rafa", displayName: "Rafa", roleLabel: "Ventas" },
  caseCommandsEnabled: true,
  crmAuthoringEnabled: true,
};

function rev(over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: "r1",
    revision_no: 1,
    status: "sent",
    origin: null,
    sent_at: "2026-11-10T15:00:00Z",
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: { source_record_id: null, message_id: "m1", thread_id: "t1", url: "https://mail.google.com/mail/u/0/#all/abc123def", subject: "Cotización Balanza analítica" },
    drive: null,
    quote_number: "CN09876",
    candidates: [],
    ...over,
  } as RevisionCard;
}

function card(id: string, over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  const r = rev();
  return {
    opportunity_id: id,
    title: "Cotización CN09876",
    stage: "quoting",
    version: 3,
    created_at: "2026-11-01T12:00:00Z",
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: { organization_id: `org-${id}`, name: "Instituto Ficticio", confirmation: "confirmed", version: 1 },
    other_organizations: [],
    contact: { source: "crm_participant", name: "Persona Uno", address: "persona@ejemplo.invalid", others: 0, phone: "+56 9 1234 5678", phone_confirmed: true },
    quotes: [{ quote_id: `q-${id}`, quote_number: "CN09876", revisions: [r] }],
    quote_numbers: ["CN09876"],
    revision_count: 1,
    money: [{ currency: "CLP", net_total: "1000000", quote_number: "CN09876" }],
    latest_revision: r,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "", source: "suggested", due_at: null },
    follow_up: { operator_id: null, operator_name: null, channel: null },
    call_counts: { total: 0, today: 0 },
    last_call_any: null,
    open_tasks: [],
    ...over,
  } as OpportunityCardData;
}

/** A Rafa case untouched for a week, a Karla case called today, an unassigned one sent yesterday. */
function fixture(callsOnA = 0): PipelineResponse {
  return {
    total: 3,
    drive_configured: false,
    calls_by_operator: [
      { operator_id: "op-rafa", operator_name: "Rafa", today: callsOnA },
      { operator_id: "op-karla", operator_name: "Karla", today: 1 },
    ],
    items: [
      card(A, {
        follow_up: { operator_id: "op-rafa", operator_name: "Rafa", channel: "llamada" },
        call_counts: { total: callsOnA, today: callsOnA },
        last_call_any: callsOnA ? { at: "2026-11-18T14:00:00Z", outcome: "no_contesto", author_name: "Rafa" } : null,
      }),
      card(B, {
        organization: { organization_id: "org-b", name: "Laboratorio Ejemplo", confirmation: "confirmed", version: 1 },
        contact: { source: "crm_participant", name: "Persona Dos", address: "dos@ejemplo.invalid", others: 0, phone: null, phone_confirmed: null },
        follow_up: { operator_id: "op-karla", operator_name: "Karla", channel: "correo" },
        call_counts: { total: 2, today: 1 },
        last_call_any: { at: "2026-11-18T13:00:00Z", outcome: "contesto", author_name: "Karla" },
      }),
      card(C, {
        organization: { organization_id: "org-c", name: "Centro Prueba", confirmation: "confirmed", version: 1 },
        contact: { source: "crm_participant", name: "Persona Tres", address: "tres@ejemplo.invalid", others: 0, phone: null, phone_confirmed: null },
        quotes: [{ quote_id: "q-c", quote_number: "CN09877", revisions: [rev({ quote_number: "CN09877", sent_at: "2026-11-17T15:00:00Z" })] }] as OpportunityCardData["quotes"],
        quote_numbers: ["CN09877"],
        latest_revision: rev({ quote_number: "CN09877", sent_at: "2026-11-17T15:00:00Z" }),
        money: [{ currency: "CLP", net_total: "2000000", quote_number: "CN09877" }],
      }),
    ],
  };
}

interface Call {
  method: string;
  path: string;
  body: Record<string, unknown>;
  key: string | null;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Pipeline reads count the call notes posted so far, as the API would. */
function stubApi(pipeline: (notes: number) => PipelineResponse = fixture): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(raw, "http://localhost").pathname;
      const method = init?.method ?? "GET";
      const headers = new Headers(init?.headers);
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : {}, key: headers.get("Idempotency-Key") });
      if (path.endsWith("/auth/profiles")) return Promise.resolve(json({ profiles: PROFILES }));
      if (path.endsWith("/v2/workspace/pipeline")) {
        const notes = calls.filter((c) => c.path.endsWith("/add-note")).length;
        return Promise.resolve(json(pipeline(notes)));
      }
      if (path.endsWith("/add-note")) return Promise.resolve(json({ ok: true, note_id: "n1", version: 1 }));
      if (path.endsWith("/create-task")) {
        return Promise.resolve(json({ command: "create_task", opportunity_id: A, opportunity_version: 9, command_receipt_id: "r", idempotency_key: "k", replayed: false }));
      }
      return Promise.resolve(json({}, 404));
    }),
  );
  return calls;
}

const pipelineReads = (calls: Call[]) => calls.filter((c) => c.method === "GET" && c.path.endsWith("/v2/workspace/pipeline")).length;

function renderPage(props: { initialOpportunityId?: string | null } = {}) {
  return render(
    <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
      <SeguimientoPage now={NOW} {...props} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
}

const row = (id: string) => screen.getByTestId(`seg-row-${id}`);
const shownIds = () => screen.queryAllByTestId(/^seg-row-/).map((r) => r.getAttribute("data-testid")!.slice("seg-row-".length));
/** «Llamé» waits for any reread to finish, so a call replaces the reminder the case really has. */
async function openCall(id: string) {
  await waitFor(() => expect(within(row(id)).getByRole("button", { name: "Llamé" })).toBeEnabled());
  fireEvent.click(within(row(id)).getByRole("button", { name: "Llamé" }));
}
const filters = () => within(screen.getByRole("group", { name: "Encargado" }));

beforeEach(() => {
  window.location.hash = "#/ventas/seguimiento";
});
afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("SeguimientoPage", () => {
  it("filters by encargado and Mis casos", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    expect(shownIds().sort()).toEqual([A, B, C].sort());
    expect(filters().getByRole("button", { name: /^Todos/ })).toHaveAttribute("aria-pressed", "true");
    // C has no encargado yet: Karla is its suggestion, so it is in her list and in «Sin asignar».
    expect(filters().getByRole("button", { name: /^Karla/ })).toHaveTextContent("2");

    fireEvent.click(filters().getByRole("button", { name: /^Karla/ }));
    expect(shownIds().sort()).toEqual([B, C].sort());
    expect(window.location.hash).toContain("encargado=karla");

    fireEvent.click(filters().getByRole("button", { name: /^Mis casos/ }));
    expect(shownIds()).toEqual([A]);
    expect(window.location.hash).toContain("encargado=mios");

    fireEvent.click(filters().getByRole("button", { name: /^Sin asignar/ }));
    expect(shownIds()).toEqual([C]);
  });

  it("keeps the filter from the hash", async () => {
    window.location.hash = "#/ventas/seguimiento?encargado=mios";
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    expect(shownIds()).toEqual([A]);
  });

  it("searches client, contact and quote N°", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    const search = screen.getByRole("searchbox", { name: "Buscar" });
    fireEvent.change(search, { target: { value: "CN09877" } });
    expect(shownIds()).toEqual([C]);
    fireEvent.change(search, { target: { value: "persona dos" } });
    expect(shownIds()).toEqual([B]);
    fireEvent.change(search, { target: { value: "laboratorio" } });
    expect(shownIds()).toEqual([B]);
  });

  it("Solo atrasados hides touched rows", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(screen.getByRole("button", { name: "Solo atrasados" }));
    // B was called today and C was sent yesterday: only A is three days or more without a touch.
    expect(shownIds()).toEqual([A]);
  });

  it("shows the KPI strip from the pipeline read", async () => {
    stubApi(() => fixture(2));
    renderPage();
    const kpis = within(await screen.findByRole("region", { name: "Indicadores de hoy" }));
    expect(kpis.getByTestId("kpi-calls")).toHaveTextContent("3");
    expect(kpis.getByTestId("kpi-calls")).toHaveTextContent("de 25");
    expect(kpis.getByTestId("kpi-touched")).toHaveTextContent("2");
    expect(kpis.getByTestId("kpi-money")).toHaveTextContent("3 cotizaciones");
  });

  it("Llamé saves note then schedules follow-up and refetches", async () => {
    const calls = stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    const before = pipelineReads(calls);
    await openCall(A);
    const form = within(screen.getByTestId(`seg-call-${A}`));
    fireEvent.click(form.getByRole("button", { name: "Contestó" }));
    fireEvent.click(form.getByRole("button", { name: "Mañana" }));
    fireEvent.change(form.getByLabelText("Nota (opcional)"), { target: { value: "La orden sale el viernes" } });
    fireEvent.click(form.getByRole("button", { name: "Guardar llamada" }));

    await screen.findByText("Llamada guardada · próximo: Mañana");
    const writes = calls.filter((c) => c.method === "POST");
    expect(writes.map((c) => c.path.split("/").pop())).toEqual(["add-note", "create-task"]);
    expect(writes[0].body).toMatchObject({ subject_kind: "opportunity", subject_id: A, note_kind: "llamada", call_outcome: "contesto", body: "La orden sale el viernes" });
    expect(writes[0].key).toBeTruthy();
    expect(writes[1].body).toMatchObject({ opportunity_id: A, title: "Seguimiento de CN09876" });
    expect(new Date(String(writes[1].body.due_at)).getTime()).toBeGreaterThan(NOW.getTime() + 12 * 3_600_000);
    await waitFor(() => expect(pipelineReads(calls)).toBeGreaterThan(before));
    expect(screen.queryByTestId(`seg-call-${A}`)).not.toBeInTheDocument();
  });

  it("no contestó preselects otra vez hoy, due in two hours", async () => {
    const calls = stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    await openCall(A);
    const form = within(screen.getByTestId(`seg-call-${A}`));
    fireEvent.click(form.getByRole("button", { name: "No contestó" }));
    expect(form.getByRole("button", { name: "Otra vez hoy (en 2 h)" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(form.getByRole("button", { name: "Buzón de voz" }));
    expect(form.getByRole("button", { name: "Otra vez hoy (en 2 h)" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(form.getByRole("button", { name: "Guardar llamada" }));
    await screen.findByText("Llamada guardada · próximo: Hoy en 2 h");
    const task = calls.find((c) => c.path.endsWith("/create-task"))!;
    expect(task.body.due_at).toBe(new Date(NOW.getTime() + 2 * 3_600_000).toISOString());
  });

  it("three saves increment hoy count", async () => {
    stubApi((notes) => fixture(notes));
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    for (let i = 0; i < 3; i += 1) {
      await openCall(A);
      const form = within(screen.getByTestId(`seg-call-${A}`));
      fireEvent.click(form.getByRole("button", { name: "No contestó" }));
      fireEvent.click(form.getByRole("button", { name: "Guardar llamada" }));
      await waitFor(() => expect(within(row(A)).getByTestId("seg-calls")).toHaveTextContent(`${i + 1} hoy`));
    }
    expect(within(row(A)).getByTestId("seg-calls")).toHaveTextContent("3 hoy · 3");
    // Rafa's three plus Karla's one.
    expect(screen.getByTestId("kpi-calls")).toHaveTextContent("4");
    expect(within(screen.getByTestId("kpi-calls")).getByTestId("kpi-calls-op-rafa")).toHaveTextContent("3");
  });

  it("guion fills placeholders", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(row(A)).getByRole("button", { name: "Ver guion para Instituto Ficticio" }));
    const guion = within(screen.getByRole("complementary", { name: "Guion de llamada" }));
    const say = guion.getByTestId("guion-say");
    expect(say).toHaveTextContent("¿hablo con Persona Uno?");
    expect(say).toHaveTextContent("Le habla Rafa, de ventas de OrigenLab");
    expect(say).toHaveTextContent("cotización CN09876 de Balanza analítica que le enviamos el 10 nov");
    expect(guion.getByText(/Lo vuelvo a llamar más tarde/)).toHaveTextContent("Hola Persona Uno, le habla Rafa de OrigenLab por la cotización CN09876.");
    fireEvent.click(guion.getByRole("button", { name: "Cerrar guion" }));
    expect(screen.queryByRole("complementary", { name: "Guion de llamada" })).not.toBeInTheDocument();
  });

  it("caso param selects row", async () => {
    window.location.hash = `#/ventas/seguimiento?caso=${B}`;
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${B}`);
    await waitFor(() => expect(row(B)).toHaveAttribute("aria-current", "true"));
    expect(row(A)).not.toHaveAttribute("aria-current");
    expect(scrolled).toHaveBeenCalled();
  });

  it("shows phone, Gmail link and the per-row facts", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    expect(within(row(A)).getByRole("link", { name: /\+56 9 1234 5678/ })).toHaveAttribute("href", "tel:+56912345678");
    expect(within(row(B)).getByText("Sin fono · pedir por correo")).toBeInTheDocument();
    expect(within(row(A)).getByRole("link", { name: "Abrir el correo de Instituto Ficticio en Gmail" })).toHaveAttribute(
      "href",
      expect.stringContaining("authuser="),
    );
    expect(within(row(A)).getByTestId("seg-days")).toHaveTextContent("8 d");
  });

  it("empty state", async () => {
    stubApi(() => ({ items: [], total: 0, drive_configured: false }));
    renderPage();
    expect(await screen.findByText("No hay cotizaciones enviadas esperando respuesta.")).toBeInTheDocument();
  });
});
