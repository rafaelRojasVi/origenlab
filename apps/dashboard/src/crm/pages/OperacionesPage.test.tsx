import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OperationsPayload } from "../operations";
import type { DatosTab } from "../crmRoute";
import { CRM_NAV, parseCrmHash } from "../crmRoute";
import { OperacionesPage } from "./OperacionesPage";

// Every value below is invented; the repository is public.
const PAYLOAD: OperationsPayload = {
  generated_at: "2026-10-13T12:00:00+00:00",
  actions_today: 1,
  counts: { cases_without_institution: 2, institutions_por_confirmar: 3, sent_quotes_without_case: 1,
            sent_quotes_unrecorded_on_case: 1 },
  sent_quotes_without_case: [
    { source_record_id: "s-1", quote_number: "09877-26", sent_at: "2026-10-01T15:00:00+00:00", thread_id: "hilo-1",
      suggested_institution: "Instituto Ficticio (IF)" },
  ],
  sent_quotes_unrecorded_on_case: [
    { source_record_id: "s-2", quote_number: "09251-26", sent_at: "2026-09-30T16:57:00+00:00", thread_id: "hilo-2",
      case_id: "caso-9", case_title: "Cotización 09176-26 — Empresa Ficticia", case_version: 4,
      organization_name: "Empresa Ficticia" },
  ],
  cards: [
    { key: "correo", title: "Correo", status: "ok", summary: "Última lectura hace 1 min · 4 correos hoy",
      at: "2026-10-13T11:59:00+00:00", facts: [{ label: "Correos hoy", value: 4 }],
      events: [{ at: "2026-10-13T11:58:00+00:00", text: "Correo recibido" }] },
    { key: "clasificacion", title: "Clasificación", status: "warn", summary: "2 correos esperando, el más antiguo hace 16 min",
      at: null, facts: [], events: [] },
    { key: "drive", title: "Archivo Drive", status: "bad", summary: "Algo falló", at: null, facts: [], events: [] },
    { key: "version", title: "Versión", status: "info", summary: "En producción: abc1234", at: null,
      facts: [{ label: "GitHub", value: "https://github.com/rafaelRojasVi/origenlab/commit/abc1234" }], events: [] },
  ],
  actions: [
    // 01:30 UTC on the 14th is still the 13th in Chile: the API's `today` flag decides, not the date string.
    { receipt_id: "r-1", at: "2026-10-14T01:30:00+00:00", rule_id: "R1", automatic: true, today: true, case_id: "c-1",
      case_title: "Caso Ficticio", quote_number: null, undone: false },
  ],
};

let completed: Record<string, unknown>[] = [];

