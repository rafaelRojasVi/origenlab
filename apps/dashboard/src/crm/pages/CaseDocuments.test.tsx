import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { OperatorApiError } from "../../api/operatorClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { documentRefusalText, emailDocumentOptions, looksLikePurchaseOrder, mayAttachCaseDocuments } from "../caseDocuments";
import type { CaseDocument, OpportunityCardData, PipelineResponse, RevisionCard } from "../crmTypes";
import { clearResourceCache } from "../useResource";
import { Toaster } from "../ui";
import { PipelinePage } from "./PipelinePage";

// Every value below is invented; the repository is public.
const CASE = "11111111-1111-4111-8111-111111111111";
const QUOTE = "44444444-4444-4444-8444-444444444444";
const MESSAGE = "55555555-5555-4555-8555-555555555555";
const DOC = "66666666-6666-4666-8666-666666666666";
const SHA_QUOTE = "b".repeat(64);
const SHA_OC = "c".repeat(64);
const SHA_OTHER = "d".repeat(64);

const MAIL_DOCUMENTS = {
  opportunity_id: CASE,
  messages: [
    {
      source_record_id: MESSAGE,
      subject: "RE: Cotización equipo ficticio",
      sent_at: "2026-10-01T15:00:00+00:00",
      documents: [
        { sha256: SHA_QUOTE, filename: "CN00001.pdf", cn_tokens: ["CN00001"],
          recorded: { quote_number: "00001-26", revision_no: 2, on_this_case: true } },
        { sha256: SHA_OTHER, filename: "Ficha tecnica.pdf", cn_tokens: [], recorded: null },
        { sha256: SHA_OC, filename: "Orden de compra 4321.pdf", cn_tokens: [], recorded: null },
      ],
    },
  ],
};

function rev(no: number, over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: `rev-${no}`, revision_no: no, status: "sent", origin: "historical_import",
    sent_at: `2026-0${no}-10T12:00:00Z`, superseded_by_revision_no: null, is_active: true,
    document: null, gmail: null, drive: null, quote_number: "00001-26", ...over,
  };
}

function card(over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  const revisions = [rev(2)];
  return {
    opportunity_id: CASE, title: "Cotización 00001-26 — Universidad Ficticia", stage: "negotiating", version: 5,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-02T00:00:00Z", closed_at: null, close_reason: null,
    organization: { organization_id: "33333333-3333-4333-8333-333333333333", name: "Universidad Ficticia",
                    confirmation: "confirmed", version: 3 },
    other_organizations: [], contact: null,
    quotes: [{ quote_id: QUOTE, quote_number: "00001-26", number_origin: "printed_historical", revisions }],
    quote_numbers: ["00001-26"], revision_count: 1, latest_revision: revisions[0], drive_folder: null,
    attention: [], status: "ok",
    next_action: { text: "Hacer seguimiento de 00001-26", source: "suggested", due_at: null },
    ...over,
  } as OpportunityCardData;
}

const OC: CaseDocument = {
  document_id: DOC, quote_revision_id: "rev-2", kind: "purchase_order", document_number: "4321",
  original_filename: "Orden de compra 4321.pdf", content_type: "application/pdf", byte_size: 2048,
  source: "email", attached_at: "2026-10-12T15:00:00Z", attached_by: "Vendedora Ficticia",
};

function session(role = "sales", documents = true): AuthSessionState {
  return {
    kind: "signed_in", method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Vendedora Ficticia", role },
    caseCommandsEnabled: true, crmAuthoringEnabled: true, caseDocumentsEnabled: documents,
  };
}

interface Call { path: string; method: string; body: unknown; key: string | null }

function stubApi(pipelines: PipelineResponse[], onPost: (c: Call) => { status?: number; body: unknown } | undefined = () => undefined) {
  const calls: Call[] = [];
  let reads = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    const raw = init?.body;
    const body = raw instanceof FormData ? raw : raw ? JSON.parse(String(raw)) : null;
    const call: Call = { path, method: init?.method ?? "GET", body, key: new Headers(init?.headers).get("Idempotency-Key") };
    calls.push(call);
    const json = (b: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
    if (call.method === "GET" && path === "/v2/workspace/pipeline") return json(pipelines[Math.min(reads++, pipelines.length - 1)]);
    if (call.method === "GET" && path.endsWith("/mail-documents")) return json(MAIL_DOCUMENTS);
    if (call.method === "GET" && path.endsWith("/purchase-order-candidates")) return json({ opportunity_id: CASE, candidates: [] });
    if (call.method === "GET" && path.endsWith("/notes")) return json({ opportunity_id: CASE, notes: [] });
    if (call.method === "GET" && path.endsWith("/threads")) return json({ opportunity_id: CASE, threads: [] });
    if (call.method === "GET" && path === `/v2/workspace/opportunities/${CASE}/documents/${DOC}/url`) {
      return json({ url: "https://storage.example/signed/oc.pdf?token=x", expires_in: 600 });
    }
    if (call.method === "POST") {
      const answer = onPost(call);
      if (answer) return json(answer.body, answer.status ?? 200);
    }
    return json({ detail: "not found" }, 404);
  }));
  return calls;
}

