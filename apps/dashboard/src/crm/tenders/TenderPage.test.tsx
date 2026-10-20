/**
 * A tender's page: who sees it, the estado control, «Subir anexos», the reading line and its polling,
 * Resumen citations, Checklist chips, «Llenar formularios», «Descargar paquete», the offer, and the
 * Spanish lines for refusals and failure codes. Invented data only.
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { LicitacionesPage } from "./LicitacionesPage";
import { TenderPage } from "./TenderPage";
import { POLL_MS } from "./useTenderPage";

const NOW = new Date("2026-10-19T15:00:00Z");
const TENDER = "33333333-3333-4333-8333-333333333333";
const READ = "44444444-4444-4444-8444-444444444444";
const FILL = "55555555-5555-4555-8555-555555555555";
const DOC1 = "66666666-6666-4666-8666-666666666666";
const QUOTE = "77777777-7777-4777-8777-777777777777";
const REV = "88888888-8888-4888-8888-888888888888";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const refusal = (status: number, code: string, message = "texto del servidor que no se muestra") =>
  json({ error: { code: "conflict", message, details: { code, message } } }, status);

function prep(phase: "read" | "fill", over: Record<string, unknown> = {}) {
  return {
    id: phase === "read" ? READ : FILL, phase, status: "ready", requested_at: "2026-10-19T14:00:00Z", started_at: null,
    finished_at: null, failure_code: null, annex_document_ids: [DOC1], based_on_preparation_id: phase === "fill" ? READ : null,
    quote_revision_id: null, result: {}, outputs: [], ...over,
  };
}

const READ_RESULT = {
  resumen: {
    objeto: { texto: "Balanzas analíticas de ejemplo", citas: [{ archivo_id: DOC1, ubicacion: "p.3" }], sin_cita: false },
    cierre: { texto: "No se indica", citas: [], sin_cita: true },
  },
  checklist: [{ documento: "Anexo 1 de ejemplo", tipo: "formulario", requiere_firma: false, archivo_formulario_id: DOC1,
    fuente: { archivo_id: DOC1, ubicacion: "p.5" }, sin_cita: false }],
  items: [{ linea: 1, descripcion: "Balanza analítica", cantidad: "2", unidad: "unidades", requisitos: [], citas: [], sin_cita: true,
    product_id: "p-1", match_reason: null, candidatos: [{ product_id: "p-1", modelo: "BX-1", nombre: "Balanza Ejemplo BX-1" }] }],
  annexes: [{ document_id: DOC1, filename: "bases_ejemplo.pdf", outcome: "ok" }],
  match: { estado: "hecho" },
};

const FILL_RESULT = {
  read_preparation_id: READ,
  checklist: [
    { documento: "Anexo 1 de ejemplo", tipo: "formulario", estado: "listo", fuente: { archivo_id: DOC1, ubicacion: "p.5" }, sin_cita: false, archivos: ["formulario_01"] },
    { documento: "Garantía de seriedad", tipo: "garantia", estado: "falta", falta: "sin_documento", fuente: null, sin_cita: true, archivos: [] },
    { documento: "Declaración jurada simple", tipo: "declaracion", estado: "firmar", fuente: { archivo_id: DOC1, ubicacion: "p.7" }, sin_cita: false, archivos: ["oferente_01"] },
  ],
  fichas: [{ product_id: "p-1", estado: "adjunta", file_key: "ficha_01" }, { product_id: "p-2", estado: "sin_ficha" }],
  drive: "apagado",
};
const FILL_OUTPUTS = [
  { file_key: "formulario_01", filename: "01_Anexo_1.docx", kind: "formulario", completar_count: 2 },
  { file_key: "oferta_economica", filename: "Oferta_economica.docx", kind: "oferta_economica", completar_count: 0 },
  { file_key: "ficha_01", filename: "Ficha_BX-1.pdf", kind: "ficha_tecnica", completar_count: 0 },
  { file_key: "paquete", filename: "Oferta_1234567-12-LE26.zip", kind: "paquete", completar_count: 0 },
];

function page(over: Record<string, unknown> = {}) {
  return {
    tender: {
      id: TENDER, version: 4, code: "1234567-12-LE26", title: "Compra de balanzas de ejemplo",
      institution: { id: "o1", name: "Hospital Ejemplo", confirmation: "confirmed" },
      closes_at: "2026-10-21T18:00:00Z", status: "preparando", amount: null, offer_quote_number: null,
      preparation: { read: null, fill: null },
      portal_url: "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idlicitacion=1234567-12-LE26",
      notice_head_summary: { description: null, estimated_amount: null, currency: "CLP", source_status: null },
    },
    annexes: [{ document_id: DOC1, filename: "bases_ejemplo.pdf", content_type: "application/pdf", byte_size: 1000, sha256: "ab", source: "upload", attached_at: "2026-10-19T13:00:00Z" }],
    preparations: { read: null, fill: null },
    offer_quote: null,
    spend: { spent_usd: "1.00", cap_usd: "60", month: "2026-10", reached: false },
    bidder_ready: true,
    ...over,
  };
}

interface Call {
  method: string;
  path: string;
  key: string | null;
  body: unknown;
}

/** Page reads in flight right now, and the most ever at once. */
const reading = { now: 0, max: 0 };

