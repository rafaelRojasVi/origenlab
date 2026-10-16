import { describe, expect, it } from "vitest";
import { quoteLabel } from "./quoteLabel";

describe("quoteLabel", () => {
  it("prints CN + serial for a first revision", () => {
    expect(quoteLabel({ label: "00042-26", quote_number: "00042-26", revision_no: 1 })).toBe("CN00042");
  });

  it("takes the revision letter from the printed label", () => {
    expect(quoteLabel({ label: "00042A-26", quote_number: "00042-26", revision_no: 2 })).toBe("CN00042A");
  });

  it("derives the letter from the revision number when the label is missing", () => {
    expect(quoteLabel({ label: null, quote_number: "00042-26", revision_no: 3 })).toBe("CN00042B");
    expect(quoteLabel({ label: null, quote_number: "00042-26", revision_no: 1 })).toBe("CN00042");
    expect(quoteLabel({ label: null, quote_number: "00042-26" })).toBe("CN00042");
  });

  it("shows a historical number outside the series as it is", () => {
    expect(quoteLabel({ label: null, quote_number: "COT-7/2024", revision_no: 1 })).toBe("COT-7/2024");
  });
});
