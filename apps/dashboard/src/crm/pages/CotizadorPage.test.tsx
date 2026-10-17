/**
 * The Cotizador page reads one quote's workspace (`GET /v2/quotes/{id}`). Every value below is
 * invented. A viewer is refused by the API (403 `role_may_not_view_costs`); a missing quote is a 404.
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearCatalogIndexMemory } from "../catalog/catalogIndex";
import type { QuoteDraft } from "../quotes/quoteApi";
import { computedFixture, workspaceFixture } from "../quotes/quoteFixtures";
import { SAVE_DELAY_MS } from "../quotes/useQuoteDraft";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { CotizadorPage } from "./CotizadorPage";

const QUOTE_ID = "6bafddfa-0000-4000-8000-0000000000d1";
const CASE_ID = "6bafddfa-0000-4000-8000-0000000000d2";

function workspace(over: { status?: string; label?: string | null; revision_no?: number; title?: string | null } = {}) {
  return {
    quote: { id: QUOTE_ID, quote_number: "00042-26", opportunity_id: CASE_ID },
    case: {
      id: CASE_ID,
      title: over.title === undefined ? "Universidad Ejemplo — agitador" : over.title,
      stage: "quoting",
      closed: false,
      institution: { id: "i1", name: "Universidad Ejemplo", rut: null },
      participants: [],
      contacts: [],
      lo_que_pidio: null,
    },
    revisions: [],
    current: {
      id: "r1",
      revision_no: over.revision_no ?? 1,
      status: over.status ?? "draft",
      origin: "authored",
      version: 1,
      label: over.label === undefined ? "00042-26" : over.label,
    },
    suggestions: { mentioned: [], kits: [] },
    price_history: null,
  };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stub(answer: () => Response) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      urls.push(new URL(raw, "http://localhost").pathname);
      return Promise.resolve(answer());
    }),
  );
  return urls;
}

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: `op-${role}`, email: "ventas@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

function renderPage(role = "sales", quoteId: string | null = QUOTE_ID) {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <CotizadorPage quoteId={quoteId} navigate={navigate} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
  return navigate;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearResourceCache();
  clearCatalogIndexMemory();
  sessionStorage.clear();
});

describe("Cotizador page", () => {
  it("shows a skeleton while the workspace is read", () => {
    stub(() => json(workspace()));
    renderPage();
    expect(screen.getByRole("status", { name: "Cargando" })).toBeInTheDocument();
  });

  it("shows the number, the status and the way back to the case", async () => {
    const urls = stub(() => json(workspace()));
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Cotización CN00042" })).toBeInTheDocument();
    expect(urls).toEqual([`/v2/quotes/${QUOTE_ID}`]);
    expect(screen.getByText("Borrador")).toBeInTheDocument();
    const back = screen.getByRole("link", { name: /Universidad Ejemplo — agitador/ });
    expect(back).toHaveAttribute("href", `#/crm/oportunidades/${CASE_ID}`);
    expect(back.textContent).toMatch(/^←/);
  });

  it("names a later revision with its letter and labels each status", async () => {
    const cases: [string, string][] = [
      ["draft", "Borrador"],
      ["approved", "Aprobada"],
      ["sent", "Enviada"],
      ["void", "Anulada"],
    ];
    for (const [status, text] of cases) {
      stub(() => json(workspace({ status, label: "00042A-26", revision_no: 2 })));
      const { unmount } = render(
        <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
          <CotizadorPage quoteId={QUOTE_ID} navigate={vi.fn()} />
        </AuthSessionContext.Provider>,
      );
      expect(await screen.findByRole("heading", { level: 1, name: "Cotización CN00042A" })).toBeInTheDocument();
      expect(screen.getByText(text)).toBeInTheDocument();
      unmount();
      clearResourceCache();
    }
  });

  it("falls back to the institution when the case has no title", async () => {
    stub(() => json(workspace({ title: null })));
    renderPage();
    expect(await screen.findByRole("link", { name: /Universidad Ejemplo/ })).toBeInTheDocument();
  });

  it("tells a viewer only sales and administration can quote", async () => {
    stub(() => json({ detail: { code: "role_may_not_view_costs", message: "no" } }, 403));
    renderPage("viewer");
    expect(await screen.findByText("Sólo ventas y administración pueden cotizar.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("says the quote does not exist on a 404, with a way back", async () => {
    stub(() => json({ detail: "Not Found" }, 404));
    renderPage();
    expect(await screen.findByText("Esta cotización no existe.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Volver a Oportunidades" })).toHaveAttribute("href", "#/crm/oportunidades");
  });

  it("offers a retry on a server error", async () => {
    let calls = 0;
    stub(() => (++calls === 1 ? json({ detail: "boom" }, 500) : json(workspace())));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Reintentar" }));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "Cotización CN00042" })).toBeInTheDocument());
    expect(calls).toBe(2);
  });

  it("makes no request without a quote id", () => {
    const urls = stub(() => json(workspace()));
    renderPage("sales", null);
    expect(screen.getByText("Esta cotización no existe.")).toBeInTheDocument();
    expect(urls).toEqual([]);
  });
});

/** An authored draft the editor can change; the API answers by method and path. */
function editableWorkspace() {
  const ws = workspaceFixture();
  return {
    ...ws,
    quote: { ...ws.quote, id: QUOTE_ID, opportunity_id: CASE_ID },
    case: {
      ...ws.case,
      id: CASE_ID,
      contacts: [{ participant_id: "pa-1", person_id: null, contact_point_id: null, name: "Ana Prueba", email: "ana@ejemplo.cl", source: "participant" }],
      lo_que_pidio: "Un equipo de prueba.",
    },
  };
}

