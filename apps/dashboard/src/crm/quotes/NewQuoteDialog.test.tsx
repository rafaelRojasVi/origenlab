/**
 * «Nueva cotización»: the operator picks a client; the API finds or opens the case and creates the
 * quote. Every value below is invented: this repository is public.
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CLIENT_SEARCH_DELAY_MS, NewQuoteDialog } from "./NewQuoteDialog";
import type { QuoteClientSuggestions } from "./quoteApi";

const ORG_ONE = "aaaaaaaa-0000-4000-8000-000000000001";
const ORG_TWO = "aaaaaaaa-0000-4000-8000-000000000002";
const ORG_NONE = "aaaaaaaa-0000-4000-8000-000000000003";
const ORG_TWIN = "aaaaaaaa-0000-4000-8000-000000000004";
const CASE_1 = "cccccccc-0000-4000-8000-000000000001";
const CASE_2 = "cccccccc-0000-4000-8000-000000000002";
const CASE_3 = "cccccccc-0000-4000-8000-000000000003";
const PERSON = "bbbbbbbb-0000-4000-8000-000000000001";
const POINT = "dddddddd-0000-4000-8000-000000000001";
const LONE_POINT = "dddddddd-0000-4000-8000-000000000002";
const NEW_QUOTE = "99999999-9999-4999-8999-999999999999";

const NOW = new Date("2026-10-16T12:00:00Z");
const daysAgo = (n: number) => new Date(NOW.getTime() - n * 86_400_000).toISOString();

const SUGGESTIONS: QuoteClientSuggestions = {
  organizations: [
    {
      id: ORG_ONE, name: "Laboratorio Ejemplo", kind: "laboratory", domains: ["laboratorio-ejemplo.test"],
      open_cases: [{ id: CASE_1, title: "Balanza analítica", stage: "qualifying", requested_at: daysAgo(2) }],
    },
    {
      id: ORG_TWO, name: "Laboratorio Doble", kind: "company", domains: [],
      open_cases: [
        { id: CASE_2, title: "Centrífuga", stage: "qualifying", requested_at: daysAgo(5) },
        { id: CASE_3, title: "Agitador", stage: "quoting", requested_at: daysAgo(1) },
      ],
    },
    { id: ORG_NONE, name: "Laboratorio Sin Casos", kind: "company", domains: [], open_cases: [] },
  ],
  people: [
    {
      person_id: PERSON, contact_point_id: POINT, name: "Ana Pérez", email: "compras@laboratorio-ejemplo.test",
      organization: { id: ORG_ONE, name: "Laboratorio Ejemplo" },
      open_cases: [{ id: CASE_1, title: "Balanza analítica", stage: "qualifying", requested_at: daysAgo(2) }],
    },
    { person_id: null, contact_point_id: LONE_POINT, name: null, email: "ventas@otro-ejemplo.test", organization: null, open_cases: [] },
  ],
};
const EMPTY: QuoteClientSuggestions = { organizations: [], people: [] };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
/** The API's real refusal envelope (`apps/api` errors.py): a generic code, the command's own under `details`. */
const refused = (status: number, code: string, message: string, extra: Record<string, unknown> = {}) =>
  json({ error: { code: status === 409 ? "conflict" : "validation_error", message, details: { code, message, ...extra }, request_id: "req-ejemplo" } }, status);
const created = () => json({ ok: true, quote_id: NEW_QUOTE, quote_number: "00042-26", revision_id: "r-new", version: 1, opportunity_id: CASE_1 });

interface Call {
  path: string;
  method: string;
  q: string | null;
  key: string | null;
  body: Record<string, unknown> | null;
}

