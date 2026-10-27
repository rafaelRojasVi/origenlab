import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { OperatorApiError } from "../../api/operatorClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData } from "../crmTypes";
import { clearResourceCache } from "../useResource";
import type { TodoItem, TodoList } from "./todoApi";

// The toaster is mounted by the CRM shell; read what the panel asked it to say.
const toasts = vi.hoisted(() => [] as string[]);
vi.mock("../ui", async (importActual) => {
  const actual = await importActual<typeof import("../ui")>();
  return { ...actual, toast: (text: string) => { toasts.push(text); } };
});

vi.mock("./todoApi", async (importActual) => {
  const actual = await importActual<typeof import("./todoApi")>();
  return {
    ...actual,
    fetchTodos: vi.fn(),
    createTodo: vi.fn(),
    updateTodo: vi.fn(),
    moveTodo: vi.fn(),
    completeTodo: vi.fn(),
    reopenTodo: vi.fn(),
    deleteTodo: vi.fn(),
  };
});

import * as api from "./todoApi";
import { PendientesPanel } from "./PendientesPanel";

// Every value is invented; the repository is public.
const NOW = new Date("2026-10-27T15:00:00Z"); // Tuesday 27 October in Santiago

function item(todo_id: string, body: string, extra: Partial<TodoItem> = {}): TodoItem {
  return {
    todo_id, version: 1, body, is_urgent: false, position: "1", due_on: null, status: "open",
    completed_at: null, created_at: "2026-10-27T12:00:00+00:00", created_by: "Ana Prueba", opportunity: null,
    ...extra,
  };
}

const LIST: TodoList = {
  open: [
    item("t-1", "Llamar a Laboratorio Ficticio"),
    item("t-2", "Enviar ficha técnica de la balanza", { is_urgent: true }),
    item("t-3", "Revisar la orden de compra", { due_on: "2026-10-23" }),
  ],
  done: [item("t-9", "Pedir factura al proveedor", { status: "done", completed_at: "2026-10-26T12:00:00+00:00" })],
};

const RECEIPT = { command: "x", todo_id: "t", todo_version: 2, replayed: false };

const fetchTodos = vi.mocked(api.fetchTodos);
const createTodo = vi.mocked(api.createTodo);
const updateTodo = vi.mocked(api.updateTodo);
const completeTodo = vi.mocked(api.completeTodo);
const reopenTodo = vi.mocked(api.reopenTodo);
const moveTodo = vi.mocked(api.moveTodo);

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in", method: "google_profile",
    operator: { operatorId: "op", email: "v@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true, crmAuthoringEnabled: true,
  } as AuthSessionState;
}

function renderPanel(role = "sales", cases: OpportunityCardData[] = []) {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <PendientesPanel cases={cases} navigate={navigate} now={NOW} />
    </AuthSessionContext.Provider>,
  );
  return { navigate };
}

const rows = () => screen.getAllByTestId("todo-row");
const input = () => screen.getByRole("textbox", { name: "Nuevo pendiente" });

beforeEach(() => {
  fetchTodos.mockResolvedValue(LIST);
  for (const f of [createTodo, updateTodo, completeTodo, reopenTodo, moveTodo]) f.mockResolvedValue(RECEIPT);
});

afterEach(() => {
  vi.clearAllMocks();
  toasts.length = 0;
  clearResourceCache();
});

