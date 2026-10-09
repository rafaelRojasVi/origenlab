import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { mayRunCaseCommands, nextStages, undeterminedQuotes, winnableRevisions } from "../caseCommands";
import type { OpportunityCardData, PipelineResponse, RevisionCard } from "../crmTypes";
import { clearResourceCache } from "../useResource";
import { Toaster } from "../ui";
import { DecideCases, proposeDecision } from "./DecideCases";
import { PipelinePage } from "./PipelinePage";
import { moveRefusal } from "./CaseMove";

// Every value below is invented; the repository is public.
const CASE = "11111111-1111-4111-8111-111111111111";
const ORG = "33333333-3333-4333-8333-333333333333";
const QUOTE = "44444444-4444-4444-8444-444444444444";
const MESSAGE = "55555555-5555-4555-8555-555555555555";
const SHA_NEW = "a".repeat(64);
const SHA_RECORDED = "b".repeat(64);
const TASK = "66666666-6666-4666-8666-666666666666";

const MAIL_DOCUMENTS = {
  opportunity_id: CASE,
  messages: [
    {
      source_record_id: MESSAGE,
      subject: "Cotización equipo ficticio",
      sent_at: "2026-09-25T15:00:00+00:00",
      documents: [
        { sha256: SHA_NEW, filename: "CN01239.pdf", cn_tokens: ["CN01239"], recorded: null },
        {
          sha256: SHA_RECORDED,
          filename: "CN00001.pdf",
          cn_tokens: ["CN00001"],
          recorded: { quote_number: "00001-26", revision_no: 2, on_this_case: true },
        },
      ],
    },
  ],
};

function rev(no: number, over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: `rev-${no}`,
    revision_no: no,
    status: "sent",
    origin: "historical_import",
    sent_at: `2026-0${no}-10T12:00:00Z`,
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: null,
    drive: null,
    quote_number: "00001-26",
    ...over,
  };
}

function card(over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  const revisions = [rev(1, { superseded_by_revision_no: 2, is_active: false }), rev(2)];
  return {
    opportunity_id: CASE,
    title: "Cotización 00001-26 — Universidad Ficticia",
    stage: "quoting",
    version: 5,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    closed_at: null,
    close_reason: null,
    organization: { organization_id: ORG, name: "Universidad Ficticia", confirmation: "confirmed", version: 3 },
    other_organizations: [],
    contact: null,
    quotes: [{ quote_id: QUOTE, quote_number: "00001-26", number_origin: "printed_historical", revisions }],
    quote_numbers: ["00001-26"],
    revision_count: 2,
    latest_revision: revisions[1],
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "Hacer seguimiento de 00001-26", source: "suggested", due_at: null },
    ...over,
  };
}

function session(role: string, flags: { cases?: boolean; authoring?: boolean } = {}): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Vendedora Ficticia", role },
    caseCommandsEnabled: flags.cases ?? true,
    crmAuthoringEnabled: flags.authoring ?? true,
  };
}

function withSession(s: AuthSessionState, node: ReactNode) {
  return <AuthSessionContext.Provider value={{ session: s, signOut: async () => true }}>{node}</AuthSessionContext.Provider>;
}

interface Call {
  path: string;
  method: string;
  body: Record<string, unknown> | null;
  key: string | null;
}

type Handler = (call: Call, n: number) => { status?: number; body: unknown } | undefined;

/** A fetch stub: `pipelines` are served in order (the last one repeats); POSTs go to `onPost`. */
function stubApi({
  pipelines,
  notes = { opportunity_id: CASE, notes: [] },
  mailDocuments = MAIL_DOCUMENTS,
  quoteCandidates = { opportunity_id: CASE, candidates: [] },
  purchaseOrderCandidates = { opportunity_id: CASE, candidates: [] },
  organizationConfirmation = "confirmed",
  onPost = () => undefined,
}: {
  pipelines: PipelineResponse[];
  notes?: unknown;
  mailDocuments?: unknown;
  quoteCandidates?: unknown;
  purchaseOrderCandidates?: unknown;
  organizationConfirmation?: "confirmed" | "machine_proposed";
  onPost?: Handler;
}) {
  const calls: Call[] = [];
  let pipelineReads = 0;
  let posts = 0;
  let orgConfirmed = organizationConfirmation === "confirmed";
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      const call: Call = {
        path,
        method: init?.method ?? "GET",
        body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null,
        key: new Headers(init?.headers).get("Idempotency-Key"),
      };
      calls.push(call);
      const json = (body: unknown, status = 200) =>
        Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
      if (call.method === "GET" && path === "/v2/workspace/pipeline") {
        const i = Math.min(pipelineReads++, pipelines.length - 1);
        return json(pipelines[i]);
      }
      if (call.method === "GET" && path === `/v2/workspace/opportunities/${CASE}/notes`) return json(notes);
      if (call.method === "GET" && path === `/v2/workspace/opportunities/${CASE}/mail-documents`) return json(mailDocuments);
      if (call.method === "GET" && path === `/v2/workspace/opportunities/${CASE}/quote-candidates`) return json(quoteCandidates);
      if (call.method === "GET" && path === `/v2/workspace/opportunities/${CASE}/purchase-order-candidates`) return json(purchaseOrderCandidates);
      if (call.method === "GET" && path === "/v2/organizations") {
        return json({
          items: [{
            organization_id: ORG,
            name: "Universidad Ficticia",
            confirmation: orgConfirmed ? "confirmed" : "machine_proposed",
            relationship_roles: [],
          }],
          total: 1, limit: 50, offset: 0,
          facets: null,
        });
      }
      if (call.method === "GET" && path === `/v2/workspace/organizations/${ORG}/authoring`) {
        return json({
          organization: {
            id: ORG,
            name: "Universidad Ficticia",
            confirmation: orgConfirmed ? "confirmed" : "machine_proposed",
            status: "active",
            version: organizationConfirmation === "machine_proposed" && orgConfirmed ? 4 : 3,
          },
        });
      }
      if (call.method === "POST") {
        const answer = onPost(call, posts++);
        if (answer) {
          if (call.path === "/v2/commands/confirm-organization-record" && (answer.status ?? 200) < 400) {
            orgConfirmed = true;
          }
          return json(answer.body, answer.status ?? 200);
        }
      }
      return json({ detail: "not found" }, 404);
    }),
  );
  return calls;
}

const page = (items: OpportunityCardData[]): PipelineResponse => ({ items, total: items.length, drive_configured: true });

