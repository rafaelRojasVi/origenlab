import { describe, expect, it } from "vitest";

import type { ArchiveCase, CaseRevision } from "../api/caseArchive";
import { EMPTY_CASE_FILTER, crmChip, filterCases } from "./caseArchive";

function rev(over: Partial<CaseRevision> = {}): CaseRevision {
  return {
    revision_no: 1,
    document_sha256: "a".repeat(64),
    sent_at: "2026-05-01T10:00:00-04:00",
    role: "quotation",
    pending_code: null,
    gmail_message_id: "g1",
    gmail_url: "https://mail.google.com/mail/u/0/#all/g1",
    lifecycle: "historical_sent",
    lifecycle_label: "Cotización enviada (histórica)",
    archive: { status: "legacy_only", file: null, legacy_files: [] },
    ...over,
  };
}

function kase(over: Partial<ArchiveCase> = {}): ArchiveCase {
  return {
    case_key: "11111111-1111-5111-8111-111111111111",
    opening_quote_number: "01244-26",
    printed_addressee: "Sylvia Saavedra - Gemco",
    printed_organization: "Gemco",
    folder_name: "Caso 01244 — Sylvia Saavedra - Gemco",
    crm: {
      status: "ready_to_import",
      source: "import_plan",
      plan_status: "ready",
      organization_confirmed: true,
      organization_action: "confirm_existing",
      organization_id: "org",
      reasons: [],
      warnings: [],
    },
    archive: { folder: null, planned_folder_action: "create_case_folder", counts: { legacy_only: 1 } },
    flags: [],
    quotes: [{ quote_number: "01244-26", collision: null, revisions: [rev()] }],
    ...over,
  };
}

const waitingArchived = kase({
  case_key: "22222222-2222-5222-8222-222222222222",
  opening_quote_number: "01246-26",
  printed_addressee: "Carla Peñailillo – Corteva",
  printed_organization: "Corteva",
  folder_name: "Caso 01246 — Carla Peñailillo – Corteva",
  crm: { ...kase().crm, status: "pending_organization_confirmation", organization_confirmed: false, organization_id: null },
  flags: ["missing_organization"],
  quotes: [{ quote_number: "01246-26", collision: null, revisions: [rev({ archive: { status: "archived_verified", file: null, legacy_files: [] } })] }],
});

describe("caseArchive", () => {
  it("never shows a Drive-archived case as CRM-ready", () => {
    const chip = crmChip(waitingArchived);
    expect(chip.label).toBe("Pendiente: confirmar institución");
    expect(chip.tone).toBe("warn");
  });

  it("filters by CRM status and by archive status independently", () => {
    const all = [kase(), waitingArchived];
    expect(filterCases(all, { ...EMPTY_CASE_FILTER, crm: "ready_to_import" })).toHaveLength(1);
    expect(filterCases(all, { ...EMPTY_CASE_FILTER, archive: "archived_verified" })[0].case_key).toBe(waitingArchived.case_key);
    expect(filterCases(all, { ...EMPTY_CASE_FILTER, crm: "ready_to_import", archive: "archived_verified" })).toHaveLength(0);
  });

  it("finds a case by any spelling of its quote serial or by folded text", () => {
    const all = [kase(), waitingArchived];
    for (const q of ["1246", "01246", "CN01246", "01246-26"]) {
      expect(filterCases(all, { ...EMPTY_CASE_FILTER, text: q }).map((c) => c.opening_quote_number)).toEqual(["01246-26"]);
    }
    expect(filterCases(all, { ...EMPTY_CASE_FILTER, text: "penailillo" })).toHaveLength(1);
  });

  it("filters by flag", () => {
    expect(filterCases([kase(), waitingArchived], { ...EMPTY_CASE_FILTER, flag: "missing_organization" })).toHaveLength(1);
  });
});
