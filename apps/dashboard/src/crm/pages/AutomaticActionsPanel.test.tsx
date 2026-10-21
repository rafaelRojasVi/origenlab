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
    expect(screen.getByText(/1 solicitud abierta y 1 correo archivado en su caso en los últimos dos días/)).toBeInTheDocument();
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

  it("a Cotizador quote marked sent (R16) says so and has no «Quitar»", async () => {
    stub([{ receipt_id: RECEIPT_B, applied_at: "2026-10-21T12:00:00+00:00", rule_id: "R16", kind: "filed",
      source: "email", reasons: ["el correo está en el caso", "contacto@ envió el PDF de la cotización 09876A-26"],
      case_id: "caso-2", case_title: "Universidad Ficticia — balanzas", quote_number: "09876A-26",
      what: "Cotización balanzas" }]);
    renderPanel("admin");
    const [row] = await screen.findAllByTestId("automatic-row");
    expect(row).toHaveTextContent("Cotización enviada");
    expect(within(row).queryByRole("button", { name: "Quitar" })).toBeNull();
    expect(within(row).getByRole("button", { name: "Abrir caso" })).toBeInTheDocument();
  });
});

describe("Hecho automáticamente, only what is news", () => {
  const row = (n: number, over: Record<string, unknown>) => ({
    receipt_id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`, applied_at: "2026-10-14T13:00:00+00:00",
    rule_id: "R2", kind: "filed", source: "email", reasons: ["motivo"], case_id: `caso-${n}`,
    case_title: `Caso ${n}`, quote_number: null, what: `Correo ${n}`, ...over });

  it("leaves out a reply filed on its own thread, groups emails filed on one case, and shows five rows", async () => {
    stub([
      row(1, { rule_id: "R1", what: "Re: hilo conocido" }),
      row(2, { case_id: "caso-g", case_title: "Caso ganado", what: "Transferencia 1" }),
      row(3, { case_id: "caso-g", case_title: "Caso ganado", what: "Transferencia 2" }),
      row(4, { case_id: "caso-g", case_title: "Caso ganado", what: "Transferencia 3" }),
      ...[5, 6, 7, 8, 9, 10].map((n) => row(n, { kind: "opened", rule_id: "R7" })),
    ]);
    renderPanel();
    const rows = await screen.findAllByTestId("automatic-row");
    expect(rows).toHaveLength(5);
    expect(screen.queryByText("Re: hilo conocido")).toBeNull();
    const grouped = rows.find((r) => r.textContent?.includes("Caso ganado"))!;
    expect(grouped).toHaveTextContent("3 correos archivados");
    fireEvent.click(screen.getByRole("button", { name: "Ver 2 más" }));
    expect(screen.getAllByTestId("automatic-row")).toHaveLength(7);
  });

  it("«Quitar» on a grouped row undoes every email in it", async () => {
    stub([row(2, { case_id: "caso-g", what: "T1" }), row(3, { case_id: "caso-g", what: "T2" })]);
    renderPanel();
    const [grouped] = await screen.findAllByTestId("automatic-row");
    fireEvent.click(within(grouped).getByRole("button", { name: "Quitar" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts.map((p) => p.body.receipt_id).sort()).toEqual([row(2, {}).receipt_id, row(3, {}).receipt_id]);
  });

  it("renders nothing when the only actions were replies on known threads", async () => {
    stub([row(1, { rule_id: "R1" })]);
    renderPanel();
    await waitFor(() => expect(screen.queryByTestId("automatic-row")).toBeNull());
    expect(screen.queryByText("Hecho automáticamente")).toBeNull();
  });
});