const CATALOG = { version: "v1", fields: ["id", "model_number", "name"], suppliers: [], items: [] };

type Call = { method: string; path: string; body: unknown; key: string | null };

function stubApi(
  onSave: (call: number) => Response | Promise<Response>,
  workspace: (read: number) => unknown = editableWorkspace,
  commands: Record<string, (body: unknown, call: number) => Response | Promise<Response>> = {},
) {
  const calls: Call[] = [];
  let saves = 0;
  let reads = 0;
  const counts: Record<string, number> = {};
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(raw, "http://localhost").pathname;
      const method = init?.method ?? "GET";
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ method, path, body, key: headers["Idempotency-Key"] ?? null });
      if (path === "/v2/catalog/index") return Promise.resolve(json(CATALOG));
      if (path === "/v2/quotes/preview") return Promise.resolve(json({ computed: computedFixture("200000") }));
      if (path === "/v2/commands/save-quote-draft") return Promise.resolve(onSave(++saves));
      if (commands[path]) {
        counts[path] = (counts[path] ?? 0) + 1;
        return Promise.resolve(commands[path](body, counts[path]));
      }
      return Promise.resolve(json(workspace(++reads)));
    }),
  );
  return calls;
}

/**
 * The autosave waits SAVE_DELAY_MS after the last edit. Tests that need it do not wait on the wall
 * clock: the page's timers are fake (and still advance, so Testing Library's polling runs), and
 * `autosave()` moves them past the delay at once.
 */
function fakeTimers() {
  vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout"] });
}
async function autosave() {
  await act(async () => {
    vi.advanceTimersByTime(SAVE_DELAY_MS + 50);
  });
}

