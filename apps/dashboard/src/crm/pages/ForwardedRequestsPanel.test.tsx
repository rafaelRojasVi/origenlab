import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearResourceCache } from "../useResource";
import { ForwardedRequestsPanel } from "./ForwardedRequestsPanel";

// Every value is invented.
const ITEMS = [
  { id: "f-2", ids: ["f-1", "f-2"], received_at: "2026-10-13T11:04:00+00:00",
    customer: { name: "Ana Prueba", email: "ana@laboratorio-ficticio.cl", company: "Laboratorio Ficticio EIRL",
      rut: "11.111.111-1", phone: "900000001" },
    requested_item: "Tabletas de verificación para durómetro", relay: "marketplace",
    origins: ["labdelivery", "origenlab"], gmail_url: "https://mail.google.com/mail/#all/g2", institution: null },
  { id: "f-9", ids: ["f-9"], received_at: "2026-10-05T09:00:00+00:00",
    customer: { name: null, email: "pedro@universidad-ficticia.cl", company: null, rut: null, phone: null },
    requested_item: "Gradillas", relay: null, origins: ["labdelivery"], gmail_url: null,
    institution: { id: "org-1", name: "Universidad Ficticia", version: 3 } },
];

const posts: { path: string; body: Record<string, unknown> }[] = [];
function stub(items: unknown[] = ITEMS) {
  posts.length = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200,
      headers: { "Content-Type": "application/json" } }));
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      return json({ command: "x", opportunity_id: "caso-nuevo", opportunity_version: 1, command_receipt_id: "r",
        replayed: false });
    }
    if (path === "/v2/workspace/forwarded-requests") return json({ items });
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
    <ForwardedRequestsPanel navigate={navigate} onChanged={onChanged} />
  </AuthSessionContext.Provider>);
  return { navigate, onChanged };
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("Solicitudes reenviadas", () => {
  it("lists each forwarded request once with who asked, what, and where it came from", async () => {
    stub();
    renderPanel();
    const rows = await screen.findAllByTestId("forwarded-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Ana Prueba");
    expect(rows[0]).toHaveTextContent("Laboratorio Ficticio EIRL");
    expect(rows[0]).toHaveTextContent("Tabletas de verificación para durómetro");
    expect(within(rows[0]).getByRole("link", { name: /Abrir correo/ })).toBeInTheDocument();
    // A forward that only came through the Labdelivery mailbox has no email to open.
    expect(within(rows[1]).queryByRole("link", { name: /Abrir correo/ })).toBeNull();
  });

  it("«Crear solicitud» proposes the company as a new institution, then goes to the case", async () => {
    stub();
    const { navigate, onChanged } = renderPanel();
    const [row] = await screen.findAllByTestId("forwarded-row");
    fireEvent.click(within(row).getByRole("button", { name: "Crear solicitud" }));
    expect(within(row).getByLabelText("Institución")).toHaveValue("Laboratorio Ficticio EIRL");
    fireEvent.click(within(row).getByRole("button", { name: "Crear" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("oportunidades", "caso-nuevo"));
    expect(posts[0]).toMatchObject({ path: "/v2/commands/create-request-from-forward",
      body: { forwarded_request_id: "f-2", new_organization_name: "Laboratorio Ficticio EIRL" } });
    expect(onChanged).toHaveBeenCalled();
  });

  it("uses the institution the CRM already knows for the customer's domain", async () => {
    stub();
    renderPanel();
    const rows = await screen.findAllByTestId("forwarded-row");
    fireEvent.click(within(rows[1]).getByRole("button", { name: "Crear solicitud" }));
    expect(within(rows[1]).getByText(/Universidad Ficticia/)).toBeInTheDocument();
    fireEvent.click(within(rows[1]).getByRole("button", { name: "Crear" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ forwarded_request_id: "f-9", organization_id: "org-1", organization_version: 3 });
  });

  it("«Descartar» takes it off the list", async () => {
    stub();
    renderPanel();
    const [row] = await screen.findAllByTestId("forwarded-row");
    fireEvent.click(within(row).getByRole("button", { name: "Descartar" }));
    await waitFor(() => expect(posts[0]?.path).toBe("/v2/commands/dismiss-forwarded-request"));
    await waitFor(() => expect(screen.getAllByTestId("forwarded-row")).toHaveLength(1));
  });

  it("shows a viewer the list without any button that writes, and nothing at all when empty", async () => {
    stub();
    renderPanel("viewer");
    await screen.findAllByTestId("forwarded-row");
    expect(screen.queryByRole("button", { name: /Crear solicitud|Descartar/ })).toBeNull();
  });

  it("renders nothing when there is nothing forwarded", async () => {
    stub([]);
    const { container } = render(<AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
      <ForwardedRequestsPanel navigate={vi.fn()} onChanged={vi.fn()} />
    </AuthSessionContext.Provider>);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