function stub({
  clients = () => json(SUGGESTIONS),
  create = () => created(),
}: {
  clients?: (q: string) => Response | Promise<Response>;
  create?: (n: number) => Response | Promise<Response>;
} = {}) {
  const calls: Call[] = [];
  let creates = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      const headers = (init?.headers ?? {}) as Record<string, string>;
      calls.push({
        path: url.pathname,
        method: init?.method ?? "GET",
        q: url.searchParams.get("q"),
        key: headers["Idempotency-Key"] ?? null,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      if (url.pathname === "/v2/quotes/clients") return Promise.resolve(clients(url.searchParams.get("q") ?? ""));
      if (url.pathname === "/v2/commands/create-quote") return Promise.resolve(create(++creates));
      return Promise.resolve(json({}, 404));
    }),
  );
  return {
    calls,
    searches: () => calls.filter((c) => c.path === "/v2/quotes/clients").map((c) => c.q),
    creates: () => calls.filter((c) => c.path === "/v2/commands/create-quote"),
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function renderDialog() {
  const onClose = vi.fn();
  const onCreated = vi.fn();
  render(<NewQuoteDialog onClose={onClose} onCreated={onCreated} now={() => NOW} />);
  return { onClose, onCreated };
}

const clientBox = () => screen.getByRole("combobox", { name: "Cliente" });
const createButton = () => screen.getByRole("button", { name: "Crear y abrir" });

/** Types into «Cliente» and lets the debounce run out. */
async function search(text: string) {
  fireEvent.change(clientBox(), { target: { value: text } });
  await act(async () => {
    await new Promise((r) => setTimeout(r, CLIENT_SEARCH_DELAY_MS + 20));
  });
}

async function pick(name: RegExp) {
  const option = await screen.findByRole("option", { name });
  fireEvent.click(option);
}

const caseGroup = () => screen.getByRole("group", { name: "Caso" });

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the client search", () => {
  it("waits for a pause in typing, and asks once for the last text", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout"] });
    const api = stub();
    renderDialog();
    for (const t of ["L", "La", "Lab", "Labo"]) {
      fireEvent.change(clientBox(), { target: { value: t } });
      await act(async () => {
        vi.advanceTimersByTime(100);
      });
    }
    expect(api.searches()).toEqual([]);
    await act(async () => {
      vi.advanceTimersByTime(CLIENT_SEARCH_DELAY_MS);
    });
    expect(api.searches()).toEqual(["Labo"]);
    expect(await screen.findByRole("option", { name: /Laboratorio Ejemplo/ })).toBeInTheDocument();
  });

  it("never shows an answer for a text no longer typed", async () => {
    const late = deferred<Response>();
    const api = stub({ clients: (q) => (q === "Lab" ? late.promise : json({ organizations: [{ ...SUGGESTIONS.organizations[2], name: "Universidad Ejemplo" }], people: [] })) });
    renderDialog();
    await search("Lab");
    await search("Univ");
    await screen.findByRole("option", { name: /Universidad Ejemplo/ });
    await act(async () => {
      late.resolve(json(SUGGESTIONS));
    });
    expect(api.searches()).toEqual(["Lab", "Univ"]);
    expect(screen.queryByRole("option", { name: /Laboratorio Ejemplo/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Universidad Ejemplo/ })).toBeInTheDocument();
  });

  it("asks nothing for one letter", async () => {
    const api = stub();
    renderDialog();
    await search("L");
    expect(api.searches()).toEqual([]);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("lists institutions, then people, then «+ Cliente nuevo»", async () => {
    stub();
    renderDialog();
    await search("Labo");
    const options = within(await screen.findByRole("listbox", { name: "Clientes" })).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "Laboratorio Ejemploinstitución · 1 caso",
      "Laboratorio Dobleinstitución · 2 casos",
      "Laboratorio Sin Casosinstitución · sin casos abiertos",
      "compras@laboratorio-ejemplo.testpersona · Ana Pérez",
      "ventas@otro-ejemplo.testdirección sin persona",
      "+ Cliente nuevo «Labo»",
    ]);
    expect(clientBox()).toHaveAttribute("aria-expanded", "true");
  });

  it("moves through the suggestions with the arrows and picks one with Enter", async () => {
    stub();
    renderDialog();
    await search("Labo");
    await screen.findByRole("listbox");
    fireEvent.keyDown(clientBox(), { key: "ArrowDown" });
    expect(screen.getByRole("option", { name: /Laboratorio Doble/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(clientBox(), { key: "Enter" });
    expect(clientBox()).toHaveValue("Laboratorio Doble");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(within(caseGroup()).getAllByRole("radio")).toHaveLength(3);
  });

  it("Esc folds the suggestions first; a second Esc closes the dialog; typing unfolds them", async () => {
    stub();
    const { onClose } = renderDialog();
    await search("Labo");
    await screen.findByRole("listbox");
    fireEvent.keyDown(clientBox(), { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(clientBox()).toHaveAttribute("aria-expanded", "false");
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(clientBox(), { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    fireEvent.change(clientBox(), { target: { value: "Labor" } });
    expect(clientBox()).toHaveAttribute("aria-expanded", "true");
  });

  it("while it searches, the combobox controls the listbox, and «Buscando…» is said apart", async () => {
    const answer = deferred<Response>();
    stub({ clients: () => answer.promise });
    renderDialog();
    await search("Labo");
    expect(screen.getByText("Buscando…")).toHaveAttribute("role", "status");
    const controlled = document.getElementById(clientBox().getAttribute("aria-controls") ?? "");
    expect(controlled).not.toBeNull();
    expect(controlled).toHaveAttribute("role", "listbox");
    await act(async () => {
      answer.resolve(json(SUGGESTIONS));
    });
    expect(await screen.findByRole("option", { name: /Laboratorio Ejemplo/ })).toBeInTheDocument();
  });

  it("after a failed search a new client can still be offered", async () => {
    const api = stub({ clients: () => new Response("down", { status: 502 }) });
    renderDialog();
    await search("Laboratorio Nuevo");
    await screen.findByText("No se pudo buscar clientes.");
    await pick(/\+ Cliente nuevo «Laboratorio Nuevo»/);
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(1));
    expect(api.creates()[0].body).toEqual({ client: { new_organization: { name: "Laboratorio Nuevo", kind: "company" } } });
  });

  it("says when the search failed, and searches again", async () => {
    let n = 0;
    const api = stub({ clients: () => (++n === 1 ? new Response("down", { status: 502 }) : json(SUGGESTIONS)) });
    renderDialog();
    await search("Labo");
    expect(await screen.findByText("No se pudo buscar clientes.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    await screen.findByRole("option", { name: /Laboratorio Ejemplo/ });
    expect(api.searches()).toEqual(["Labo", "Labo"]);
  });
});

describe("the case", () => {
  it("an institution with one open case: it is chosen, and the quote goes on it", async () => {
    const api = stub();
    const { onCreated } = renderDialog();
    expect(createButton()).toBeDisabled();
    await search("Labo");
    await pick(/Laboratorio Ejemplo/);
    expect(screen.getByRole("radio", { name: /Balanza analítica/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Balanza analítica/ })).toHaveAccessibleName(/abierto · pidió precio hace 2 días/);
    expect(screen.getByRole("radio", { name: /Abrir una Solicitud nueva/ })).not.toBeChecked();
    expect(screen.getByText("Si el cliente tiene un solo caso abierto, se elige solo. Si no tiene ninguno, se abre una Solicitud nueva.")).toBeInTheDocument();
    fireEvent.click(createButton());
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.creates()[0].body).toEqual({ client: { organization_id: ORG_ONE }, opportunity_id: CASE_1 });
    expect(api.creates()[0].key).toBeTruthy();
  });

  it("several open cases: the operator chooses one, and it is sent", async () => {
    const api = stub();
    renderDialog();
    await search("Labo");
    await pick(/Laboratorio Doble/);
    const radios = within(caseGroup()).getAllByRole("radio");
    expect(radios.map((r) => (r as HTMLInputElement).checked)).toEqual([false, false, false]);
    expect(createButton()).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Agitador/ }));
    expect(createButton()).toBeEnabled();
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(1));
    expect(api.creates()[0].body).toEqual({ client: { organization_id: ORG_TWO }, opportunity_id: CASE_3 });
  });

  it("the operator may open a new Solicitud instead of the open case", async () => {
    const api = stub();
    renderDialog();
    await search("Labo");
    await pick(/Laboratorio Ejemplo/);
    fireEvent.click(screen.getByRole("radio", { name: /Abrir una Solicitud nueva/ }));
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(1));
    expect(api.creates()[0].body).toEqual({ client: { organization_id: ORG_ONE }, new_case: true });
  });

  it("no open case: «Abrir una Solicitud nueva» is the only choice, and it is sent", async () => {
    const api = stub();
    renderDialog();
    await search("Labo");
    await pick(/Laboratorio Sin Casos/);
    const radios = within(caseGroup()).getAllByRole("radio");
    expect(radios).toHaveLength(1);
    expect(radios[0]).toBeChecked();
    expect(radios[0]).toHaveAccessibleName(/Abrir una Solicitud nueva/);
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(1));
    expect(api.creates()[0].body).toEqual({ client: { organization_id: ORG_NONE }, new_case: true });
  });
});

