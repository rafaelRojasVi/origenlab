import { describe, expect, it } from "vitest";
import { institutionFromQuoteFile, quotePdfOf } from "./sentQuote";

// Mirrors apps/api tests/test_v2_mail_rules.py (institution_segment): every name below is invented.
describe("institutionFromQuoteFile", () => {
  it.each([
    ["CN09876-Instituto Ficticio de Ensayos (IFDE).pdf", "Instituto Ficticio de Ensayos (IFDE)"],
    ["CN09876-Persona Ficticia-Empresa Ficticia SA.pdf", "Empresa Ficticia SA"],
    ["CN09876-Persona Ficticia - Comercial Ficticia S.A.pdf", "Comercial Ficticia S.A"],
    ["CN09876-Persona Ficticia – Ficticia Chile.pdf", "Ficticia Chile"],
    ["CN09876-Persona Ficticia - EMPRESA FICTICIA SPA-AB12.pdf", "EMPRESA FICTICIA SPA"],
    ["CN09876-Pedro Ficticio Pérez.pdf", null],
    ["CN09876-Pedro Ficticio-AB12.pdf", null],
    ["CN09876-Empresa Uno SA - Empresa Dos Ltda.pdf", null],
    ["Folleto.pdf", null],
    // One part with a person before the institution: never suggested (mirrors the API).
    ["CN09876-Pedro Ficticio Universidad Ficticia.pdf", null],
    ["CN09876-Pedro Ficticio (UFI).pdf", null],
  ])("%s → %s", (file, expected) => {
    expect(institutionFromQuoteFile(file)).toBe(expected);
  });
});

describe("quotePdfOf", () => {
  const doc = (filename: string, tokens: string[], recorded: unknown = null) =>
    ({ sha256: "a".repeat(64), filename, cn_tokens: tokens, recorded }) as never;
  it("finds the one unrecorded quote PDF a message carries", () => {
    const messages = [{ source_record_id: "s-1", subject: null, sent_at: "2026-10-13T12:00:00+00:00",
      documents: [doc("Folleto.pdf", []), doc("CN09876-Persona Ficticia.pdf", ["CN09876"])] }];
    expect(quotePdfOf(messages)).toEqual({ sourceRecordId: "s-1", filename: "CN09876-Persona Ficticia.pdf", token: "CN09876" });
  });
  it("offers nothing when the PDF is already a quote or there are several", () => {
    const recorded = [{ source_record_id: "s-1", subject: null, sent_at: null,
      documents: [doc("CN09876-X.pdf", ["CN09876"], { quote_number: "09876-26", revision_no: 1, on_this_case: true })] }];
    expect(quotePdfOf(recorded)).toBeNull();
    const two = [{ source_record_id: "s-1", subject: null, sent_at: null,
      documents: [doc("CN09876-X.pdf", ["CN09876"]), doc("CN09877-X.pdf", ["CN09877"])] }];
    expect(quotePdfOf(two)).toBeNull();
  });
});
