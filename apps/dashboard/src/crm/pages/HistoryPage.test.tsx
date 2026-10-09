import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { HistoryPage } from "./HistoryPage";

// Every value is invented; the repository is public.
const HISTORY = {
  items: [
    { receipt_id: "r2", at: "2026-10-09T15:00:00+00:00", operator: "Tatiana", action: "Marcó ganada · 01253-26",
      case: { opportunity_id: "11111111-1111-4111-8111-111111111111", title: "Pipetas Ficticias" }, organization: null,
      undo: null, undone_at: null },
    { receipt_id: "r1", at: "2026-10-09T14:00:00+00:00", operator: "Rafael", action: "Acción automática del correo",
      case: { opportunity_id: "22222222-2222-4222-8222-222222222222", title: "Balanza Ficticia" }, organization: null,
      undo: { kind: "mail_rule", receipt_id: "r1" }, undone_at: null },
    { receipt_id: "r0", at: "2026-10-08T14:00:00+00:00", operator: "Rafael", action: "Confirmó una institución",
      case: null, organization: { organization_id: "g1", name: "Universidad Ficticia" }, undo: null, undone_at: null },
  ],
};

function respond() {
  const calls: { path: string; method: string }[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    calls.push({ path, method: init?.method ?? "GET" });
    const body = path === "/v2/workspace/history" ? HISTORY : { undoes_receipt_id: "r1" };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  }));
  return calls;
}

function asRole(role: "admin" | "sales"): AuthSessionState {
  return { kind: "signed_in", method: "google_session",
    operator: { operatorId: "op-1", email: "op@ejemplo.invalid", displayName: "Operadora", role } } as AuthSessionState;
}

function renderHistory(role: "admin" | "sales") {
  return render(
    <AuthSessionContext.Provider value={{ session: asRole(role), signOut: async () => true }}>
      <HistoryPage navigate={() => undefined} />
    </AuthSessionContext.Provider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Historial", () => {
  it("lists who decided what, on which case or institution, newest first", async () => {
    respond();
    renderHistory("sales");
    const rows = await screen.findAllByTestId("history-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Tatiana");
    expect(rows[0]).toHaveTextContent("Marcó ganada · 01253-26");
    expect(within(rows[0]).getByRole("button", { name: "Pipetas Ficticias" })).toBeInTheDocument();
    expect(rows[2]).toHaveTextContent("Universidad Ficticia");
    expect(document.body.textContent).not.toMatch(/@/);
  });

  it("offers «Deshacer» on an automatic mail action to an admin only, with a reason", async () => {
    const calls = respond();
    const { unmount } = renderHistory("sales");
    await screen.findAllByTestId("history-row");
    expect(screen.queryByRole("button", { name: "Deshacer" })).not.toBeInTheDocument();
    unmount();

    renderHistory("admin");
    fireEvent.click(await screen.findByRole("button", { name: "Deshacer" }));
    fireEvent.change(screen.getByLabelText("Motivo"), { target: { value: "No correspondía" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v2/commands/undo-mail-rule-action")).toBe(true));
  });
});