describe("the client sent", () => {
  it("a person goes by their person_id", async () => {
    const api = stub();
    const { onCreated } = renderDialog();
    await search("Labo");
    await pick(/persona · Ana Pérez/);
    expect(clientBox()).toHaveValue("compras@laboratorio-ejemplo.test");
    expect(screen.getByRole("radio", { name: /Balanza analítica/ })).toBeChecked();
    fireEvent.click(createButton());
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.creates()[0].body).toEqual({ client: { person_id: PERSON }, opportunity_id: CASE_1 });
    // The tab names the person's institution, as the quote's own page will.
    expect(onCreated).toHaveBeenCalledWith(NEW_QUOTE, { label: "CN00042", client: "Laboratorio Ejemplo", status: "draft" });
  });

  it("an address with no person goes by its contact_point_id", async () => {
    const api = stub();
    renderDialog();
    await search("Labo");
    await pick(/ventas@otro-ejemplo\.test/);
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(1));
    expect(api.creates()[0].body).toEqual({ client: { contact_point_id: LONE_POINT }, new_case: true });
  });

  it("an address the CRM lacks is offered as it is; the API finds its case", async () => {
    const api = stub({ clients: () => json(EMPTY) });
    renderDialog();
    await search("nueva@ejemplo.test");
    expect(screen.queryByRole("option", { name: /Cliente nuevo/ })).not.toBeInTheDocument();
    await pick(/Usar la dirección nueva@ejemplo\.test/);
    expect(screen.getByRole("radio", { name: /Su caso abierto, si tiene uno/ })).toBeChecked();
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(1));
    expect(api.creates()[0].body).toEqual({ client: { email: "nueva@ejemplo.test" } });
  });

  it("a new client is a new institution, with its kind and an optional address; the API finds or opens its case", async () => {
    const api = stub({ clients: () => json(EMPTY) });
    const { onCreated } = renderDialog();
    await search("Laboratorio Nuevo");
    await pick(/\+ Cliente nuevo «Laboratorio Nuevo»/);
    const kind = screen.getByLabelText("Tipo de institución");
    expect(kind).toHaveValue("company");
    expect(within(kind).getByRole("option", { name: "Laboratorio" })).toBeInTheDocument();
    fireEvent.change(kind, { target: { value: "laboratory" } });
    fireEvent.change(screen.getByLabelText("Correo (opcional)"), { target: { value: "contacto@nuevo-ejemplo.test" } });
    const radios = within(caseGroup()).getAllByRole("radio");
    expect(radios).toHaveLength(1);
    expect(radios[0]).toBeChecked();
    fireEvent.click(createButton());
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.creates()[0].body).toEqual({
      client: { new_organization: { name: "Laboratorio Nuevo", kind: "laboratory", email: "contacto@nuevo-ejemplo.test" } },
    });
    // No `new_case`: an institution the API turns out to hold keeps its one open case.
    expect(api.creates()[0].body).not.toHaveProperty("new_case");
    expect(onCreated).toHaveBeenCalledWith(NEW_QUOTE, { label: "CN00042", client: "Laboratorio Nuevo", status: "draft" });
  });

  it("a new client without an address sends only its name and kind; a bad address holds the button", async () => {
    const api = stub({ clients: () => json(EMPTY) });
    renderDialog();
    await search("Clínica Nueva");
    await pick(/\+ Cliente nuevo/);
    const email = screen.getByLabelText("Correo (opcional)");
    fireEvent.change(email, { target: { value: "no-es-correo" } });
    expect(createButton()).toBeDisabled();
    expect(screen.getByText("Escribe una dirección de correo válida, o déjala vacía.")).toBeInTheDocument();
    fireEvent.change(email, { target: { value: "" } });
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(1));
    expect(api.creates()[0].body).toEqual({ client: { new_organization: { name: "Clínica Nueva", kind: "company" } } });
  });

  it("typing again forgets the client chosen", async () => {
    stub();
    renderDialog();
    await search("Labo");
    await pick(/Laboratorio Ejemplo/);
    expect(screen.getByRole("group", { name: "Caso" })).toBeInTheDocument();
    fireEvent.change(clientBox(), { target: { value: "Laboratorio Ejempl" } });
    expect(screen.queryByRole("group", { name: "Caso" })).not.toBeInTheDocument();
    expect(createButton()).toBeDisabled();
  });
});