describe("«Pendientes de la semana»", () => {
  it("shows urgent lines above the others", async () => {
    renderPanel();
    await screen.findByText("Llamar a Laboratorio Ficticio");
    expect(rows().map((r) => r.textContent)).toEqual([
      expect.stringContaining("Enviar ficha técnica"),
      expect.stringContaining("Llamar a Laboratorio Ficticio"),
      expect.stringContaining("Revisar la orden de compra"),
    ]);
  });

  it("marks a line whose day has passed as late, and keeps it visible", async () => {
    renderPanel();
    const row = (await screen.findByText("Revisar la orden de compra")).closest("[data-testid=todo-row]") as HTMLElement;
    expect(within(row).getByText(/atrasada/)).toBeInTheDocument();
    const other = screen.getByText("Llamar a Laboratorio Ficticio").closest("[data-testid=todo-row]") as HTMLElement;
    expect(within(other).queryByText(/atrasada/)).toBeNull();
  });

  it("sends nothing for a blank line", async () => {
    renderPanel();
    await screen.findByText("Llamar a Laboratorio Ficticio");
    fireEvent.change(input(), { target: { value: "   " } });
    fireEvent.keyDown(input(), { key: "Enter" });
    expect(createTodo).not.toHaveBeenCalled();
  });

  it("Enter twice fast creates the line once", async () => {
    let finish: (v: Record<string, unknown>) => void = () => {};
    createTodo.mockImplementation(() => new Promise((res) => { finish = res; }));
    renderPanel();
    await screen.findByText("Llamar a Laboratorio Ficticio");
    fireEvent.change(input(), { target: { value: "Cotizar centrífuga" } });
    fireEvent.keyDown(input(), { key: "Enter" });
    fireEvent.keyDown(input(), { key: "Enter" });
    expect(createTodo).toHaveBeenCalledTimes(1);
    expect(createTodo.mock.calls[0][0]).toEqual({ body: "Cotizar centrífuga", is_urgent: false, due_on: null, opportunity_id: null });
    await act(async () => finish(RECEIPT));
    await waitFor(() => expect(input()).toHaveValue(""));
    expect(fetchTodos).toHaveBeenCalledTimes(2); // refetched after the write
  });

  it("pasting three lines creates three, in order", async () => {
    renderPanel();
    await screen.findByText("Llamar a Laboratorio Ficticio");
    fireEvent.paste(input(), { clipboardData: { getData: () => "- Uno\n• Dos\n\nTres" } });
    await waitFor(() => expect(createTodo).toHaveBeenCalledTimes(3));
    expect(createTodo.mock.calls.map((c) => c[0].body)).toEqual(["Uno", "Dos", "Tres"]);
  });

  it("★ marks the line urgent with its full state", async () => {
    renderPanel();
    const row = (await screen.findByText("Llamar a Laboratorio Ficticio")).closest("[data-testid=todo-row]") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Urgente" }));
    await waitFor(() => expect(updateTodo).toHaveBeenCalledTimes(1));
    expect(updateTodo.mock.calls[0][0]).toEqual({
      todo_id: "t-1", todo_version: 1, body: "Llamar a Laboratorio Ficticio", is_urgent: true, due_on: null, opportunity_id: null,
    });
  });

  it("tick then «Deshacer» completes then reopens the line", async () => {
    renderPanel();
    const row = (await screen.findByText("Llamar a Laboratorio Ficticio")).closest("[data-testid=todo-row]") as HTMLElement;
    fireEvent.click(within(row).getByRole("checkbox"));
    await waitFor(() => expect(completeTodo).toHaveBeenCalledWith({ todo_id: "t-1", todo_version: 1 }));
    fireEvent.click(await screen.findByRole("button", { name: "Deshacer" }));
    await waitFor(() => expect(reopenTodo).toHaveBeenCalledWith({ todo_id: "t-1", todo_version: 2 }));
  });

  it("a version conflict says so and reloads the list", async () => {
    updateTodo.mockRejectedValue(
      new OperatorApiError(JSON.stringify({ error: { code: "conflict", message: "x", details: { code: "todo_version_conflict", message: "stale" } } }), 409),
    );
    renderPanel();
    const row = (await screen.findByText("Llamar a Laboratorio Ficticio")).closest("[data-testid=todo-row]") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Urgente" }));
    await waitFor(() => expect(fetchTodos).toHaveBeenCalledTimes(2));
    expect(toasts).toContain("Alguien cambió esta lista; la actualicé.");
  });

  it("dragging places a line after another of its group; across groups does nothing", async () => {
    renderPanel();
    const row = (text: string) => screen.getByText(text).closest("[data-testid=todo-row]") as HTMLElement;
    await screen.findByText("Llamar a Laboratorio Ficticio");
    const data = { getData: () => "t-1", setData: () => {}, effectAllowed: "", dropEffect: "" };
    fireEvent.dragStart(row("Llamar a Laboratorio Ficticio"), { dataTransfer: data });
    fireEvent.dragOver(row("Enviar ficha técnica de la balanza"), { dataTransfer: data });
    fireEvent.drop(row("Enviar ficha técnica de la balanza"), { dataTransfer: data });
    expect(moveTodo).not.toHaveBeenCalled();
    fireEvent.dragStart(row("Llamar a Laboratorio Ficticio"), { dataTransfer: data });
    fireEvent.dragOver(row("Revisar la orden de compra"), { dataTransfer: data, clientY: 10 });
    fireEvent.drop(row("Revisar la orden de compra"), { dataTransfer: data });
    await waitFor(() => expect(moveTodo).toHaveBeenCalledWith({ todo_id: "t-1", todo_version: 1, after_todo_id: "t-3" }));
  });

  it("«Subir» moves a line to the top of its group; the first line cannot go up", async () => {
    renderPanel();
    const row = (await screen.findByText("Revisar la orden de compra")).closest("[data-testid=todo-row]") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: /Más acciones/ }));
    fireEvent.click(within(row).getByRole("menuitem", { name: "Subir" }));
    await waitFor(() => expect(moveTodo).toHaveBeenCalledWith({ todo_id: "t-3", todo_version: 1, after_todo_id: null }));
    const first = screen.getByText("Enviar ficha técnica de la balanza").closest("[data-testid=todo-row]") as HTMLElement;
    fireEvent.click(within(first).getByRole("button", { name: /Más acciones/ }));
    expect(within(first).getByRole("menuitem", { name: "Subir" })).toBeDisabled();
    expect(within(first).getByRole("menuitem", { name: "Bajar" })).toBeDisabled();
  });

  it("the case chip opens the case", async () => {
    fetchTodos.mockResolvedValue({ open: [item("t-5", "Confirmar despacho", {
      opportunity: { id: "op-7", title: "Balanza analítica", stage: "quoting", organization_name: "Universidad Ficticia", quote_number: "CN09999" },
    })], done: [] });
    const { navigate } = renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /CN09999/ }));
    expect(navigate).toHaveBeenCalledWith("oportunidades", "op-7");
  });

  it("a viewer sees the list but gets no input", async () => {
    renderPanel("viewer");
    await screen.findByText("Llamar a Laboratorio Ficticio");
    expect(screen.queryByRole("textbox", { name: "Nuevo pendiente" })).toBeNull();
    expect(screen.getAllByRole("checkbox").every((c) => (c as HTMLInputElement).disabled)).toBe(true);
  });

  it("fails quietly in its own box when the list cannot load", async () => {
    fetchTodos.mockRejectedValue(new OperatorApiError("not found", 404));
    renderPanel();
    expect(await screen.findByText("No pude cargar los pendientes.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    await waitFor(() => expect(fetchTodos).toHaveBeenCalledTimes(2));
  });

  it("says so when there is nothing pending", async () => {
    fetchTodos.mockResolvedValue({ open: [], done: [] });
    renderPanel();
    expect(await screen.findByText("Nada pendiente. Escribe lo que tengas que hacer esta semana.")).toBeInTheDocument();
  });
});

