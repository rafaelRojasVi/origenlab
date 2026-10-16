import { describe, expect, it } from "vitest";
import { formatMoney } from "./money";

describe("formatMoney", () => {
  it("groups pesos without decimals and other currencies with two", () => {
    expect(formatMoney("1190000", "CLP")).toBe("$ 1.190.000");
    expect(formatMoney("1299.75", "USD")).toBe("US$ 1.299,75");
    expect(formatMoney(null, "CLP")).toBeNull();
    expect(formatMoney("no", "CLP")).toBeNull();
  });
});
