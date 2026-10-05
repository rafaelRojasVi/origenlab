import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { MailRulesPreview } from "../mailRules";
import { MailRulesPanel } from "./MailRulesPanel";
import { ReviewPage } from "./ReviewPage";

// Every value below is invented; the repository is public.
const PREVIEW: MailRulesPreview = {
  label: "Sistema (correo)",
  commands_enabled: true,
  emails_considered: 4,
  already_applied: 1,
  counts: { R3: { auto: 1 }, R8: { none: 1 }, R1: { proposal: 1 } },
  actions: [
    {
      evidence_id: "e-1", rule_id: "R3", mode: "auto", reasons: ["cotización nueva 01239-26"],
      case_id: null, case_title: "Cliente Ficticio — cotización 01239-26", organization_id: "o-1",
      organization_name: "Cliente Ficticio", proposed_domain: null, quote_number: "01239-26", candidates: [],
      commands: [], subject: "Cotización", sent_at: "2026-10-01T15:00:00+00:00", direction: "outbound",
    },
    {
      evidence_id: "e-2", rule_id: "R8", mode: "none", reasons: ["remitente con correo gratuito"],
      case_id: null, case_title: null, organization_id: null, organization_name: null, proposed_domain: null,
      quote_number: null, candidates: [], commands: [], subject: "Hola", sent_at: null, direction: "inbound",
    },
    {
      evidence_id: "e-3", rule_id: "R1", mode: "proposal", reasons: ["2 casos coinciden"],
      case_id: null, case_title: null, organization_id: null, organization_name: null, proposed_domain: null,
      quote_number: null, candidates: ["c-1", "c-2"], commands: [], subject: "RE: x", sent_at: null, direction: "inbound",
    },
  ],
  applied: [
    {
      receipt_id: "00000000-0000-4000-8000-0000000000aa", applied_at: "2026-10-05T12:00:00+00:00",
      applied_by: "Admin Ficticia", label: "Sistema (correo)", evidence_id: "e-0", rule_id: "R1",
      reasons: ["mismo hilo de Gmail"], case_id: "c-1", case_title: "Caso Ficticio", organization_id: null,
      organization_name: null, quote_number: null, undone: false, undone_at: null, undone_by: null, undo_note: null,
    },
    {
      receipt_id: "00000000-0000-4000-8000-0000000000bb", applied_at: "2026-10-05T11:00:00+00:00",
      applied_by: "Admin Ficticia", label: "Sistema (correo)", evidence_id: "e-9", rule_id: "R6",
      reasons: ["otro proveedor"], case_id: "c-2", case_title: "Otro Caso", organization_id: null,
      organization_name: null, quote_number: null, undone: true, undone_at: "2026-10-05T11:30:00+00:00",
      undone_by: "Otra Persona", undo_note: "no era",
    },
  ],
};

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_session",
    operator: { operatorId: "o1", email: "op@example.test", displayName: "Operadora", role },
  } as AuthSessionState;
}

function withRole(role: string, node: ReactNode) {
  return <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>{node}</AuthSessionContext.Provider>;
}

interface Call {
  path: string;
  method: string;
  body: unknown;
  key: string | null;
}

function respond(routes: Record<string, unknown>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      const headers = new Headers(init?.headers);
      calls.push({ path, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null, key: headers.get("Idempotency-Key") });
      const body = routes[path];
      if (body === undefined) return Promise.resolve(new Response("{}", { status: 404 }));
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("Revisión → Acciones automáticas del correo", () => {
  it("is offered to an admin only", async () => {
    respond({
      "/v2/cockpit/work-queue": { items: [], total: 0, limit: 200, offset: 0 },
      "/v2/workspace/review": { archived_not_in_crm: [], open_assertions: [], ambiguous_organizations: [], drive_configured: true },
    });
    const { unmount } = render(withRole("sales", <ReviewPage navigate={() => undefined} />));
    await screen.findByText("Sin bloqueos en el CRM");
    expect(screen.queryByRole("button", { name: /Acciones automáticas/ })).not.toBeInTheDocument();
    unmount();
    render(withRole("admin", <ReviewPage navigate={() => undefined} />));
    expect(await screen.findByRole("button", { name: /Acciones automáticas/ })).toBeInTheDocument();
    expect(render(withRole("viewer", <MailRulesPanel />)).container).toBeEmptyDOMElement();
  });

  it("shows the planned actions grouped by rule, with reasons, after «Vista previa»", async () => {
    respond({ "/v2/workspace/mail-rules/preview": PREVIEW });
    render(withRole("admin", <MailRulesPanel />));
    expect(await screen.findByText("Caso Ficticio")).toBeInTheDocument();
    expect(screen.queryByText("cotización nueva 01239-26")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Vista previa" }));
    expect(await screen.findByText("cotización nueva 01239-26")).toBeInTheDocument();
    expect(screen.getByText(/R3 · Caso nuevo/)).toBeInTheDocument();
    expect(screen.getByText(/R8 · Queda en Revisión/)).toBeInTheDocument();
    expect(screen.getByText("Cliente Ficticio — cotización 01239-26")).toBeInTheDocument();
    expect(screen.getAllByText("Propuesta").length).toBeGreaterThan(0);
  });

  it("applies with a key and shows what was applied and refused", async () => {
    const calls = respond({
      "/v2/workspace/mail-rules/preview": PREVIEW,
      "/v2/commands/apply-mail-rules": {
        applied: [{ evidence_id: "e-1", rule_id: "R3" }],
        refused: [{ evidence_id: "e-4", rule_id: "R1", code: "case_moved", message: "el caso cambió" }],
        proposals_left_for_review: 1,
        no_rule_applies: 1,
      },
    });
    render(withRole("admin", <MailRulesPanel />));
    fireEvent.click(await screen.findByRole("button", { name: /Aplicar/ }));
    expect(await screen.findByText(/1 aplicada/)).toBeInTheDocument();
    expect(screen.getByText(/el caso cambió/)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.path).toBe("/v2/commands/apply-mail-rules");
    expect(post?.key).toBeTruthy();
    expect(post?.body).toEqual({});
  });

  it("undoes one applied action with a note, and never offers to undo twice", async () => {
    const calls = respond({
      "/v2/workspace/mail-rules/preview": PREVIEW,
      "/v2/commands/undo-mail-rule-action": { undoes_receipt_id: "00000000-0000-4000-8000-0000000000aa" },
    });
    render(withRole("admin", <MailRulesPanel />));
    await screen.findByText("Caso Ficticio");
    expect(screen.getAllByRole("button", { name: "Deshacer" })).toHaveLength(1);
    expect(screen.getByText(/deshecha por Otra Persona/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Deshacer" }));
    const confirm = screen.getByRole("button", { name: "Confirmar" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Motivo"), { target: { value: "no era de este caso" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.path === "/v2/commands/undo-mail-rule-action")).toBe(true));
    const undo = calls.find((c) => c.path === "/v2/commands/undo-mail-rule-action");
    expect(undo?.body).toEqual({ receipt_id: "00000000-0000-4000-8000-0000000000aa", note: "no era de este caso" });
    expect(undo?.key).toBeTruthy();
  });

  it("says so when the commands are not enabled", async () => {
    respond({ "/v2/workspace/mail-rules/preview": { ...PREVIEW, commands_enabled: false } });
    render(withRole("admin", <MailRulesPanel />));
    expect(await screen.findByRole("button", { name: /Aplicar/ })).toBeDisabled();
  });
});