function receipt(command: string, stage: string, version: number, id: string) {
  return {
    command,
    opportunity_id: CASE,
    opportunity_version: version,
    stage,
    idempotency_key: "k",
    command_receipt_id: id,
    replayed: false,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("case command helpers", () => {
  it("shows an unconfirmed request's email and records an incorrect case as abandoned", async () => {
    const request = card({ stage: "lead", organization: null, contact: null, quotes: [], latest_revision: null,
      quote_numbers: [], revision_count: 0, title: "Solicitud de cotización — Balanza ficticia",
      last_contact: { inbound: { at: "2026-10-07T12:00:00Z", subject: "Solicitud de balanza ficticia",
        url: "https://mail.google.com/mail/u/0/#all/example1", sender_name: "Persona Ficticia" }, outbound: null } });
    const calls = stubApi({ pipelines: [page([request])],
      onPost: () => ({ body: receipt("advance_case_stage", "abandoned", 6, "aaaaaaaa-1234") }) });
    render(withSession(session("sales"), <PipelinePage initialOpportunityId={CASE} />));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Solicitud de balanza ficticia")).toBeInTheDocument();
    expect(within(dialog).getByText("Persona Ficticia")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Abrir correo en Gmail" })).toHaveAttribute("href",
      "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/example1");
    fireEvent.click(within(dialog).getByRole("button", { name: "No es una solicitud" }));
    const form = within(dialog).getByRole("form", { name: "Mover a «Perdida»" });
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    fireEvent.change(within(form).getByLabelText(/Detalle/), { target: { value: "Es una oferta del proveedor" } });
    fireEvent.click(within(form).getByRole("button", { name: "Cerrar caso incorrecto" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/v2/commands/advance-case-stage");
    expect(post.body).toMatchObject({ opportunity_id: CASE, opportunity_version: 5,
      stage: "abandoned", close_reason: "No es una solicitud: Es una oferta del proveedor" });
    expect(post.key).toBeTruthy();
  });
  it("offers only the moves the stage table allows, never «won»", () => {
    expect(nextStages("quoting")).toEqual(["negotiating", "qualified", "abandoned", "lost"]);
    expect(nextStages("negotiating")).toEqual(["lost", "quoting", "abandoned"]);
    expect(nextStages("won")).toEqual([]);
    expect(nextStages("unknown")).toEqual([]);
  });

  it("wins only against a sent revision nobody replaced", () => {
    const c = card();
    c.quotes[0].revisions.push(rev(3, { status: "void", is_active: false }));
    expect(winnableRevisions(c).map((r) => [r.quote_id, r.revision_no])).toEqual([[QUOTE, 2]]);
  });

  it("finds the quotes with more than one current revision", () => {
    expect(undeterminedQuotes(card())).toEqual([]);
    const c = card();
    c.quotes[0].revisions = [rev(1), rev(2), rev(3, { status: "void", is_active: false })];
    expect(undeterminedQuotes(c).map((q) => [q.quote_id, q.revisions.map((r) => r.revision_no)])).toEqual([[QUOTE, [1, 2]]]);
  });

  it("needs the switch and a deciding role", () => {
    expect(mayRunCaseCommands(session("sales"))).toBe(true);
    expect(mayRunCaseCommands(session("admin"))).toBe(true);
    expect(mayRunCaseCommands(session("viewer"))).toBe(false);
    expect(mayRunCaseCommands(session("sales", { cases: false }))).toBe(false);
    expect(mayRunCaseCommands({ kind: "loading" })).toBe(false);
  });
});

describe("stage movement requesting-institution prerequisites", () => {
  it("refuses En estudio without an institution before any command is sent", () => {
    const request = card({ stage: "lead", organization: null });
    expect(moveRefusal(request, "estudio")).toMatch(/asociar y confirmar/);
  });

  it("refuses En estudio when the institution is only machine-proposed", () => {
    const request = card({
      stage: "qualifying",
      organization: { organization_id: ORG, name: "Universidad Ficticia", confirmation: "machine_proposed", version: 3 },
      requesting_institution_confirmation: "machine_proposed",
    });
    expect(moveRefusal(request, "estudio")).toMatch(/confirmar la institución/);
  });

  it("allows a confirmed requester even if the organization profile is still proposed", () => {
    const request = card({
      stage: "qualifying",
      organization: {
        organization_id: ORG,
        name: "Universidad Ficticia",
        confirmation: "machine_proposed",
        version: 3,
      },
      requesting_institution_confirmation: "confirmed",
    });
    expect(moveRefusal(request, "estudio")).toBeNull();
  });

  it("refuses an unconfirmed requester even if the organization profile is confirmed", () => {
    const request = card({
      stage: "qualifying",
      requesting_institution_confirmation: "machine_proposed",
    });
    expect(moveRefusal(request, "estudio")).toMatch(/confirmar la institución/);
  });

  it("allows En estudio when the requesting institution is confirmed", () => {
    const request = card({ stage: "lead" });
    expect(moveRefusal(request, "estudio")).toBeNull();
  });

  it("allows resuming a paused case in its current stage without revalidating the requester", () => {
    const request = card({
      stage: "qualified",
      organization: null,
      requesting_institution_confirmation: null,
      open_tasks: [{
        task_id: TASK,
        title: "Retomar",
        due_at: "2099-01-01T09:00:00Z",
        version: 1,
        owner: null,
      }],
    });
    expect(moveRefusal(request, "estudio", new Date("2026-10-08T12:00:00Z"))).toBeNull();
  });

  it("allows closing an unidentified request without an institution", () => {
    const request = card({ stage: "lead", organization: null });
    expect(moveRefusal(request, "perdida")).toBeNull();
  });
});

describe("case drawer actions", () => {
  async function openDrawer(s: AuthSessionState) {
    render(
      withSession(
        s,
        <>
          <PipelinePage initialOpportunityId={CASE} />
          <Toaster />
        </>,
      ),
    );
    const dialog = await screen.findByRole("dialog");
    // Every action, including the ones under «Más…».
    fireEvent.click(within(dialog).getByRole("button", { name: "Más…" }));
    return dialog;
  }

  it("requires exact printed quotation reference before linking a standalone OC and recording a win", async () => {
    const live = rev(1, {
      quote_number: "01253-26",
      document: { sha256: SHA_NEW, filename: "CN01253A-ficticia.pdf" },
    });
    const active = card({ stage: "negotiating", quotes: [{
      quote_id: QUOTE, quote_number: "01253-26", number_origin: "printed_historical",
      revisions: [live],
    }], latest_revision: live, revision_count: 1, quote_numbers: ["01253-26"] });
    const won = card({ ...active, stage: "won", version: 6, closed_at: "2026-10-09T12:00:00Z" });
    const calls = stubApi({
      pipelines: [page([active]), page([won])],
      purchaseOrderCandidates: { opportunity_id: CASE, candidates: [{
        source_record_id: MESSAGE, subject: "OC ficticia", filename: "OC 55512345.pdf",
        purchase_order_number: "55512345", sent_at: "2026-10-09T12:00:00Z",
        document_sha256: SHA_RECORDED, reason: "destinatario compartido",
        gmail_url: "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/abc",
      }] },
      onPost: (call) =>
        call.path === "/v2/commands/link-case-evidence"
          ? { body: receipt("link_case_evidence", "negotiating", 5, "linked-receipt") }
          : call.path === "/v2/commands/record-case-won"
            ? { body: receipt("record_case_won", "won", 6, "won-receipt") }
            : undefined,
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Buscar OC recibida en otro hilo" }));
    const review = await within(dialog).findByRole("region", { name: "Revisar OC de otro hilo" });
    fireEvent.click(await within(review).findByRole("button", { name: "Revisar esta OC" }));
    const form = within(review).getByRole("form", { name: "Confirmar OC y venta" });
    const submit = within(form).getByRole("button", { name: "Vincular OC y marcar ganada" });
    fireEvent.change(within(form).getByLabelText(/Número impreso en la OC/), { target: { value: "01261-26" } });
    fireEvent.change(within(form).getByLabelText(/Número de OC/), { target: { value: "55512345" } });
    fireEvent.click(within(form).getByRole("checkbox"));
    expect(submit).toBeDisabled();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.change(within(form).getByLabelText(/Número impreso en la OC/), { target: { value: "01253A-25" } });
    expect(submit).toBeDisabled();
    fireEvent.change(within(form).getByLabelText(/Número impreso en la OC/), { target: { value: "01253A-26" } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").length).toBe(2));
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
      "/v2/commands/link-case-evidence", "/v2/commands/record-case-won",
    ]);
    const link = calls.find((c) => c.path === "/v2/commands/link-case-evidence");
    const win = calls.find((c) => c.path === "/v2/commands/record-case-won");
    expect(link?.body?.source_record_id).toBe(MESSAGE);
    expect(link?.body?.note).toContain("01253A-26");
    expect(win?.body?.quote_id).toBe(QUOTE);
    expect(win?.body?.revision_no).toBe(1);
    expect(win?.body?.note).toContain("55512345");
  });

  it("keeps «Cambiar estado» and «Marcar ganada» disabled when the case commands are off", async () => {
    stubApi({ pipelines: [page([card()])] });
    const dialog = await openDrawer(session("sales", { cases: false }));
    expect(within(dialog).getByRole("button", { name: "Cambiar estado" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Marcar ganada" })).toBeDisabled();
    // add-note is a CRM authoring command: its own switch is on, so the follow-up is offered.
    expect(within(dialog).getByRole("button", { name: "Registrar seguimiento" })).toBeEnabled();
  });

  it("keeps every write disabled for a viewer", async () => {
    stubApi({ pipelines: [page([card()])] });
    const dialog = await openDrawer(session("viewer"));
    for (const name of ["Cambiar estado", "Marcar ganada", "Registrar seguimiento", "Registrar cotización", "Nueva revisión"]) {
      expect(within(dialog).getByRole("button", { name })).toBeDisabled();
    }
  });

  it("closes a case as «Perdida» with one click on a reason, with the shown version", async () => {
    const calls = stubApi({
      pipelines: [page([card()]), page([card({ stage: "lost", version: 6, closed_at: "2026-10-05T00:00:00Z" })])],
      onPost: (call) =>
        call.path === "/v2/commands/advance-case-stage" ? { body: receipt("advance_case_stage", "lost", 6, "aaaaaaaa-0001") } : undefined,
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cambiar estado" }));
    const picker = within(dialog).getByRole("group", { name: "Nuevo estado" });
    // Every state but the one the case is in; «Ganada» is «Marcar ganada».
    expect(within(picker).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Solicitada",
      "En estudio",
      "Conversación",
      "En pausa",
      "Perdida",
    ]);
    fireEvent.click(within(picker).getByRole("button", { name: "Perdida" }));
    const form = within(dialog).getByRole("form", { name: "Mover a «Perdida»" });
    const submit = within(form).getByRole("button", { name: "Marcar perdida" });
    expect(submit).toBeDisabled(); // a reason first
    fireEvent.click(within(form).getByRole("button", { name: "Precio" }));
    fireEvent.change(within(form).getByLabelText(/Detalle/), { target: { value: "eligieron otra marca" } });
    fireEvent.click(submit);

    expect(await within(dialog).findByText(/Estado → Perdida: registrado/)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/v2/commands/advance-case-stage");
    expect(post.key).toBeTruthy();
    expect(post.body).toEqual({
      opportunity_id: CASE,
      opportunity_version: 5,
      stage: "lost",
      close_reason: "Precio: eligieron otra marca",
      note: "Precio: eligieron otra marca",
    });
    await waitFor(() => expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2));
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Cambiar estado" })).toBeDisabled());
  });

  it("closes «Sin respuesta» as abandoned", async () => {
    const calls = stubApi({
      pipelines: [page([card()])],
      onPost: () => ({ body: receipt("advance_case_stage", "abandoned", 6, "aaaaaaaa-0002") }),
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cambiar estado" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Perdida" }));
    const form = within(dialog).getByRole("form", { name: "Mover a «Perdida»" });
    fireEvent.click(within(form).getByRole("button", { name: "Sin respuesta" }));
    fireEvent.click(within(form).getByRole("button", { name: "Marcar perdida" }));
    await within(dialog).findByText(/Estado → Cerrada · abandonada: registrado/);
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ stage: "abandoned", close_reason: "Sin respuesta" });
  });

  it("walks a case back from «Enviada» to «Solicitada» one stage at a time", async () => {
    const versions = [6, 7, 8];
    const calls = stubApi({
      pipelines: [page([card()])],
      onPost: (call, i) => ({ body: receipt("advance_case_stage", String(call.body?.stage), versions[i], `0000000${i}-0001`) }),
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cambiar estado" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Solicitada" }));
    const form = within(dialog).getByRole("form", { name: "Mover a «Solicitada»" });
    expect(within(form).getByTestId("case-move-steps")).toHaveTextContent("3 pasos");
    expect(within(form).getByLabelText(/^Nota/)).toHaveValue("Movido de «Enviada» a «Solicitada».");
    fireEvent.click(within(form).getByRole("button", { name: "Mover" }));
    await within(dialog).findByText(/3\/3 · Estado → Solicitada: registrado/);
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts.map((c) => [c.body?.stage, c.body?.opportunity_version])).toEqual([
      ["qualified", 5],
      ["qualifying", 6],
      ["lead", 7],
    ]);
    expect(new Set(posts.map((c) => c.key)).size).toBe(3);
  });

  it("shows a stale-version refusal in Spanish and refetches", async () => {
    const calls = stubApi({
      pipelines: [page([card()])],
      onPost: () => ({
        status: 409,
        body: { detail: { code: "case_version_conflict", message: "this case changed since it was shown to you" } },
      }),
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cambiar estado" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Conversación" }));
    const form = within(dialog).getByRole("form", { name: "Mover a «Conversación»" });
    fireEvent.click(within(form).getByRole("button", { name: "Mover" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Otra persona cambió este caso");
    expect(within(dialog).queryByText(/this case changed/)).not.toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2));
  });

  it("pauses a case until a date with a reason, as a task, and resumes it", async () => {
    const paused = card({
      open_tasks: [{ task_id: TASK, title: "Retomar: Esperando fondos o proyecto", due_at: "2099-11-02T12:00:00Z", version: 1, owner: "Ventas" }],
      next_action: { text: "Retomar: Esperando fondos o proyecto", source: "task", due_at: "2099-11-02T12:00:00Z" },
    });
    const calls = stubApi({
      pipelines: [page([card()]), page([paused]), page([card()])],
      onPost: (call) =>
        call.path === "/v2/commands/create-task"
          ? { body: { command: "create_task", task_id: TASK, task_version: 1, opportunity_id: CASE, command_receipt_id: "9999aaaa-1", replayed: false } }
          : { body: { command: "cancel_task", task_id: TASK, task_version: 2, opportunity_id: CASE, command_receipt_id: "9999bbbb-1", replayed: false } },
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cambiar estado" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "En pausa" }));
    const form = within(dialog).getByRole("form", { name: "Mover a «En pausa»" });
    fireEvent.change(within(form).getByLabelText(/Retomar el/), { target: { value: "2099-11-02" } });
    fireEvent.click(within(form).getByRole("button", { name: "Esperando fondos o proyecto" }));
    fireEvent.click(within(form).getByRole("button", { name: "Pausar" }));

    const banner = await within(dialog).findByTestId("case-paused");
    expect(banner).toHaveTextContent("En pausa hasta 02 nov 2099");
    const create = calls.find((c) => c.path === "/v2/commands/create-task")!;
    expect(create.body).toEqual({
      opportunity_id: CASE,
      title: "Retomar: Esperando fondos o proyecto",
      due_at: new Date("2099-11-02T09:00:00").toISOString(),
      note: "Esperando fondos o proyecto",
    });
    // The stage never moved.
    expect(calls.some((c) => c.path === "/v2/commands/advance-case-stage")).toBe(false);

    fireEvent.click(within(banner).getByRole("button", { name: "Retomar ahora" }));
    await waitFor(() => expect(within(dialog).queryByTestId("case-paused")).not.toBeInTheDocument());
    const cancel = calls.find((c) => c.path === "/v2/commands/cancel-task")!;
    expect(cancel.body).toEqual({ task_id: TASK, task_version: 1, note: "Retomado antes de la fecha." });
  });

  it("pausing replaces a follow-up already due, so the case really goes to «En pausa»", async () => {
    const DUE = "77777777-0000-4000-8000-0000000000d1";
    const withDue = card({
      open_tasks: [{ task_id: DUE, title: "Seguimiento de 01239-26", due_at: "2020-01-01T12:00:00Z", version: 2, owner: "Ventas" }],
    });
    const calls = stubApi({
      pipelines: [page([withDue]), page([card()])],
      onPost: (call) =>
        call.path === "/v2/commands/create-task"
          ? { body: { command: "create_task", task_id: TASK, task_version: 1, opportunity_id: CASE, command_receipt_id: "9999aaaa-2", replayed: false } }
          : { body: { command: "cancel_task", task_id: DUE, task_version: 3, opportunity_id: CASE, command_receipt_id: "9999bbbb-2", replayed: false } },
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cambiar estado" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "En pausa" }));
    const form = within(dialog).getByRole("form", { name: "Mover a «En pausa»" });
    fireEvent.change(within(form).getByLabelText(/Retomar el/), { target: { value: "2099-11-02" } });
    fireEvent.click(within(form).getByRole("button", { name: "Esperando fondos o proyecto" }));
    fireEvent.click(within(form).getByRole("button", { name: "Pausar" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v2/commands/create-task")).toBe(true));
    const posts = calls.filter((c) => c.path.startsWith("/v2/commands/")).map((c) => c.path);
    expect(posts).toEqual(["/v2/commands/cancel-task", "/v2/commands/create-task"]);
    expect(calls.find((c) => c.path === "/v2/commands/cancel-task")!.body).toEqual({
      task_id: DUE,
      task_version: 2,
      note: "Reemplazada por la pausa hasta 02 nov 2099.",
    });
  });

  it("marks a quoting case won in two steps, each with its receipt", async () => {
    const calls = stubApi({
      pipelines: [page([card()]), page([card({ stage: "won", version: 7, closed_at: "2026-10-05T00:00:00Z" })])],
      onPost: (call) => {
        if (call.path === "/v2/commands/advance-case-stage") return { body: receipt("advance_case_stage", "negotiating", 6, "bbbbbbbb-0001") };
        if (call.path === "/v2/commands/record-case-won") return { body: receipt("record_case_won", "won", 7, "cccccccc-0002") };
        return undefined;
      },
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Marcar ganada" }));
    const form = within(dialog).getByRole("form", { name: "Marcar ganada" });
    expect(within(form).getByTestId("won-two-steps")).toBeInTheDocument();
    // Only the current revision is offered, and it is preselected.
    expect(within(form).getAllByRole("option").map((o) => o.textContent)).toEqual([expect.stringMatching(/^00001-26 r2/)]);
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "Llegó la orden de compra" } });
    fireEvent.click(within(form).getByRole("button", { name: "Marcar ganada" }));

    const outcome = await within(dialog).findByTestId("case-action-outcome");
    expect(outcome).toHaveTextContent("1/2 · Etapa → Conversación: registrado · recibo bbbbbbbb");
    expect(outcome).toHaveTextContent("2/2 · Ganada: registrado · recibo cccccccc");
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts.map((c) => c.path)).toEqual(["/v2/commands/advance-case-stage", "/v2/commands/record-case-won"]);
    expect(posts[0].body).toEqual({ opportunity_id: CASE, opportunity_version: 5, stage: "negotiating", note: "Llegó la orden de compra" });
    // The win compares against the version the first step returned, not the one first shown.
    expect(posts[1].body).toEqual({
      opportunity_id: CASE,
      opportunity_version: 6,
      quote_id: QUOTE,
      revision_no: 2,
      note: "Llegó la orden de compra",
    });
    expect(posts[0].key).not.toEqual(posts[1].key);
    await waitFor(() => expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2));
  });

  it("marks a negotiating case won in one step", async () => {
    const calls = stubApi({
      pipelines: [page([card({ stage: "negotiating" })])],
      onPost: () => ({ body: receipt("record_case_won", "won", 6, "dddddddd-0001") }),
    });
    const dialog = await openDrawer(session("admin"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Marcar ganada" }));
    const form = within(dialog).getByRole("form", { name: "Marcar ganada" });
    expect(within(form).queryByTestId("won-two-steps")).not.toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "OC recibida" } });
    fireEvent.click(within(form).getByRole("button", { name: "Marcar ganada" }));
    expect(await within(dialog).findByText(/^Ganada: registrado · recibo dddddddd/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/v2/commands/record-case-won"]);
  });

  it("says honestly when the first step landed and the win was refused", async () => {
    stubApi({
      pipelines: [page([card()])],
      onPost: (call) =>
        call.path === "/v2/commands/advance-case-stage"
          ? { body: receipt("advance_case_stage", "negotiating", 6, "eeeeeeee-0001") }
          : { status: 409, body: { detail: { code: "quote_revision_not_current", message: "only a sent, current revision is won" } } },
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Marcar ganada" }));
    const form = within(dialog).getByRole("form", { name: "Marcar ganada" });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "OC" } });
    fireEvent.click(within(form).getByRole("button", { name: "Marcar ganada" }));
    const outcome = await within(dialog).findByTestId("case-action-outcome");
    expect(outcome).toHaveTextContent("1/2 · Etapa → Conversación: registrado");
    expect(outcome).toHaveTextContent("2/2 · Ganada: no se registró — Sólo se gana contra una revisión enviada y vigente");
    expect(outcome).toHaveTextContent("El caso quedó en «Conversación».");
  });

  it("disables «Marcar ganada» without a sent, current revision or outside quoting/negotiating", async () => {
    stubApi({ pipelines: [page([card({ stage: "qualified" })])] });
    const dialog = await openDrawer(session("sales"));
    expect(within(dialog).getByRole("button", { name: "Marcar ganada" })).toBeDisabled();
    expect(within(dialog).getByText("Se marca ganada desde «Enviada» o «Conversación»")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Cambiar estado" })).toBeEnabled();
  });

  it("assigns an existing confirmed requesting institution with audited versions", async () => {
    const unassigned = card({ stage: "lead", organization: null });
    const assigned = card({ stage: "lead", version: 6 });

    const calls = stubApi({
      pipelines: [page([unassigned]), page([assigned])],
      onPost: (call) =>
        call.path === "/v2/commands/add-case-organization"
          ? { body: receipt("add_case_organization", "lead", 6, "aaaaaaaa-0003") }
          : undefined,
    });

    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Asignar institución solicitante" }));

    const form = within(dialog).getByRole("region", {
      name: "Asignar institución solicitante",
    });
    fireEvent.change(within(form).getByPlaceholderText("Nombre de la institución"), {
      target: { value: "Universidad Ficticia" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Buscar" }));

    const selector = await within(form).findByRole("combobox", {
      name: "Institución solicitante",
    });
    fireEvent.change(selector, { target: { value: ORG } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Motivo de la asignación" }), {
      target: { value: "Solicitante verificado por el operador." },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Confirmar asignación" }));

    await waitFor(() =>
      expect(calls.some((c) => c.path === "/v2/commands/add-case-organization")).toBe(true),
    );
    const post = calls.find((c) => c.path === "/v2/commands/add-case-organization")!;
    expect(post.method).toBe("POST");
    expect(post.key).toBeTruthy();
    expect(post.body).toEqual({
      opportunity_id: CASE,
      opportunity_version: 5,
      organization_id: ORG,
      organization_version: 3,
      role: "requesting_institution",
      note: "Solicitante verificado por el operador.",
    });
    await waitFor(() =>
      expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2),
    );
  });


  it("confirms a machine-proposed organization only after explicit review, then assigns it separately", async () => {
    const unassigned = card({ stage: "lead", organization: null });
    const assigned = card({ stage: "lead", version: 6 });
    const calls = stubApi({
      pipelines: [page([unassigned]), page([assigned])],
      organizationConfirmation: "machine_proposed",
      onPost: (call) => {
        if (call.path === "/v2/commands/confirm-organization-record") return { body: { ok: true, version: 4 } };
        if (call.path === "/v2/commands/add-case-organization") {
          return { body: receipt("add_case_organization", "lead", 6, "aaaaaaaa-0030") };
        }
        return undefined;
      },
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Asignar institución solicitante" }));
    const form = within(dialog).getByRole("region", { name: "Asignar institución solicitante" });
    fireEvent.change(within(form).getByPlaceholderText("Nombre de la institución"), {
      target: { value: "Universidad Ficticia" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Buscar" }));
    const selector = await within(form).findByRole("combobox", { name: "Institución solicitante" });
    fireEvent.change(selector, { target: { value: ORG } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Motivo de la asignación" }), {
      target: { value: "El correo original identifica expresamente a la universidad." },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Confirmar asignación" }));
    const confirm = await within(form).findByRole("button", { name: "Confirmar ficha de institución" });
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    expect(within(form).getByRole("button", { name: "Confirmar asignación" })).toBeDisabled();
    fireEvent.click(confirm);
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/v2/commands/confirm-organization-record")).toBe(true);
    });
    const orgPost = calls.find((c) => c.path === "/v2/commands/confirm-organization-record")!;
    expect(orgPost.body).toMatchObject({ organization_id: ORG, expected_version: 3 });
    expect(calls.some((c) => c.path === "/v2/commands/add-case-organization")).toBe(false);
    await within(form).findByText(/Pulsa «Confirmar asignación»/);
    fireEvent.click(within(form).getByRole("button", { name: "Confirmar asignación" }));
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/v2/commands/add-case-organization")).toBe(true);
    });
    expect(calls.find((c) => c.path === "/v2/commands/add-case-organization")!.body)
      .toMatchObject({ organization_version: 4, opportunity_version: 5 });
  });

  it("blocks cross-thread PDF attachment when its Gmail thread already belongs to another case", async () => {
    const blocked = card({
      title: "Compra de productos_ cotización N°01259-26",
      stage: "lead",
      organization: null,
      quotes: [],
      quote_numbers: [],
      revision_count: 0,
      latest_revision: null,
    });
    const otherId = "22222222-2222-4222-8222-222222222222";
    const calls = stubApi({
      pipelines: [page([blocked])],
      quoteCandidates: {
        opportunity_id: CASE,
        candidates: [{
          source_record_id: MESSAGE,
          quote_token: "CN01259",
          filename: "CN01259-ficticio.pdf",
          subject: "Re: Solicitud de cotización",
          sent_at: "2026-10-07T14:00:00Z",
          document_sha256: SHA_NEW,
          gmail_url: "https://mail.google.com/mail/u/0/#all/abc123",
          reason: "Número exacto y destinatario externo compartido",
          recorded_elsewhere: false,
          other_cases_on_quote_thread: [{ opportunity_id: otherId, title: "Solicitud de productos original" }],
        }],
      },
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Buscar cotización enviada en otro hilo" }));
    const review = await within(dialog).findByRole("region", { name: "Cotización en otro hilo" });
    expect(within(review).getByText(/Este hilo ya corresponde a otro caso/)).toBeInTheDocument();
    expect(within(review).getByRole("link", { name: /Abrir caso original/ }))
      .toHaveAttribute("href", `#/crm/oportunidades/${otherId}`);
    expect(within(review).queryByRole("button", { name: "Revisar vínculo con este caso" }))
      .not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("only links a cross-thread PDF after the operator reads it and writes a reason", async () => {
    const unassigned = card({
      title: "Compra de productos_ cotización N°01259-26",
      stage: "lead",
      organization: null,
      quotes: [],
      quote_numbers: [],
      revision_count: 0,
      latest_revision: null,
    });
    const calls = stubApi({
      pipelines: [page([unassigned]), page([unassigned])],
      quoteCandidates: {
        opportunity_id: CASE,
        candidates: [{
          source_record_id: MESSAGE,
          quote_token: "CN01259",
          filename: "CN01259-ficticio.pdf",
          subject: "Re: Solicitud de cotización",
          sent_at: "2026-10-07T14:00:00Z",
          document_sha256: SHA_NEW,
          gmail_url: "https://mail.google.com/mail/u/0/#all/abc123",
          reason: "Número exacto y destinatario externo compartido",
          recorded_elsewhere: false,
        }],
      },
      onPost: (call) => call.path === "/v2/commands/link-case-evidence"
        ? { body: receipt("link_case_evidence", "lead", 6, "aaaaaaaa-0031") }
        : undefined,
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Buscar cotización enviada en otro hilo" }));
    const review = await within(dialog).findByRole("region", { name: "Cotización en otro hilo" });
    expect(within(review).getByRole("link", { name: /Abrir mensaje original/ }))
      .toHaveAttribute("href", "https://mail.google.com/mail/u/0/#all/abc123");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.click(within(review).getByRole("button", { name: "Revisar vínculo con este caso" }));
    expect(within(review).getByRole("button", { name: "Vincular correo revisado" })).toBeDisabled();
    fireEvent.change(within(review).getByRole("textbox", { name: "Motivo y evidencia del vínculo" }), {
      target: { value: "Comprobé el PDF y la dirección de Irina en los dos hilos de Gmail." },
    });
    fireEvent.click(within(review).getByRole("button", { name: "Vincular correo revisado" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v2/commands/link-case-evidence")).toBe(true));
    expect(calls.find((c) => c.path === "/v2/commands/link-case-evidence")!.body).toEqual({
      opportunity_id: CASE, opportunity_version: 5, relation: "mentions",
      source_record_id: MESSAGE,
      note: "Comprobé el PDF y la dirección de Irina en los dos hilos de Gmail.",
    });
    expect(calls.some((c) => c.path === "/v2/commands/record-case-quotation")).toBe(false);
  });

  it("promotes a reviewed machine mention to the requesting role without inventing an organization", async () => {
    const relation = "77777777-7777-4777-8777-777777777777";
    const unassigned = card({
      stage: "lead",
      organization: null,
      pending_institution_mentions: [{
        opportunity_organization_id: relation,
        organization_id: ORG,
        name: "Universidad Ficticia",
      }],
    });
    const assigned = card({ stage: "lead", version: 6 });
    const calls = stubApi({
      pipelines: [page([unassigned]), page([assigned])],
      onPost: (call) =>
        call.path === "/v2/commands/set-case-organization-role"
          ? { body: receipt("set_case_organization_role", "lead", 6, "aaaaaaaa-0005") }
          : undefined,
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Asignar institución solicitante" }));
    const review = within(dialog).getByRole("form", { name: "Revisar institución mencionada" });
    const confirm = within(review).getByRole("button", { name: "Confirmar rol de solicitante" });
    expect(confirm).toBeDisabled();

    fireEvent.change(within(review).getByRole("combobox", { name: "Institución mencionada" }), {
      target: { value: relation },
    });
    fireEvent.change(within(review).getByRole("textbox", { name: "Evidencia de que es solicitante" }), {
      target: { value: "El correo original identifica expresamente a la institución como solicitante." },
    });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(calls.some((c) => c.path === "/v2/commands/set-case-organization-role")).toBe(true),
    );
    const post = calls.find((c) => c.path === "/v2/commands/set-case-organization-role")!;
    expect(post.method).toBe("POST");
    expect(post.key).toBeTruthy();
    expect(post.body).toEqual({
      opportunity_id: CASE,
      opportunity_version: 5,
      opportunity_organization_id: relation,
      role: "requesting_institution",
      note: "El correo original identifica expresamente a la institución como solicitante.",
    });
    expect(calls.some((c) => c.path === "/v2/commands/add-case-organization")).toBe(false);
    await waitFor(() =>
      expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2),
    );
  });

  it("passes an explicit supplier exception only when the operator wrote one", async () => {
    const relation = "77777777-7777-4777-8777-777777777777";
    const calls = stubApi({
      pipelines: [page([card({
        stage: "lead",
        organization: null,
        pending_institution_mentions: [{
          opportunity_organization_id: relation,
          organization_id: ORG,
          name: "Universidad Ficticia",
        }],
      })])],
      onPost: (call) =>
        call.path === "/v2/commands/set-case-organization-role"
          ? { body: receipt("set_case_organization_role", "lead", 6, "aaaaaaaa-0006") }
          : undefined,
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Asignar institución solicitante" }));
    const review = within(dialog).getByRole("form", { name: "Revisar institución mencionada" });
    fireEvent.change(within(review).getByRole("combobox", { name: "Institución mencionada" }), {
      target: { value: relation },
    });
    fireEvent.change(within(review).getByRole("textbox", { name: "Evidencia de que es solicitante" }), {
      target: { value: "Confirmado mediante orden de compra." },
    });
    fireEvent.change(within(review).getByRole("textbox", { name: "Excepción de proveedor (solo si corresponde)" }), {
      target: { value: "También es proveedor, pero en esta compra figura como cliente." },
    });
    fireEvent.click(within(review).getByRole("button", { name: "Confirmar rol de solicitante" }));
    await waitFor(() =>
      expect(calls.some((c) => c.path === "/v2/commands/set-case-organization-role")).toBe(true),
    );
    const post = calls.find((c) => c.path === "/v2/commands/set-case-organization-role")!;
    expect(post.body).toMatchObject({
      supplier_exception_reason: "También es proveedor, pero en esta compra figura como cliente.",
    });
  });

  it("prevents a viewer from assigning a requesting institution", async () => {
    const calls = stubApi({
      pipelines: [page([card({ stage: "lead", organization: null })])],
    });
    const dialog = await openDrawer(session("viewer"));
    expect(within(dialog).getByRole("button", {
      name: "Asignar institución solicitante",
    })).toBeDisabled();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("confirms an institution «por confirmar» with its version, then refetches", async () => {
    const proposed = card({
      organization: { organization_id: ORG, name: "nuevo-ficticio.example.cl", confirmation: "machine_proposed", version: 3 },
    });
    const calls = stubApi({
      pipelines: [page([proposed]), page([card()])],
      onPost: (call) =>
        call.path === "/v2/commands/confirm-organization-record"
          ? { body: { ok: true, replayed: false, idempotency_key: "k", command_receipt_id: "r", version: 4 } }
          : undefined,
    });
    const dialog = await openDrawer(session("sales"));
    const box = within(dialog).getByTestId("case-confirm-institution");
    expect(within(box).getByRole("link", { name: /Abrir institución/ })).toHaveAttribute("href", `#/crm/organizaciones/${ORG}`);
    fireEvent.click(within(box).getByRole("button", { name: "Confirmar institución" }));
    expect(await within(dialog).findByText(/Institución «nuevo-ficticio.example.cl» confirmada/)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({ organization_id: ORG, expected_version: 3 });
    await waitFor(() => expect(within(dialog).queryByTestId("case-confirm-institution")).not.toBeInTheDocument());
  });

  it("does not offer «Confirmar institución» without CRM authoring", async () => {
    stubApi({
      pipelines: [
        page([card({ organization: { organization_id: ORG, name: "x.example.cl", confirmation: "machine_proposed", version: 1 } })]),
      ],
    });
    const dialog = await openDrawer(session("sales", { authoring: false }));
    expect(within(dialog).getByRole("button", { name: "Confirmar institución" })).toBeDisabled();
  });

  it("reads the case's notes and opens the note form from «Registrar seguimiento»", async () => {
    const calls = stubApi({
      pipelines: [page([card()])],
      onPost: (call) =>
        call.path === "/v2/commands/add-note"
          ? { body: { ok: true, replayed: false, idempotency_key: "k", command_receipt_id: "r", note_id: "n2" } }
          : undefined,
      notes: {
        opportunity_id: CASE,
        notes: [
          {
            id: "n1", root_note_id: "n1", revision_no: 1, body: "Pidieron plazo de entrega.", author_operator_id: "op-1",
            author_name: "Vendedora Ficticia", created_at: "2026-10-01T00:00:00Z", status: "active", archived_at: null,
            archive_reason: null, version: 1, is_latest: true,
          },
        ],
      },
    });
    const dialog = await openDrawer(session("sales"));
    expect(await within(dialog).findByText("Pidieron plazo de entrega.")).toBeInTheDocument();
    expect(within(dialog).getByText("Notas (1)")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Registrar seguimiento" }));
    fireEvent.change(await within(dialog).findByPlaceholderText("Escribe la nota…"), { target: { value: "Llamé al laboratorio." } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Agregar" }));
    const notesPath = `/v2/workspace/opportunities/${CASE}/notes`;
    await waitFor(() => expect(calls.filter((c) => c.path === notesPath)).toHaveLength(2));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/v2/commands/add-note");
    expect(post.body).toEqual({ subject_kind: "opportunity", subject_id: CASE, body: "Llamé al laboratorio." });
  });

  it("opens the note form once, on the case it was asked on — never by itself on the next drawer", async () => {
    stubApi({ pipelines: [page([card()])] });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Registrar seguimiento" }));
    expect(await within(dialog).findByPlaceholderText("Escribe la nota…")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cerrar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // Reopen the same case from its card: the note form stays closed.
    fireEvent.click(screen.getAllByRole("button", { name: /Universidad Ficticia/ })[0]);
    const again = await screen.findByRole("dialog");
    await within(again).findByText(/Notas \(/);
    expect(within(again).queryByPlaceholderText("Escribe la nota…")).not.toBeInTheDocument();
  });

  it("keeps the cursor where it is when the pipeline refreshes after a save, and says it is refreshing", async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((r) => {
      release = r;
    });
    const calls = stubApi({
      pipelines: [page([card()]), page([card({ stage: "negotiating", version: 6 })])],
      onPost: () => ({ body: receipt("advance_case_stage", "negotiating", 6, "ffffffff-0001") }),
    });
    const dialog = await openDrawer(session("sales"));
    // Hold the second pipeline read so the "refreshing" state is visible.
    const original = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (url.includes("/v2/workspace/pipeline")) await held;
        return (original as typeof fetch)(input, init);
      }),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Cambiar estado" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Conversación" }));
    const form = within(dialog).getByRole("form", { name: "Mover a «Conversación»" });
    fireEvent.click(within(form).getByRole("button", { name: "Mover" }));
    expect(await within(dialog).findByTestId("drawer-refreshing")).toHaveTextContent("Actualizando…");
    const notesButton = within(dialog).getByRole("button", { name: "Agregar nota" });
    notesButton.focus();
    release();
    await waitFor(() => expect(within(dialog).queryByTestId("drawer-refreshing")).not.toBeInTheDocument());
    expect(document.activeElement).toBe(notesButton);
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Cambio registrado.");
  });

  it("lets the operator retry a refused «Marcar ganada», with a new key for the refused step", async () => {
    const calls = stubApi({
      pipelines: [page([card({ stage: "negotiating" })])],
      onPost: (_call, n) =>
        n === 0
          ? { status: 503, body: { detail: { code: "database_unavailable", message: "try again" } } }
          : { body: receipt("record_case_won", "won", 6, "abababab-0001") },
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Marcar ganada" }));
    const form = within(dialog).getByRole("form", { name: "Marcar ganada" });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "OC" } });
    const submit = within(form).getByRole("button", { name: "Marcar ganada" });
    fireEvent.click(submit);
    await within(dialog).findByRole("alert");
    // While a write runs the button cannot be pressed again; after a refusal it can.
    await waitFor(() => expect(within(form).getByRole("button", { name: "Marcar ganada" })).toBeEnabled());
    fireEvent.click(within(form).getByRole("button", { name: "Marcar ganada" }));
    expect(await within(dialog).findByText(/^Ganada: registrado/)).toBeInTheDocument();
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts).toHaveLength(2);
    expect(posts[0].key).not.toEqual(posts[1].key); // a refusal renews the key of the refused step
  });

  function undetermined(over: Partial<OpportunityCardData> = {}) {
    const revisions = [rev(1, { document: { sha256: "c".repeat(64), filename: "CN00001-a.pdf" } }), rev(2)];
    return card({
      quotes: [{ quote_id: QUOTE, quote_number: "00001-26", number_origin: "printed_historical", revisions }],
      latest_revision: revisions[1],
      attention: [
        { code: "canonical_undetermined", label: "Hay más de una revisión vigente: no se sabe cuál es la canónica", blocking: true },
      ],
      status: "blocked",
      ...over,
    });
  }

  it("offers «Elegir revisión vigente» on a blocked case and sends the chosen revision", async () => {
    const calls = stubApi({
      pipelines: [page([undetermined()]), page([card({ version: 6 })])],
      onPost: (call) =>
        call.path === "/v2/commands/resolve-current-revision"
          ? {
              body: {
                command: "resolve_current_revision", opportunity_id: CASE, opportunity_version: 6, idempotency_key: "k",
                command_receipt_id: "ffffffff-0001", replayed: false, current_revision_no: 1, superseded_revision_nos: [2],
              },
            }
          : undefined,
    });
    const dialog = await openDrawer(session("sales"));
    const box = within(dialog).getByTestId("case-undetermined-revision");
    expect(box).toHaveTextContent("Hay más de una revisión vigente de 00001-26");
    fireEvent.click(within(box).getByRole("button", { name: "Elegir revisión vigente" }));
    const form = within(dialog).getByRole("form", { name: "Elegir revisión vigente" });
    const submit = within(form).getByRole("button", { name: "Elegir revisión vigente" });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "La firmada es la vigente" } });
    expect(submit).toBeDisabled(); // a revision must be chosen out loud
    fireEvent.click(within(form).getByLabelText(/00001-26 r1 .* CN00001-a\.pdf/));
    fireEvent.click(submit);

    const outcome = await within(dialog).findByTestId("case-action-outcome");
    expect(outcome).toHaveTextContent("Revisión vigente → 00001-26 r1: registrado · recibo ffffffff");
    expect(outcome).toHaveTextContent("Quedan reemplazadas: r2.");
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/v2/commands/resolve-current-revision");
    expect(post.key).toBeTruthy();
    expect(post.body).toEqual({
      opportunity_id: CASE, opportunity_version: 5, quote_id: QUOTE, revision_no: 1, note: "La firmada es la vigente",
    });
    await waitFor(() => expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2));
    await waitFor(() => expect(within(dialog).queryByTestId("case-undetermined-revision")).not.toBeInTheDocument());
  });

  it("says «Pendiente de archivar» when the archiver will file the PDF, and asks for a manual upload otherwise", async () => {
    const revisions = [rev(1, { drive: null, drive_pending: true }), rev(2, { drive: null, drive_pending: false })];
    stubApi({ pipelines: [page([card({ quotes: [{ quote_id: QUOTE, quote_number: "00001-26", number_origin: "printed_historical", revisions }] })])] });
    const dialog = await openDrawer(session("sales"));
    expect(within(dialog).getByText("Pendiente de archivar en Drive")).toBeInTheDocument();
    expect(within(dialog).getByText("PDF sin copia en Drive: súbelo a mano")).toBeInTheDocument();
  });

  it("keeps «Elegir revisión vigente» disabled on a closed case: the API never edits one", async () => {
    stubApi({ pipelines: [page([undetermined({ stage: "won", closed_at: "2026-05-10T00:00:00Z", status: "pending" })])] });
    const dialog = await openDrawer(session("sales"));
    expect(within(dialog).getByRole("button", { name: "Elegir revisión vigente" })).toBeDisabled();
  });

  it("keeps «Elegir revisión vigente» disabled without the case commands", async () => {
    stubApi({ pipelines: [page([undetermined()])] });
    const dialog = await openDrawer(session("sales", { cases: false }));
    expect(within(dialog).getByRole("button", { name: "Elegir revisión vigente" })).toBeDisabled();
  });

  it("registers a sent quotation from a linked message's document, never one already recorded", async () => {
    const calls = stubApi({
      pipelines: [page([card()])],
      onPost: (call) =>
        call.path === "/v2/commands/record-case-quotation"
          ? {
              body: {
                command: "record_case_quotation", opportunity_id: CASE, opportunity_version: 6, idempotency_key: "k",
                command_receipt_id: "abababab-0001", replayed: false, quote_number: "01239-26", revision_no: 1,
              },
            }
          : undefined,
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Registrar cotización" }));
    const form = within(dialog).getByRole("form", { name: "Registrar cotización" });
    const select = await within(form).findByLabelText(/Documento enviado/);
    const labels = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(labels).toEqual(["Elige un documento…", expect.stringMatching(/^CN01239\.pdf · Cotización equipo ficticio/)]);
    fireEvent.change(select, { target: { value: `${MESSAGE}|${SHA_NEW}` } });
    expect(within(form).getByText(/El nombre del archivo dice CN01239/)).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText(/Número de cotización/), { target: { value: " 01239-26 " } });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "Enviada por Gmail" } });
    fireEvent.click(within(form).getByRole("button", { name: "Registrar cotización" }));

    expect(await within(dialog).findByText(/Cotización 01239-26 r1: registrado · recibo abababab/)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/v2/commands/record-case-quotation");
    expect(post.body).toEqual({
      opportunity_id: CASE, opportunity_version: 5, quote_number: "01239-26", source_record_id: MESSAGE,
      document_sha256: SHA_NEW, supersedes_revision_no: null, note: "Enviada por Gmail",
    });
    await waitFor(() => expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2));
  });

  it("shows a number that belongs to another case as a Spanish refusal", async () => {
    stubApi({
      pipelines: [page([card()])],
      onPost: () => ({
        status: 409,
        body: { detail: { code: "number_on_other_opportunity", message: "this printed number is already a quote on another case" } },
      }),
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Registrar cotización" }));
    const form = within(dialog).getByRole("form", { name: "Registrar cotización" });
    fireEvent.change(await within(form).findByLabelText(/Documento enviado/), { target: { value: `${MESSAGE}|${SHA_NEW}` } });
    fireEvent.change(within(form).getByLabelText(/Número de cotización/), { target: { value: "01239-26" } });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "x" } });
    fireEvent.click(within(form).getByRole("button", { name: "Registrar cotización" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Ese número de cotización ya pertenece a otro caso.");
    // Refused: the form stays open so the operator can correct the number.
    expect(within(dialog).getByRole("form", { name: "Registrar cotización" })).toBeInTheDocument();
  });

  it("says so when the linked messages carry no unrecorded document", async () => {
    stubApi({ pipelines: [page([card()])], mailDocuments: { opportunity_id: CASE, messages: [] } });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Registrar cotización" }));
    expect(await within(dialog).findByTestId("case-quotation-no-documents")).toBeInTheDocument();
    const form = within(dialog).getByRole("form", { name: "Registrar cotización" });
    expect(within(form).getByRole("button", { name: "Registrar cotización" })).toBeDisabled();
  });

  it("records «Nueva revisión» under the replaced revision's number", async () => {
    const calls = stubApi({
      pipelines: [page([card({ stage: "negotiating" })])],
      onPost: () => ({
        body: {
          command: "record_case_quotation", opportunity_id: CASE, opportunity_version: 6, idempotency_key: "k",
          command_receipt_id: "cdcdcdcd-0001", replayed: false, quote_number: "00001-26", revision_no: 3,
        },
      }),
    });
    const dialog = await openDrawer(session("admin"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Nueva revisión" }));
    const form = within(dialog).getByRole("form", { name: "Nueva revisión" });
    expect(within(form).getByLabelText(/Revisión que reemplaza/)).toHaveValue("rev-2");
    expect(within(form).queryByLabelText(/Número de cotización/)).not.toBeInTheDocument();
    fireEvent.change(await within(form).findByLabelText(/Documento enviado/), { target: { value: `${MESSAGE}|${SHA_NEW}` } });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "Cambió el plazo" } });
    fireEvent.click(within(form).getByRole("button", { name: "Nueva revisión" }));
    const outcome = await within(dialog).findByTestId("case-action-outcome");
    expect(outcome).toHaveTextContent("Cotización 00001-26 r3: registrado · recibo cdcdcdcd");
    expect(outcome).toHaveTextContent("Reemplaza a 00001-26 r2.");
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      opportunity_id: CASE, opportunity_version: 5, quote_number: "00001-26", source_record_id: MESSAGE,
      document_sha256: SHA_NEW, supersedes_revision_no: 2, note: "Cambió el plazo",
    });
  });

  it("disables «Registrar cotización» and «Nueva revisión» before «Enviada»", async () => {
    stubApi({ pipelines: [page([card({ stage: "qualified" })])] });
    const dialog = await openDrawer(session("sales"));
    expect(within(dialog).getByRole("button", { name: "Registrar cotización" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Nueva revisión" })).toBeDisabled();
  });
});

describe("Tablero drag and drop", () => {
  function drag() {
    const data: Record<string, string> = {};
    return {
      setData: (k: string, v: string) => {
        data[k] = v;
      },
      getData: (k: string) => data[k] ?? "",
      effectAllowed: "",
      dropEffect: "",
    };
  }

  async function board(s: AuthSessionState) {
    render(
      withSession(
        s,
        <>
          <PipelinePage />
          <Toaster />
        </>,
      ),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Tablero" }));
    return screen.getByTestId(`board-card-${CASE}`);
  }

  it("asks for the reason when a card is dropped on «Perdida», then records it", async () => {
    const calls = stubApi({
      pipelines: [page([card()]), page([card({ stage: "abandoned", version: 6, closed_at: "2026-10-05T00:00:00Z" })])],
      onPost: () => ({ body: receipt("advance_case_stage", "abandoned", 6, "abababab-0001") }),
    });
    const b = await board(session("sales"));
    const dataTransfer = drag();
    fireEvent.dragStart(b, { dataTransfer });
    fireEvent.drop(screen.getByRole("region", { name: "Perdida" }), { dataTransfer });
    const modal = screen.getByRole("dialog", { name: "Mover a «Perdida»" });
    // Nothing is recorded by the drop itself.
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.click(within(modal).getByRole("button", { name: "Sin respuesta" }));
    fireEvent.click(within(modal).getByRole("button", { name: "Marcar perdida" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Movido a «Perdida».");
    expect(screen.queryByRole("dialog", { name: "Mover a «Perdida»" })).not.toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ stage: "abandoned", opportunity_version: 5 });
    await waitFor(() => expect(screen.getByTestId("board-count-perdida")).toHaveTextContent("1"));
  });

  it("opens «Marcar ganada» when a card is dropped on «Ganada»", async () => {
    stubApi({ pipelines: [page([card()])] });
    const b = await board(session("sales"));
    const dataTransfer = drag();
    fireEvent.dragStart(b, { dataTransfer });
    fireEvent.drop(screen.getByRole("region", { name: "Ganada" }), { dataTransfer });
    const drawer = await screen.findByRole("dialog", { name: "Universidad Ficticia" });
    expect(within(drawer).getByRole("form", { name: "Marcar ganada" })).toBeInTheDocument();
  });

  it("does not let a viewer drag", async () => {
    stubApi({ pipelines: [page([card()])] });
    const b = await board(session("viewer"));
    expect(b).toHaveAttribute("draggable", "false");
  });
});

describe("Decidir casos", () => {
  const NOW = new Date("2026-10-06T12:00:00Z");
  const sentAt = (iso: string) => {
    const r = rev(2, { sent_at: iso });
    return { latest_revision: r, quotes: [{ quote_id: QUOTE, quote_number: "00001-26", number_origin: "printed_historical", revisions: [r] }] };
  };
  const replied = card({
    opportunity_id: "aaaaaaaa-0000-4000-8000-000000000001",
    ...sentAt("2026-09-20T12:00:00Z"),
    last_contact: { outbound: null, inbound: { at: "2026-09-25T12:00:00Z", subject: "Re", url: null } },
  });
  const silent = card({ opportunity_id: "aaaaaaaa-0000-4000-8000-000000000002", ...sentAt("2026-07-01T12:00:00Z") });
  const recent = card({ opportunity_id: "aaaaaaaa-0000-4000-8000-000000000003", ...sentAt("2026-10-01T12:00:00Z") });

  it("proposes Conversación after a reply, Perdida after 45 silent days, a follow-up otherwise", () => {
    expect(proposeDecision(replied, NOW)).toEqual({ decision: "conversacion", why: "Respondió 25 sept" });
    expect(proposeDecision(silent, NOW).decision).toBe("perdida");
    expect(proposeDecision(silent, NOW).why).toBe("97 días sin respuesta");
    expect(proposeDecision(recent, NOW)).toEqual({ decision: "seguir", why: "Enviada hace 5 días" });
  });

  it("applies every decision with its own command, and lets the operator change or leave out a row", async () => {
    const calls = stubApi({
      pipelines: [page([replied, silent, recent])],
      onPost: (call) => ({
        body: { ...receipt("x", String(call.body?.stage ?? ""), 9, `rcpt-${calls.length}`), task_id: "t", task_version: 1 },
      }),
    });
    render(
      withSession(
        session("sales"),
        <>
          <DecideCases cards={[replied, silent, recent]} onApplied={() => undefined} onClose={() => undefined} now={NOW} />
          <Toaster />
        </>,
      ),
    );
    const decide = screen.getByTestId("decide-cases");
    expect(within(decide).getByRole("heading")).toHaveTextContent("Decidir 3 casos históricos");
    const pressed = (id: string) =>
      within(screen.getByTestId(`decide-row-${id}`))
        .getAllByRole("button")
        .filter((b) => b.getAttribute("aria-pressed") === "true")
        .map((b) => b.textContent);
    expect(pressed(replied.opportunity_id)).toEqual(["Conversación"]);
    expect(pressed(silent.opportunity_id)).toEqual(["Perdida"]);
    expect(pressed(recent.opportunity_id)).toEqual(["Seguimiento"]);
    // The recent one waits for funds instead; the silent one is left for later.
    fireEvent.click(within(screen.getByTestId(`decide-row-${recent.opportunity_id}`)).getByRole("button", { name: "En pausa" }));
    fireEvent.click(within(screen.getByTestId(`decide-row-${silent.opportunity_id}`)).getByRole("checkbox"));
    fireEvent.click(within(decide).getByRole("button", { name: "Aplicar 2 decisiones" }));

    expect(await screen.findByTestId("toaster")).toHaveTextContent("2 casos decididos.");
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts.map((c) => c.path)).toEqual(["/v2/commands/advance-case-stage", "/v2/commands/create-task"]);
    expect(posts[0].body).toMatchObject({ opportunity_id: replied.opportunity_id, stage: "negotiating" });
    expect(posts[1].body).toMatchObject({ opportunity_id: recent.opportunity_id, title: "Retomar: Pidió volver a contactar" });
  });

  it("is opened from the notice on Oportunidades, for a deciding role only", async () => {
    stubApi({ pipelines: [page([replied, silent, recent])] });
    const { unmount } = render(withSession(session("viewer"), <PipelinePage />));
    expect(await screen.findByTestId("historical-stage-notice")).toHaveTextContent("3 de 3");
    expect(screen.queryByRole("button", { name: "Decidir 3 casos" })).not.toBeInTheDocument();
    unmount();
    render(withSession(session("sales"), <PipelinePage />));
    fireEvent.click(await screen.findByRole("button", { name: "Decidir 3 casos" }));
    expect(screen.getByTestId("decide-cases")).toBeInTheDocument();
    expect(screen.queryByTestId("historical-stage-notice")).not.toBeInTheDocument();
  });
});