/** `pages` are served in order to each page read (the last one repeats; a function answers itself);
 *  `posts` answer by path. */
function stubApi(pages: unknown[], posts: Record<string, () => Response> = {}) {
  reading.now = 0;
  reading.max = 0;
  const calls: Call[] = [];
  let reads = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const u = new URL(raw, "http://localhost");
    const method = init?.method ?? "GET";
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const body = init?.body instanceof FormData ? init.body : typeof init?.body === "string" ? JSON.parse(init.body) : null;
    calls.push({ method, path: u.pathname, key: headers["Idempotency-Key"] ?? null, body });
    if (method === "GET" && u.pathname === `/v2/tenders/${TENDER}`) {
      const p = pages[Math.min(reads, pages.length - 1)];
      reads += 1;
      reading.now += 1;
      reading.max = Math.max(reading.max, reading.now);
      const answer = typeof p === "function" ? Promise.resolve((p as () => Response | Promise<Response>)()) : Promise.resolve(json(p));
      return answer.finally(() => {
        reading.now -= 1;
      });
    }
    const answer = posts[u.pathname];
    return Promise.resolve(answer ? answer() : json({}, 404));
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

function renderPage(role: "sales" | "admin" | "viewer" = "sales", pollMs?: number) {
  const navigate = vi.fn();
  const view = render(
    <AuthSessionContext.Provider value={{ session: sessionAs(role), signOut: async () => true }}>
      <TenderPage id={TENDER} navigate={navigate} now={NOW} pollMs={pollMs} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
  return { navigate, ...view };
}

const pageReads = (calls: Call[]) => calls.filter((c) => c.method === "GET" && c.path === `/v2/tenders/${TENDER}`).length;
const posted = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.path === path);

beforeEach(() => clearResourceCache());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("TenderPage — who sees it", () => {
  it("a viewer sees the restriction line and nothing is read", () => {
    const calls = stubApi([page()]);
    renderPage("viewer");
    expect(screen.getByText("Solo ventas y administración pueden ver esta licitación.")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("shows the header: code, title, institución, cierre and the portal link", async () => {
    stubApi([page()]);
    renderPage();
    expect(await screen.findByRole("heading", { name: "Compra de balanzas de ejemplo" })).toBeInTheDocument();
    expect(screen.getByText("1234567-12-LE26")).toBeInTheDocument();
    expect(screen.getByText("Hospital Ejemplo")).toBeInTheDocument();
    expect(screen.getByText(/^Cierre 21/)).toBeInTheDocument();
    const portal = screen.getByRole("link", { name: /Abrir en Mercado Público/ });
    expect(portal).toHaveAttribute("href", expect.stringContaining("idlicitacion=1234567-12-LE26"));
    expect(portal).toHaveAttribute("target", "_blank");
    for (const title of ["Anexos", "Resumen", "Checklist", "Formularios llenados", "Oferta económica", "Fichas técnicas", "Paquete"]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
  });
});

describe("TenderPage — estado", () => {
  it("posts set-tender-status with the version shown, and «Adjudicada» is disabled with its reason", async () => {
    const calls = stubApi([page()], { "/v2/commands/set-tender-status": () => json({ command: "set-tender-status" }) });
    renderPage();
    const group = await screen.findByRole("group", { name: "Estado de la licitación" });
    const adjudicada = within(group).getByRole("button", { name: /Adjudicada/ });
    expect(adjudicada).toBeDisabled();
    expect(screen.getByText("Se registra al publicarse la adjudicación (próxima fase).")).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Preparando" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(within(group).getByRole("button", { name: "Postulada" }));
    await waitFor(() => expect(posted(calls, "/v2/commands/set-tender-status")).toHaveLength(1));
    const [call] = posted(calls, "/v2/commands/set-tender-status");
    expect(call.body).toEqual({ opportunity_id: TENDER, status: "postulada", expected_version: 4 });
    expect(call.key).toBeTruthy();
    await waitFor(() => expect(pageReads(calls)).toBe(2));
  });

  it("«Perdida» asks for the motivo before posting", async () => {
    const calls = stubApi([page()], { "/v2/commands/set-tender-status": () => json({ command: "set-tender-status" }) });
    renderPage();
    const group = await screen.findByRole("group", { name: "Estado de la licitación" });
    fireEvent.click(within(group).getByRole("button", { name: "Perdida" }));
    fireEvent.click(screen.getByRole("button", { name: "Guardar como perdida" }));
    expect(await screen.findByText("Escribe el motivo.")).toBeInTheDocument();
    expect(posted(calls, "/v2/commands/set-tender-status")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Motivo"), { target: { value: "Precio fuera de rango" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar como perdida" }));
    await waitFor(() => expect(posted(calls, "/v2/commands/set-tender-status")).toHaveLength(1));
    expect(posted(calls, "/v2/commands/set-tender-status")[0].body).toEqual({
      opportunity_id: TENDER, status: "perdida", expected_version: 4, reason: "Precio fuera de rango",
    });
  });
});

describe("TenderPage — anexos", () => {
  it("«Subir anexos» posts multipart with every file and lists what was attached and skipped", async () => {
    const calls = stubApi([page()], {
      "/v2/commands/attach-tender-annexes": () => json({
        command: "attach-tender-annexes",
        attached: [{ document_id: DOC1, filename: "bases.pdf" }],
        skipped: [{ filename: "macro.xlsm", reason: "unsupported_type" }, { filename: "(y 3 archivos más)", reason: "not_listed" }],
      }),
    });
    renderPage();
    const input = (await screen.findByLabelText("Subir anexos")) as HTMLInputElement;
    expect(input).toHaveAttribute("multiple");
    expect(input).toHaveAttribute("accept", ".pdf,.docx,.xlsx,.doc,.xls,.jpg,.png,.zip");
    const a = new File(["%PDF-1"], "bases.pdf", { type: "application/pdf" });
    const b = new File(["x"], "macro.xlsm", { type: "application/octet-stream" });
    fireEvent.change(input, { target: { files: [a, b] } });
    await waitFor(() => expect(posted(calls, "/v2/commands/attach-tender-annexes")).toHaveLength(1));
    const [call] = posted(calls, "/v2/commands/attach-tender-annexes");
    const form = call.body as FormData;
    expect(form.get("opportunity_id")).toBe(TENDER);
    expect((form.getAll("files") as File[]).map((f) => f.name)).toEqual(["bases.pdf", "macro.xlsm"]);
    expect(call.key).toBeTruthy();
    const result = await screen.findByRole("region", { name: "Resultado de la subida" });
    expect(within(result).getByText("bases.pdf")).toBeInTheDocument();
    expect(within(result).getByText("macro.xlsm")).toBeInTheDocument();
    expect(within(result).getByText("Tipo de archivo no aceptado")).toBeInTheDocument();
    expect(within(result).getByText("y 3 archivos más")).toBeInTheDocument();
  });

  it("retries an upload that may have run with the same key, and a definitive refusal drops it", async () => {
    let answer: () => Response = () => new Response("bad gateway", { status: 502 });
    const calls = stubApi([page()], { "/v2/commands/attach-tender-annexes": () => answer() });
    renderPage();
    const input = (await screen.findByLabelText("Subir anexos")) as HTMLInputElement;
    const file = new File(["%PDF-1"], "bases.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });
    await screen.findByText("No se pudo completar. Intenta de nuevo.");
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(posted(calls, "/v2/commands/attach-tender-annexes")).toHaveLength(2));
    const [first, second] = posted(calls, "/v2/commands/attach-tender-annexes");
    expect(second.key).toBe(first.key);
    answer = () => refusal(422, "not_a_tender_case");
    fireEvent.change(input, { target: { files: [file] } });
    await screen.findByText("Los anexos se suben a una licitación.");
    answer = () => json({ command: "attach-tender-annexes", attached: [], skipped: [] });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(posted(calls, "/v2/commands/attach-tender-annexes")).toHaveLength(4));
    expect(posted(calls, "/v2/commands/attach-tender-annexes")[3].key).not.toBe(first.key);
  });
});

describe("TenderPage — preparing", () => {
  it("«Preparar oferta» posts the read phase", async () => {
    const calls = stubApi([page()], { "/v2/commands/prepare-tender-bid": () => json({ command: "prepare-tender-bid", preparation_id: READ }) });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Preparar oferta" }));
    await waitFor(() => expect(posted(calls, "/v2/commands/prepare-tender-bid")).toHaveLength(1));
    expect(posted(calls, "/v2/commands/prepare-tender-bid")[0].body).toEqual({ opportunity_id: TENDER, phase: "read" });
  });

  it("a queued read shows the reading line and polls until it is ready", async () => {
    expect(POLL_MS).toBe(5000);
    const queued = page({ preparations: { read: prep("read", { status: "queued", result: {} }), fill: null } });
    const running = page({ preparations: { read: prep("read", { status: "running", result: {} }), fill: null } });
    const ready = page({ preparations: { read: prep("read", { result: READ_RESULT }), fill: null } });
    const calls = stubApi([queued, running, ready]);
    renderPage("sales", 20);
    const line = await screen.findByText("Leyendo los anexos… (unos minutos)");
    expect(line.closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
    await screen.findByText("Balanzas analíticas de ejemplo");
    expect(screen.queryByText("Leyendo los anexos… (unos minutos)")).not.toBeInTheDocument();
    const settled = pageReads(calls);
    expect(settled).toBe(3);
    await new Promise((r) => setTimeout(r, 80));
    expect(pageReads(calls)).toBe(settled);
  });

  it("stops polling when the page is closed", async () => {
    const queued = page({ preparations: { read: prep("read", { status: "queued" }), fill: null } });
    const calls = stubApi([queued]);
    const { unmount } = renderPage("sales", 20);
    await screen.findByText("Leyendo los anexos… (unos minutos)");
    unmount();
    const at = pageReads(calls);
    await new Promise((r) => setTimeout(r, 80));
    expect(pageReads(calls)).toBe(at);
  });

  it("says the budget refusal in Spanish", async () => {
    stubApi([page()], { "/v2/commands/prepare-tender-bid": () => refusal(409, "model_budget_reached") });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Preparar oferta" }));
    expect(
      await screen.findByText("Se alcanzó el tope mensual de US$ 60 para licitaciones; vuelve a intentarlo el 1 del próximo mes."),
    ).toBeInTheDocument();
    expect(screen.queryByText("texto del servidor que no se muestra")).not.toBeInTheDocument();
  });

  it("says a failure code in Spanish", async () => {
    stubApi([page({ preparations: { read: prep("read", { status: "failed", failure_code: "model_refused" }), fill: null } })]);
    renderPage();
    expect(await screen.findByText("El modelo no pudo leer estos anexos.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Regenerar/ })).toBeInTheDocument();
  });

  it("an unknown failure code is one generic line, never the raw code", async () => {
    stubApi([page({ preparations: { read: prep("read", { status: "failed", failure_code: "something_new" }), fill: null } })]);
    renderPage();
    expect(await screen.findByText("La preparación no se pudo completar. Vuelve a intentarlo.")).toBeInTheDocument();
    expect(screen.queryByText(/something_new/)).not.toBeInTheDocument();
  });
});

describe("TenderPage — the reading", () => {
  it("Resumen cites «archivo · p.3» and says «sin fuente» for sin_cita", async () => {
    stubApi([page({ preparations: { read: prep("read", { result: READ_RESULT }), fill: null } })]);
    renderPage();
    const resumen = await screen.findByRole("region", { name: "Resumen" });
    expect(within(resumen).getByText("Objeto")).toBeInTheDocument();
    expect(within(resumen).getByText("bases_ejemplo.pdf · p.3")).toBeInTheDocument();
    expect(within(resumen).getByText("No se indica")).toBeInTheDocument();
    expect(within(resumen).getByText("sin fuente")).toBeInTheDocument();
  });

  it("Checklist shows the chips «Listo», «Falta» and «Firmar» with their source", async () => {
    stubApi([page({
      preparations: { read: prep("read", { result: READ_RESULT }), fill: prep("fill", { result: FILL_RESULT, outputs: FILL_OUTPUTS }) },
    })]);
    renderPage();
    const checklist = await screen.findByRole("region", { name: "Checklist" });
    const rows = within(checklist).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("Listo")).toBeInTheDocument();
    expect(within(rows[0]).getByText("bases_ejemplo.pdf · p.5")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Falta")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Falta el documento")).toBeInTheDocument();
    expect(within(rows[1]).getByText("sin fuente")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Firmar")).toBeInTheDocument();
  });
});

describe("TenderPage — offer, forms and package", () => {
  it("«Crear oferta económica» posts the ready read, then «Abrir en Cotizador» opens the quote", async () => {
    const withRead = page({ preparations: { read: prep("read", { result: READ_RESULT }), fill: null } });
    const withOffer = page({
      preparations: { read: prep("read", { result: READ_RESULT }), fill: null },
      offer_quote: { quote_id: QUOTE, revision_id: REV, number: "01300-26", status: "draft", neto: 100000, iva: 19000, total: 119000 },
    });
    const calls = stubApi([withRead, withOffer], {
      "/v2/commands/create-tender-offer-quote": () => json({ command: "create-tender-offer-quote", quote_id: QUOTE, revision_id: REV, quote_number: "01300-26", matched: 1, free: 0, existing: false }),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Crear oferta económica" }));
    await waitFor(() => expect(posted(calls, "/v2/commands/create-tender-offer-quote")).toHaveLength(1));
    expect(posted(calls, "/v2/commands/create-tender-offer-quote")[0].body).toEqual({ opportunity_id: TENDER, preparation_id: READ });
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const cotizador = await screen.findByRole("link", { name: "Abrir en Cotizador" });
    expect(cotizador).toHaveAttribute("href", `#/crm/cotizador/${QUOTE}`);
    fireEvent.click(cotizador);
    scroll.mockRestore();
    expect(window.location.hash).toBe(`#/crm/cotizador/${QUOTE}`);
  });

  it("«Llenar formularios» is disabled with the reason when the bidder's data is missing", async () => {
    stubApi([page({
      bidder_ready: false,
      preparations: { read: prep("read", { result: READ_RESULT }), fill: null },
      offer_quote: { quote_id: QUOTE, revision_id: REV, number: "01300-26", status: "approved", neto: 100000, iva: 19000, total: 119000 },
    })]);
    renderPage();
    const button = await screen.findByRole("button", { name: "Llenar formularios" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Completa los Datos para licitaciones en Preferencias.");
  });

  it("«Llenar formularios» posts the fill phase when everything is ready", async () => {
    const calls = stubApi([page({
      preparations: { read: prep("read", { result: READ_RESULT }), fill: null },
      offer_quote: { quote_id: QUOTE, revision_id: REV, number: "01300-26", status: "approved", neto: 100000, iva: 19000, total: 119000 },
    })], { "/v2/commands/prepare-tender-bid": () => json({ command: "prepare-tender-bid", preparation_id: FILL }) });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Llenar formularios" }));
    await waitFor(() => expect(posted(calls, "/v2/commands/prepare-tender-bid")).toHaveLength(1));
    expect(posted(calls, "/v2/commands/prepare-tender-bid")[0].body).toEqual({ opportunity_id: TENDER, phase: "fill" });
  });

  it("lists the filled forms with «N por completar» and the spec sheets", async () => {
    stubApi([page({
      preparations: { read: prep("read", { result: READ_RESULT }), fill: prep("fill", { result: FILL_RESULT, outputs: FILL_OUTPUTS }) },
    })]);
    renderPage();
    const forms = await screen.findByRole("region", { name: "Formularios llenados" });
    expect(within(forms).getByText("01_Anexo_1.docx")).toBeInTheDocument();
    expect(within(forms).getByText("2 por completar")).toBeInTheDocument();
    expect(within(forms).getByText("Oferta_economica.docx")).toBeInTheDocument();
    expect(within(forms).queryByText("Oferta_1234567-12-LE26.zip")).not.toBeInTheDocument();
    const fichas = screen.getByRole("region", { name: "Fichas técnicas" });
    expect(within(fichas).getByText("Balanza Ejemplo BX-1")).toBeInTheDocument();
    expect(within(fichas).getByText("Adjunta")).toBeInTheDocument();
    expect(within(fichas).getByText("Sin ficha en el catálogo")).toBeInTheDocument();
  });

  it("«Descargar paquete» fetches the paquete's link and shows it as a link", async () => {
    const calls = stubApi([page({
      preparations: { read: prep("read", { result: READ_RESULT }), fill: prep("fill", { result: FILL_RESULT, outputs: FILL_OUTPUTS }) },
    })], {
      [`/v2/tenders/${TENDER}/preparations/${FILL}/files/paquete/url`]: () =>
        json({ url: "https://storage.example.cl/signed/paquete.zip?token=x", expires_in: 600, filename: "Oferta_1234567-12-LE26.zip" }),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Descargar paquete" }));
    const link = await screen.findByRole("link", { name: /Descargar ahora/ });
    expect(link).toHaveAttribute("href", "https://storage.example.cl/signed/paquete.zip?token=x");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(calls.some((c) => c.method === "GET" && c.path === `/v2/tenders/${TENDER}/preparations/${FILL}/files/paquete/url`)).toBe(true);
  });

  it("refuses a signed link that is not https", async () => {
    stubApi([page({
      preparations: { read: prep("read", { result: READ_RESULT }), fill: prep("fill", { result: FILL_RESULT, outputs: FILL_OUTPUTS }) },
    })], {
      [`/v2/tenders/${TENDER}/preparations/${FILL}/files/paquete/url`]: () => json({ url: "javascript:alert(1)" }),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Descargar paquete" }));
    await screen.findByText("No se pudo completar. Intenta de nuevo.");
    expect(screen.queryByRole("link", { name: /Descargar ahora/ })).not.toBeInTheDocument();
  });
});

/* ── fix round 1 ─────────────────────────────────────────────────────────── */

const queuedRead = () => page({ preparations: { read: prep("read", { status: "queued" }), fill: null } });
/** Moves the fake clock inside `act`, so React renders what the timers caused. */
const tick = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
const flush = async () => {
  for (let i = 0; i < 5; i += 1) await tick(0);
};

describe("TenderPage — polling when reads fail", () => {
  it("backs off 5 → 10 → 20 → 60 s and says it is retrying after two failures", async () => {
    vi.useFakeTimers();
    const down = () => new Response("bad gateway", { status: 502 });
    const calls = stubApi([queuedRead(), down]);
    renderPage();
    await flush();
    expect(pageReads(calls)).toBe(1);
    await tick(5000);
    expect(pageReads(calls)).toBe(2);
    expect(screen.queryByText("No se pudo actualizar; reintentando…")).not.toBeInTheDocument();
    await tick(9000);
    expect(pageReads(calls)).toBe(2);
    await tick(1000);
    expect(pageReads(calls)).toBe(3);
    expect(screen.getByText("No se pudo actualizar; reintentando…")).toBeInTheDocument();
    expect(screen.getByText("Leyendo los anexos… (unos minutos)")).toBeInTheDocument();
    await tick(19000);
    expect(pageReads(calls)).toBe(3);
    await tick(1000);
    expect(pageReads(calls)).toBe(4);
    await tick(59000);
    expect(pageReads(calls)).toBe(4);
    await tick(1000);
    expect(pageReads(calls)).toBe(5);
  });

  it("stops polling on an API refusal and asks for a reload", async () => {
    vi.useFakeTimers();
    const calls = stubApi([queuedRead(), () => refusal(404, "tender_not_found")]);
    renderPage();
    await flush();
    await tick(5000);
    expect(pageReads(calls)).toBe(2);
    expect(screen.getByText("No se pudo actualizar la licitación. Recarga la página.")).toBeInTheDocument();
    await tick(120_000);
    expect(pageReads(calls)).toBe(2);
  });

  it("a reload asked for during a poll waits for it: reads never overlap", async () => {
    vi.useFakeTimers();
    let release: (r: Response) => void = () => {};
    const slow = () => new Promise<Response>((resolve) => { release = resolve; });
    const calls = stubApi([queuedRead(), slow, queuedRead()], {
      "/v2/commands/set-tender-status": () => json({ command: "set-tender-status" }),
    });
    renderPage();
    await flush();
    await tick(5000);
    expect(pageReads(calls)).toBe(2);
    fireEvent.click(within(screen.getByRole("group", { name: "Estado de la licitación" })).getByRole("button", { name: "Postulada" }));
    await flush();
    expect(posted(calls, "/v2/commands/set-tender-status")).toHaveLength(1);
    expect(pageReads(calls)).toBe(2);
    release(json(queuedRead()));
    await flush();
    expect(pageReads(calls)).toBe(3);
    expect(reading.max).toBe(1);
  });
});

describe("TenderPage — moving between tenders", () => {
  it("tender A's late read never shows under tender B, and A is not read again", async () => {
    const B = "99999999-9999-4999-8999-999999999999";
    let releaseA: (r: Response) => void = () => {};
    const seen: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const u = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      seen.push(u.pathname);
      if (u.pathname === `/v2/tenders/${TENDER}`) return new Promise<Response>((resolve) => { releaseA = resolve; });
      if (u.pathname === `/v2/tenders/${B}`) {
        const b = page();
        return Promise.resolve(json({ ...b, tender: { ...b.tender, id: B, title: "Reactivos de ejemplo B", code: "7654321-1-L126" } }));
      }
      return Promise.resolve(json({}, 404));
    }));
    const navigate = vi.fn();
    const tree = (id: string) => (
      <AuthSessionContext.Provider value={{ session: sessionAs("sales"), signOut: async () => true }}>
        <LicitacionesPage id={id} navigate={navigate} now={NOW} />
      </AuthSessionContext.Provider>
    );
    const { rerender } = render(tree(TENDER));
    await waitFor(() => expect(seen).toContain(`/v2/tenders/${TENDER}`));
    rerender(tree(B));
    expect(await screen.findByRole("heading", { name: "Reactivos de ejemplo B" })).toBeInTheDocument();
    releaseA(json(queuedRead()));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByText("Compra de balanzas de ejemplo")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Reactivos de ejemplo B" })).toBeInTheDocument();
    expect(seen.filter((p) => p === `/v2/tenders/${TENDER}`)).toHaveLength(1);
  });
});

describe("TenderPage — announcing and small steps", () => {
  it("announces «Lectura lista.» when the read finishes", async () => {
    const ready = page({ preparations: { read: prep("read", { result: READ_RESULT }), fill: null } });
    stubApi([queuedRead(), ready]);
    renderPage("sales", 20);
    await screen.findByText("Leyendo los anexos… (unos minutos)");
    const live = await screen.findByText("Lectura lista.");
    expect(live.closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
  });

  it("announces the failure line when a fill fails", async () => {
    const base = { read: prep("read", { result: READ_RESULT }) };
    const running = page({ preparations: { ...base, fill: prep("fill", { status: "running" }) } });
    const failed = page({ preparations: { ...base, fill: prep("fill", { status: "failed", failure_code: "package_too_large" }) } });
    stubApi([running, failed]);
    renderPage("sales", 20);
    await screen.findByText("Llenando los formularios… (unos minutos)");
    const status = screen.getByText("Llenando los formularios… (unos minutos)").closest("[aria-live]") as HTMLElement;
    await waitFor(() => expect(within(status).getByText("El paquete es demasiado grande (más de 45 MB).")).toBeInTheDocument());
  });

  it("«Llenar formularios» waits for a running read", async () => {
    stubApi([page({
      preparations: { read: prep("read", { status: "running" }), fill: null },
      offer_quote: { quote_id: QUOTE, revision_id: REV, number: "01300-26", status: "approved", neto: 1, iva: 0, total: 1 },
    })]);
    renderPage("sales", 60_000);
    const button = await screen.findByRole("button", { name: "Llenar formularios" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Espera a que termine la lectura.");
  });

  it("drops a signed link after nine minutes so it is asked for again", async () => {
    vi.useFakeTimers();
    stubApi([page({
      preparations: { read: prep("read", { result: READ_RESULT }), fill: prep("fill", { result: FILL_RESULT, outputs: FILL_OUTPUTS }) },
    })], {
      [`/v2/tenders/${TENDER}/preparations/${FILL}/files/paquete/url`]: () => json({ url: "https://storage.example.cl/signed/p.zip" }),
    });
    renderPage();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Descargar paquete" }));
    await flush();
    expect(screen.getByRole("link", { name: /Descargar ahora/ })).toBeInTheDocument();
    await tick(8 * 60_000);
    expect(screen.getByRole("link", { name: /Descargar ahora/ })).toBeInTheDocument();
    await tick(60_000);
    expect(screen.queryByRole("link", { name: /Descargar ahora/ })).not.toBeInTheDocument();
  });
});

describe("TenderPage — idempotency keys", () => {
  it("«Preparar oferta» keeps its key for a retry and takes a fresh one after a definitive refusal", async () => {
    let answer: () => Response = () => new Response("bad gateway", { status: 502 });
    const calls = stubApi([page()], { "/v2/commands/prepare-tender-bid": () => answer() });
    renderPage();
    const button = await screen.findByRole("button", { name: "Preparar oferta" });
    fireEvent.click(button);
    await screen.findByText("No se pudo completar. Intenta de nuevo.");
    fireEvent.click(button);
    await waitFor(() => expect(posted(calls, "/v2/commands/prepare-tender-bid")).toHaveLength(2));
    answer = () => refusal(422, "no_annexes");
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);
    await screen.findByText("Sube los anexos antes de preparar la oferta.");
    fireEvent.click(button);
    await waitFor(() => expect(posted(calls, "/v2/commands/prepare-tender-bid")).toHaveLength(4));
    const keys = posted(calls, "/v2/commands/prepare-tender-bid").map((c) => c.key);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).toBe(keys[0]);
    expect(keys[3]).not.toBe(keys[0]);
  });

  it("the estado keeps its key for a retry and takes a fresh one after a definitive refusal", async () => {
    let answer: () => Response = () => new Response("bad gateway", { status: 502 });
    const calls = stubApi([page()], { "/v2/commands/set-tender-status": () => answer() });
    renderPage();
    const group = await screen.findByRole("group", { name: "Estado de la licitación" });
    const postulada = within(group).getByRole("button", { name: "Postulada" });
    fireEvent.click(postulada);
    await screen.findByText("No se pudo completar. Intenta de nuevo.");
    answer = () => refusal(409, "tender_already_at_status");
    fireEvent.click(postulada);
    await screen.findByText("La licitación ya estaba en ese estado.");
    fireEvent.click(postulada);
    await waitFor(() => expect(posted(calls, "/v2/commands/set-tender-status")).toHaveLength(3));
    const keys = posted(calls, "/v2/commands/set-tender-status").map((c) => c.key);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("«Crear oferta económica» keeps its key for a retry and takes a fresh one after a definitive refusal", async () => {
    let answer: () => Response = () => new Response("bad gateway", { status: 502 });
    const calls = stubApi([page({ preparations: { read: prep("read", { result: READ_RESULT }), fill: null } })], {
      "/v2/commands/create-tender-offer-quote": () => answer(),
    });
    renderPage();
    const button = await screen.findByRole("button", { name: "Crear oferta económica" });
    fireEvent.click(button);
    await screen.findByText("No se pudo completar. Intenta de nuevo.");
    answer = () => refusal(422, "no_items");
    fireEvent.click(button);
    await screen.findByText("La lectura no encontró ítems para cotizar.");
    fireEvent.click(button);
    await waitFor(() => expect(posted(calls, "/v2/commands/create-tender-offer-quote")).toHaveLength(3));
    const keys = posted(calls, "/v2/commands/create-tender-offer-quote").map((c) => c.key);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });
});
