import { describe, expect, it } from "vitest";
import { fmtClp, fmtRate, parseAmount, toClp } from "./fx";

describe("parseAmount", () => {
  it.each([
    ["1250", 1250],
    ["1.250", 1250],
    ["1.250.000", 1250000],
    ["1.250,5", 1250.5],
    ["1250,75", 1250.75],
    ["12.5", 12.5],
    ["0,5", 0.5],
    [" $ 3.400 ", 3400],
    ["USD 2.000", 2000],
  ])("reads %j as %d", (text, value) => {
    expect(parseAmount(text)).toBe(value);
  });

  it.each(["", "  ", "abc", "1,2,3", "1.2.3,4,5", "-5", "1e5"])("refuses %j", (text) => {
    expect(parseAmount(text)).toBeNull();
  });
});

describe("pesos", () => {
  it("converts and rounds to whole pesos", () => {
    expect(toClp(1250, 983.84)).toBe(1229800);
    expect(toClp(1, 41089.96)).toBe(41090);
  });

  it("formats pesos and rates the Chilean way", () => {
    expect(fmtClp(1229800)).toBe("$1.229.800");
    expect(fmtRate(983.84)).toBe("$983,84");
    expect(fmtRate(41089.96)).toBe("$41.089,96");
  });
});
