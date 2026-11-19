import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OpportunityCardData } from "../crmTypes";
import type { SegRow } from "../seguimiento";
import { SeguimientoCallRow } from "./SeguimientoCallRow";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-18T15:00:00Z");
const card = {
  opportunity_id: "o1",
  title: "Cotización CN09876",
  contact: { source: "crm_participant", name: "Persona Uno", address: null, others: 0 },
  quote_numbers: ["CN09876"],
  latest_revision: null,
  open_tasks: [{ task_id: "t1", title: "Seguimiento de CN09876", due_at: "2026-11-17T12:00:00Z", version: 2, owner: null }],
} as unknown as OpportunityCardData;
const row = { card } as SegRow;

function renderRow(onSaved = vi.fn(), clock: () => Date = () => NOW) {
  render(
    <table>
      <tbody>
        <SeguimientoCallRow row={row} colSpan={11} clock={clock} onSaved={onSaved} onCancel={vi.fn()} />
      </tbody>
    </table>,
  );
  return onSaved;
}

afterEach(() => vi.unstubAllGlobals());

describe("SeguimientoCallRow", () => {
  it("retries a failed save with the same keys and the same bodies, even as time passes", async () => {
    const posts: { path: string; key: string | null; body: Record<string, unknown> }[] = [];
    let taskTries = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), "http://localhost").pathname;
        posts.push({ path, key: new Headers(init?.headers).get("Idempotency-Key"), body: JSON.parse(String(init?.body ?? "{}")) });
        // The first reminder fails after the call note was recorded.
        if (path.endsWith("/create-task") && ++taskTries === 1) return Promise.resolve(new Response("{}", { status: 500 }));
        return Promise.resolve(new Response(JSON.stringify({ ok: true, opportunity_id: "o1", opportunity_version: 2 }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }),
    );
    // Ten minutes pass between the two clicks.
    let clockAt = NOW;
    const onSaved = renderRow(vi.fn(), () => clockAt);
    fireEvent.click(screen.getByRole("button", { name: "No contestó" }));
    fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    clockAt = new Date(NOW.getTime() + 10 * 60_000);
    fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith("Hoy en 2 h"));
    expect(posts.map((p) => p.path.split("/").pop())).toEqual(["add-note", "create-task", "add-note", "create-task", "cancel-task"]);
    const notes = posts.filter((p) => p.path.endsWith("/add-note"));
    expect(notes[1].key).toBe(notes[0].key);
    expect(notes[1].body).toEqual(notes[0].body);
    const tasks = posts.filter((p) => p.path.endsWith("/create-task"));
    expect(tasks[1].key).toBe(tasks[0].key);
    expect(tasks[1].body).toEqual(tasks[0].body);
    expect(tasks[1].body.due_at).toBe(new Date(NOW.getTime() + 2 * 3_600_000).toISOString());
  });

  it("defaults to Contestó and Mañana, and no contestó proposes otra vez hoy", () => {
    renderRow();
    expect(screen.getByRole("button", { name: "Contestó" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Mañana" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "No contestó" }));
    expect(screen.getByRole("button", { name: "Otra vez hoy (en 2 h)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Mañana" })).toHaveAttribute("aria-pressed", "false");
  });
});
