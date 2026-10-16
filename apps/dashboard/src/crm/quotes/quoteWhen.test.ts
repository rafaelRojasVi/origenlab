import { describe, expect, it } from "vitest";
import { quoteWhen } from "./quoteWhen";

// Local times, so the test does not depend on the machine's time zone.
const NOW = new Date(2026, 9, 16, 12, 0, 0); // vie 16 Oct 2026, 12:00
const at = (day: number, h: number, m = 0) => new Date(2026, 9, day, h, m).toISOString();

describe("quoteWhen", () => {
  it("says minutes for the last hour, the clock time for today, and «ayer»", () => {
    expect(quoteWhen(new Date(NOW.getTime() - 20_000).toISOString(), NOW)).toBe("ahora");
    expect(quoteWhen(new Date(NOW.getTime() - 5 * 60_000).toISOString(), NOW)).toBe("hace 5 min");
    expect(quoteWhen(at(16, 10, 12), NOW)).toBe("hoy 10:12");
    expect(quoteWhen(at(15, 23, 30), NOW)).toBe("ayer");
  });

  it("says the weekday and day within a week, then the short date", () => {
    expect(quoteWhen(at(13, 9), NOW)).toBe("mar 13");
    expect(quoteWhen(at(1, 9), NOW)).toBe(new Date(2026, 9, 1, 9).toLocaleDateString("es-CL", { day: "2-digit", month: "short", year: "numeric" }));
  });

  it("is a dash for nothing or nonsense, and a date for a time in the future", () => {
    expect(quoteWhen(null, NOW)).toBe("—");
    expect(quoteWhen("not a date", NOW)).toBe("—");
    expect(quoteWhen(at(17, 9), NOW)).not.toMatch(/hace|hoy|ayer/);
  });
});
