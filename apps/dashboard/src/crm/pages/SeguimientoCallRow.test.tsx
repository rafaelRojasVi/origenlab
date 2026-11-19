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

function renderRow(onSaved = vi.fn()) {
  render(
    <table>
      <tbody>
        <SeguimientoCallRow row={row} colSpan={11} clock={() => NOW} onSaved={onSaved} onCancel={vi.fn()} />
      </tbody>
    </table>,
  );
  return onSaved;
}

afterEach(() => vi.unstubAllGlobals());

describe("SeguimientoCallRow", () => {
  it("retries a failed save with the same keys, and replaces the earlier reminder", async () => {
    let n = 0;
    const posts: { path: string; key: string | null }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), "http://localhost").pathname;
        posts.push({ path, key: new Headers(init?.headers).get("Idempotency-Key") });
        n += 1;
        if (n === 1) return Promise.resolve(new Response("{}", { status: 500 }));
        return Promise.resolve(new Response(JSON.stringify({ ok: true, opportunity_id: "o1", opportunity_version: 2 }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }),
    );
    const onSaved = renderRow();
    fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith("Mañana"));
    const notes = posts.filter((p) => p.path.endsWith("/add-note"));
    expect(notes).toHaveLength(2);
    expect(notes[1].key).toBe(notes[0].key);
    expect(posts.map((p) => p.path.split("/").pop())).toEqual(["add-note", "add-note", "create-task", "cancel-task"]);
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
