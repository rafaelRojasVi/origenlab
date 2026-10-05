import { describe, expect, it } from "vitest";
import type { DriveFolder, OpportunityCardData } from "./crmTypes";
import { findUses, knownQuoteNumbers, lastAndNext, parseQuoteNumber } from "./quoteNumbers";

// Every institution and number below is invented; the repository is public.

describe("parseQuoteNumber", () => {
  it.each([
    ["01246-26", 26, 1246],
    ["1013-26", 26, 1013], // leading zero dropped
    ["012392-26", 26, 1239], // a revision digit glued on
    ["011728A-25", 25, 1172], // a letter suffix, another year
    ["011024AI-26", 26, 1102],
    ["CN01247", 26, 1247], // as written in folder names, year from today
    ["  1247 ", 26, 1247],
    ["01247-2026", 26, 1247],
  ])("reads %j as year %d, correlative %d", (raw, year, correlative) => {
    expect(parseQuoteNumber(raw, 26)).toMatchObject({ year, correlative });
  });

  it.each(["", "abc", "-26", "12a34", "01247-"])("refuses %j", (raw) => {
    expect(parseQuoteNumber(raw, 26)).toBeNull();
  });
});

const card = (number: string, org: string) =>
  ({ quotes: [{ quote_id: number, quote_number: number, number_origin: null, revisions: [] }], organization: { organization_id: org, name: org, confirmation: null } }) as unknown as OpportunityCardData;
const folder = (numbers: string[], org: string | null, caseKey: string | null) =>
  ({ quote_numbers: numbers, organization_name: org, case_key: caseKey, documents: [], folder_id: null, folder_url: null, in_crm: 0 }) as DriveFolder;

const KNOWN = knownQuoteNumbers(
  [card("01245-26", "Laboratorio Andino"), card("011728A-25", "Instituto Sur")],
  [folder(["01245-26"], "Laboratorio Andino", null), folder(["01246-26"], null, "CN01246-Persona Ejemplo – Agrícola Norte"), folder(["012392-26"], "Universidad del Valle", null)],
);

describe("known quote numbers", () => {
  it("merges the CRM and the Drive archive, one entry per number, with where each was seen", () => {
    const byNumber = Object.fromEntries(KNOWN.map((k) => [k.number, k]));
    expect(byNumber["01245-26"]).toMatchObject({ label: "Laboratorio Andino", sources: ["CRM", "Drive"] });
    expect(byNumber["01246-26"]).toMatchObject({ label: "CN01246-Persona Ejemplo – Agrícola Norte", sources: ["Drive"] });
  });

  it("names the last number of the year and the next one, ignoring other years", () => {
    const { last, next } = lastAndNext(KNOWN, 26);
    expect(last?.number).toBe("01246-26");
    expect(next).toBe("01247-26");
    expect(lastAndNext(KNOWN, 27)).toEqual({ last: null, next: "00001-27" });
  });

  it("finds every use of a typed number, whatever the spelling", () => {
    expect(findUses(KNOWN, parseQuoteNumber("1246", 26)!).map((k) => k.number)).toEqual(["01246-26"]);
    expect(findUses(KNOWN, parseQuoteNumber("01239-26", 26)!).map((k) => k.number)).toEqual(["012392-26"]);
    expect(findUses(KNOWN, parseQuoteNumber("1300", 26)!)).toEqual([]);
    expect(findUses(KNOWN, parseQuoteNumber("1172", 26)!)).toEqual([]); // 011728A is -25
  });
});