const page = (items: OpportunityCardData[]): PipelineResponse => ({ items, total: items.length, drive_configured: true });

async function openDrawer(s: AuthSessionState) {
  render(
    <AuthSessionContext.Provider value={{ session: s, signOut: async () => true }}>
      <PipelinePage initialOpportunityId={CASE} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
  return screen.findByRole("dialog");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearResourceCache();
});

describe("the OC options and refusals", () => {
  it("lists a case email's PDFs that are not quotations, likely purchase orders first", () => {
    const options = emailDocumentOptions(MAIL_DOCUMENTS, null);
    expect(options.map((o) => o.filename)).toEqual(["Orden de compra 4321.pdf", "Ficha tecnica.pdf"]);
    expect(options[0].looks_like_po).toBe(true);
    expect(looksLikePurchaseOrder("OC 5500012345.pdf")).toBe(true);
    expect(looksLikePurchaseOrder("ORD-CO-17.pdf")).toBe(true);
    expect(looksLikePurchaseOrder("Detalle_Transferencia.pdf")).toBe(false);
  });

  it("is offered only to sales and admin, with case commands and storage on", () => {
    expect(mayAttachCaseDocuments(session("sales"))).toBe(true);
    expect(mayAttachCaseDocuments(session("viewer"))).toBe(false);
    expect(mayAttachCaseDocuments(session("admin", false))).toBe(false);
  });

  it("explains a refusal by its detailed code", () => {
    const err = new OperatorApiError(JSON.stringify({ error: { code: "conflict", details: { code: "message_not_stored" } } }), 409);
    expect(documentRefusalText(err)).toMatch(/descarga el PDF desde Gmail y súbelo/);
  });
});

describe("«Marcar ganada» with its purchase order", () => {
  it("attaches the OC from a case email first, then records the win against the same version", async () => {
    const calls = stubApi([page([card()]), page([card({ stage: "won", version: 6, documents: [OC] })])], (c) => {
      if (c.path === "/v2/commands/attach-case-document-from-email") {
        return { body: { document: { document_id: DOC, document_number: "4321", original_filename: "Orden de compra 4321.pdf" }, replayed: false } };
      }
      if (c.path === "/v2/commands/record-case-won") {
        return { body: { command: "record_case_won", opportunity_id: CASE, opportunity_version: 6, stage: "won",
                         idempotency_key: "k", command_receipt_id: "cccccccc-0001", replayed: false } };
      }
      return undefined;
    });
    const dialog = await openDrawer(session());
    fireEvent.click(within(dialog).getByRole("button", { name: "Marcar ganada" }));
    const form = within(dialog).getByRole("form", { name: "Marcar ganada" });
    fireEvent.click(within(form).getByRole("radio", { name: "De un correo del caso" }));
    const select = await within(form).findByLabelText("PDF del correo");
    fireEvent.change(select, { target: { value: `${MESSAGE}:${SHA_OC}` } });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "Llegó la OC" } });
    fireEvent.click(within(form).getByRole("button", { name: "Marcar ganada" }));

    expect(await within(dialog).findByText(/OC adjuntada: OC Nº 4321/)).toBeInTheDocument();
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts.map((c) => c.path)).toEqual(["/v2/commands/attach-case-document-from-email", "/v2/commands/record-case-won"]);
    expect(posts[0].body).toEqual({
      opportunity_id: CASE, source_record_id: MESSAGE, document_sha256: SHA_OC, quote_revision_id: "rev-2",
      document_number: null, note: "Llegó la OC",
    });
    expect((posts[1].body as { opportunity_version: number }).opportunity_version).toBe(5);
  });

  it("does not record the win when the OC could not be attached", async () => {
    const calls = stubApi([page([card()])], (c) =>
      c.path === "/v2/commands/attach-case-document-from-email"
        ? { status: 409, body: { error: { code: "conflict", details: { code: "message_not_stored" } } } }
        : undefined,
    );
    const dialog = await openDrawer(session());
    fireEvent.click(within(dialog).getByRole("button", { name: "Marcar ganada" }));
    const form = within(dialog).getByRole("form", { name: "Marcar ganada" });
    fireEvent.click(within(form).getByRole("radio", { name: "De un correo del caso" }));
    fireEvent.change(await within(form).findByLabelText("PDF del correo"), { target: { value: `${MESSAGE}:${SHA_OC}` } });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "Llegó la OC" } });
    fireEvent.click(within(form).getByRole("button", { name: "Marcar ganada" }));
    expect(await within(dialog).findByText(/La OC no se adjuntó y el caso no se marcó ganado/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/v2/commands/attach-case-document-from-email"]);
  });

  it("offers no OC field when document storage is off", async () => {
    stubApi([page([card()])]);
    const dialog = await openDrawer(session("sales", false));
    fireEvent.click(within(dialog).getByRole("button", { name: "Marcar ganada" }));
    expect(within(dialog).queryByTestId("oc-picker")).not.toBeInTheDocument();
  });
});

