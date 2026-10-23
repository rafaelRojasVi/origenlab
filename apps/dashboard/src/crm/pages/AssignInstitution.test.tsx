/**
 * «Asignar institución solicitante»: an institution found by name, or one created from the text
 * searched (`add-case-organization` with `new_organization`). Every value is invented.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OpportunityCardData } from "../crmTypes";
import { AssignInstitution } from "./AssignInstitution";

const CASE = "cccccccc-0000-4000-8000-0000000000aa";
const TWIN_A = "aaaaaaaa-0000-4000-8000-0000000000a1";
const TWIN_B = "aaaaaaaa-0000-4000-8000-0000000000a2";

const card = { opportunity_id: CASE, version: 7, pending_institution_mentions: [] } as unknown as OpportunityCardData;

interface Call {
  path: string;
  method: string;
  body: Record<string, unknown> | null;
  key: string | null;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
/** The API's real refusal envelope: a generic code, the command's own under `details`. */
const refused = (status: number, code: string, message: string, extra: Record<string, unknown> = {}) =>
  json({ error: { code: status === 409 ? "conflict" : "validation_error", message, details: { code, message, ...extra }, request_id: "req-ejemplo" } }, status);

function stub(post: (n: number) => Response, items: unknown[] = []) {
  const calls: Call[] = [];
  let posts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      const call = {
        path,
        method: init?.method ?? "GET",
        body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null,
        key: new Headers(init?.headers).get("Idempotency-Key"),
      };
      calls.push(call);
      if (call.method === "GET" && path === "/v2/organizations") {
        return Promise.resolve(json({ items, total: items.length, limit: 50, offset: 0, facets: null }));
      }
      if (call.method === "POST" && path === "/v2/commands/add-case-organization") return Promise.resolve(post(++posts));
      return Promise.resolve(json({ detail: "not found" }, 404));
    }),
  );
  return { calls, posts: () => calls.filter((c) => c.method === "POST") };
}

const receipt = (extra: Record<string, unknown>) =>
  json({
    command: "add_case_organization", opportunity_id: CASE, opportunity_version: 8, stage: "lead",
    idempotency_key: "k", command_receipt_id: "r", replayed: false, ...extra,
  });

function renderIt() {
  const onDone = vi.fn();
  render(<AssignInstitution card={card} onCancel={vi.fn()} onDone={onDone} />);
  return onDone;
}