describe("Cotizador page — the draft editor", () => {
  it("shows the client and the products of an authored draft, saved", async () => {
    stubApi(() => json({}));
    renderPage();
    expect(await screen.findByRole("heading", { level: 2, name: "Productos" })).toBeInTheDocument();
    expect(screen.getByText("Institución de prueba")).toBeInTheDocument();
    expect(screen.getByText("Un equipo de prueba.")).toBeInTheDocument();
    expect(screen.getByLabelText("Descripción de la línea 2")).toHaveValue("Servicio de prueba");
    expect(screen.getByText("guardado")).toBeInTheDocument();
  });

  it("saves an edit by itself and says so", async () => {
    fakeTimers();
    const calls = stubApi(() => json({ revision_id: "r-1", version: 4, computed: computedFixture("200000") }));
    renderPage();
    const qty = await screen.findByLabelText("Cantidad de la línea 1");
    fireEvent.focus(qty);
    fireEvent.change(qty, { target: { value: "3" } });
    expect(screen.getByText("sin guardar")).toBeInTheDocument();
    await autosave();
    expect(await screen.findByText(/guardado automáticamente hace \d+ s/)).toBeInTheDocument();
    const save = calls.find((c) => c.path === "/v2/commands/save-quote-draft");
    expect(save?.body).toMatchObject({ revision_id: "r-1", expected_version: 3 });
    expect((save?.body as { draft: { items: { quantity: string }[] } }).draft.items[0].quantity).toBe("3");
  });

  it("offers a retry when the save fails", async () => {
    fakeTimers();
    stubApi((n) => (n === 1 ? json({ detail: "boom" }, 500) : json({ revision_id: "r-1", version: 4, computed: computedFixture() })));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "2" } });
    await autosave();
    const retry = await screen.findByRole("button", { name: "Reintentar" });
    expect(screen.queryByText(/boom/)).not.toBeInTheDocument();
    fireEvent.click(retry);
    expect(await screen.findByText(/guardado automáticamente/)).toBeInTheDocument();
  });

  it("stops on someone else's save and reloads on «Ver cambios»", async () => {
    fakeTimers();
    const calls = stubApi(() => json({ detail: { code: "stale_version", message: "otra versión" } }, 409));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "2" } });
    await autosave();
    expect(await screen.findByText("Otra persona cambió este borrador.")).toBeInTheDocument();
    expect(screen.getByLabelText("Cantidad de la línea 1")).toBeDisabled();
    const reads = calls.filter((c) => c.path === `/v2/quotes/${QUOTE_ID}`).length;
    fireEvent.click(screen.getByRole("button", { name: "Ver cambios" }));
    await waitFor(() => expect(screen.queryByText("Otra persona cambió este borrador.")).not.toBeInTheDocument());
    expect(calls.filter((c) => c.path === `/v2/quotes/${QUOTE_ID}`).length).toBe(reads + 1);
    expect(screen.getByLabelText("Cantidad de la línea 1")).toBeEnabled();
  });

  it("saves a condition and a switch to manual expenses, without the editor's row ids", async () => {
    fakeTimers();
    const calls = stubApi(() => json({ revision_id: "r-1", version: 4, computed: computedFixture("200000") }));
    renderPage();
    // «Editar montos» first: once an edit is waiting for its price, it waits too (nothing stale is copied).
    fireEvent.click(await screen.findByRole("button", { name: "Editar montos" }));
    fireEvent.change(screen.getByLabelText("Plazo de entrega"), { target: { value: "4 semanas" } });
    fireEvent.click(screen.getByRole("button", { name: "+ Agregar otra condición" }));
    fireEvent.change(screen.getByLabelText("Nombre de la condición 1"), { target: { value: "Embalaje" } });
    fireEvent.change(screen.getByLabelText("Texto de la condición 1"), { target: { value: "Caja de madera" } });
    await autosave();
    expect(await screen.findByText(/guardado automáticamente/)).toBeInTheDocument();
    const save = calls.filter((c) => c.path === "/v2/commands/save-quote-draft").pop();
    const sent = (save?.body as { draft: QuoteDraft }).draft;
    expect(sent.expense_method).toBe("manual");
    expect(sent.manual_expenses).toEqual([
      { label: "Flete", amount: "150.5", currency: "USD" },
      { label: "Desaduanaje", amount: "45000", currency: "CLP" },
    ]);
    expect(sent.terms).toMatchObject({ delivery_terms: "4 semanas", terms_extra: [{ label_es: "Embalaje", text_es: "Caja de madera" }] });
    expect(JSON.stringify(sent)).not.toContain("uid");
  });

  it("keeps the expenses and conditions read-only after someone else's save", async () => {
    fakeTimers();
    stubApi(() => json({ detail: { code: "stale_version", message: "otra versión" } }, 409));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Plazo de entrega"), { target: { value: "4 semanas" } });
    await autosave();
    expect(await screen.findByText("Otra persona cambió este borrador.")).toBeInTheDocument();
    expect(screen.getByLabelText("Plazo de entrega")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Perfil" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Editar montos" })).not.toBeInTheDocument();
  });

  it("shows a refused recipient under «Para», and points the save line there", async () => {
    fakeTimers();
    stubApi(() => json({ detail: { code: "recipient_own_address", message: "Esa dirección es de OrigenLab." } }, 422));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "2" } });
    await autosave();
    expect(await screen.findByText("Esa dirección es de OrigenLab.")).toBeInTheDocument();
    expect(screen.getByText(/no se pudo guardar: revisa «Para»/)).toBeInTheDocument();
  });

  it("says the server's reason for any other refusal", async () => {
    fakeTimers();
    stubApi(() => json({ detail: { code: "numbering_not_configured", message: "Falta configurar la numeración." } }, 422));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "2" } });
    await autosave();
    expect(await screen.findByText(/no se pudo guardar: Falta configurar la numeración\./)).toBeInTheDocument();
  });

  it("shows the totals and the margin beside the editor", async () => {
    stubApi(() => json({}));
    renderPage();
    const totals = await screen.findByRole("region", { name: "Totales" });
    expect(within(totals).getByText("Total").nextSibling).toHaveTextContent("$ 119.000");
    expect(within(totals).getByText("Margen bruto")).toBeInTheDocument();
  });

  it("«Guardar ahora» saves a draft whose prices changed since its last save (B4)", async () => {
    const stale = () => {
      const ws = editableWorkspace();
      return { ...ws, current: { ...ws.current!, needs_saving: true, totals_computed_at: "2026-10-10T12:00:00Z" } };
    };
    const calls = stubApi(() => json({ revision_id: "r-1", version: 4, computed: computedFixture("200000") }), stale);
    renderPage();
    const banner = await screen.findByText(/Los precios cambiaron desde el último guardado/);
    expect(banner).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Guardar ahora" }));
    await waitFor(() => expect(screen.queryByText(/Los precios cambiaron desde el último guardado/)).not.toBeInTheDocument());
    const save = calls.find((c) => c.path === "/v2/commands/save-quote-draft");
    expect(save?.body).toMatchObject({ revision_id: "r-1", expected_version: 3 });
  });
});

describe("Cotizador page — an issued revision", () => {
  it("shows its stored totals", async () => {
    const issued = () => {
      const ws = editableWorkspace();
      return {
        ...ws,
        current: { ...ws.current!, status: "approved", subtotal: "500000", tax_total: "95000", grand_total: "595000", draft: undefined, computed: undefined, lines: [] },
      };
    };
    stubApi(() => json({}), issued);
    renderPage();
    const totals = await screen.findByRole("region", { name: "Totales" });
    expect(within(totals).getByText("Total").nextSibling).toHaveTextContent("$ 595.000");
    expect(screen.queryByText("Margen bruto")).not.toBeInTheDocument();
  });
});

/* ── approve, void and revise (Task 7) ─────────────────────────────────────── */

const APPROVE = "/v2/commands/approve-quote-revision";
const VOID = "/v2/commands/void-quote-revision";
const REVISE = "/v2/commands/create-quote-revision";

