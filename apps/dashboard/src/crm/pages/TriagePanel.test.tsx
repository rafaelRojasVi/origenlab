import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData, RevisionCard } from "../crmTypes";
import type { TriageReading } from "../triage";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { TriagePanel } from "./TriagePanel";

// Every value below is invented; the repository is public.

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

function revision(no: number, superseded: number | null = null): RevisionCard {
  return {
    revision_id: `r-${no}`, revision_no: no, status: "sent", origin: "gmail_capture", sent_at: `2026-10-0${no}T10:00:00Z`,
    superseded_by_revision_no: superseded, is_active: superseded === null, document: null, gmail: null, drive: null,
    quote_number: "01253-26",
  };
}

function card(over: Partial<OpportunityCardData> = {}, revisions: RevisionCard[] = [revision(1)]): OpportunityCardData {
  return {
    opportunity_id: "o-1", title: "Pipetas", stage: "quoting", version: 4, created_at: null, updated_at: null, closed_at: null,
    close_reason: null, organization: { organization_id: "org-1", name: "Laboratorio Ejemplo", confirmation: "confirmed" },
    other_organizations: [], contact: null,
    quotes: [{ quote_id: "q-1", quote_number: "01253-26", number_origin: "printed", revisions }],
    quote_numbers: ["01253-26"], revision_count: revisions.length, latest_revision: revisions[0] ?? null,
    drive_folder: null, attention: [], status: "ok", next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  } as OpportunityCardData;
}

const base: Omit<TriageReading, "assertion_id" | "subject" | "sender" | "sent_at" | "thread_id"> = {
  source_record_id: "src-1", version: "v", class: "quote_request", reasons: [], model_state: "off", stage: null,
  intent: null, urgency: null, summary_es: null, needs_reply: null, requester_organization: null, products: [],
  candidates: [], cases: [], transitions: [], review: null, sender_is_supplier: false,
};

function reading(id: string, over: Partial<TriageReading> = {}): TriageReading {
  return {
    ...base, assertion_id: id, subject: `Asunto ${id}`, sender: `${id}@cliente.example`, sent_at: "2026-10-09T10:00:00Z",
    thread_id: `t-${id}`, ...over,
  };
}

interface Call { path: string; body: Record<string, unknown> | null; key: string | null }

function stub(readings: TriageReading[], refuse: Record<string, number> = {}): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      const key = init?.headers ? (new Headers(init.headers).get("Idempotency-Key")) : null;
      calls.push({ path, body, key });
      const json = (b: unknown, status = 200) =>
        Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
      if (refuse[path]) return json({ detail: { code: "refused_in_test" } }, refuse[path]);
      if (path === "/v2/workspace/triage-readings") return json({ status: "pending", items: readings });
      if (path === "/v2/commands/review-triage") return json({ review_id: "rv-1" });
      if (path === "/v2/commands/reopen-commercial-case") {
        return json({ command: "reopen_commercial_case", opportunity_id: "o-reopened", opportunity_version: 5,
                      stage: (body as { stage: string }).stage, reopened_from_opportunity_id: "o-lost" });
      }
      if (path === "/v2/commands/open-commercial-case") {
        return json({ command: "open_commercial_case", opportunity_id: "o-new", opportunity_version: 1, stage: "lead",
          idempotency_key: key, command_receipt_id: "rcpt-open", replayed: false });
      }
      return json({ command: "x", opportunity_id: "o-1", opportunity_version: 5, command_receipt_id: "rcpt-1", replayed: false });
    }),
  );
  return calls;
}