describe("the API's refusals", () => {
  it("case_choice_needed shows the client's cases, and the retry sends the one chosen with the same key", async () => {
    const candidates = [
      { id: CASE_2, title: "Centrífuga", stage: "qualifying", requested_at: daysAgo(5) },
      { id: CASE_3, title: "Agitador", stage: "quoting", requested_at: daysAgo(1) },
    ];
    const api = stub({
      clients: () => json(EMPTY),
      create: (n) =>
        n === 1
          ? refused(409, "case_choice_needed", "Este cliente tiene varios casos abiertos: elige uno, o abre uno nuevo.", { candidates })
          : created(),
    });
    const { onCreated } = renderDialog();
    await search("compras@doble-ejemplo.test");
    await pick(/Usar la dirección/);
    fireEvent.click(createButton());
    await screen.findByRole("radio", { name: /Centrífuga/ });
    expect(screen.getByRole("radio", { name: /Agitador/ })).toHaveAccessibleName(/pidió precio hace 1 día$/);
    // Said in the live region, and the focus moves to the cases to choose from.
    expect(screen.getByRole("status")).toHaveTextContent("Este cliente tiene varios casos abiertos: elige uno, o abre una Solicitud nueva.");
    await waitFor(() => expect(caseGroup()).toHaveFocus());
    expect(screen.queryByRole("radio", { name: /Su caso abierto/ })).not.toBeInTheDocument();
    expect(createButton()).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Centrífuga/ }));
    fireEvent.click(createButton());
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    const [first, second] = api.creates();
    expect(second.body).toEqual({ client: { email: "compras@doble-ejemplo.test" }, opportunity_id: CASE_2 });
    expect(second.key).toBe(first.key);
  });

  it("a new client the API finds with several cases: «Abrir una Solicitud nueva» then sends new_case, with the same key", async () => {
    const candidates = [
      { id: CASE_2, title: "Centrífuga", stage: "qualifying", requested_at: daysAgo(5) },
      { id: CASE_3, title: "Agitador", stage: "quoting", requested_at: daysAgo(1) },
    ];
    const api = stub({
      clients: () => json(EMPTY),
      create: (n) => (n === 1 ? refused(409, "case_choice_needed", "Este cliente tiene varios casos abiertos: elige uno, o abre uno nuevo.", { candidates }) : created()),
    });
    const { onCreated } = renderDialog();
    await search("Laboratorio Doble");
    await pick(/\+ Cliente nuevo «Laboratorio Doble»/);
    fireEvent.click(createButton());
    await screen.findByRole("radio", { name: /Centrífuga/ });
    fireEvent.click(screen.getByRole("radio", { name: /Abrir una Solicitud nueva/ }));
    fireEvent.click(createButton());
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    const [first, second] = api.creates();
    expect(first.body).toEqual({ client: { new_organization: { name: "Laboratorio Doble", kind: "company" } } });
    expect(second.body).toEqual({ client: { new_organization: { name: "Laboratorio Doble", kind: "company" } }, new_case: true });
    expect(second.key).toBe(first.key);
  });

  it("organization_name_ambiguous lists the institutions of that name, and the operator picks one", async () => {
    const twins = [
      { id: ORG_ONE, name: "Laboratorio Ejemplo" },
      { id: ORG_TWIN, name: "Laboratorio Ejemplo" },
    ];
    const api = stub({
      clients: (q) =>
        json(
          q === "Laboratorio Ejemplo"
            ? { organizations: [SUGGESTIONS.organizations[0], { ...SUGGESTIONS.organizations[2], id: ORG_TWIN, name: "Laboratorio Ejemplo", domains: ["gemelo-ejemplo.test"] }], people: [] }
            : EMPTY,
        ),
      create: (n) =>
        n === 1
          ? refused(409, "organization_name_ambiguous", "Hay varias instituciones con ese nombre: elige una de ellas.", { candidates: twins })
          : created(),
    });
    renderDialog();
    await search("Laboratorio Ejemplo");
    await pick(/\+ Cliente nuevo «Laboratorio Ejemplo»/);
    fireEvent.click(createButton());
    const choices = await screen.findByRole("group", { name: "¿Cuál de ellas?" });
    await waitFor(() => expect(choices).toHaveFocus());
    expect(screen.getByRole("status")).toHaveTextContent("Hay varias instituciones con ese nombre: elige una de ellas.");
    await waitFor(() => expect(within(choices).getByRole("button", { name: /gemelo-ejemplo\.test/ })).toBeInTheDocument());
    fireEvent.click(within(choices).getByRole("button", { name: /gemelo-ejemplo\.test/ }));
    expect(screen.queryByRole("group", { name: "¿Cuál de ellas?" })).not.toBeInTheDocument();
    expect(clientBox()).toHaveValue("Laboratorio Ejemplo");
    expect(screen.getByRole("radio", { name: /Abrir una Solicitud nueva/ })).toBeChecked();
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(2));
    const [first, second] = api.creates();
    expect(second.body).toEqual({ client: { organization_id: ORG_TWIN }, new_case: true });
    expect(second.key).toBe(first.key);
  });

  it("an answer that may have run keeps the key: the next click repeats the same command", async () => {
    const api = stub({
      create: (n) => {
        if (n === 1) throw new TypeError("Failed to fetch");
        if (n === 2) return new Response("<html>Bad gateway</html>", { status: 502 });
        return created();
      },
    });
    const { onCreated } = renderDialog();
    await search("Labo");
    await pick(/Laboratorio Ejemplo/);
    fireEvent.click(createButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo crear la cotización.");
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(2));
    await screen.findByRole("alert");
    fireEvent.click(createButton());
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    const keys = api.creates().map((c) => c.key);
    expect(new Set(keys).size).toBe(1);
  });

  it("command_in_progress (the same command may still finish) keeps the key", async () => {
    const api = stub({
      create: (n) => (n === 1 ? refused(409, "command_in_progress", "a command with this Idempotency-Key is still running") : created()),
    });
    const { onCreated } = renderDialog();
    await search("Labo");
    await pick(/Laboratorio Ejemplo/);
    fireEvent.click(createButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("todavía se está procesando");
    fireEvent.click(createButton());
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    const [first, second] = api.creates();
    expect(second.key).toBe(first.key);
  });

  it("a refusal for certain is said in Spanish, and the next click is a new command", async () => {
    const api = stub({
      create: (n) =>
        n === 1
          ? refused(422, "supplier_exception_required", "OrigenLab le compra a esta institución…")
          : created(),
    });
    renderDialog();
    await search("Labo");
    await pick(/Laboratorio Ejemplo/);
    fireEvent.click(createButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("proveedora o fabricante");
    fireEvent.click(createButton());
    await waitFor(() => expect(api.creates()).toHaveLength(2));
    const [first, second] = api.creates();
    expect(second.key).not.toBe(first.key);
  });

  it("is busy while it works: one click, one command; the dialog cannot be closed meanwhile", async () => {
    const answer = deferred<Response>();
    const api = stub({ create: () => answer.promise });
    const { onClose, onCreated } = renderDialog();
    await search("Labo");
    await pick(/Laboratorio Ejemplo/);
    fireEvent.click(createButton());
    const busy = await screen.findByRole("button", { name: /Creando/ });
    expect(busy).toBeDisabled();
    expect(busy.className).toContain("min-h-[44px]");
    fireEvent.click(busy);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => {
      answer.resolve(created());
    });
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.creates()).toHaveLength(1);
  });
});