type Rev = { id: string; revision_no: number; status: string; version?: number; grand_total?: string | null; approved_at?: string | null; sent_at?: string | null; superseded_by_revision_no?: number | null };

function revisionsOf(list: Rev[]) {
  const letter = (n: number) => (n >= 2 ? String.fromCharCode(63 + n) : "");
  return list.map((r) => ({
    id: r.id,
    revision_no: r.revision_no,
    label: `00042${letter(r.revision_no)}-26`,
    status: r.status,
    origin: "authored",
    version: r.version ?? 2,
    grand_total: r.grand_total ?? null,
    approved_at: r.approved_at ?? null,
    sent_at: r.sent_at ?? null,
    superseded_by_revision_no: r.superseded_by_revision_no ?? null,
  }));
}

/** An issued revision: stored lines, stored totals and what approval froze for the client. */
function issuedWorkspace(over: { status?: string; superseded_by_revision_no?: number | null; revisions?: Rev[]; revision_no?: number; id?: string } = {}) {
  const ws = editableWorkspace();
  const status = over.status ?? "approved";
  const id = over.id ?? "r-1";
  const revision_no = over.revision_no ?? 1;
  const stored = (line_no: number, description: string, quantity: string, unit: string, total: string, kind = "item") => ({
    line_no, line_kind: kind, description, product_id: null, quantity, item_type: "equipment", cost_currency: "CLP",
    unit_cost: "1000", fx_rate: null, fx_as_of: null, fx_source: null, margin_mode: "markup", margin_pct: "0.3",
    unit_price_qc: unit, line_total_qc: total, is_principal: line_no === 1, allocated_to_line_no: null,
    expense_method: null, supplier_organization_id: null, cost_observation_id: null, line_note_es: line_no === 2 ? "Incluye traslado." : null,
  });
  return {
    ...ws,
    revisions: revisionsOf(over.revisions ?? [{ id, revision_no, status, version: 5, grand_total: "595000", approved_at: "2026-10-16T13:00:00Z", superseded_by_revision_no: over.superseded_by_revision_no ?? null }]),
    current: {
      ...ws.current!,
      id,
      revision_no,
      label: revision_no === 1 ? "00042-26" : "00042A-26",
      status,
      version: 5,
      subtotal: "500000",
      tax_total: "95000",
      grand_total: "595000",
      valid_until: "2026-11-15",
      approved_at: "2026-10-16T13:00:00Z",
      superseded_by_revision_no: over.superseded_by_revision_no ?? null,
      draft: undefined,
      computed: undefined,
      needs_saving: undefined,
      lines: [
        stored(1, "Agitador de prueba", "2", "200000", "400000"),
        stored(2, "Servicio de prueba", "1", "100000", "100000"),
        stored(3, "Flete", "1", "5000", "5000", "expense"),
      ],
      party_snapshot: {
        institution: { id: "i-1", name: "Institución de prueba", rut: "76.000.000-0" },
        recipient: { participant_id: "pa-1", name: "Ana Prueba", email: "ana@ejemplo.cl" },
        terms: { delivery_terms: "4 semanas", payment_terms: "Contado", warranty_terms_es: null, delivery_place_es: null, installation_terms_es: null, client_notes_es: "Precios netos.", validity_days: 30, terms_extra: [{ label_es: "Embalaje", text_es: "Caja de madera" }] },
        quote_number: "00042-26",
        revision_label: "00042-26",
        approved_by: "Rafa Prueba",
        approved_at: "2026-10-16",
        valid_until: "2026-11-15",
      },
    },
  };
}

const approved = (version = 5) => json({ ok: true, revision_id: "r-1", version, quote_number: "00042-26", revision_label: "00042-26", negotiated_costs_skipped: [] });

