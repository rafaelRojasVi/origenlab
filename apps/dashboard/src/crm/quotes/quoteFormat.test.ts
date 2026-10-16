import { describe, expect, it } from "vitest";
import { decimalToPercentText, formatPct, percentTextToDecimal, priceTextToDecimal, quantityTextToDecimal } from "./quoteFormat";

describe("quote number formatting", () => {
  it("prints an API ratio as a percentage with one decimal, es-CL", () => {
    expect(formatPct("0.2857")).toBe("28,6 %");
    expect(formatPct("0.4")).toBe("40,0 %");
    expect(formatPct("-0.05")).toBe("-5,0 %");
    expect(formatPct(null)).toBeNull();
    expect(formatPct("abc")).toBeNull();
  });

  it("shows a stored recargo as the percentage the operator typed, exactly", () => {
    expect(decimalToPercentText("0.45")).toBe("45");
    expect(decimalToPercentText("0.455")).toBe("45,5");
    expect(decimalToPercentText("0.3")).toBe("30");
    expect(decimalToPercentText("0.05")).toBe("5");
    expect(decimalToPercentText("1")).toBe("100");
    expect(decimalToPercentText("0.000001")).toBe("0,0001");
    expect(decimalToPercentText(null)).toBe("");
  });

  it("turns a typed percentage into the API's ratio without floating point", () => {
    expect(percentTextToDecimal("45")).toBe("0.45");
    expect(percentTextToDecimal("45,5")).toBe("0.455");
    expect(percentTextToDecimal("5")).toBe("0.05");
    expect(percentTextToDecimal("100")).toBe("1");
    expect(percentTextToDecimal(" 12.25 ")).toBe("0.1225");
    expect(percentTextToDecimal("0")).toBe("0");
    expect(percentTextToDecimal("")).toBeNull();
    // Not a number: passed through so the editor reports it instead of guessing.
    expect(percentTextToDecimal("4x")).toBe("4x");
  });

  it("reads a typed price the Chilean way", () => {
    expect(priceTextToDecimal("1.500.000")).toBe("1500000");
    expect(priceTextToDecimal("$ 1.500.000")).toBe("1500000");
    expect(priceTextToDecimal("1500000")).toBe("1500000");
    expect(priceTextToDecimal("")).toBeNull();
    expect(priceTextToDecimal("mil")).toBe("mil");
  });

  it("reads a typed quantity with a decimal comma", () => {
    expect(quantityTextToDecimal("2,5")).toBe("2.5");
    expect(quantityTextToDecimal(" 3 ")).toBe("3");
    expect(quantityTextToDecimal("")).toBe("");
  });
});
