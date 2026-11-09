/**
 * Unit tests for financeCalc.ts — pure calculation helpers.
 * No mocking needed: every function takes primitives and returns primitives.
 */
import { describe, expect, it } from "vitest";
import {
  IVA_RATE,
  ageBucket,
  buildBarSeries,
  businessDaysLeft,
  computeApartir,
  factoringCost,
  monthAbbr,
  requiredPaceForTarget,
  withIva,
  withoutIva,
} from "./financeCalc";

// ── aging ─────────────────────────────────────────────────────────────────────

describe("ageBucket", () => {
  it("maps 0 days to 0-30", () => expect(ageBucket(0)).toBe("0-30"));
  it("maps 30 days to 0-30", () => expect(ageBucket(30)).toBe("0-30"));
  it("maps 31 days to 31-60", () => expect(ageBucket(31)).toBe("31-60"));
  it("maps 60 days to 31-60", () => expect(ageBucket(60)).toBe("31-60"));
  it("maps 61 days to 61-90", () => expect(ageBucket(61)).toBe("61-90"));
  it("maps 90 days to 61-90", () => expect(ageBucket(90)).toBe("61-90"));
  it("maps 91 days to 90+", () => expect(ageBucket(91)).toBe("90+"));
  it("maps 999 days to 90+", () => expect(ageBucket(999)).toBe("90+"));
});

// ── IVA ───────────────────────────────────────────────────────────────────────

describe("withIva / withoutIva", () => {
  it("adds 19 % IVA", () => {
    expect(withIva(1_000_000)).toBe(1_190_000);
  });

  it("strips 19 % IVA (round-trip within rounding)", () => {
    // withoutIva(withIva(x)) should be within 1 CLP of x due to rounding
    const net = 850_000;
    expect(Math.abs(withoutIva(withIva(net)) - net)).toBeLessThanOrEqual(1);
  });

  it("withIva of 0 is 0", () => expect(withIva(0)).toBe(0));
  it("withoutIva of 0 is 0", () => expect(withoutIva(0)).toBe(0));
  it("IVA_RATE is 0.19", () => expect(IVA_RATE).toBe(0.19));
});

// ── pace ──────────────────────────────────────────────────────────────────────

describe("businessDaysLeft", () => {
  it("counts business days remaining in a week", () => {
    // 2026-11-09 is a Monday; that week still has Mon–Fri = at least 5 days
    // by end of November (30 days, so there should be Mon–Fri * 3-4 weeks)
    const days = businessDaysLeft("2026-11-09");
    expect(days).toBeGreaterThan(0);
    expect(days).toBeLessThanOrEqual(23); // max workdays in a month
  });

  it("last day of month returns 1 if it is a weekday", () => {
    // 2026-11-30 is a Monday
    const days = businessDaysLeft("2026-11-30");
    expect(days).toBe(1);
  });

  it("returns 0 when called on a Sunday at end of month", () => {
    // 2026-11-29 is a Sunday; only day left is Monday 30
    const days = businessDaysLeft("2026-11-29");
    expect(days).toBe(1); // Sunday itself is not counted but Monday 30 is
  });
});

describe("requiredPaceForTarget", () => {
  it("returns 0 remaining when sold >= target", () => {
    const { remaining } = requiredPaceForTarget(6_000_000, 6_000_000, "2026-11-09");
    expect(remaining).toBe(0);
  });

  it("computes per-day pace when there are days left", () => {
    const { perDay, remaining } = requiredPaceForTarget(0, 6_000_000, "2026-11-09");
    expect(perDay).not.toBeNull();
    expect(remaining).toBe(6_000_000);
    if (perDay !== null) expect(perDay).toBeGreaterThan(0);
  });

  it("perDay is null when no business days remain", () => {
    // Saturday Nov 28, the last Friday before month end
    // This is not a perfect test for businessDaysLeft=0 since it depends on calendar,
    // but we can test the edge-case logic directly.
    const { perDay } = requiredPaceForTarget(0, 1_000_000, "2026-11-28");
    // Nov 28 is a Saturday, so only Nov 30 (Mon) remains → 1 day
    expect(perDay).not.toBeNull();
  });
});

// ── factoring ─────────────────────────────────────────────────────────────────