describe("Cotizador page — approving a draft", () => {
  it("offers «Emitir cotización» and «Vista previa PDF», which opens the draft's document marked BORRADOR", async () => {
    stubApi(() => json({}));
    renderPage();
    const preview = await screen.findByRole("button", { name: "Vista previa PDF" });
    expect(preview).toBeEnabled();
    expect(screen.getByRole("button", { name: "Emitir cotización" })).toBeEnabled();
    fireEvent.click(preview);
    const dialog = await screen.findByRole("dialog", { name: /Vista previa PDF · CN00042/ });
    expect(within(dialog).getByText("BORRADOR · sin aprobar")).toBeInTheDocument();
    // This draft's lines are not priced yet: it cannot be printed until they are.
    expect(within(dialog).getByText(/Faltan precios/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Imprimir / Guardar PDF" })).toBeDisabled();
    // The draft's import expenses and its costs and margins never reach the document.
    expect(dialog.textContent).not.toMatch(/Flete|Desaduanaje|Margen|Recargo|Costo|Proveedor/i);
    expect(document.title).toBe("Cotización CN00042 — Institución de prueba (borrador)");
    fireEvent.click(within(dialog).getByRole("button", { name: "Volver" }));
    expect(screen.queryByRole("dialog", { name: /Vista previa PDF/ })).not.toBeInTheDocument();
  });

  it("prints an approved revision from what approval froze, without its expense lines or costs", async () => {
    stubApi(() => json({}), () => issuedWorkspace());
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Vista previa PDF" }));
    const dialog = await screen.findByRole("dialog", { name: /Vista previa PDF · CN00042/ });
    const sheet = within(dialog);
    expect(sheet.getByText("76.000.000-0", { exact: false })).toBeInTheDocument();
    expect(sheet.getByText("Agitador de prueba")).toBeInTheDocument();
    expect(sheet.queryByText("Aprobada por Rafa Prueba")).toBeNull();
    expect(sheet.getByText("Tatiana Vivanco")).toBeInTheDocument();
    expect(sheet.queryByText(/BORRADOR/)).not.toBeInTheDocument();
    expect(dialog.textContent).not.toMatch(/Flete/);
    const print = vi.fn();
    vi.stubGlobal("print", print);
    fireEvent.click(sheet.getByRole("button", { name: "Imprimir / Guardar PDF" }));
    expect(print).toHaveBeenCalledTimes(1);
  });

  it("saves unsaved edits first, then approves with the version that save returned (Review Focus 3)", async () => {
    let releaseSave!: () => void;
    const saving = new Promise<void>((r) => (releaseSave = r));
    const calls = stubApi(
      () => saving.then(() => json({ revision_id: "r-1", version: 4, computed: computedFixture("200000") })),
      (read) => (read === 1 ? editableWorkspace() : issuedWorkspace()),
      { [APPROVE]: () => approved() },
    );
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Emitir cotización" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v2/commands/save-quote-draft")).toBe(true));
    // While the save is in flight the button waits for it; nothing is approved yet.
    expect(screen.getByRole("button", { name: /Emitiendo/ })).toBeDisabled();
    expect(calls.some((c) => c.path === APPROVE)).toBe(false);
    releaseSave();
    expect(await screen.findByText("Cotización emitida · CN00042")).toBeInTheDocument();
    const paths = calls.filter((c) => c.method === "POST" && c.path.startsWith("/v2/commands/")).map((c) => c.path);
    expect(paths).toEqual(["/v2/commands/save-quote-draft", APPROVE]);
    expect(calls.find((c) => c.path === APPROVE)?.body).toEqual({ revision_id: "r-1", expected_version: 4 });
    // Reloaded: the approved revision is read-only.
    expect(await screen.findByText("Aprobada por Rafa Prueba el 16 oct 2026")).toBeInTheDocument();
    expect(screen.queryByLabelText("Cantidad de la línea 1")).not.toBeInTheDocument();
  });

  it("saves a draft whose prices changed since its last save before approving (B4)", async () => {
    const stale = () => {
      const ws = editableWorkspace();
      return { ...ws, current: { ...ws.current!, needs_saving: true, totals_computed_at: "2026-10-10T12:00:00Z" } };
    };
    const calls = stubApi(() => json({ revision_id: "r-1", version: 4, computed: computedFixture("200000") }), stale, { [APPROVE]: () => approved() });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Emitir cotización" }));
    await waitFor(() => expect(calls.some((c) => c.path === APPROVE)).toBe(true));
    const paths = calls.filter((c) => c.method === "POST" && c.path.startsWith("/v2/commands/")).map((c) => c.path);
    expect(paths).toEqual(["/v2/commands/save-quote-draft", APPROVE]);
    expect(calls.find((c) => c.path === APPROVE)?.body).toMatchObject({ expected_version: 4 });
  });

  it("lists every reason the API gave under the header", async () => {
    const calls = stubApi(() => json({}), editableWorkspace, {
      [APPROVE]: () => json({ detail: { code: "missing_recipient", message: "Elige a quién va la cotización · Completa el texto de «Forma de pago»" } }, 409),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Emitir cotización" }));
    const alert = await screen.findByRole("alert", { name: "No se pudo emitir" });
    const items = within(alert).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["Elige a quién va la cotización", "Completa el texto de «Forma de pago»"]);
    expect(screen.getByRole("button", { name: "Emitir cotización" })).toBeEnabled();
    // A second try is the same action at the same version: the same key.
    fireEvent.click(screen.getByRole("button", { name: "Emitir cotización" }));
    await waitFor(() => expect(calls.filter((c) => c.path === APPROVE)).toHaveLength(2));
    const keys = calls.filter((c) => c.path === APPROVE).map((c) => c.key);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("does not ask the API to approve an incomplete draft, and says what is missing", async () => {
    const calls = stubApi(() => json({}), editableWorkspace, { [APPROVE]: () => approved() });
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Emitir cotización" }));
    const alert = await screen.findByRole("alert", { name: "No se pudo emitir" });
    expect(within(alert).getAllByRole("listitem").length).toBeGreaterThan(0);
    expect(within(alert).getByText(/cantidad/i)).toBeInTheDocument();
    expect(calls.some((c) => c.path === APPROVE || c.path === "/v2/commands/save-quote-draft")).toBe(false);
  });
});

describe("Cotizador page — an approved revision, read-only", () => {
  it("shows the stored lines, the recipient, the conditions and who approved it", async () => {
    stubApi(() => json({}), () => issuedWorkspace());
    renderPage();
    const lines = await screen.findByRole("table", { name: "Líneas aprobadas" });
    const rows = within(lines).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2); // the expense line is internal
    expect(within(rows[0]).getByText("Agitador de prueba")).toBeInTheDocument();
    expect(within(rows[0]).getByText("2")).toBeInTheDocument();
    expect(within(rows[0]).getByText("$ 200.000")).toBeInTheDocument();
    expect(within(rows[0]).getByText("$ 400.000")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Incluye traslado.")).toBeInTheDocument();
    expect(screen.getByText("Ana Prueba")).toBeInTheDocument();
    expect(screen.getByText("ana@ejemplo.cl")).toBeInTheDocument();
    expect(screen.getByText("4 semanas")).toBeInTheDocument();
    expect(screen.getByText("Contado")).toBeInTheDocument();
    expect(screen.getByText("30 días")).toBeInTheDocument();
    expect(screen.getByText("Caja de madera")).toBeInTheDocument();
    expect(screen.getByText("Válida hasta el 15 nov 2026")).toBeInTheDocument();
    expect(screen.getByText("Aprobada por Rafa Prueba el 16 oct 2026")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Emitir cotización" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nueva revisión" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Anular" })).toBeInTheDocument();
  });

  it("«Nueva revisión» opens the next draft", async () => {
    const draftB = () => {
      const ws = editableWorkspace();
      return {
        ...ws,
        revisions: revisionsOf([
          { id: "r-1", revision_no: 1, status: "approved", grand_total: "595000" },
          { id: "r-2", revision_no: 2, status: "draft" },
        ]),
        current: { ...ws.current!, id: "r-2", revision_no: 2, label: "00042A-26" },
      };
    };
    const calls = stubApi(() => json({}), (read) => (read === 1 ? issuedWorkspace() : draftB()), {
      [REVISE]: () => json({ ok: true, quote_id: QUOTE_ID, revision_id: "r-2", revision_no: 2, revision_label: "00042A-26", version: 1 }),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva revisión" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Cotización CN00042A" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === REVISE)?.body).toEqual({ quote_id: QUOTE_ID });
    expect(calls.find((c) => c.path === REVISE)?.key).toBeTruthy();
    expect(screen.getByLabelText("Cantidad de la línea 1")).toBeEnabled();
    expect(screen.getByText("Nueva revisión CN00042A abierta")).toBeInTheDocument();
  });

  it("a sent revision can be revised but not voided", async () => {
    stubApi(() => json({}), () => issuedWorkspace({ status: "sent" }));
    renderPage();
    expect(await screen.findByRole("button", { name: "Nueva revisión" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Anular" })).not.toBeInTheDocument();
  });

  it("«Anular» asks for a reason and voids with it", async () => {
    const calls = stubApi(() => json({}), (read) => (read === 1 ? issuedWorkspace() : issuedWorkspace({ status: "void" })), {
      [VOID]: () => json({ ok: true, revision_id: "r-1", version: 6 }),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Anular" }));
    const dialog = screen.getByRole("alertdialog");
    const confirm = within(dialog).getByRole("button", { name: "Anular" });
    fireEvent.click(within(dialog).getByRole("checkbox"));
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/Motivo/), { target: { value: "El cliente pidió otro modelo" } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(calls.find((c) => c.path === VOID)?.body).toEqual({ revision_id: "r-1", expected_version: 5, reason: "El cliente pidió otro modelo" });
    expect(await screen.findByText("Anulada")).toBeInTheDocument();
  });

  it("says why the API refused to void, in the dialog", async () => {
    stubApi(() => json({}), () => issuedWorkspace(), {
      [VOID]: () => json({ detail: { code: "superseded", message: "Ya fue reemplazada por la revisión 2." } }, 409),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Anular" }));
    const dialog = screen.getByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.change(within(dialog).getByLabelText(/Motivo/), { target: { value: "Error" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Anular" }));
    expect(await within(dialog).findByText("Ya fue reemplazada por la revisión 2.")).toBeInTheDocument();
  });

  it("a superseded revision says which one replaced it, and cannot be voided", async () => {
    stubApi(() => json({}), () =>
      issuedWorkspace({
        superseded_by_revision_no: 2,
        revisions: [
          { id: "r-1", revision_no: 1, status: "approved", superseded_by_revision_no: 2, grand_total: "595000" },
          { id: "r-2", revision_no: 2, status: "void", grand_total: "700000" },
        ],
      }),
    );
    renderPage();
    expect(await screen.findByText("Reemplazada por CN00042A")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Anular" })).not.toBeInTheDocument();
  });

  it("voids a draft from its header, after its pending save", async () => {
    const calls = stubApi(() => json({ revision_id: "r-1", version: 4, computed: computedFixture() }), editableWorkspace, {
      [VOID]: () => json({ ok: true, revision_id: "r-1", version: 5 }),
    });
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Anular" }));
    const dialog = screen.getByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.change(within(dialog).getByLabelText(/Motivo/), { target: { value: "Duplicada" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Anular" }));
    await waitFor(() => expect(calls.some((c) => c.path === VOID)).toBe(true));
    expect(calls.find((c) => c.path === VOID)?.body).toMatchObject({ revision_id: "r-1", expected_version: 4 });
  });
});

describe("Cotizador page — revisions", () => {
  const two = () =>
    issuedWorkspace({
      id: "r-2",
      revision_no: 2,
      revisions: [
        { id: "r-1", revision_no: 1, status: "approved", version: 3, grand_total: "595000", approved_at: "2026-10-10T13:00:00Z", superseded_by_revision_no: 2 },
        { id: "r-2", revision_no: 2, status: "approved", version: 5, grand_total: "700000", approved_at: "2026-10-16T13:00:00Z" },
      ],
    });

  it("lists the revisions and shows an older one's summary", async () => {
    stubApi(() => json({}), two);
    renderPage();
    const list = await screen.findByRole("navigation", { name: "Revisiones" });
    const older = within(list).getByRole("button", { name: /CN00042\b.*Aprobada/ });
    expect(within(list).getByRole("button", { name: /CN00042A.*Aprobada/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(older);
    expect(older).toHaveAttribute("aria-pressed", "true");
    const summary = screen.getByRole("region", { name: "Revisión CN00042" });
    expect(within(summary).getByText("$ 595.000")).toBeInTheDocument();
    expect(within(summary).getByText("Reemplazada por CN00042A")).toBeInTheDocument();
    expect(within(summary).getByText(/sólo su resumen/)).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Líneas aprobadas" })).not.toBeInTheDocument();
  });

  it("shows no list for a single revision", async () => {
    stubApi(() => json({}), () => issuedWorkspace());
    renderPage();
    await screen.findByRole("table", { name: "Líneas aprobadas" });
    expect(screen.queryByRole("navigation", { name: "Revisiones" })).not.toBeInTheDocument();
  });
});

describe("Cotizador page — leaving with unsaved edits (Review Focus 5)", () => {
  it("the way back saves first, then leaves", async () => {
    let releaseSave!: () => void;
    const saving = new Promise<void>((r) => (releaseSave = r));
    const calls = stubApi(() => saving.then(() => json({ revision_id: "r-1", version: 4, computed: computedFixture() })));
    const navigate = renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("link", { name: /Caso de prueba/ }));
    expect(await screen.findByText(/Guardando antes de salir…/)).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.path === "/v2/commands/save-quote-draft")).toBe(true));
    expect(navigate).not.toHaveBeenCalled();
    releaseSave();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("oportunidades", CASE_ID));
  });

  it("asks before leaving an incomplete draft, and stays when told to", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    stubApi(() => json({}));
    const navigate = renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("link", { name: /Caso de prueba/ }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith("Hay cambios sin guardar. ¿Salir igual?"));
    expect(navigate).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("link", { name: /Caso de prueba/ }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("oportunidades", CASE_ID));
    confirm.mockRestore();
  });

  it("says so when the page closes another way (the menu) with edits that could not be saved", async () => {
    stubApi(() => json({}));
    render(<Toaster />);
    const page = render(
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <CotizadorPage quoteId={QUOTE_ID} navigate={vi.fn()} />
      </AuthSessionContext.Provider>,
    );
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "" } });
    page.unmount();
    expect(await screen.findByText("Tus últimos cambios en la cotización CN00042 no se guardaron: el borrador estaba incompleto.")).toBeInTheDocument();
  });

  it("leaves at once when everything is saved", async () => {
    stubApi(() => json({}));
    const navigate = renderPage();
    fireEvent.click(await screen.findByRole("link", { name: /Caso de prueba/ }));
    expect(navigate).toHaveBeenCalledWith("oportunidades", CASE_ID);
  });
});

/* ── final review wave ─────────────────────────────────────────────────────── */

describe("Cotizador page — refusals in Spanish", () => {
  it("says an English save refusal in Spanish, never the API's words", async () => {
    fakeTimers();
    stubApi(() => json({ detail: { code: "product_not_found", message: "no such product" } }, 404));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "2" } });
    await autosave();
    expect(await screen.findByText(/no se pudo guardar: Un producto de la cotización ya no está en el catálogo/)).toBeInTheDocument();
    expect(screen.queryByText(/no such product/)).not.toBeInTheDocument();
  });

  it("says an English recipient refusal in Spanish under «Para»", async () => {
    fakeTimers();
    stubApi(() => json({ detail: { code: "recipient_not_on_case", message: "the recipient is not a current participant of this case" } }, 422));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "2" } });
    await autosave();
    expect(await screen.findByText("Esa persona ya no participa en el caso; elige otra.")).toBeInTheDocument();
    expect(screen.getByText(/no se pudo guardar: revisa «Para»/)).toBeInTheDocument();
    expect(screen.queryByText(/not a current participant/)).not.toBeInTheDocument();
  });

  it("says an English void refusal in Spanish, in the dialog", async () => {
    stubApi(() => json({}), () => issuedWorkspace(), {
      [VOID]: () => json({ detail: { code: "superseded", message: "superseded by a later revision" } }, 409),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Anular" }));
    const dialog = screen.getByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.change(within(dialog).getByLabelText(/Motivo/), { target: { value: "Error" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Anular" }));
    expect(await within(dialog).findByText("Ya fue reemplazada por una revisión posterior.")).toBeInTheDocument();
    expect(within(dialog).queryByText(/superseded by/)).not.toBeInTheDocument();
  });
});

describe("Cotizador page — approved or voided in another tab (I6)", () => {
  for (const [code, status] of [["not_a_draft", 409], ["revision_not_found", 404]] as const) {
    it(`a save refused with ${code} freezes the editor and offers the current state`, async () => {
      fakeTimers();
      let saves = 0;
      const calls = stubApi(() => {
        saves += 1;
        return json({ detail: { code, message: "only a draft revision is edited" } }, status);
      });
      renderPage();
      fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "2" } });
      await autosave();
      expect(await screen.findByText("Esta cotización cambió en otra pestaña (se aprobó o anuló).")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Reintentar" })).not.toBeInTheDocument();
      expect(screen.getByLabelText("Cantidad de la línea 1")).toBeDisabled();
      // Nothing more is sent: no endless retries.
      await autosave();
      expect(saves).toBe(1);
      const reads = calls.filter((c) => c.path === `/v2/quotes/${QUOTE_ID}`).length;
      fireEvent.click(screen.getByRole("button", { name: "Ver estado actual" }));
      await waitFor(() => expect(calls.filter((c) => c.path === `/v2/quotes/${QUOTE_ID}`).length).toBe(reads + 1));
      await waitFor(() => expect(screen.queryByText("Esta cotización cambió en otra pestaña (se aprobó o anuló).")).not.toBeInTheDocument());
    });
  }
});

describe("Cotizador page — no «Vista cliente» (owner, 2026-10-17)", () => {
  it("offers only «Vista previa PDF» as the client's view", async () => {
    stubApi(() => json({}));
    renderPage();
    expect(await screen.findByRole("button", { name: "Vista previa PDF" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Vista cliente" })).toBeNull();
  });
});

describe("Cotizador page — voiding an older revision with the draft open (I4)", () => {
  const withDraft = () => {
    const ws = editableWorkspace();
    return {
      ...ws,
      revisions: revisionsOf([
        { id: "r-0", revision_no: 1, status: "approved", version: 3, grand_total: "595000" },
        { id: "r-1", revision_no: 2, status: "draft" },
      ]),
      current: { ...ws.current!, id: "r-1", revision_no: 2, label: "00042A-26" },
    };
  };

  async function voidOlder() {
    fireEvent.click(within(screen.getByRole("navigation", { name: "Revisiones" })).getByRole("button", { name: /CN00042\b.*Aprobada/ }));
    fireEvent.click(screen.getByRole("button", { name: "Anular CN00042" }));
    const dialog = screen.getByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.change(within(dialog).getByLabelText(/Motivo/), { target: { value: "Duplicada" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Anular" }));
  }

  it("saves the draft's pending edits first", async () => {
    const calls = stubApi(() => json({ revision_id: "r-1", version: 4, computed: computedFixture() }), withDraft, {
      [VOID]: () => json({ ok: true, revision_id: "r-0", version: 4 }),
    });
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "3" } });
    await voidOlder();
    await waitFor(() => expect(calls.some((c) => c.path === VOID)).toBe(true));
    const posts = calls.filter((c) => c.method === "POST" && c.path.startsWith("/v2/commands/")).map((c) => c.path);
    expect(posts).toEqual(["/v2/commands/save-quote-draft", VOID]);
    expect(calls.find((c) => c.path === VOID)?.body).toMatchObject({ revision_id: "r-0", expected_version: 3 });
  });

  it("asks before voiding when the draft's edits cannot be saved, and keeps them when told not to", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const calls = stubApi(() => json({}), withDraft, { [VOID]: () => json({ ok: true, revision_id: "r-0", version: 4 }) });
    renderPage();
    fireEvent.change(await screen.findByLabelText("Cantidad de la línea 1"), { target: { value: "" } });
    await voidOlder();
    await waitFor(() => expect(confirm).toHaveBeenCalledWith("Hay cambios sin guardar en el borrador. ¿Anular igual?"));
    expect(calls.some((c) => c.path === VOID)).toBe(false);
    // The edit is still there.
    fireEvent.click(within(screen.getByRole("navigation", { name: "Revisiones" })).getByRole("button", { name: /CN00042A/ }));
    expect(screen.getByLabelText("Cantidad de la línea 1")).toHaveValue("");
    confirm.mockRestore();
  });
});

describe("Cotizador page — while a command runs (M1)", () => {
  it("the editor's inputs are off while approving", async () => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    stubApi(() => json({}), (read) => (read === 1 ? editableWorkspace() : issuedWorkspace()), { [APPROVE]: () => held.then(() => approved()) });
    renderPage();
    const qty = await screen.findByLabelText("Cantidad de la línea 1");
    expect(qty).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Emitir cotización" }));
    await waitFor(() => expect(screen.getByLabelText("Cantidad de la línea 1")).toBeDisabled());
    expect(screen.getByLabelText("Plazo de entrega")).toBeDisabled();
    release();
    expect(await screen.findByText("Cotización emitida · CN00042")).toBeInTheDocument();
  });
});
