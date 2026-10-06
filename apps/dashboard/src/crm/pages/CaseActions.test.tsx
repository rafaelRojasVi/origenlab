import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { mayRunCaseCommands, nextStages, winnableRevisions } from "../caseCommands";
import type { OpportunityCardData, PipelineResponse, RevisionCard } from "../crmTypes";
import { clearResourceCache } from "../useResource";
import { PipelinePage } from "./PipelinePage";

// Every value below is invented; the repository is public.
const CASE = "11111111-1111-4111-8111-111111111111";
const ORG = "33333333-3333-4333-8333-333333333333";
const QUOTE = "44444444-4444-4444-8444-444444444444";

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
  onPost = () => undefined,
}: {
  pipelines: PipelineResponse[];
  notes?: unknown;
  onPost?: Handler;
}) {
  const calls: Call[] = [];
  let pipelineReads = 0;
  let posts = 0;
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
      if (call.method === "POST") {
        const answer = onPost(call, posts++);
        if (answer) return json(answer.body, answer.status ?? 200);
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

  it("needs the switch and a deciding role", () => {
    expect(mayRunCaseCommands(session("sales"))).toBe(true);
    expect(mayRunCaseCommands(session("admin"))).toBe(true);
    expect(mayRunCaseCommands(session("viewer"))).toBe(false);
    expect(mayRunCaseCommands(session("sales", { cases: false }))).toBe(false);
    expect(mayRunCaseCommands({ kind: "loading" })).toBe(false);
  });
});

describe("case drawer actions", () => {
  async function openDrawer(s: AuthSessionState) {
    render(withSession(s, <PipelinePage initialOpportunityId={CASE} />));
    return screen.findByRole("dialog");
  }

  it("keeps «Cambiar etapa» and «Marcar ganada» disabled when the case commands are off", async () => {
    stubApi({ pipelines: [page([card()])] });
    const dialog = await openDrawer(session("sales", { cases: false }));
    expect(within(dialog).getByRole("button", { name: "Cambiar etapa" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Marcar ganada" })).toBeDisabled();
    // add-note is a CRM authoring command: its own switch is on, so the follow-up is offered.
    expect(within(dialog).getByRole("button", { name: "Registrar seguimiento" })).toBeEnabled();
  });

  it("keeps every write disabled for a viewer", async () => {
    stubApi({ pipelines: [page([card()])] });
    const dialog = await openDrawer(session("viewer"));
    for (const name of ["Cambiar etapa", "Marcar ganada", "Registrar seguimiento", "Nueva revisión"]) {
      expect(within(dialog).getByRole("button", { name })).toBeDisabled();
    }
  });

  it("changes stage with the shown version and refetches the pipeline", async () => {
    const calls = stubApi({
      pipelines: [page([card()]), page([card({ stage: "lost", version: 6, closed_at: "2026-10-05T00:00:00Z" })])],
      onPost: (call) =>
        call.path === "/v2/commands/advance-case-stage" ? { body: receipt("advance_case_stage", "lost", 6, "aaaaaaaa-0001") } : undefined,
    });
    const dialog = await openDrawer(session("sales"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cambiar etapa" }));
    const form = within(dialog).getByRole("form", { name: "Cambiar etapa" });
    const options = within(form).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Negociando", "Calificada", "Abandonada", "Perdida"]);
    fireEvent.change(within(form).getByLabelText(/Nueva etapa/), { target: { value: "lost" } });
    const submit = within(form).getByRole("button", { name: "Cambiar etapa" });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "Compraron a otro proveedor" } });
    expect(submit).toBeDisabled(); // closing needs a motive
    fireEvent.change(within(form).getByLabelText(/Motivo de cierre/), { target: { value: "precio" } });
    fireEvent.click(submit);

    expect(await within(dialog).findByText(/Etapa → Perdida: registrado/)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/v2/commands/advance-case-stage");
    expect(post.key).toBeTruthy();
    expect(post.body).toEqual({
      opportunity_id: CASE,
      opportunity_version: 5,
      stage: "lost",
      close_reason: "precio",
      note: "Compraron a otro proveedor",
    });
    await waitFor(() => expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2));
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Cambiar etapa" })).toBeDisabled());
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
    fireEvent.click(within(dialog).getByRole("button", { name: "Cambiar etapa" }));
    const form = within(dialog).getByRole("form", { name: "Cambiar etapa" });
    fireEvent.change(within(form).getByLabelText(/^Nota/), { target: { value: "avanza" } });
    fireEvent.click(within(form).getByRole("button", { name: "Cambiar etapa" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Otra persona cambió este caso");
    expect(within(dialog).queryByText(/this case changed/)).not.toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path === "/v2/workspace/pipeline")).toHaveLength(2));
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
    expect(outcome).toHaveTextContent("1/2 · Etapa → Negociando: registrado · recibo bbbbbbbb");
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
    expect(outcome).toHaveTextContent("1/2 · Etapa → Negociando: registrado");
    expect(outcome).toHaveTextContent("2/2 · Ganada: no se registró — Sólo se gana contra una revisión enviada y vigente");
    expect(outcome).toHaveTextContent("El caso quedó en «Negociando».");
  });

  it("disables «Marcar ganada» without a sent, current revision or outside quoting/negotiating", async () => {
    stubApi({ pipelines: [page([card({ stage: "qualified" })])] });
    const dialog = await openDrawer(session("sales"));
    expect(within(dialog).getByRole("button", { name: "Marcar ganada" })).toBeDisabled();
    expect(within(dialog).getByText("Se marca ganada desde «Cotizando» o «Negociando»")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Cambiar etapa" })).toBeEnabled();
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
});