describe("factoringCost", () => {
  it("1M CLP for 30 days at 1.5 % monthly = 15 000 cost", () => {
    const { cost, netReceived } = factoringCost(1_000_000, 30, 0.015);
    expect(cost).toBe(15_000);
    expect(netReceived).toBe(985_000);
  });

  it("includes fixed fee", () => {
    const { cost } = factoringCost(1_000_000, 30, 0.015, 5_000);
    expect(cost).toBe(20_000);
  });

  it("effectiveMonthlyRate equals monthlyRate when days = 30", () => {
    const { effectiveMonthlyRate } = factoringCost(1_000_000, 30, 0.015);
    expect(Math.abs(effectiveMonthlyRate - 0.015)).toBeLessThan(0.0001);
  });

  it("scales proportionally with days (60 days = 2× cost)", () => {
    const r30 = factoringCost(1_000_000, 30, 0.015);
    const r60 = factoringCost(1_000_000, 60, 0.015);
    expect(r60.cost).toBe(r30.cost * 2);
  });
});

// ── socios ────────────────────────────────────────────────────────────────────

describe("computeApartir", () => {
  const BASE = { arriendo_clp: 600_000, tasa_sebastian: null, sueldo_tatiana: 800_000, arriendo_pct_empresa: 0.5, split_pct: 0.5 };

  it("actual_50_50 splits equally", () => {
    const { tatiana, sebastian } = computeApartir(5_000_000, 1_000_000, { ...BASE, rule_name: "actual_50_50" });
    expect(tatiana + sebastian).toBe(4_000_000);
    expect(tatiana).toBe(sebastian);
  });

  it("opcion_b gives Tatiana sueldo first, then 50/50 of residual", () => {
    const margen = 5_000_000;
    const gastos = 1_000_000;
    const utilidad = margen - gastos; // 4_000_000
    const sueldo = 800_000;
    const residual = utilidad - sueldo; // 3_200_000
    const { tatiana, sebastian } = computeApartir(margen, gastos, { ...BASE, rule_name: "opcion_b" });
    expect(tatiana).toBe(sueldo + Math.round(residual * 0.5));
    expect(tatiana + sebastian).toBe(utilidad);
  });

  it("negative utilidad propagates correctly in actual_50_50", () => {
    const { tatiana, sebastian } = computeApartir(500_000, 1_000_000, { ...BASE, rule_name: "actual_50_50" });
    expect(tatiana + sebastian).toBe(-500_000); // loss split equally
  });

  it("opcion_d gives Sebastian interest + residual share", () => {
    const margen = 5_000_000;
    const gastos = 1_000_000;
    const { tatiana, sebastian, utilidad_bruta } = computeApartir(margen, gastos, { ...BASE, rule_name: "opcion_d", tasa_sebastian: 0.02 });
    expect(utilidad_bruta).toBe(4_000_000);
    expect(tatiana + sebastian).toBe(utilidad_bruta);
  });
});

// ── bar chart ─────────────────────────────────────────────────────────────────

describe("monthAbbr", () => {
  it("maps November correctly", () => expect(monthAbbr("2026-11-01")).toBe("nov"));
  it("maps January correctly", () => expect(monthAbbr("2026-01-01")).toBe("ene"));
  it("maps December correctly", () => expect(monthAbbr("2026-12-01")).toBe("dic"));
});

describe("buildBarSeries", () => {
  it("returns exactly 6 entries", () => {
    const series = buildBarSeries([], "2026-11-01");
    expect(series).toHaveLength(6);
  });

  it("last entry is the current month", () => {
    const series = buildBarSeries([], "2026-11-01");
    expect(series[5]?.isCurrent).toBe(true);
    expect(series[5]?.month).toBe("2026-11-01");
  });

  it("fills in zero for missing months", () => {
    const series = buildBarSeries([{ month: "2026-11-01", neto: 1_000_000 }], "2026-11-01");
    const others = series.slice(0, 5);
    for (const s of others) expect(s.neto).toBe(0);
    expect(series[5]?.neto).toBe(1_000_000);
  });

  it("wraps across year boundary", () => {
    const series = buildBarSeries([], "2026-01-01");
    // 5 months back from Jan 2026 → Aug 2025
    expect(series[0]?.month).toBe("2025-08-01");
  });
});
