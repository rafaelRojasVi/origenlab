import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";
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
function stubApi(
  pipeline: (notes: number, calls: Call[]) => PipelineResponse = fixture,
  extra: (call: Call) => Response | null = () => null,
): Call[] {
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
        return Promise.resolve(json(pipeline(notes, calls)));
      }
      const answer = extra(calls[calls.length - 1]);
      if (answer) return Promise.resolve(answer);
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

function renderPage(props: { initialOpportunityId?: string | null; now?: Date; clock?: () => Date } = {}, as: AuthSessionState = session) {
  return render(
    <AuthSessionContext.Provider value={{ session: as, signOut: async () => true }}>
      <SeguimientoPage now={NOW} clock={() => NOW} {...props} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
}

const row = (id: string) => screen.getByTestId(`seg-row-${id}`);
/** The rows on screen, leaving ones (fading out) not counted. */
const shownIds = () =>
  screen
    .queryAllByTestId(/^seg-row-/)
    .filter((r) => !r.hasAttribute("data-leaving"))
    .map((r) => r.getAttribute("data-testid")!.slice("seg-row-".length));
const showing = async (ids: string[]) => waitFor(() => expect(shownIds().sort()).toEqual([...ids].sort()));
/** «Llamé» waits for any reread to finish, so a call replaces the reminder the case really has. */
async function openCall(id: string) {
  await waitFor(() => expect(within(row(id)).getByRole("button", { name: "Llamé" })).toBeEnabled());
  fireEvent.click(within(row(id)).getByRole("button", { name: "Llamé" }));
}
const filters = () => within(screen.getByRole("group", { name: "Encargado" }));

/** A screen `width` px wide: the media queries the page asks answer as a browser that wide would. */
function screenWidth(width: number) {
  vi.stubGlobal("matchMedia", (q: string) => {
    const min = /min-width:\s*(\d+)px/.exec(q);
    const max = /max-width:\s*(\d+)px/.exec(q);
    const matches = (!min || width >= Number(min[1])) && (!max || width <= Number(max[1]));
    return { matches, media: q, addEventListener: () => undefined, removeEventListener: () => undefined };
  });
}

beforeEach(() => {
  window.location.hash = "#/ventas/seguimiento";
  localStorage.clear();
  screenWidth(1440);
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
    // C has no encargado yet: Karla is its suggestion, so it is in her list and in «Sin confirmar».
    await waitFor(() => expect(filters().getByRole("button", { name: /^Karla/ })).toHaveTextContent("2"));

    fireEvent.click(filters().getByRole("button", { name: /^Karla/ }));
    await showing([B, C]);
    expect(window.location.hash).toContain("encargado=karla");

    // OR inside the group: Karla or mine.
    fireEvent.click(filters().getByRole("button", { name: /^Mis casos/ }));
    await showing([A, B, C]);
    expect(window.location.hash).toContain("encargado=karla%2Cmios");

    fireEvent.click(filters().getByRole("button", { name: /^Karla/ }));
    await showing([A]);
    fireEvent.click(filters().getByRole("button", { name: /^Mis casos/ }));
    fireEvent.click(filters().getByRole("button", { name: /^Sin confirmar/ }));
    await showing([C]);
    expect(screen.getByTestId("seg-showing")).toHaveTextContent("Mostrando 1 de 3");
    fireEvent.click(screen.getByRole("button", { name: "Limpiar" }));
    await showing([A, B, C]);
  });

  it("keeps the filter from the hash", async () => {
    window.location.hash = "#/ventas/seguimiento?encargado=mios";
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    expect(shownIds()).toEqual([A]);
  });

  it("an unknown encargado in the hash shows everything", async () => {
    window.location.hash = "#/ventas/seguimiento?encargado=nadie";
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    await waitFor(() => expect(filters().getByRole("button", { name: /^Karla/ })).toBeInTheDocument());
    expect(shownIds().sort()).toEqual([A, B, C].sort());
    expect(filters().queryAllByRole("button", { pressed: true })).toHaveLength(0);
  });

  it("searches client, contact and quote N°", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    const search = screen.getByRole("searchbox", { name: "Buscar" });
    fireEvent.change(search, { target: { value: "CN09877" } });
    await showing([C]);
    fireEvent.change(search, { target: { value: "persona dos" } });
    await showing([B]);
    fireEvent.change(search, { target: { value: "laboratorio" } });
    await showing([B]);
  });

  it("«Atrasado» hides touched rows", async () => {
    // «Atrasado» is the próximo contacto's day gone by (audit 2026-11-22): A's reminder was due on
    // Monday. B was called today and C was sent yesterday: their next contacts are ahead.
    stubApi(() => {
      const f = fixture();
      f.items[0] = { ...f.items[0], open_tasks: [{ task_id: "t-a", title: "Seguimiento de CN09876", due_at: "2026-11-16T13:00:00Z", version: 1, owner: null }] };
      return f;
    });
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(screen.getByRole("group", { name: "Estado" })).getByRole("button", { name: /^Atrasado/ }));
    await showing([A]);
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

  it("a call saved hours after the page loaded is due two hours after the save", async () => {
    const calls = stubApi();
    const saveAt = new Date(NOW.getTime() + 3 * 3_600_000);
    renderPage({ clock: () => saveAt });
    await screen.findByTestId(`seg-row-${A}`);
    await openCall(A);
    const form = within(screen.getByTestId(`seg-call-${A}`));
    fireEvent.click(form.getByRole("button", { name: "No contestó" }));
    fireEvent.click(form.getByRole("button", { name: "Guardar llamada" }));
    await screen.findByText("Llamada guardada · próximo: Hoy en 2 h");
    const task = calls.find((c) => c.path.endsWith("/create-task"))!;
    expect(task.body.due_at).toBe(new Date(NOW.getTime() + 5 * 3_600_000).toISOString());
  });

  it("«Mañana» is the day after the save, even when the page loaded before midnight", async () => {
    const calls = stubApi();
    // Local (browser) days, as the reminder's 09:00 is local.
    const loaded = new Date(2026, 10, 17, 23, 30);
    const saveAt = new Date(2026, 10, 18, 1, 0);
    renderPage({ now: loaded, clock: () => saveAt });
    await screen.findByTestId(`seg-row-${A}`);
    await openCall(A);
    const form = within(screen.getByTestId(`seg-call-${A}`));
    fireEvent.click(form.getByRole("button", { name: "Mañana" }));
    fireEvent.click(form.getByRole("button", { name: "Guardar llamada" }));
    await screen.findByText("Llamada guardada · próximo: Mañana");
    const task = calls.find((c) => c.path.endsWith("/create-task"))!;
    expect(task.body.due_at).toBe(new Date(2026, 10, 19, 9, 0).toISOString());
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
    fireEvent.click(within(row(A)).getByRole("button", { name: "Más acciones para Instituto Ficticio" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Ver guion" }));
    const guion = within(screen.getByRole("complementary", { name: "Guion de llamada" }));
    const say = guion.getByTestId("guion-say");
    expect(say).toHaveTextContent("¿hablo con Persona Uno?");
    expect(say).toHaveTextContent("Le habla Rafa, de ventas de OrigenLab");
    expect(say).toHaveTextContent("cotización CN09876 de Balanza analítica que le enviamos el 10 nov");
    expect(guion.getByText(/Lo vuelvo a llamar más tarde/)).toHaveTextContent("Hola Persona Uno, le habla Rafa de OrigenLab por la cotización CN09876.");
    fireEvent.click(guion.getByRole("button", { name: "Cerrar guion" }));
    expect(screen.queryByRole("complementary", { name: "Guion de llamada" })).not.toBeInTheDocument();
  });

  it("caso param selects the row, opens its panel and lights the row", async () => {
    window.location.hash = `#/ventas/seguimiento?caso=${B}`;
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${B}`);
    await waitFor(() => expect(row(B)).toHaveAttribute("aria-current", "true"));
    expect(row(A)).not.toHaveAttribute("aria-current");
    expect(row(B)).toHaveAttribute("data-flash", "true");
    expect(scrolled).toHaveBeenCalled();
    expect(await screen.findByRole("dialog")).toHaveTextContent("Laboratorio Ejemplo");
  });

  it("shows phone, Gmail link and the per-row facts", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    expect(within(row(A)).getByRole("link", { name: /\+56 9 1234 5678/ })).toHaveAttribute("href", "tel:+56912345678");
    expect(within(row(B)).getByText("Sin teléfono")).toBeInTheDocument();
    fireEvent.click(within(row(A)).getByRole("button", { name: "Más acciones para Instituto Ficticio" }));
    expect(await screen.findByRole("menuitem", { name: "Abrir el correo en Gmail" })).toHaveAttribute("href", expect.stringContaining("authuser="));
    expect(within(row(A)).getByTestId("seg-days")).toHaveTextContent("8 d");
  });

  it("empty state", async () => {
    stubApi(() => ({ items: [], total: 0, drive_configured: false }));
    renderPage();
    expect(await screen.findByText("No hay cotizaciones enviadas esperando respuesta.")).toBeInTheDocument();
  });

  it("«Ya no sigue» closes the case with the drawer's reasons and the row leaves", async () => {
    const calls = stubApi(
      // Once closed, the next read no longer has the case.
      (notes, all) => {
        const data = fixture(notes);
        return all.some((c) => c.path.endsWith("/advance-case-stage")) ? { ...data, items: data.items.filter((c) => c.opportunity_id !== A) } : data;
      },
      (call) =>
        call.path.endsWith("/advance-case-stage")
          ? json({ command: "advance_case_stage", opportunity_id: A, opportunity_version: 4, stage: call.body.stage, command_receipt_id: "r-close", idempotency_key: "k", replayed: false })
          : null,
    );
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(row(A)).getByRole("button", { name: "Más acciones para Instituto Ficticio" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Ya no sigue" }));
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "Sin respuesta" }));
    expect(dialog.getByText("¿Por qué ya no sigue?")).toBeInTheDocument();
    fireEvent.click(dialog.getByRole("button", { name: "Ya no sigue" }));
    await screen.findByText("Caso cerrado: sale del seguimiento.");
    const close = calls.find((c) => c.path.endsWith("/advance-case-stage"))!;
    expect(close.body).toMatchObject({ opportunity_id: A, opportunity_version: 3, stage: "abandoned", close_reason: "Sin respuesta" });
    await showing([B, C]);
    // The leaving row fades out, then goes.
    await waitFor(() => expect(screen.queryByTestId(`seg-row-${A}`)).not.toBeInTheDocument());
  });

  it("«Pausar…» uses the drawer's pause form", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(row(A)).getByRole("button", { name: "Más acciones para Instituto Ficticio" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Pausar…" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Pausar el seguimiento" }));
    expect(dialog.getByLabelText(/Retomar el/)).toBeInTheDocument();
    expect(dialog.getByRole("group", { name: "Motivo de la pausa" })).toBeInTheDocument();
  });

  it("the case name opens the case drawer", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(row(A)).getByRole("button", { name: "Instituto Ficticio" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Instituto Ficticio");
    expect(screen.queryByRole("link", { name: "Ver en Seguimiento" })).not.toBeInTheDocument();
  });

  it("keeps the view in the hash and in this browser", async () => {
    // The rhythm counts days from the quotes: pin the clock (only Date; timers stay real for findBy).
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    onTestFinished(() => {
      vi.useRealTimers();
    });
    window.location.hash = "#/ventas/seguimiento?vista=agenda&caso=" + B;
    stubApi();
    renderPage();
    const agenda = await screen.findByTestId("seg-agenda");
    // No reminder on any: the follow-up rhythm dates them, as Hoy does — A and B (quote 8 days
    // without an answer) today, C (quote yesterday) on its day 3, Friday. None is «Sin fecha».
    expect(within(agenda).getByRole("heading", { name: /^Hoy/ })).toHaveTextContent("2");
    expect(within(agenda).getByRole("heading", { name: /Esta semana/ })).toHaveTextContent("1");
    expect(within(agenda).queryByRole("heading", { name: /Sin fecha/ })).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("group", { name: "Vista" })).getByRole("button", { name: "Por encargado" }));
    await screen.findByTestId("seg-board");
    expect(window.location.hash).toContain("vista=encargado");
    expect(window.location.hash).toContain(`caso=${B}`);
    expect(localStorage.getItem("seguimiento.vista")).toBe("encargado");
  });

  it("follows a link to another view while open", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    act(() => {
      window.location.hash = "#/ventas/seguimiento?vista=encargado&canal=correo";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    await screen.findByTestId("seg-board");
    expect(within(screen.getByRole("group", { name: "Canal" })).getByRole("button", { name: /^Correo/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("orders by the chosen order and writes it in the hash", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.change(screen.getByRole("combobox", { name: /Orden/ }), { target: { value: "monto" } });
    // C has the biggest amount.
    await waitFor(() => expect(shownIds()[0]).toBe(C));
    expect(window.location.hash).toContain("orden=monto");
  });

  it("on a phone the sheet is stacked cards", async () => {
    screenWidth(390);
    stubApi();
    renderPage();
    const cards = await screen.findByTestId("seg-cards");
    expect(screen.queryByTestId("seg-sheet")).not.toBeInTheDocument();
    expect(within(cards).getAllByRole("article")).toHaveLength(3);
    expect(within(screen.getByTestId(`seg-card-${A}`)).getByRole("button", { name: "Llamé" })).toBeInTheDocument();
  });

  describe("Por encargado", () => {
    beforeEach(() => {
      window.location.hash = "#/ventas/seguimiento?vista=encargado";
    });
    const col = (key: string) => screen.getByTestId(`seg-col-${key}`);
    const cardIn = (key: string, id: string) => within(col(key)).queryByTestId(`seg-card-${id}`);
    const drag = (id: string, to: string) => {
      const dataTransfer = { data: "", setData(_t: string, v: string) { this.data = v; }, getData() { return this.data; }, dropEffect: "", effectAllowed: "" };
      fireEvent.dragStart(screen.getByTestId(`seg-card-${id}`), { dataTransfer });
      fireEvent.dragOver(col(to), { dataTransfer });
      fireEvent.drop(col(to), { dataTransfer });
    };

    it("a card dropped on another person sets the encargado, keeping the channel", async () => {
      const calls = stubApi(fixture, (call) =>
        call.path.endsWith("/set-case-follow-up")
          ? json({ command: "set_case_follow_up", opportunity_id: B, opportunity_version: 4, operator_id: call.body.follow_up_operator_id, channel: call.body.channel, command_receipt_id: "r", idempotency_key: "k", replayed: false })
          : null,
      );
      renderPage();
      await screen.findByTestId("seg-board");
      await waitFor(() => expect(cardIn("op-karla", B)).toBeInTheDocument());
      drag(B, "op-rafa");
      await waitFor(() => expect(cardIn("op-rafa", B)).toBeInTheDocument());
      const post = calls.find((c) => c.path.endsWith("/set-case-follow-up"))!;
      expect(post.body).toEqual({ opportunity_id: B, opportunity_version: 3, follow_up_operator_id: "op-rafa", channel: "correo" });
      expect(await screen.findByText("Asignado a Rafa")).toBeInTheDocument();
    });

    it("a refused move puts the card back and rereads", async () => {
      const calls = stubApi(fixture, (call) => (call.path.endsWith("/set-case-follow-up") ? json({ detail: { code: "case_version_conflict" } }, 409) : null));
      renderPage();
      await screen.findByTestId("seg-board");
      await waitFor(() => expect(cardIn("op-karla", B)).toBeInTheDocument());
      const reads = pipelineReads(calls);
      drag(B, "sin");
      await waitFor(() => expect(cardIn("sin", B)).toBeInTheDocument());
      await waitFor(() => expect(cardIn("op-karla", B)).toBeInTheDocument());
      await waitFor(() => expect(cardIn("sin", B)).not.toBeInTheDocument());
      await waitFor(() => expect(pipelineReads(calls)).toBeGreaterThan(reads));
    });
  });
});

const receipt = (id: string, version: number, over: Record<string, unknown> = {}) =>
  json({ command: "x", opportunity_id: id, opportunity_version: version, command_receipt_id: "r", idempotency_key: "k", replayed: false, ...over });

describe("Seguimiento QA fixes (2026-11-19)", () => {
  it("an open «Llamé» and its typed note survive a re-mount and a refetch; only one is open", async () => {
    stubApi();
    const first = renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    await openCall(A);
    fireEvent.click(within(screen.getByTestId(`seg-call-${A}`)).getByRole("button", { name: "Buzón de voz" }));
    fireEvent.change(within(screen.getByTestId(`seg-call-${A}`)).getByLabelText("Nota (opcional)"), { target: { value: "Dejé recado con secretaría" } });
    // The dashboard re-mounts (a session re-read): the form comes back as it was.
    first.unmount();
    renderPage();
    const form = within(await screen.findByTestId(`seg-call-${A}`));
    expect(form.getByLabelText("Nota (opcional)")).toHaveValue("Dejé recado con secretaría");
    expect(form.getByRole("button", { name: "Buzón de voz" })).toHaveAttribute("aria-pressed", "true");
    // Opening another case's «Llamé» closes this one; its draft stays for when it reopens.
    await openCall(B);
    expect(screen.queryByTestId(`seg-call-${A}`)).not.toBeInTheDocument();
    expect(screen.getAllByTestId(/^seg-call-[a-f0-9]{8}-/)).toHaveLength(1);
  });

  it("fits the sheet at 1440 px and shows cards under 1280 px", async () => {
    stubApi();
    screenWidth(1200);
    renderPage();
    expect(await screen.findByTestId("seg-cards")).toBeInTheDocument();
    expect(screen.queryByTestId("seg-sheet")).not.toBeInTheDocument();
  });

  it("a phone card shows and sets encargado and canal, calls, WhatsApp for Tati, and the guion", async () => {
    screenWidth(390);
    stubApi((n) => {
      const data = fixture(n);
      data.items[0] = { ...data.items[0], follow_up: { operator_id: "op-tati", operator_name: "Tati", channel: "whatsapp" } };
      return data;
    });
    renderPage();
    const cardA = within(await screen.findByTestId(`seg-card-${A}`));
    expect(cardA.getByLabelText("Encargado")).toHaveValue("op-tati");
    expect(cardA.getByLabelText("Próximo contacto")).toHaveValue("whatsapp");
    expect(cardA.getByRole("link", { name: /Llamar a Instituto Ficticio/ })).toHaveAttribute("href", "tel:+56912345678");
    expect(cardA.getByRole("link", { name: /WhatsApp/ })).toHaveAttribute("href", "https://wa.me/56912345678");
    const cardB = within(screen.getByTestId(`seg-card-${B}`));
    expect(cardB.getByText("Sin teléfono")).toBeInTheDocument();
    expect(cardB.queryByRole("link", { name: /WhatsApp/ })).not.toBeInTheDocument();
    fireEvent.click(cardA.getByRole("button", { name: "Ver guion para Instituto Ficticio" }));
    expect(await screen.findByRole("dialog", { name: "Guion de llamada" })).toHaveTextContent("¿hablo con Persona Uno?");
  });

  it("search ignores accents and case, and finds contact, institution, N° and product", async () => {
    stubApi((n) => {
      const data = fixture(n);
      data.items[1] = { ...data.items[1], organization: { organization_id: "org-b", name: "Universidad Católica Ficticia", confirmation: "confirmed", version: 1 } };
      return data;
    });
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    const search = screen.getByRole("searchbox", { name: "Buscar" });
    for (const [text, ids] of [["catolica", [B]], ["CATÓLICA", [B]], ["persona tres", [C]], ["balanza", [A, B, C]], ["cn09877", [C]]] as const) {
      fireEvent.change(search, { target: { value: text } });
      await showing([...ids]);
    }
  });

  it("filters, order and view stay after a save, and rows keep their place", async () => {
    // After the call A would sort last: the reread drops its score.
    stubApi((n) => {
      const data = fixture(n);
      if (n > 0) data.items[0] = { ...data.items[0], money: [{ currency: "CLP", net_total: "1", quote_number: "CN09876" }] };
      return data;
    });
    window.location.hash = "#/ventas/seguimiento?canal=llamada,correo";
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    const before = shownIds();
    await openCall(A);
    fireEvent.click(within(screen.getByTestId(`seg-call-${A}`)).getByRole("button", { name: "Guardar llamada" }));
    await screen.findByText(/Llamada guardada/);
    await waitFor(() => expect(row(A)).not.toHaveTextContent("$ 1.000.000"));
    expect(shownIds()).toEqual(before);
    expect(window.location.hash).toMatch(/canal=llamada(,|%2C)correo/);
    expect(within(screen.getByRole("group", { name: "Canal" })).getByRole("button", { name: /^Correo/ })).toHaveAttribute("aria-pressed", "true");
    // The name still opens its own case.
    fireEvent.click(within(row(before[0])).getAllByRole("button").find((b) => b.getAttribute("data-case") === before[0])!);
    const name = before[0] === A ? "Instituto Ficticio" : before[0] === B ? "Laboratorio Ejemplo" : "Centro Prueba";
    expect(await screen.findByRole("dialog")).toHaveTextContent(name);
    expect(window.location.hash).toContain(`caso=${before[0]}`);
  });

  it("the guion for the technical person speaks as her, with no hand-off", async () => {
    stubApi();
    renderPage({}, { ...session, kind: "signed_in", profile: { id: "op-tati", displayName: "Tati", roleLabel: "Ventas" } } as AuthSessionState);
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(screen.getByRole("button", { name: "Guion de llamada" }));
    const guion = within(await screen.findByRole("complementary", { name: "Guion de llamada" }));
    expect(guion.getByTestId("guion-say")).toHaveTextContent("Le habla Tati, de OrigenLab.");
    expect(guion.queryByText(/con Tati/)).not.toBeInTheDocument();
    expect(guion.getByText(/respóndela tú/)).toBeInTheDocument();
  });

  it("the KPI amount is compact, with the exact amount as its title", async () => {
    stubApi();
    renderPage();
    const money = await screen.findByTestId("kpi-money-value");
    expect(money).toHaveTextContent("$4 M");
    expect(money).toHaveAttribute("title", "$ 4.000.000");
  });

  it("assigns the selected cases one command each and says how many failed", async () => {
    let n = 0;
    const calls = stubApi(fixture, (call) => {
      if (!call.path.endsWith("/set-case-follow-up")) return null;
      n += 1;
      return n === 2 ? json({ detail: { code: "case_version_conflict" } }, 409) : receipt(String(call.body.opportunity_id), 4, { operator_id: call.body.follow_up_operator_id, channel: call.body.channel });
    });
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(screen.getByRole("button", { name: "Seleccionar" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Seleccionar todos los visibles" }));
    expect(screen.getByTestId("seg-bulk-bar")).toHaveTextContent("3 seleccionados");
    const reads = pipelineReads(calls);
    fireEvent.change(screen.getByRole("combobox", { name: "Asignar a…" }), { target: { value: "op-tati" } });
    expect(await screen.findByText("2 asignados, 1 falló")).toBeInTheDocument();
    const posts = calls.filter((c) => c.path.endsWith("/set-case-follow-up"));
    expect(posts.map((p) => p.body.follow_up_operator_id)).toEqual(["op-tati", "op-tati", "op-tati"]);
    expect(new Set(posts.map((p) => p.key)).size).toBe(3);
    await waitFor(() => expect(pipelineReads(calls)).toBeGreaterThan(reads));
  });

  it("«Confirmar todos los sugeridos» confirms each suggestion with its channel", async () => {
    const calls = stubApi(fixture, (call) =>
      call.path.endsWith("/set-case-follow-up") ? receipt(String(call.body.opportunity_id), 4, { operator_id: call.body.follow_up_operator_id, channel: call.body.channel }) : null,
    );
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(await screen.findByRole("button", { name: "Confirmar todos los sugeridos (1)" }));
    expect(await screen.findByText("1 asignado")).toBeInTheDocument();
    const [post] = calls.filter((c) => c.path.endsWith("/set-case-follow-up"));
    expect(post.body).toEqual({ opportunity_id: C, opportunity_version: 3, follow_up_operator_id: "op-karla", channel: "correo" });
  });

  it("«Modo llamadas» walks the cases due today, «N de M», opening each «Llamé»", async () => {
    stubApi((n, all) => {
      const data = fixture(n);
      // C has a reminder due this morning; A is late with no date; B was called today.
      data.items[2] = { ...data.items[2], open_tasks: [{ task_id: "t-c", title: "Seguimiento de CN09877", due_at: "2026-11-18T12:00:00Z", version: 1, owner: null }] };
      if (all.some((c) => c.path.endsWith("/add-note"))) data.items[0] = { ...data.items[0], call_counts: { total: 1, today: 1 } };
      return data;
    });
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(screen.getByRole("button", { name: "Modo llamadas" }));
    const mode = await screen.findByTestId("seg-call-mode");
    expect(within(mode).getByTestId("seg-call-mode-at")).toHaveTextContent("1 de 2");
    const firstId = screen.getAllByTestId(/^seg-call-[a-f0-9]{8}-/)[0].getAttribute("data-testid")!.slice("seg-call-".length);
    fireEvent.click(within(screen.getByTestId(`seg-call-${firstId}`)).getByRole("button", { name: "Guardar llamada" }));
    await waitFor(() => expect(within(screen.getByTestId("seg-call-mode")).getByTestId("seg-call-mode-at")).toHaveTextContent("2 de 2"));
    const other = firstId === A ? C : A;
    expect(await screen.findByTestId(`seg-call-${other}`)).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId("seg-call-mode")).getByRole("button", { name: "Salir" }));
    expect(screen.queryByTestId("seg-call-mode")).not.toBeInTheDocument();
    expect(screen.queryByTestId(`seg-call-${other}`)).not.toBeInTheDocument();
  });

  it("after a call, «Siguiente →» opens the next due case", async () => {
    stubApi((n) => {
      const data = fixture(n);
      data.items[2] = { ...data.items[2], open_tasks: [{ task_id: "t-c", title: "Seguimiento de CN09877", due_at: "2026-11-18T12:00:00Z", version: 1, owner: null }] };
      return data;
    });
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    await openCall(A);
    fireEvent.click(within(screen.getByTestId(`seg-call-${A}`)).getByRole("button", { name: "Guardar llamada" }));
    fireEvent.click(await screen.findByRole("button", { name: "Siguiente →" }));
    expect(await screen.findByTestId(`seg-call-${C}`)).toBeInTheDocument();
  });

  it("«Ya no sigue» can be undone: the case reopens at its previous stage", async () => {
    const calls = stubApi(fixture, (call) =>
      call.path.endsWith("/advance-case-stage") ? receipt(A, call.body.stage === "quoting" ? 5 : 4, { command: "advance_case_stage", stage: call.body.stage }) : null,
    );
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(row(A)).getByRole("button", { name: "Más acciones para Instituto Ficticio" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Ya no sigue" }));
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "Precio" }));
    fireEvent.click(dialog.getByRole("button", { name: "Ya no sigue" }));
    fireEvent.click(await screen.findByRole("button", { name: "Deshacer" }));
    expect(await screen.findByText("Listo: el caso vuelve al seguimiento.")).toBeInTheDocument();
    const moves = calls.filter((c) => c.path.endsWith("/advance-case-stage"));
    expect(moves.map((m) => [m.body.stage, m.body.opportunity_version])).toEqual([["lost", 3], ["quoting", 4]]);
  });

  it("«Pausar…» can be undone: the pause is lifted", async () => {
    const calls = stubApi(fixture, (call) => (call.path.endsWith("/set-case-pause") ? receipt(A, call.body.paused_until ? 4 : 5, { command: "set_case_pause" }) : null));
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(row(A)).getByRole("button", { name: "Más acciones para Instituto Ficticio" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Pausar…" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Pausar el seguimiento" }));
    fireEvent.click(dialog.getByRole("button", { name: dialog.getAllByRole("button").find((b) => b.closest("[role=group]"))!.textContent! }));
    fireEvent.click(dialog.getByRole("button", { name: "Pausar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Deshacer" }));
    expect(await screen.findByText("Listo: la pausa se deshizo.")).toBeInTheDocument();
    const pauses = calls.filter((c) => c.path.endsWith("/set-case-pause"));
    expect(pauses[1].body).toMatchObject({ opportunity_id: A, opportunity_version: 4, paused_until: null, reason: null });
  });

  it("«Ya no sigue · Sin respuesta» on a case with a reminder is one command, and «Deshacer» reopens it", async () => {
    // The close cancels the case's tasks itself (API 2026-11-16): cancelling them again was refused,
    // so the toast said «1/2 · Estado → Cerrada · abandonada» and offered no «Deshacer».
    const calls = stubApi(
      (n) => {
        const data = fixture(n);
        data.items[0] = { ...data.items[0], open_tasks: [{ task_id: "t-a", title: "Seguimiento de CN09876", due_at: "2026-11-19T12:00:00Z", version: 1, owner: null }] };
        return data;
      },
      (call) => (call.path.endsWith("/advance-case-stage") ? receipt(A, call.body.stage === "quoting" ? 5 : 4, { command: "advance_case_stage", stage: call.body.stage }) : null),
    );
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(row(A)).getByRole("button", { name: "Más acciones para Instituto Ficticio" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Ya no sigue" }));
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "Sin respuesta" }));
    fireEvent.click(dialog.getByRole("button", { name: "Ya no sigue" }));
    expect(await screen.findByText("Caso cerrado: sale del seguimiento.")).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/cancel-task"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Deshacer" }));
    expect(await screen.findByText("Listo: el caso vuelve al seguimiento.")).toBeInTheDocument();
    const moves = calls.filter((c) => c.path.endsWith("/advance-case-stage"));
    expect(moves.map((m) => [m.body.stage, m.body.opportunity_version])).toEqual([["abandoned", 3], ["quoting", 4]]);
  });

  it("the «⋯» menu sits outside the table, opens upward without room below, and works by keyboard", async () => {
    stubApi();
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    const more = within(row(A)).getByRole("button", { name: "Más acciones para Instituto Ficticio" });
    // The button sits 40 px above the bottom of an 800 px window; the menu is 120 px tall.
    vi.spyOn(more, "getBoundingClientRect").mockReturnValue({ top: 716, bottom: 760, left: 900, right: 944, width: 44, height: 44, x: 900, y: 716, toJSON: () => ({}) } as DOMRect);
    const tall = vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(120);
    vi.stubGlobal("innerHeight", 800);
    fireEvent.click(more);
    const menu = await screen.findByRole("menu");
    // In <body>, not inside the row or the table: nothing can clip it.
    expect(row(A).contains(menu)).toBe(false);
    expect(menu.parentElement).toBe(document.body);
    await waitFor(() => expect(menu.style.top).toBe(`${716 - 120 - 4}px`));
    tall.mockRestore();
    // Focus starts on the first item; arrows move; Escape closes and returns the focus to «⋯».
    const items = within(menu).getAllByRole("menuitem");
    await waitFor(() => expect(items[0]).toHaveFocus());
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(items[1]).toHaveFocus();
    fireEvent.keyDown(items[1], { key: "ArrowUp" });
    expect(items[0]).toHaveFocus();
    fireEvent.keyDown(items[0], { key: "End" });
    expect(items[items.length - 1]).toHaveTextContent("Ya no sigue");
    expect(items[items.length - 1]).toHaveFocus();
    // A click inside the portaled menu is not a click «outside»: the item runs.
    fireEvent.mouseDown(items[items.length - 1]);
    fireEvent.click(items[items.length - 1]);
    expect(await screen.findByRole("dialog")).toHaveTextContent("Ya no sigue");
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancelar" }));
    fireEvent.click(more);
    const again = await screen.findByRole("menu");
    fireEvent.keyDown(within(again).getAllByRole("menuitem")[0], { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    expect(more).toHaveFocus();
  });

  it("leaving the tab clears the search; the filters in the address stay", async () => {
    window.location.hash = "#/ventas/seguimiento?encargado=karla";
    stubApi();
    const first = renderPage();
    await screen.findByTestId(`seg-row-${B}`);
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar" }), { target: { value: "laboratorio" } });
    await waitFor(() => expect(screen.getByRole("searchbox", { name: "Buscar" })).toHaveValue("laboratorio"));
    // A re-read of the page itself (same tab) keeps it…
    act(() => {
      window.location.hash = "#/ventas/seguimiento?encargado=karla&caso=" + A;
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(screen.getByRole("searchbox", { name: "Buscar" })).toHaveValue("laboratorio");
    // …going to Hoy and back does not.
    act(() => {
      window.location.hash = "#/hoy";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    first.unmount();
    window.location.hash = "#/ventas/seguimiento?encargado=karla";
    renderPage();
    await screen.findByTestId(`seg-row-${B}`);
    expect(screen.getByRole("searchbox", { name: "Buscar" })).toHaveValue("");
    expect(within(screen.getByRole("group", { name: "Encargado" })).getByRole("button", { pressed: true })).toBeInTheDocument();
  });

  it("at phone width the small controls are 44 px tap targets, and a card's name and contact are one", async () => {
    screenWidth(390);
    stubApi((n) => {
      const data = fixture(n);
      // A landline: no WhatsApp button, even for the technical person.
      data.items[0] = { ...data.items[0], follow_up: { operator_id: "op-tati", operator_name: "Tati", channel: "llamada" }, contact: { ...data.items[0].contact!, phone: "+56 2 2123 4567" } };
      return data;
    });
    renderPage();
    const cardA = within(await screen.findByTestId(`seg-card-${A}`));
    const name = cardA.getByRole("button", { name: "Instituto Ficticio" });
    expect(name).toHaveTextContent("Persona Uno");
    expect(name).toHaveAccessibleDescription(/Persona Uno/);
    expect(name.className).toMatch(/max-sm:min-h-11/);
    expect(name.className).toMatch(/w-full/);
    expect(cardA.getByRole("link", { name: /Llamar a Instituto Ficticio/ })).toBeInTheDocument();
    expect(cardA.queryByRole("link", { name: /WhatsApp/ })).not.toBeInTheDocument();
    fireEvent.click(cardA.getByRole("button", { name: "Ver guion para Instituto Ficticio" }));
    const modal = await screen.findByRole("dialog", { name: "Guion de llamada" });
    expect(within(modal).getByRole("button", { name: "Cerrar guion" })).toHaveClass("tap-44");
    expect(within(modal).getAllByRole("button", { name: "Cerrar" })[0]).toHaveClass("tap-44");
  });

  it("a pause we answered by email after its day is over: no pause line, «Próximo» is the follow-up due today", async () => {
    // The rhythm counts days from the 15 Nov email: pin the clock (only Date; timers stay real for findBy).
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    onTestFinished(() => {
      vi.useRealTimers();
    });
    stubApi((n) => {
      const data = fixture(n);
      data.items[0] = {
        ...data.items[0], paused_until: "2026-10-25", pause_reason: "Evaluando internamente",
        last_contact: { inbound: null, outbound: { at: "2026-11-15T14:00:00Z", url: null } } as OpportunityCardData["last_contact"],
      };
      return data;
    });
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(row(A)).getByRole("button", { name: "Instituto Ficticio" }));
    const panel = within(await screen.findByRole("dialog"));
    // Paused until 25 Oct, emailed on 15 Nov: the email resumed it (owner 2026-11-22: «we contacted
    // them and they're still there»), so the panel no longer says «Volvió de pausa».
    expect(panel.queryByTestId("case-drawer-pause")).not.toBeInTheDocument();
    expect(panel.getByTestId("case-drawer-followup")).toHaveTextContent("Seguimiento 1 de 3 · hoy");
    expect(panel.getByTestId("drawer-next-rhythm")).toHaveTextContent("Próximo contacto: Seguimiento 1 de 3 · hoy");
    expect(panel.queryByTestId("drawer-next-pause")).not.toBeInTheDocument();
    expect(within(row(A)).getByTestId("seg-next")).toHaveTextContent(/^hoy/);
  });

  it("«Pausar» from the case panel offers «Deshacer» too", async () => {
    const calls = stubApi(fixture, (call) => (call.path.endsWith("/set-case-pause") ? receipt(A, call.body.paused_until ? 4 : 5, { command: "set_case_pause" }) : null));
    renderPage();
    await screen.findByTestId(`seg-row-${A}`);
    fireEvent.click(within(row(A)).getByRole("button", { name: "Instituto Ficticio" }));
    const panel = within(await screen.findByRole("dialog"));
    fireEvent.click(panel.getByTestId("case-inline-pausa"));
    const form = within(panel.getByTestId("case-move-form"));
    fireEvent.click(form.getAllByRole("button").find((b) => b.closest("[role=group]"))!);
    fireEvent.click(form.getByRole("button", { name: "Pausar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Deshacer" }));
    expect(await screen.findByText("Listo: la pausa se deshizo.")).toBeInTheDocument();
    const pauses = calls.filter((c) => c.path.endsWith("/set-case-pause"));
    expect(pauses[1].body).toMatchObject({ opportunity_id: A, opportunity_version: 4, paused_until: null });
  });

  it("shows the newest note under the client, and the last three on tap", async () => {
    stubApi((n) => {
      const data = fixture(n);
      data.items[1] = {
        ...data.items[1],
        recent_notes: [
          { at: "2026-11-18T13:00:00Z", body: "Llamada: Contestó", kind: "llamada", outcome: "contesto", author: "Karla" },
          { at: "2026-11-17T13:00:00Z", body: "Pide la ficha técnica del equipo ficticio antes de decidir.", kind: null, outcome: null, author: "Rafa" },
          { at: "2026-11-16T13:00:00Z", body: "Dejé recado", kind: "llamada", outcome: "buzon", author: "Karla" },
        ],
      };
      return data;
    });
    renderPage();
    await screen.findByTestId(`seg-row-${B}`);
    const last = within(row(B)).getByTestId("seg-last-note");
    expect(last).toHaveTextContent("Contestó · Karla · hoy 10:00");
    fireEvent.click(last);
    const pop = within(row(B)).getByTestId("seg-notes-popover");
    expect(pop).toHaveAttribute("data-open", "true");
    expect(pop).toHaveTextContent("Pide la ficha técnica del equipo ficticio antes de decidir.");
    expect(pop).toHaveTextContent("Buzón de voz");
  });

  it("a reassign from the board picker says «Asignado a …»", async () => {
    window.location.hash = "#/ventas/seguimiento?vista=encargado";
    stubApi(fixture, (call) => (call.path.endsWith("/set-case-follow-up") ? receipt(B, 4, { operator_id: call.body.follow_up_operator_id, channel: call.body.channel }) : null));
    renderPage();
    const board = await screen.findByTestId("seg-board");
    expect(within(board).getAllByRole("heading")[0]).toHaveTextContent("Sin confirmar");
    const cardB = within(await screen.findByTestId(`seg-card-${B}`));
    await waitFor(() => expect(cardB.getByLabelText("Encargado")).toHaveValue("op-karla"));
    fireEvent.change(cardB.getByLabelText("Encargado"), { target: { value: "op-tati" } });
    expect(await screen.findByText("Asignado a Tati")).toBeInTheDocument();
  });
});
