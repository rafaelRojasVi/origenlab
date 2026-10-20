/**
 * «Datos para licitaciones» in Preferencias: the bidder's data and standard documents. A viewer
 * never sees the card; a save posts `save-bidder-profile` with an Idempotency-Key; the company-only
 * fields show for «Empresa» only; a 409 `bidder_profile_changed` says so. Invented data only.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { BidderPreferences } from "./BidderPreferences";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const refusal = (status: number, outer: string, code: string, message: string) =>
  json({ error: { code: outer, message, details: { code, message }, request_id: "req-1" } }, status);

const EMPTY = { profile: null, history: [], documents: [] };
const SAVED = {
  profile: {
    id: "11111111-1111-4111-8111-111111111111", bidder_kind: "natural_person", legal_name: "Oferente Ejemplo", rut: "61111111-8",
    giro: "Venta de equipos", address: null, comuna: "Santiago", region: null, phone: null, email: null, legal_rep_name: null,
    legal_rep_rut: null, bank_name: null, bank_account_type: "vista", bank_account_number: null, chileproveedores_status: "habil",
    chileproveedores_checked_on: "2026-10-01", is_emt: true, is_local_supplier: false, reason: "Carga inicial", created_at: "2026-10-01T12:00:00Z",
    set_by: "Ventas Ejemplo",
  },
  history: [{ id: "11111111-1111-4111-8111-111111111111", set_by: "Ventas Ejemplo", reason: "Carga inicial", created_at: "2026-10-01T12:00:00Z" }],
  documents: [
    { id: "22222222-2222-4222-8222-222222222222", kind: "cedula", title: "Cédula de ejemplo", sha256: "a".repeat(64), content_type: "application/pdf", byte_size: 1000, original_filename: "cedula.pdf", added_at: "2026-10-01T12:00:00Z" },
  ],
};

type Call = { path: string; method: string; headers: Headers; body: unknown };

function stubApi(read: unknown = EMPTY, onPost: (call: Call) => Response = () => json({ command: "save-bidder-profile", profile_id: "p" })) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(raw, "http://localhost").pathname;
      const method = init?.method ?? "GET";
      if (method === "GET" && path === "/v2/bidder") return Promise.resolve(json(read));
      if (method === "GET" && /^\/v2\/bidder\/documents\/[^/]+\/url$/.test(path)) return Promise.resolve(json({ url: "https://files.example/doc" }));
      if (method === "POST") {
        const call: Call = { path, method, headers: new Headers(init?.headers), body: init?.body };
        calls.push(call);
        return Promise.resolve(onPost(call));
      }
      return Promise.resolve(json({}, 404));
    }),
  );
  return calls;
}

function sessionAs(role: "sales" | "admin" | "viewer"): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: `op-${role}`, email: `${role}@example.cl`, displayName: role, role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

function renderCard(role: "sales" | "admin" | "viewer" = "sales") {
  return render(
    <AuthSessionContext.Provider value={{ session: sessionAs(role), signOut: async () => true }}>
      <BidderPreferences />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
}

const typeInto = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

beforeEach(() => clearResourceCache());
afterEach(() => vi.unstubAllGlobals());

describe("BidderPreferences", () => {
  it("shows nothing to a viewer and never reads the bidder", () => {
    const calls = stubApi();
    renderCard("viewer");
    expect(screen.queryByRole("heading", { name: "Datos para licitaciones" })).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
    expect(calls).toEqual([]);
  });

  it("saves a natural person with an Idempotency-Key, the reason and no profile to replace", async () => {
    const calls = stubApi();
    renderCard();
    await screen.findByLabelText("Nombre o razón social");
    typeInto("Nombre o razón social", "Oferente Ejemplo");
    typeInto("RUT", "61.111.111-8");
    typeInto("Motivo", "Carga inicial");
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].path).toBe("/v2/commands/save-bidder-profile");
    expect(calls[0].headers.get("Idempotency-Key")).toBeTruthy();
    const body = JSON.parse(String(calls[0].body));
    expect(body).toMatchObject({
      bidder_kind: "natural_person", legal_name: "Oferente Ejemplo", rut: "61.111.111-8", reason: "Carga inicial", expected_current_id: null,
    });
    expect(body).not.toHaveProperty("legal_rep_name");
  });

  it("shows the company fields only for «Empresa»", async () => {
    stubApi();
    renderCard();
    await screen.findByLabelText("RUT");
    expect(screen.queryByLabelText("Representante legal")).toBeNull();
    expect(screen.queryByLabelText("RUT representante")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Empresa" }));
    expect(screen.getByLabelText("Representante legal")).toBeInTheDocument();
    expect(screen.getByLabelText("RUT representante")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Persona natural" }));
    expect(screen.queryByLabelText("Representante legal")).toBeNull();
  });

  it("shows the saved profile and sends the profile it saw as the one to replace", async () => {
    const calls = stubApi(SAVED);
    renderCard();
    expect(await screen.findByLabelText("Nombre o razón social")).toHaveValue("Oferente Ejemplo");
    expect(screen.getByLabelText("RUT")).toHaveValue("61.111.111-8");
    typeInto("Comuna", "Providencia");
    typeInto("Motivo", "Cambio de comuna");
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(JSON.parse(String(calls[0].body))).toMatchObject({ expected_current_id: SAVED.profile.id, comuna: "Providencia", is_emt: true });
  });

  it("asks for the reason before sending, next to its field", async () => {
    const calls = stubApi();
    renderCard();
    await screen.findByLabelText("RUT");
    typeInto("Nombre o razón social", "Oferente Ejemplo");
    typeInto("RUT", "61.111.111-8");
    typeInto("Motivo", "ab");
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Escribe un motivo de al menos 3 caracteres.")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("says when someone else changed the data (409 bidder_profile_changed)", async () => {
    stubApi(EMPTY, () => refusal(409, "conflict", "bidder_profile_changed", "cambió"));
    renderCard();
    await screen.findByLabelText("RUT");
    typeInto("Nombre o razón social", "Oferente Ejemplo");
    typeInto("RUT", "61.111.111-8");
    typeInto("Motivo", "Carga inicial");
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Otra persona cambió estos datos. Recarga para ver la versión actual.")).toBeInTheDocument();
  });

  it("shows an invalid RUT next to the RUT field", async () => {
    stubApi(EMPTY, () => refusal(422, "validation_error", "rut_invalid", "El RUT del oferente no es válido."));
    renderCard();
    await screen.findByLabelText("RUT");
    typeInto("Nombre o razón social", "Oferente Ejemplo");
    typeInto("RUT", "61.111.111-9");
    typeInto("Motivo", "Carga inicial");
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("El RUT no es válido; revisa el número y el dígito verificador.")).toBeInTheDocument();
    expect(screen.getByLabelText("RUT")).toHaveAttribute("aria-invalid", "true");
  });

  it("lists the documents, opens one through its signed link and retires one with a reason", async () => {
    const calls = stubApi(SAVED, () => json({ command: "retire-bidder-document", document_id: "d" }));
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    renderCard();
    expect(await screen.findByText("Cédula de ejemplo")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Abrir Cédula de ejemplo" }));
    await waitFor(() => expect(open).toHaveBeenCalledWith("https://files.example/doc", "_blank", "noopener,noreferrer"));
    fireEvent.click(screen.getByRole("button", { name: "Retirar Cédula de ejemplo" }));
    fireEvent.change(screen.getByLabelText("Motivo del retiro"), { target: { value: "Venció" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar retiro" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].path).toBe("/v2/commands/retire-bidder-document");
    expect(calls[0].headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(String(calls[0].body))).toEqual({ document_id: SAVED.documents[0].id, reason: "Venció" });
    open.mockRestore();
  });

  it("uploads a document as multipart with its kind and title", async () => {
    const calls = stubApi(EMPTY, () => json({ command: "add-bidder-document", document_id: "d" }));
    renderCard();
    await screen.findByLabelText("Tipo de documento");
    fireEvent.change(screen.getByLabelText("Tipo de documento"), { target: { value: "declaracion_jurada" } });
    fireEvent.change(screen.getByLabelText("Título del documento"), { target: { value: "Declaración 2026" } });
    const file = new File(["%PDF-1.4"], "decl.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Archivo"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Subir" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].path).toBe("/v2/commands/add-bidder-document");
    expect(calls[0].headers.get("Idempotency-Key")).toBeTruthy();
    const form = calls[0].body as FormData;
    expect(form.get("kind")).toBe("declaracion_jurada");
    expect(form.get("title")).toBe("Declaración 2026");
    expect((form.get("file") as File).name).toBe("decl.pdf");
  });
});
