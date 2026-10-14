import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearResourceCache } from "../useResource";
import { AutomaticActionsPanel } from "./AutomaticActionsPanel";

// Every value is invented.
const RECEIPT_A = "00000000-0000-4000-8000-0000000000a1";
const RECEIPT_B = "00000000-0000-4000-8000-0000000000b2";
const ITEMS = [
  { receipt_id: RECEIPT_A, applied_at: "2026-10-14T13:00:00+00:00", rule_id: "R11", kind: "opened", source: "forward",
    reasons: ["solicitud reenviada de Ana Prueba: Gradillas", "sin institución hasta confirmarla"],
    case_id: "caso-1", case_title: "Solicitud — Gradillas", quote_number: null, what: "Gradillas · Ana Prueba" },
  { receipt_id: RECEIPT_B, applied_at: "2026-10-14T12:00:00+00:00", rule_id: "R2", kind: "filed", source: "email",
    reasons: ["el número 09259-26 (nombrado en el texto del correo) ya es una cotización del caso"],
    case_id: "caso-2", case_title: "Universidad Ficticia — balanzas", quote_number: "09259-26",
    what: "Aviso de transferencia de fondos" },
];

const posts: { path: string; body: Record<string, unknown>; key: string | null }[] = [];
function stub(items: unknown[] = ITEMS) {
  posts.length = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200,
      headers: { "Content-Type": "application/json" } }));
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)),
        key: new Headers(init.headers).get("Idempotency-Key") });
      return json({ undoes_receipt_id: RECEIPT_A, command_receipt_id: "r", replayed: false });
    }
    if (path === "/v2/workspace/automatic-actions") return json({ items });
    return Promise.resolve(new Response("{}", { status: 404 }));
  }));
}

function session(role: string): AuthSessionState {
  return { kind: "signed_in", method: "google_profile",
    operator: { operatorId: "op", email: "v@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true, crmAuthoringEnabled: true } as AuthSessionState;
}

function renderPanel(role = "sales") {
  const navigate = vi.fn();
  const onChanged = vi.fn();
  render(<AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
    <AutomaticActionsPanel navigate={navigate} onChanged={onChanged} />
  </AuthSessionContext.Provider>);
  return { navigate, onChanged };
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("Hecho automáticamente", () => {
  it("says what the system opened and filed, and why", async () => {
    stub();
    renderPanel();
    const rows = await screen.findAllByTestId("automatic-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Solicitud abierta");
    expect(rows[0]).toHaveTextContent("Gradillas · Ana Prueba");
    expect(rows[1]).toHaveTextContent("Archivado en el caso");
    expect(rows[1]).toHaveTextContent("09259-26");
    expect(rows[1]).toHaveTextContent("nombrado en el texto del correo");
  });

  it("opens the case, and «Quitar» undoes the action with a key and the row leaves", async () => {
    stub();
    const { navigate, onChanged } = renderPanel();
    const [first] = await screen.findAllByTestId("automatic-row");
    fireEvent.click(within(first).getByRole("button", { name: "Abrir caso" }));
    expect(navigate).toHaveBeenCalledWith("oportunidades", "caso-1");
    fireEvent.click(within(first).getByRole("button", { name: "Quitar" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/v2/commands/remove-automatic-action");
    expect(posts[0].body).toEqual({ receipt_id: RECEIPT_A });
    expect(posts[0].key).toBeTruthy();
    await waitFor(() => expect(screen.getAllByTestId("automatic-row")).toHaveLength(1));
    expect(onChanged).toHaveBeenCalled();
  });

  it("a viewer sees the rows without «Quitar», and nothing renders when the system did nothing", async () => {
    stub();
    renderPanel("viewer");
    const rows = await screen.findAllByTestId("automatic-row");
    expect(within(rows[0]).queryByRole("button", { name: "Quitar" })).toBeNull();
    clearResourceCache();
    document.body.innerHTML = "";
    stub([]);
    renderPanel();
    await waitFor(() => expect(screen.queryByText("Hecho automáticamente")).toBeNull());
  });
});