function respond(body: unknown = PAYLOAD) {
  completed = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    if (path === "/v2/commands/complete-case-from-sent-quote") {
      completed.push(JSON.parse(String((init as RequestInit | undefined)?.body ?? "{}")));
      return Promise.resolve(new Response(JSON.stringify({ command: "complete_case_from_sent_quote", opportunity_id: "caso-nuevo",
        opportunity_version: 1, idempotency_key: "k", command_receipt_id: "r", replayed: false }),
        { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (path === "/v2/workspace/operations") {
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return Promise.resolve(new Response("{}", { status: 404 }));
  }));
}

afterEach(() => vi.unstubAllGlobals());

function renderOps(role: "admin" | "sales" | "viewer" = "sales", tab?: DatosTab, navigate = vi.fn()) {
  const session = { kind: "signed_in", method: "google_session", caseCommandsEnabled: true,
    operator: { operatorId: "op-1", email: "op@ejemplo.invalid", displayName: "Operadora", role } } as AuthSessionState;
  return render(
    <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
      <OperacionesPage navigate={navigate} tab={tab} />
    </AuthSessionContext.Provider>,
  );
}

describe("Operaciones", () => {
  it("is in the sidebar for everyone, as «Operaciones»", () => {
    const item = CRM_NAV.find((n) => n.id === "datos");
    expect(item?.label).toBe("Operaciones");
    expect(item?.adminOnly).toBeFalsy();
  });

  it("shows each card with its state in words, and its activity on demand", async () => {
    respond();
    renderOps();
    expect(await screen.findByText("Última lectura hace 1 min · 4 correos hoy")).toBeInTheDocument();
    expect(screen.getByTestId("ops-card-correo")).toHaveTextContent("OK");
    expect(screen.getByTestId("ops-card-clasificacion")).toHaveTextContent("Atención");
    expect(screen.getByTestId("ops-card-drive")).toHaveTextContent("Falla");
    expect(screen.queryByText("Correo recibido")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Ver actividad de Correo/ }));
    expect(screen.getByText("Correo recibido")).toBeInTheDocument();
  });

  it("lists what the system did, with Deshacer only for an admin", async () => {
    respond();
    renderOps("sales");
    fireEvent.click(await screen.findByRole("button", { name: "Lo que hizo el sistema" }));
    expect(screen.getByText("Vinculó un correo al caso «Caso Ficticio» — mismo hilo de Gmail (R1)")).toBeInTheDocument();
    const undo = screen.getByRole("button", { name: /Deshacer/ });
    expect(undo).toBeDisabled();
    expect(undo).toHaveAttribute("title", "Solo administración");
    expect(screen.getByText(/automático/)).toBeInTheDocument();
  });

  it("explains every rule in Spanish and says which act alone", async () => {
    respond();
    renderOps();
    fireEvent.click(await screen.findByRole("button", { name: "Reglas" }));
    expect(screen.getAllByTestId(/^rule-R\d+$/)).toHaveLength(11);
    expect(screen.getByTestId("rule-R1")).toHaveTextContent("Automática");
    expect(screen.getByTestId("rule-R3")).toHaveTextContent("Con «Aplicar»");
    expect(screen.getByTestId("rule-R1")).toHaveTextContent("hoy: 1");
  });

  it("counts what waits for a person", async () => {
    respond();
    renderOps();
    fireEvent.click(await screen.findByRole("button", { name: "Por decidir" }));
    expect(screen.getByText(/2 casos sin institución/)).toBeInTheDocument();
    expect(screen.getByText(/3 instituciones «por confirmar»/)).toBeInTheDocument();
  });

  it("keeps the technical views under Más, for an admin only", async () => {
    respond();
    renderOps("sales");
    fireEvent.click(await screen.findByRole("button", { name: "Más" }));
    expect(screen.getByRole("button", { name: "Proveedores" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Técnico" })).not.toBeInTheDocument();
  });

  it("an old Datos link still lands on its view under Más", async () => {
    expect(parseCrmHash("#/crm/datos/evidencia")).toEqual({ section: "datos", id: null, tab: "evidencia" });
    respond();
    renderOps("admin", "proveedores");
    await waitFor(() => expect(screen.getByRole("button", { name: "Proveedores" })).toHaveAttribute("aria-pressed", "true"));
  });
});


describe("Operaciones · cotizaciones enviadas sin caso", () => {
  it("offers «Completar…» with the institution the PDF names, and goes to the case it completed", async () => {
    respond();
    const navigate = vi.fn();
    renderOps("sales", undefined, navigate);
    fireEvent.click(await screen.findByRole("button", { name: "Por decidir" }));
    expect(screen.getByText(/Cotización 09877-26/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Completar la cotización 09877-26" }));
    expect(screen.getByLabelText("Institución")).toHaveValue("Instituto Ficticio (IF)");
    fireEvent.click(screen.getByRole("button", { name: "Completar" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("oportunidades", "caso-nuevo"));
    expect(completed[0]).toMatchObject({ source_record_id: "s-1", new_organization_name: "Instituto Ficticio (IF)" });
  });

  it("lists a quote sent on a case's email but never recorded, and records it on that case", async () => {
    respond();
    const navigate = vi.fn();
    renderOps("sales", undefined, navigate);
    fireEvent.click(await screen.findByRole("button", { name: "Por decidir" }));
    expect(screen.getByText(/Cotización 09251-26/).closest("li")).toHaveTextContent("en Cotización 09176-26 — Empresa Ficticia");
    fireEvent.click(screen.getByRole("button", { name: "Registrar la cotización 09251-26 en su caso" }));
    expect(screen.getByLabelText("Institución")).toHaveValue("Empresa Ficticia");
    fireEvent.click(screen.getByRole("button", { name: "Completar" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("oportunidades", "caso-nuevo"));
    expect(completed[0]).toMatchObject({ source_record_id: "s-2", opportunity_id: "caso-9", opportunity_version: 4 });
  });

  it("does not offer it to a viewer", async () => {
    respond();
    renderOps("viewer");
    fireEvent.click(await screen.findByRole("button", { name: "Por decidir" }));
    expect(screen.getByText(/Cotización 09877-26/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Completar/ })).not.toBeInTheDocument();
  });
});
