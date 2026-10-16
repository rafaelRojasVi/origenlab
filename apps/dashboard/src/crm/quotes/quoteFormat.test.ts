import { describe, expect, it } from "vitest";
import { amountTextToDecimal, decimalToPercentText, formatPct, percentTextToDecimal, priceTextToDecimal, readQuantity } from "./quoteFormat";

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

  it("reads a typed price the Chilean way, without floating point", () => {
    expect(priceTextToDecimal("1.500.000")).toBe("1500000");
    expect(priceTextToDecimal("$ 1.500.000")).toBe("1500000");
    expect(priceTextToDecimal("1500000")).toBe("1500000");
    expect(priceTextToDecimal("1.500.000,5")).toBe("1500000.5");
    expect(priceTextToDecimal("12345678901234567,25")).toBe("12345678901234567.25");
    expect(priceTextToDecimal("007")).toBe("7");
    expect(priceTextToDecimal("")).toBeNull();
    expect(priceTextToDecimal("mil")).toBe("mil");
  });

  it("reads a cost in any currency the same way", () => {
    expect(amountTextToDecimal("€ 6.900,50")).toBe("6900.50");
    expect(amountTextToDecimal("US$ 1299.75")).toBe("1299.75");
    expect(amountTextToDecimal("980")).toBe("980");
  });

  it("reads a typed quantity with a decimal comma, and refuses a dot that may be thousands", () => {
    expect(readQuantity("2,5")).toEqual({ value: "2.5", problem: null });
    expect(readQuantity(" 3 ")).toEqual({ value: "3", problem: null });
    expect(readQuantity("2.5")).toEqual({ value: "2.5", problem: null });
    expect(readQuantity("")).toEqual({ value: "", problem: null });
    expect(readQuantity("1.000")).toEqual({ value: "1.000", problem: "Usa coma para decimales (1,5) — ¿quisiste decir 1000?" });
    expect(readQuantity("12.500").problem).toBe("Usa coma para decimales (1,5) — ¿quisiste decir 12500?");
  });
});
