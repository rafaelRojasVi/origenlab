import { describe, expect, it } from "vitest";
import { parseDashboardSectionFromHash, dashboardSectionToHash } from "./dashboardHashRoute";

describe("dashboardHashRoute — pipeline section", () => {
  it("parses #/pipeline into the pipeline section", () => {
    expect(parseDashboardSectionFromHash("#/pipeline")).toBe("pipeline");
  });

  it("round-trips the pipeline section to its hash", () => {
    expect(dashboardSectionToHash("pipeline")).toBe("#/pipeline");
  });
});
