import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OperatorApiError } from "../../api/operatorClient";
import { keyAfterFailure, refusedByApi } from "../caseCommands";
import type { OpportunityCardData } from "../crmTypes";
import { FollowUpReminder } from "./FollowUpReminder";

// Every value below is invented; the repository is public.
const NOW = new Date(2026, 10, 18, 12);

const CARD = {
  opportunity_id: "case-1", title: "Cotización CN09876", stage: "quoting", version: 3, quote_numbers: ["CN09876"],
  latest_revision: { quote_number: "CN09876" }, open_tasks: [],
} as unknown as OpportunityCardData;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("keyAfterFailure", () => {
  it("keeps the key when nobody answered or the server failed, and renews it after a refusal", () => {
    expect(keyAfterFailure(new TypeError("Failed to fetch"), "k1")).toBe("k1");
    expect(keyAfterFailure(new OperatorApiError("gateway timeout", 504), "k1")).toBe("k1");
    expect(keyAfterFailure(new OperatorApiError("conflict", 409), "k1")).not.toBe("k1");
    expect(refusedByApi(new OperatorApiError("conflict", 409))).toBe(true);
    expect(refusedByApi(new TypeError("Failed to fetch"))).toBe(false);
  });
});

describe("«Recordarme…» after a dropped connection", () => {
  it("stays open and «Guardar» again replays the note and the reminder under the same keys", async () => {
    const sent: { path: string; key: string | null }[] = [];
    let first = true;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      sent.push({ path, key: new Headers(init?.headers).get("Idempotency-Key") });
      // The reminder's first send never answers: the note may be in, the task may be in.
      if (path === "/v2/commands/create-task" && first) {
        first = false;
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      return Promise.resolve(new Response(JSON.stringify({ command: "x", opportunity_id: "case-1", opportunity_version: 4, command_receipt_id: "r", replayed: false, note: { note_id: "n1" } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    const onDone = vi.fn();
    render(<FollowUpReminder card={CARD} onDone={onDone} onCancel={() => undefined} now={NOW} />);
    fireEvent.change(screen.getByLabelText("¿Qué tienes que hacer?"), { target: { value: "Llamar a Ana" } });
    const save = () => fireEvent.click(screen.getByRole("button", { name: "Guardar recordatorio" }));

    save();
    await waitFor(() => expect(sent.filter((x) => x.path === "/v2/commands/create-task")).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "Guardar recordatorio" })).toBeEnabled());
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByTestId("followup-reminder")).toBeInTheDocument();

    save();
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const keys = (suffix: string) => sent.filter((x) => x.path.endsWith(suffix)).map((x) => x.key);
    const notes = keys("note");
    expect(notes).toHaveLength(2);
    expect(notes[1]).toBe(notes[0]);
    const tasks = keys("/create-task");
    expect(tasks[1]).toBe(tasks[0]);
  });
});
