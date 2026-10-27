import { describe, expect, it } from "vitest";
import type { TodoItem } from "./todoApi";
import { dayChoices, dueLabel, isLate, neighbourAbove, neighbourForMoveDown, santiagoToday, splitPasted } from "./todoOrder";

// 2026-10-28T01:30Z is still Tuesday 27 October in Santiago (UTC-3 in summer time).
const LATE_EVENING = new Date("2026-10-28T01:30:00Z");
const TUESDAY = new Date("2026-10-27T15:00:00Z");

function item(todo_id: string, is_urgent: boolean): TodoItem {
  return {
    todo_id, version: 1, body: todo_id, is_urgent, position: "1", due_on: null, status: "open",
    completed_at: null, created_at: "2026-10-27T12:00:00+00:00", created_by: "Ana Prueba", opportunity: null,
  };
}

describe("santiagoToday / isLate", () => {
  it("uses the Santiago calendar day, not UTC", () => {
    expect(santiagoToday(LATE_EVENING)).toBe("2026-10-27");
  });
  it("is late only when strictly before today", () => {
    expect(isLate("2026-10-27", LATE_EVENING)).toBe(false);
    expect(isLate("2026-10-26", LATE_EVENING)).toBe(true);
    expect(isLate(null, LATE_EVENING)).toBe(false);
  });
});

describe("dayChoices", () => {
  it("on a Tuesday offers Hoy, Mañana, Jueves, Viernes", () => {
    expect(dayChoices(TUESDAY)).toEqual([
      { label: "Hoy", value: "2026-10-27" },
      { label: "Mañana", value: "2026-10-28" },
      { label: "Jueves", value: "2026-10-29" },
      { label: "Viernes", value: "2026-10-30" },
    ]);
  });
  it("on a Friday offers only Hoy and Mañana", () => {
    expect(dayChoices(new Date("2026-10-30T15:00:00Z")).map((c) => c.label)).toEqual(["Hoy", "Mañana"]);
  });
});

describe("dueLabel", () => {
  it("names near days and falls back to the date", () => {
    expect(dueLabel("2026-10-27", TUESDAY)).toBe("Hoy");
    expect(dueLabel("2026-10-28", TUESDAY)).toBe("Mañana");
    expect(dueLabel("2026-10-29", TUESDAY)).toBe("Jueves");
    expect(dueLabel("2026-11-02", TUESDAY)).toBe("Lunes");
    expect(dueLabel("2026-11-20", TUESDAY)).toMatch(/20/);
  });
});

describe("splitPasted", () => {
  it("one entry per non-blank line, bullets stripped, order kept", () => {
    expect(splitPasted("-uno\n\n - dos\n• tres")).toEqual(["uno", "dos", "tres"]);
    expect(splitPasted("* a\r\n   \r\nb")).toEqual(["a", "b"]);
  });
});

describe("neighbour helpers", () => {
  // urgent: u1 u2 u3 ; normal: n1 n2
  const list = [item("u1", true), item("u2", true), item("u3", true), item("n1", false), item("n2", false)];
  it("neighbourAbove: id two places above, null when it becomes first or is first", () => {
    expect(neighbourAbove(list, "u3")).toBe("u1");
    expect(neighbourAbove(list, "u2")).toBeNull();
    expect(neighbourAbove(list, "u1")).toBeNull();
    expect(neighbourAbove(list, "n2")).toBeNull();
    expect(neighbourAbove(list, "n1")).toBeNull();
  });
  it("neighbourForMoveDown: next item in the group, null when last", () => {
    expect(neighbourForMoveDown(list, "u1")).toBe("u2");
    expect(neighbourForMoveDown(list, "u3")).toBeNull();
    expect(neighbourForMoveDown(list, "n1")).toBe("n2");
    expect(neighbourForMoveDown(list, "n2")).toBeNull();
  });
  it("unknown id gives null", () => {
    expect(neighbourAbove(list, "zz")).toBeNull();
    expect(neighbourForMoveDown(list, "zz")).toBeNull();
  });
});