function renderPanel(cards: OpportunityCardData[], role = "sales") {
  const onCaseChanged = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <TriagePanel cards={cards} onCaseChanged={onCaseChanged} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
  return onCaseChanged;
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

const PO_CASE = { opportunity_id: "o-1", title: "Pipetas", stage: "quoting", version: 4 };

describe("«Correos sin caso» · ¿marcar ganada?", () => {
  it("shows a purchase order on a winnable case as a proposal and wins the case on «Marcar ganada»", async () => {
    const calls = stub([reading("oc", { class: "purchase_order", cases: [PO_CASE] })]);
    const onCaseChanged = renderPanel([card()]);
    const row = await screen.findByTestId("won-proposal");
    expect(within(row).getByText(/Laboratorio Ejemplo · ¿marcar ganada 01253-26 r1\?/)).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Marcar ganada" }));
    await waitFor(() => expect(onCaseChanged).toHaveBeenCalled());
    const posts = calls.filter((c) => c.path.startsWith("/v2/commands/"));
    expect(posts.map((c) => c.path)).toEqual([
      "/v2/commands/review-triage",
      "/v2/commands/advance-case-stage",
      "/v2/commands/record-case-won",
    ]);
    expect(posts[0].body).toMatchObject({ assertion_id: "oc", verdict: "approved" });
    expect(posts[1].body).toMatchObject({ opportunity_id: "o-1", opportunity_version: 4, stage: "negotiating" });
    expect(posts[2].body).toMatchObject({ opportunity_id: "o-1", opportunity_version: 5, quote_id: "q-1", revision_no: 1 });
    expect(posts[1].key).toBeTruthy();
    expect(posts[2].key).toBeTruthy();
    expect(posts[1].key).not.toBe(posts[2].key);
    expect(await screen.findByText(/ganada contra 01253-26 r1/)).toBeInTheDocument();
  });

  it("asks which revision when two were sent, and wins against the chosen one", async () => {
    const calls = stub([reading("oc", { class: "purchase_order", cases: [{ ...PO_CASE, stage: "negotiating" }] })]);
    renderPanel([card({ stage: "negotiating" }, [revision(1), revision(2)])]);
    const row = await screen.findByTestId("won-proposal");
    const win = within(row).getByRole("button", { name: "Marcar ganada" });
    expect(win).toBeDisabled();
    fireEvent.click(within(row).getByLabelText(/01253-26 r1/));
    expect(win).toBeEnabled();
    fireEvent.click(win);
    await waitFor(() => expect(calls.some((c) => c.path === "/v2/commands/record-case-won")).toBe(true));
    const won = calls.find((c) => c.path === "/v2/commands/record-case-won");
    expect(won?.body).toMatchObject({ opportunity_version: 4, revision_no: 1 });
    expect(calls.some((c) => c.path === "/v2/commands/advance-case-stage")).toBe(false);
  });

  it("«Descartar» on the proposal records a rejected verdict and nothing else", async () => {
    const calls = stub([reading("oc", { class: "purchase_order", cases: [PO_CASE] })]);
    const onCaseChanged = renderPanel([card()]);
    const row = await screen.findByTestId("won-proposal");
    fireEvent.click(within(row).getByRole("button", { name: "Descartar" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v2/commands/review-triage")).toBe(true));
    expect(calls.filter((c) => c.path.startsWith("/v2/commands/")).map((c) => c.path)).toEqual(["/v2/commands/review-triage"]);
    expect(calls.find((c) => c.path === "/v2/commands/review-triage")?.body).toMatchObject({ verdict: "rejected" });
    expect(onCaseChanged).not.toHaveBeenCalled();
  });

  it("says what was done when the win is refused after the verdict, and does not record the verdict twice on retry", async () => {
    const calls = stub([reading("oc", { class: "purchase_order", cases: [PO_CASE] })], { "/v2/commands/record-case-won": 409 });
    renderPanel([card()]);
    const row = await screen.findByTestId("won-proposal");
    fireEvent.click(within(row).getByRole("button", { name: "Marcar ganada" }));
    expect(await screen.findByText(/no se pudo marcar ganada/)).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Marcar ganada" }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/v2/commands/record-case-won")).toHaveLength(2));
    expect(calls.filter((c) => c.path === "/v2/commands/review-triage")).toHaveLength(1);
    const wons = calls.filter((c) => c.path === "/v2/commands/record-case-won");
    expect(wons[0].key).toBe(wons[1].key);
    const advances = calls.filter((c) => c.path === "/v2/commands/advance-case-stage");
    expect(advances).toHaveLength(2);
    expect(advances[0].key).toBe(advances[1].key);
  });

  it("a purchase order on a won case is only counted, and a viewer gets no buttons", async () => {
    stub([reading("oc", { class: "purchase_order", cases: [{ ...PO_CASE, stage: "won" }] })]);
    renderPanel([card({ stage: "won" })], "viewer");
    expect(await screen.findByText("Ningún correo nuevo fuera de un caso.")).toBeInTheDocument();
    expect(screen.getByText(/No se muestran: 1 en un caso/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Marcar ganada" })).not.toBeInTheDocument();
  });
});

describe("«Correos sin caso» · Abrir caso", () => {
  it("opens the case from the email, then records the verdict, and names the case after the subject", async () => {
    const calls = stub([reading("req", { subject: "RE: Solicitud cotización balanza", source_record_id: "src-req" })]);
    const onCaseChanged = renderPanel([]);
    fireEvent.click(await screen.findByRole("button", { name: "Abrir caso" }));
    await waitFor(() => expect(onCaseChanged).toHaveBeenCalled());
    const posts = calls.filter((c) => c.path.startsWith("/v2/commands/"));
    expect(posts.map((c) => c.path)).toEqual(["/v2/commands/open-commercial-case", "/v2/commands/review-triage"]);
    expect(posts[0].body).toEqual({
      title: "Solicitud cotización balanza",
      origin_source_record_id: "src-req",
      note: "Abierto desde Hoy: solicitud de cotización recibida por correo",
    });
    expect(posts[0].key).toBeTruthy();
    expect(posts[1].body).toMatchObject({ assertion_id: "req", verdict: "approved" });
    expect(await screen.findByText(/abierto en «Solicitada»/)).toBeInTheDocument();
  });

  it("offers «Abrir caso» only on quote requests: another person's email keeps Revisar and Descartar", async () => {
    stub([reading("otro", { class: "business_other" })]);
    renderPanel([]);
    expect(await screen.findByRole("button", { name: "Descartar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revisar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Abrir caso" })).not.toBeInTheDocument();
  });

  it("a reply on a closed case asks «¿Reabrir?», reopens it as a new case at «Conversación», then records the verdict", async () => {
    const lostCase = { opportunity_id: "o-lost", title: "Pipetas", stage: "lost", version: 3 };
    const calls = stub([reading("vuelve", { class: "business_other", cases: [lostCase], source_record_id: "src-vuelve" })]);
    const onCaseChanged = renderPanel([card({ opportunity_id: "o-lost", stage: "lost", closed_at: "2026-10-01T10:00:00Z",
                                              close_reason: "compraron a otro", requesting_institution_confirmation: "confirmed" })]);
    const row = await screen.findByTestId("reopen-proposal");
    expect(row).toHaveTextContent("Laboratorio Ejemplo · caso perdido hace");
    expect(row).toHaveTextContent("¿reabrir en «Conversación»?");
    fireEvent.click(within(row).getByRole("button", { name: "Reabrir" }));
    await waitFor(() => expect(onCaseChanged).toHaveBeenCalled());
    const posts = calls.filter((c) => c.path.startsWith("/v2/commands/"));
    expect(posts.map((c) => c.path)).toEqual(["/v2/commands/reopen-commercial-case", "/v2/commands/review-triage"]);
    expect(posts[0].body).toMatchObject({ opportunity_id: "o-lost", origin_source_record_id: "src-vuelve", stage: "negotiating" });
    expect(String(posts[0].body?.note)).toMatch(/^Reabierto desde Hoy/);
    expect(posts[0].key).toBeTruthy();
    expect(posts[1].body).toMatchObject({ assertion_id: "vuelve", verdict: "approved" });
    expect(await screen.findByText(/reabierto como caso nuevo en «Conversación»/)).toBeInTheDocument();
  });

  it("«Descartar» on a reopen proposal records a rejected verdict and nothing else; a refused reopen keeps the row", async () => {
    const lostCase = { opportunity_id: "o-lost", title: "Pipetas", stage: "abandoned", version: 3, closed_at: "2026-05-01T10:00:00Z" };
    const calls = stub([reading("vuelve", { cases: [lostCase] })], { "/v2/commands/reopen-commercial-case": 409 });
    const onCaseChanged = renderPanel([]);
    const row = await screen.findByTestId("reopen-proposal");
    expect(row).toHaveTextContent("caso sin respuesta hace");
    expect(row).toHaveTextContent("¿reabrir en «Solicitada»?");
    fireEvent.click(within(row).getByRole("button", { name: "Reabrir" }));
    expect(await screen.findByText(/No se pudo reabrir el caso/)).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/v2/commands/review-triage")).toBe(false);
    expect(onCaseChanged).not.toHaveBeenCalled();
    fireEvent.click(within(row).getByRole("button", { name: "Descartar" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v2/commands/review-triage")).toBe(true));
    const verdict = calls.find((c) => c.path === "/v2/commands/review-triage")!;
    expect(verdict.body).toMatchObject({ assertion_id: "vuelve", verdict: "rejected" });
  });

  it("when the API refuses to open the case, nothing is reviewed and the row stays", async () => {
    const calls = stub([reading("req")], { "/v2/commands/open-commercial-case": 422 });
    const onCaseChanged = renderPanel([]);
    fireEvent.click(await screen.findByRole("button", { name: "Abrir caso" }));
    expect(await screen.findByText(/No se pudo abrir el caso/)).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/v2/commands/review-triage")).toBe(false);
    expect(onCaseChanged).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Abrir caso" })).toBeInTheDocument();
  });
});
