/** «Nueva licitación»: each refusal in plain Spanish, the buyer fields on `buyer_required`, one key per action. */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Toaster } from "../ui";
import { NewTenderDialog } from "./NewTenderDialog";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const refusal = (status: number, code: string, message: string, details: Record<string, unknown> = {}) =>
  json({ error: { code: "conflict", message, details: { code, message, ...details }, request_id: "req-1" } }, status);
const NEW_ID = "33333333-3333-4333-8333-333333333333";
const OLD_ID = "44444444-4444-4444-8444-444444444444";

type Call = { path: string; headers: Headers; body: Record<string, unknown> };

function stubApi(answers: Response[]) {
  const calls: Call[] = [];
  const queue = [...answers];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    calls.push({ path: new URL(raw, "http://localhost").pathname, headers: new Headers(init?.headers), body: JSON.parse(String(init?.body ?? "{}")) });
    return Promise.resolve(queue.shift() ?? json({}, 500));
  }));
  return calls;
}

function renderDialog() {
  const navigate = vi.fn();
  const onClose = vi.fn();
  render(<><NewTenderDialog navigate={navigate} onClose={onClose} /><Toaster /></>);
  return { navigate, onClose };
}
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Abrir licitación" }));
const REF = "Código o enlace de Mercado Público";

afterEach(() => vi.unstubAllGlobals());

describe("NewTenderDialog", () => {
  it("opens the tender and goes to its page", async () => {
    const calls = stubApi([json({ command: "open-tender-case", opportunity_id: NEW_ID })]);
    const { navigate, onClose } = renderDialog();
    type(REF, " 1234567-12-LE26 ");
    submit();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("licitaciones", NEW_ID));
    expect(onClose).toHaveBeenCalled();
    expect(calls[0].path).toBe("/v2/commands/open-tender-case");
    expect(calls[0].headers.get("Idempotency-Key")).toBeTruthy();
    expect(calls[0].body).toEqual({ reference: "1234567-12-LE26" });
  });

  it("buyer_required reveals the buyer fields; the retry sends them", async () => {
    const calls = stubApi([
      refusal(422, "buyer_required", "x", { fetched: false }),
      json({ command: "open-tender-case", opportunity_id: NEW_ID }),
    ]);
    const { navigate } = renderDialog();
    expect(screen.queryByLabelText("Institución compradora")).toBeNull();
    type(REF, "1234567-12-LE26");
    submit();
    expect(await screen.findByText("No pudimos leer la licitación en Mercado Público; escribe la institución compradora.")).toBeInTheDocument();
    type("Institución compradora", "Hospital Ejemplo");
    type("RUT (opcional)", "61.111.111-8");
    submit();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("licitaciones", NEW_ID));
    expect(calls[1].body).toEqual({ reference: "1234567-12-LE26", buyer: { name: "Hospital Ejemplo", rut: "61.111.111-8" } });
  });

  it("tender_code_not_in_link says to copy the code", async () => {
    stubApi([refusal(422, "tender_code_not_in_link", "x")]);
    renderDialog();
    type(REF, "https://www.mercadopublico.cl/ejemplo");
    submit();
    expect(await screen.findByText("Ese enlace no trae el código; copia el código (ej. 1234567-12-LE26).")).toBeInTheDocument();
  });

  it("tender_case_exists offers «Abrir» on the existing case", async () => {
    stubApi([refusal(409, "tender_case_exists", "x", { opportunity_id: OLD_ID })]);
    const { navigate, onClose } = renderDialog();
    type(REF, "1234567-12-LE26");
    submit();
    expect(await screen.findByText("Esta licitación ya está abierta")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Abrir" }));
    expect(navigate).toHaveBeenCalledWith("licitaciones", OLD_ID);
    expect(onClose).toHaveBeenCalled();
  });

  it.each([
    ["tender_reference_invalid", 422, "Eso no es un código de licitación ni un enlace de Mercado Público."],
    ["buyer_rut_invalid", 422, "El RUT de la institución compradora no es válido; revisa el número y el dígito verificador."],
    ["organization_name_ambiguous", 409, "Hay varias instituciones con ese nombre; pide a administración que revise cuál es."],
    ["supplier_exception_required", 422, "Esta institución figura como proveedor de OrigenLab; pide a administración que abra esta licitación."],
    ["forbidden", 403, "Solo ventas y administración pueden abrir licitaciones."],
    ["algo_nuevo", 500, "No se pudo abrir la licitación. Intenta de nuevo."],
  ])("%s is said in plain Spanish", async (code, status, text) => {
    stubApi([refusal(status, code, "texto técnico del servidor")]);
    renderDialog();
    type(REF, "1234567-12-LE26");
    submit();
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByText("texto técnico del servidor")).toBeNull();
  });

  it("keeps the key when the answer is uncertain and takes a new one after a definitive refusal", async () => {
    const calls = stubApi([
      new Response("bad gateway", { status: 502 }),
      refusal(422, "tender_code_not_in_link", "x"),
      refusal(422, "tender_code_not_in_link", "x"),
    ]);
    renderDialog();
    type(REF, "1234567-12-LE26");
    submit();
    await screen.findByText("No se pudo abrir la licitación. Intenta de nuevo.");
    submit();
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].headers.get("Idempotency-Key")).toBe(calls[0].headers.get("Idempotency-Key"));
    await screen.findByText(/Ese enlace no trae el código/);
    submit();
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls[2].headers.get("Idempotency-Key")).not.toBe(calls[1].headers.get("Idempotency-Key"));
  });

  it("does not send an empty reference", () => {
    const calls = stubApi([]);
    renderDialog();
    submit();
    expect(calls).toHaveLength(0);
  });
});
