import { describe, expect, it } from "vitest";
import { tenderClosingLines } from "../today";
import { closingLabel, closingWhen } from "./tenderTime";

// Invented instants. 2026-10-19 is spring in Chile (UTC-3): 15:00Z is noon there.
const NOW = new Date("2026-10-19T15:00:00Z");

describe("closingLabel (America/Santiago)", () => {
  it("says days left by the Chilean calendar", () => {
    expect(closingLabel("2026-10-21T18:00:00Z", NOW)).toBe("cierra en 2 días");
    expect(closingLabel("2026-10-20T18:00:00Z", NOW)).toBe("cierra mañana");
    expect(closingLabel("2026-10-19T20:00:00Z", NOW)).toBe("cierra hoy");
  });

  it("counts the day in Santiago, not in UTC", () => {
    // 02:00Z on the 20th is 23:00 on the 19th in Santiago: still today.
    expect(closingLabel("2026-10-20T02:00:00Z", NOW)).toBe("cierra hoy");
    // 12:00Z on the 19th is noon... and 01:30Z "now" is still the 18th in Santiago.
    expect(closingLabel("2026-10-19T18:00:00Z", new Date("2026-10-19T01:30:00Z"))).toBe("cierra mañana");
  });

  it("counts calendar days across the September DST change", () => {
    // Chile moves to summer time on 2026-09-06: the gap is 47 hours, still two calendar days.
    expect(closingLabel("2026-09-07T15:00:00Z", new Date("2026-09-05T23:30:00Z"))).toBe("cierra en 2 días");
  });

  it("a close at 23:59 Santiago is still today", () => {
    // 23:59 on the 19th in Santiago (UTC-3) is 02:59Z on the 20th.
    expect(closingLabel("2026-10-20T02:59:00Z", NOW)).toBe("cierra hoy");
  });

  it("says «cerró» once the time has passed", () => {
    expect(closingLabel("2026-10-19T14:00:00Z", NOW)).toBe("cerró");
    expect(closingLabel("2026-10-10T14:00:00Z", NOW)).toBe("cerró");
  });

  it("has nothing to say without a closing time", () => {
    expect(closingLabel(null, NOW)).toBe("sin fecha de cierre");
  });
});

describe("closingWhen", () => {
  it("prints the day and the hour in Santiago", () => {
    expect(closingWhen("2026-10-21T18:30:00Z")).toMatch(/21/);
    expect(closingWhen("2026-10-21T18:30:00Z")).toMatch(/15:30/);
    expect(closingWhen(null)).toBe("—");
  });
});

describe("tenderClosingLines (Hoy)", () => {
  it("is one line per tender, «Licitación cierra mañana: <título>»", () => {
    expect(tenderClosingLines([{ id: "t1", code: "1-1-L1", title: "Balanzas de ejemplo", closes_at: null }])).toEqual([
      { id: "t1", text: "Licitación cierra mañana: Balanzas de ejemplo" },
    ]);
    expect(tenderClosingLines(undefined)).toEqual([]);
  });
});
