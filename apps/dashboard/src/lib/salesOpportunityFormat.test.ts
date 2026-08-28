import { describe, expect, it, vi } from "vitest";
import {
  SALES_OPPORTUNITY_ACTIVE_STAGES,
  SALES_OPPORTUNITY_STAGE_LABELS,
  SALES_OPPORTUNITY_TOGGLE_STAGES,
  formatSalesOpportunityAge,
  isSalesOpportunityStageTerminal,
  salesOpportunityStageLabel,
} from "./salesOpportunityFormat";

describe("salesOpportunityFormat", () => {
  it("labels every durable stage in plain Spanish", () => {
    expect(salesOpportunityStageLabel("new")).toBe("Nueva");
    expect(salesOpportunityStageLabel("qualifying")).toBe("Calificando");
    expect(salesOpportunityStageLabel("qualified")).toBe("Calificada");
    expect(salesOpportunityStageLabel("quoting")).toBe("Cotizando");
    expect(salesOpportunityStageLabel("negotiating")).toBe("Negociando");
    expect(salesOpportunityStageLabel("won")).toBe("Ganada");
    expect(salesOpportunityStageLabel("lost")).toBe("Perdida");
    expect(salesOpportunityStageLabel("dormant")).toBe("Dormida");
  });

  it("defines the active board columns in pipeline order", () => {
    expect(SALES_OPPORTUNITY_ACTIVE_STAGES).toEqual([
      "new",
      "qualifying",
      "qualified",
      "quoting",
      "negotiating",
    ]);
  });

  it("defines the three toggle stages", () => {
    expect(SALES_OPPORTUNITY_TOGGLE_STAGES).toEqual(["won", "lost", "dormant"]);
  });

  it("only won and lost are terminal", () => {
    expect(isSalesOpportunityStageTerminal("won")).toBe(true);
    expect(isSalesOpportunityStageTerminal("lost")).toBe(true);
    expect(isSalesOpportunityStageTerminal("dormant")).toBe(false);
    expect(isSalesOpportunityStageTerminal("negotiating")).toBe(false);
  });

  it("formats age relative to now in Spanish", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-28T12:00:00Z"));

    expect(formatSalesOpportunityAge("2026-08-28T09:00:00Z")).toBe("Hoy");
    expect(formatSalesOpportunityAge("2026-08-27T12:00:00Z")).toBe("Hace 1 día");
    expect(formatSalesOpportunityAge("2026-08-25T12:00:00Z")).toBe("Hace 3 días");

    vi.useRealTimers();
  });

  it("formatSalesOpportunityAge tolerates malformed input", () => {
    expect(formatSalesOpportunityAge("not-a-date")).toBe("—");
  });
});