describe("the dialog", () => {
  it("is labelled, focuses the client first, and Esc or «Cancelar» close it", async () => {
    stub();
    const { onClose } = renderDialog();
    expect(screen.getByRole("dialog", { name: "Nueva cotización" })).toBeInTheDocument();
    expect(clientBox()).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(createButton().className).toContain("min-h-[44px]");
  });
});

describe("after the quote is made", () => {
  it("the focus goes to the new quote's tab once it is shown, not to the hidden button that opened the dialog", async () => {
    stub();
    function Host() {
      const [open, setOpen] = useState(true);
      const [tab, setTab] = useState<string | null>(null);
      return (
        <>
          <div hidden={tab !== null}>
            <button type="button" onClick={() => setOpen(true)}>
              Nueva cotización
            </button>
          </div>
          {tab ? (
            <button type="button" role="tab" aria-selected="true" data-quote-id={tab}>
              CN00042
            </button>
          ) : null}
          {open ? (
            <NewQuoteDialog
              now={() => NOW}
              onClose={() => setOpen(false)}
              onCreated={(id) => {
                setOpen(false);
                setTimeout(() => setTab(id), 30);
              }}
            />
          ) : null}
        </>
      );
    }
    render(<Host />);
    await search("Labo");
    await pick(/Laboratorio Ejemplo/);
    fireEvent.click(createButton());
    const tab = await screen.findByRole("tab", { name: "CN00042" });
    await waitFor(() => expect(tab).toHaveFocus());
  });
});
