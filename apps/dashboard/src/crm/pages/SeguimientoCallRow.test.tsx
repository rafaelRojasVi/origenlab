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

  describe("¿Cuándo fue?", () => {
    function stubOk() {
      const posts: { path: string; key: string | null; body: Record<string, unknown> }[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
          const path = new URL(String(input), "http://localhost").pathname;
          posts.push({ path, key: new Headers(init?.headers).get("Idempotency-Key"), body: JSON.parse(String(init?.body ?? "{}")) });
          return Promise.resolve(new Response(JSON.stringify({ ok: true, opportunity_id: "o1", opportunity_version: 2 }), { status: 200, headers: { "Content-Type": "application/json" } }));
        }),
      );
      return posts;
    }
    const note = (posts: { path: string; body: Record<string, unknown> }[]) => posts.find((p) => p.path.endsWith("/add-note"))!.body;

    it("«Ahora» sends no occurred_at", async () => {
      const posts = stubOk();
      const onSaved = renderRow();
      expect(screen.getByRole("button", { name: "Ahora" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.queryByLabelText("Hora de la llamada")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
      await waitFor(() => expect(onSaved).toHaveBeenCalled());
      expect(note(posts)).not.toHaveProperty("occurred_at");
    });

    it("«Ayer» at 15:00 is that Santiago time, defaults the next contact to Mañana, counted from the save", async () => {
      const posts = stubOk();
      const onSaved = renderRow();
      fireEvent.click(screen.getByRole("button", { name: "Ayer" }));
      // Default: the current Santiago hour, rounded down (NOW is 12:00 there).
      expect(screen.getByLabelText("Hora de la llamada")).toHaveValue("12:00");
      fireEvent.change(screen.getByLabelText("Hora de la llamada"), { target: { value: "15:00" } });
      fireEvent.click(screen.getByRole("button", { name: "No contestó" }));
      // A call made yesterday never proposes «otra vez hoy (en 2 h)».
      expect(screen.queryByRole("button", { name: "Otra vez hoy (en 2 h)" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Mañana" })).toHaveAttribute("aria-pressed", "true");
      fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
      await waitFor(() => expect(onSaved).toHaveBeenCalledWith("Mañana"));
      // 17 Nov 15:00 in Santiago (UTC-3).
      expect(note(posts).occurred_at).toBe("2026-11-17T18:00:00.000Z");
      const task = posts.find((p) => p.path.endsWith("/create-task"))!.body;
      expect(Date.parse(String(task.due_at))).toBeGreaterThan(NOW.getTime() + 12 * 3_600_000);
    });

    it("«Otra vez hoy» chosen before «Ayer» falls back to Mañana", () => {
      stubOk();
      renderRow();
      fireEvent.click(screen.getByRole("button", { name: "No contestó" }));
      expect(screen.getByRole("button", { name: "Otra vez hoy (en 2 h)" })).toHaveAttribute("aria-pressed", "true");
      fireEvent.click(screen.getByRole("button", { name: "Otro día" }));
      expect(screen.getByRole("button", { name: "Mañana" })).toHaveAttribute("aria-pressed", "true");
    });

    it("«Otro día» with no date blocks the save with a message", () => {
      const posts = stubOk();
      const onSaved = renderRow();
      fireEvent.click(screen.getByRole("button", { name: "Otro día" }));
      fireEvent.change(screen.getByLabelText("Día de la llamada"), { target: { value: "" } });
      expect(screen.getByRole("alert")).toHaveTextContent("Elige el día de la llamada.");
      expect(screen.getByRole("button", { name: "Guardar llamada" })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
      expect(posts).toHaveLength(0);
      expect(onSaved).not.toHaveBeenCalled();
    });

    it("a day more than 60 days back, or in the future, says why and blocks the save", () => {
      stubOk();
      renderRow();
      fireEvent.click(screen.getByRole("button", { name: "Otro día" }));
      fireEvent.change(screen.getByLabelText("Día de la llamada"), { target: { value: "2026-09-18" } });
      expect(screen.getByRole("alert")).toHaveTextContent("Solo se registran llamadas de los últimos 60 días");
      expect(screen.getByRole("button", { name: "Guardar llamada" })).toBeDisabled();
      fireEvent.change(screen.getByLabelText("Día de la llamada"), { target: { value: "2026-11-19" } });
      expect(screen.getByRole("alert")).toHaveTextContent("Esa hora todavía no llega");
      fireEvent.change(screen.getByLabelText("Día de la llamada"), { target: { value: "2026-09-20" } });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Guardar llamada" })).toBeEnabled();
    });

    it("«Otro día…» as the next contact schedules that day", async () => {
      const posts = stubOk();
      const onSaved = renderRow();
      fireEvent.click(screen.getByRole("button", { name: "Otro día…" }));
      fireEvent.change(screen.getByLabelText("Día del próximo contacto"), { target: { value: "2026-11-27" } });
      fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
      await waitFor(() => expect(onSaved).toHaveBeenCalled());
      const task = posts.find((p) => p.path.endsWith("/create-task"))!.body;
      expect(task.due_at).toBe(new Date(2026, 10, 27, 9, 0).toISOString());
    });

    it("says «al contacto» when the case has no contact name", () => {
      render(
        <table>
          <tbody>
            <SeguimientoCallRow row={{ card: { ...card, contact: null } } as SegRow} colSpan={8} clock={() => NOW} onSaved={vi.fn()} onCancel={vi.fn()} />
          </tbody>
        </table>,
      );
      expect(screen.getByText("¿Cómo terminó la llamada al contacto?")).toBeInTheDocument();
    });

    it("blocks a time that has not come yet", () => {
      const posts = stubOk();
      const onSaved = renderRow();
      fireEvent.click(screen.getByRole("button", { name: "Hoy, más temprano" }));
      fireEvent.change(screen.getByLabelText("Hora de la llamada"), { target: { value: "13:30" } });
      fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
      expect(screen.getByRole("alert")).toHaveTextContent("Esa hora todavía no llega");
      expect(posts).toHaveLength(0);
      expect(onSaved).not.toHaveBeenCalled();
    });

    it("«Otro día» is limited to the last 60 days", () => {
      stubOk();
      renderRow();
      fireEvent.click(screen.getByRole("button", { name: "Otro día" }));
      const day = screen.getByLabelText("Día de la llamada");
      expect(day).toHaveAttribute("max", "2026-11-18");
      expect(day).toHaveAttribute("min", "2026-09-19");
    });

    it("a retry replays the same occurred_at under the same key", async () => {
      const posts: { path: string; key: string | null; body: Record<string, unknown> }[] = [];
      let first = true;
      vi.stubGlobal(
        "fetch",
        vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
          const path = new URL(String(input), "http://localhost").pathname;
          posts.push({ path, key: new Headers(init?.headers).get("Idempotency-Key"), body: JSON.parse(String(init?.body ?? "{}")) });
          if (first) {
            first = false;
            return Promise.resolve(new Response("{}", { status: 500 }));
          }
          return Promise.resolve(new Response(JSON.stringify({ ok: true, opportunity_id: "o1", opportunity_version: 2 }), { status: 200, headers: { "Content-Type": "application/json" } }));
        }),
      );
      let clockAt = NOW;
      const onSaved = renderRow(vi.fn(), () => clockAt);
      fireEvent.click(screen.getByRole("button", { name: "Ayer" }));
      fireEvent.change(screen.getByLabelText("Hora de la llamada"), { target: { value: "15:00" } });
      fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      clockAt = new Date(NOW.getTime() + 10 * 60_000);
      fireEvent.click(screen.getByRole("button", { name: "Guardar llamada" }));
      await waitFor(() => expect(onSaved).toHaveBeenCalled());
      const notes = posts.filter((p) => p.path.endsWith("/add-note"));
      expect(notes).toHaveLength(2);
      expect(notes[1].key).toBe(notes[0].key);
      expect(notes[1].body).toEqual(notes[0].body);
      expect(notes[1].body.occurred_at).toBe("2026-11-17T18:00:00.000Z");
    });
  });
});
