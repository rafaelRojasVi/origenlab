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

const mail = (quote_number: string, first_seen_at: string, messages = 1) => ({ quote_number, first_seen_at, messages });

describe("Gmail as a third source", () => {
  const WITH_MAIL = knownQuoteNumbers(
    [card("01245-26", "Laboratorio Andino")],
    [folder(["01246-26"], null, "CN01246-Persona Ejemplo – Agrícola Norte")],
    [mail("CN01245", "2026-03-02T15:00:00Z"), mail("CN01247", "2026-03-10T15:00:00Z", 2), mail("CN01248", "2026-03-11T15:00:00Z")],
  );
  const byNumber = Object.fromEntries(WITH_MAIL.map((k) => [k.number, k]));

  it("adds a number seen only in Gmail, its year from the day it was first seen", () => {
    expect(byNumber["CN01247"]).toMatchObject({ year: 26, correlative: 1247, label: null, sources: ["Gmail"] });
  });

  it("joins a Gmail sighting to the CRM or Drive entry of the same number", () => {
    expect(byNumber["01245-26"]).toMatchObject({ label: "Laboratorio Andino", sources: ["CRM", "Gmail"] });
    expect(byNumber["CN01245"]).toBeUndefined();
  });

  it("moves the last and next numbers past what Gmail shows as sent", () => {
    const { last, next } = lastAndNext(WITH_MAIL, 26);
    expect(last).toMatchObject({ number: "CN01248", sources: ["Gmail"] });
    expect(next).toBe("01249-26");
  });

  it("finds a typed number that only Gmail knows", () => {
    expect(findUses(WITH_MAIL, parseQuoteNumber("1247", 26)!).map((k) => k.sources)).toEqual([["Gmail"]]);
  });

  it("reads the year in Santiago: New Year's Eve evening is still the old year", () => {
    const [k] = knownQuoteNumbers([], [], [mail("CN01300", "2026-01-01T02:00:00Z")]);
    expect(k).toMatchObject({ year: 25, correlative: 1300 });
  });

  it("ignores what is not a number and keeps working without Gmail", () => {
    expect(knownQuoteNumbers([], [], [mail("CN", "2026-03-02T15:00:00Z"), mail("CN01250", "not a date")])).toEqual([]);
    expect(knownQuoteNumbers([card("01245-26", "Laboratorio Andino")], [])).toHaveLength(1);
  });
});
