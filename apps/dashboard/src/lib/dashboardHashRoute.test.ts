import { describe, expect, it } from "vitest";
import { parseDashboardSectionFromHash, dashboardSectionToHash } from "./dashboardHashRoute";

describe("dashboardHashRoute — pipeline section", () => {
  it("parses #/pipeline into the pipeline section", () => {
    expect(parseDashboardSectionFromHash("#/pipeline")).toBe("pipeline");
  });

  it("round-trips the pipeline section to its hash", () => {
    expect(dashboardSectionToHash("pipeline")).toBe("#/pipeline");
  });

  it("parses #/ventas as an alias for the pipeline (Ventas) section", () => {
    expect(parseDashboardSectionFromHash("#/ventas")).toBe("pipeline");
  });

  it("parses #/ventas with a query string as the pipeline section", () => {
    expect(
      parseDashboardSectionFromHash("#/ventas?opportunity=sales_" + "a".repeat(32)),
    ).toBe("pipeline");
  });
});