async function searchFor(text: string) {
  fireEvent.change(screen.getByPlaceholderText("Nombre de la institución"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
  return screen.findByRole("button", { name: `+ Crear institución «${text}»` });
}

afterEach(() => vi.unstubAllGlobals());

describe("«+ Crear institución»", () => {
  it("is offered when the search finds nothing, and creates it with its kind as the requester", async () => {
    const api = stub(() => receipt({ organization_id: "o-1", organization_name: "Instituto Nuevo Ejemplo", organization_created: true }));
    const onDone = renderIt();
    const create = await searchFor("Instituto Nuevo Ejemplo");
    expect(screen.getByText(/No hay ninguna institución «Instituto Nuevo Ejemplo»/)).toBeInTheDocument();
    fireEvent.click(create);
    const form = screen.getByRole("form", { name: "Crear institución" });
    fireEvent.change(within(form).getByLabelText("Tipo de institución"), { target: { value: "university" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Motivo de la asignación" }), { target: { value: "Lo pidió por teléfono." } });
    fireEvent.click(within(form).getByRole("button", { name: "Crear y asignar" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const [post] = api.posts();
    expect(post.key).toBeTruthy();
    expect(post.body).toEqual({
      opportunity_id: CASE, opportunity_version: 7, role: "requesting_institution", note: "Lo pidió por teléfono.",
      new_organization: { name: "Instituto Nuevo Ejemplo", kind: "university" },
    });
    expect(onDone).toHaveBeenCalledWith({ tone: "good", lines: ["Institución «Instituto Nuevo Ejemplo» creada y asociada al caso como solicitante."] }, true);
  });

  it("is offered after the institutions found too, and says when the API used an existing one", async () => {
    stub(() => receipt({ organization_id: "o-2", organization_name: "Universidad Ejemplo", organization_created: false }), [
      { organization_id: "o-9", name: "Universidad Ejemplo Sur", confirmation: "confirmed", relationship_roles: [] },
    ]);
    const onDone = renderIt();
    const create = await searchFor("universidad ejemplo");
    expect(screen.getByRole("combobox", { name: "Institución solicitante" })).toBeInTheDocument();
    fireEvent.click(create);
    const form = screen.getByRole("form", { name: "Crear institución" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Motivo de la asignación" }), { target: { value: "Firma el correo." } });
    fireEvent.click(within(form).getByRole("button", { name: "Crear y asignar" }));
    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith({ tone: "good", lines: ["Se usó la institución existente «Universidad Ejemplo»: quedó como solicitante del caso."] }, true),
    );
  });

  it("a name several share lists them to choose from, and the retry names the one chosen", async () => {
    const twins = [{ id: TWIN_A, name: "Clínica Gemela" }, { id: TWIN_B, name: "Clínica Gemela" }];
    const api = stub((n) =>
      n === 1 ? refused(409, "organization_name_ambiguous", "Hay varias instituciones con ese nombre: elige una de ellas.", { candidates: twins }) : receipt({}),
    );
    renderIt();
    fireEvent.click(await searchFor("Clínica Gemela"));
    const form = screen.getByRole("form", { name: "Crear institución" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Motivo de la asignación" }), { target: { value: "La nombra el correo." } });
    fireEvent.click(within(form).getByRole("button", { name: "Crear y asignar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Hay varias instituciones con ese nombre: elige una de ellas.");
    const select = screen.getByRole("combobox", { name: "Institución solicitante" });
    expect(within(select).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["", TWIN_A, TWIN_B]);
    expect(api.posts()).toHaveLength(1);
  });

  it("a supplier found by that name asks for the written exception", async () => {
    stub(() => refused(422, "supplier_exception_required", "OrigenLab buys from this institution"));
    renderIt();
    fireEvent.click(await searchFor("Proveedor Ejemplo"));
    const form = screen.getByRole("form", { name: "Crear institución" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Motivo de la asignación" }), { target: { value: "Pide para su laboratorio." } });
    fireEvent.click(within(form).getByRole("button", { name: "Crear y asignar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Esta institución figura como proveedor.");
  });
});

describe("what the AI read in the client's email (owner 2026-10-23)", () => {
  const suggested = (suggestion: unknown) =>
    ({ ...card, requester_suggestion: suggestion }) as unknown as OpportunityCardData;

  it("offers «Crear y asignar» with the name read, its kind and the sender's domain, in one click", async () => {
    const api = stub(() => receipt({ organization_id: "o-7", organization_name: "Centro Ficticio de Análisis de Laboratorio", organization_created: true }));
    const onDone = vi.fn();
    render(<AssignInstitution card={suggested({ name: "Centro Ficticio de Análisis de Laboratorio", domain: "centro-ficticio.example.test" })} onCancel={vi.fn()} onDone={onDone} />);
    const box = screen.getByRole("region", { name: "Sugerencia del correo" });
    expect(box).toHaveTextContent("Centro Ficticio de Análisis de Laboratorio");
    expect(box).toHaveTextContent("centro-ficticio.example.test");
    const quick = within(box).getByRole("button", { name: "Crear y asignar «Centro Ficticio de Análisis de Laboratorio»" });
    await waitFor(() => expect(quick).toBeEnabled()); // the search run on opening has finished
    fireEvent.click(quick);
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(api.posts()[0].body).toEqual({
      opportunity_id: CASE, opportunity_version: 7, role: "requesting_institution",
      note: "Leído en el correo del cliente (centro-ficticio.example.test).",
      new_organization: { name: "Centro Ficticio de Análisis de Laboratorio", kind: "laboratory", domain: "centro-ficticio.example.test" },
    });
  });

  it("opens with the search already run for the name read", async () => {
    const api = stub(() => receipt({}), [
      { organization_id: "o-3", name: "Centro Ficticio de Análisis de Laboratorio", confirmation: "confirmed", relationship_roles: [] },
    ]);
    render(<AssignInstitution card={suggested({ name: "Centro Ficticio de Análisis de Laboratorio", domain: null })} onCancel={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByPlaceholderText("Nombre de la institución")).toHaveValue("Centro Ficticio de Análisis de Laboratorio");
    expect(await screen.findByRole("combobox", { name: "Institución solicitante" })).toBeInTheDocument();
    expect(api.calls.some((c) => c.path === "/v2/organizations")).toBe(true);
  });

  it("with only a domain, says so and creates nothing by itself", async () => {
    stub(() => receipt({}));
    render(<AssignInstitution card={suggested({ name: null, domain: "centro-ficticio.example.test" })} onCancel={vi.fn()} onDone={vi.fn()} />);
    const box = screen.getByRole("region", { name: "Sugerencia del correo" });
    expect(box).toHaveTextContent("centro-ficticio.example.test");
    expect(within(box).queryByRole("button", { name: /Crear y asignar/ })).toBeNull();
  });

  it("without a suggestion, nothing changes", () => {
    stub(() => receipt({}));
    renderIt();
    expect(screen.queryByRole("region", { name: "Sugerencia del correo" })).toBeNull();
    expect(screen.getByPlaceholderText("Nombre de la institución")).toHaveValue("");
  });
});