describe("the OC on a won case", () => {
  it("shows the OC with its quotation and opens it through a short-lived link", async () => {
    stubApi([page([card({ stage: "won", documents: [OC] })])]);
    const tab = { opener: {}, location: { href: "" }, close: vi.fn() };
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    const dialog = await openDrawer(session("viewer"));
    const block = within(dialog).getByTestId("case-documents");
    expect(block).toHaveTextContent("OC Nº 4321");
    expect(block).toHaveTextContent("cierra 00001-26 r2");
    expect(block).toHaveTextContent("desde un correo");
    expect(within(block).queryByRole("button", { name: "Quitar" })).not.toBeInTheDocument(); // a viewer only opens
    expect(within(dialog).getAllByTitle("Orden de compra adjunta").length).toBeGreaterThan(0);
    fireEvent.click(within(block).getByRole("button", { name: "Abrir" }));
    await waitFor(() => expect(tab.location.href).toBe("https://storage.example/signed/oc.pdf?token=x"));
    expect(tab.opener).toBeNull();
  });

  it("attaches an OC from the computer on a won case and reloads", async () => {
    const calls = stubApi([page([card({ stage: "won" })]), page([card({ stage: "won", documents: [{ ...OC, source: "upload" }] })])],
      (c) => (c.path === "/v2/commands/attach-case-document"
        ? { body: { document: { document_id: DOC, document_number: "4321", original_filename: "OC.pdf" }, replayed: false } }
        : undefined));
    const dialog = await openDrawer(session("admin"));
    const block = within(dialog).getByTestId("case-documents");
    expect(block).toHaveTextContent("Caso ganado sin OC adjunta.");
    fireEvent.click(within(block).getByRole("button", { name: "Adjuntar OC" }));
    const form = within(block).getByRole("form", { name: "Adjuntar OC" });
    fireEvent.click(within(form).getByRole("radio", { name: "Subir archivo" }));
    const file = new File(["%PDF-1.7 ficticio"], "OC 4321.pdf", { type: "application/pdf" });
    fireEvent.change(within(form).getByLabelText(/^Archivo/), { target: { files: [file] } });
    fireEvent.click(within(form).getByRole("button", { name: "Adjuntar OC" }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2));
    const [post] = calls.filter((c) => c.method === "POST");
    expect(post.path).toBe("/v2/commands/attach-case-document");
    expect(post.key).toBeTruthy();
    const form_ = post.body as FormData;
    expect(form_.get("opportunity_id")).toBe(CASE);
    expect(form_.get("quote_revision_id")).toBe("rev-2");
    expect((form_.get("file") as File).name).toBe("OC 4321.pdf");
    expect(await within(dialog).findByTestId("case-document")).toHaveTextContent("OC Nº 4321");
  });

  it("refuses a file over 20 MB before sending it", async () => {
    const calls = stubApi([page([card({ stage: "won" })])]);
    const dialog = await openDrawer(session("admin"));
    const block = within(dialog).getByTestId("case-documents");
    fireEvent.click(within(block).getByRole("button", { name: "Adjuntar OC" }));
    const form = within(block).getByRole("form", { name: "Adjuntar OC" });
    fireEvent.click(within(form).getByRole("radio", { name: "Subir archivo" }));
    const big = new File(["x"], "OC grande.pdf", { type: "application/pdf" });
    Object.defineProperty(big, "size", { value: 21 * 1024 * 1024 });
    fireEvent.change(within(form).getByLabelText(/^Archivo/), { target: { files: [big] } });
    expect(within(form).getByRole("alert")).toHaveTextContent("más de 20 MB");
    expect(within(form).getByRole("button", { name: "Adjuntar OC" })).toBeDisabled();
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
  });
});
