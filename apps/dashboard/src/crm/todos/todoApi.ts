/**
 * The one client for «Pendientes», the shared to-do list on «Hoy»: the list
 * (`GET /v2/workspace/todos`) and its six commands. Not `crm.task` (a case follow-up).
 *
 * The commands need an `Idempotency-Key` and a non-blank `note` (always `TODO_NOTE` — the list has
 * no reason field); this module owns their paths (pinned by `src/test/noWritePolicy.test.ts`)
 * through one internal `post()`. A stale line is refused as `todo_version_conflict`.
 */
import { OperatorApiError, fetchJsonGet, notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";
import { refusalOfError } from "../../api/refusalEnvelope";
import { newCaseCommandKey } from "../caseCommands";

export const TODO_NOTE = "Desde «Hoy».";

export interface TodoCase {
  id: string;
  title: string;
  stage: string;
  organization_name: string | null;
  quote_number: string | null;
}

export interface TodoItem {
  todo_id: string;
  version: number;
  body: string;
  is_urgent: boolean;
  /** Sort key as the database prints it; the list arrives already in order. */
  position: string;
  /** ISO date (no time), or null. */
  due_on: string | null;
  status: string;
  completed_at: string | null;
  created_at: string;
  created_by: string;
  opportunity: TodoCase | null;
}

export interface TodoList {
  open: TodoItem[];
  done: TodoItem[];
}

/** The API's refusal (code, message, status) behind an error; null for an error that is not the API's. */
export const todoRefusal = refusalOfError;

export const fetchTodos = (doneDays = 7) =>
  fetchJsonGet<TodoList>(operatorApiUrl("/v2/workspace/todos", { done_days: String(doneDays) }));

async function post(path: string, body: Record<string, unknown>, idempotencyKey: string): Promise<Record<string, unknown>> {
  const res = await fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ ...body, note: TODO_NOTE }),
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return (await res.json()) as Record<string, unknown>;
}

interface TodoRef {
  todo_id: string;
  todo_version: number;
}

export interface CreateTodoBody {
  body: string;
  is_urgent: boolean;
  due_on: string | null;
  opportunity_id: string | null;
}

/** The line's full editable state: every key is required; null clears the day / the case. */
export interface UpdateTodoBody extends TodoRef, CreateTodoBody {}

export interface MoveTodoBody extends TodoRef {
  /** Put the line right after this open line of the same group; null = top of the group. */
  after_todo_id: string | null;
}

export const createTodo = (body: CreateTodoBody, idempotencyKey: string = newCaseCommandKey()) =>
  post("/v2/commands/create-todo", { ...body }, idempotencyKey);

export const updateTodo = (body: UpdateTodoBody, idempotencyKey: string = newCaseCommandKey()) =>
  post("/v2/commands/update-todo", { ...body }, idempotencyKey);

export const moveTodo = (body: MoveTodoBody, idempotencyKey: string = newCaseCommandKey()) =>
  post("/v2/commands/move-todo", { ...body }, idempotencyKey);

export const completeTodo = (body: TodoRef, idempotencyKey: string = newCaseCommandKey()) =>
  post("/v2/commands/complete-todo", { ...body }, idempotencyKey);

export const reopenTodo = (body: TodoRef, idempotencyKey: string = newCaseCommandKey()) =>
  post("/v2/commands/reopen-todo", { ...body }, idempotencyKey);

export const deleteTodo = (body: TodoRef, idempotencyKey: string = newCaseCommandKey()) =>
  post("/v2/commands/delete-todo", { ...body }, idempotencyKey);
