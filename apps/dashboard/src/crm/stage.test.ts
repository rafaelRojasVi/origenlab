import { describe, expect, it } from "vitest";
import type { OpportunityCardData, RevisionCard } from "./crmTypes";
import { HISTORICAL_STAGE_LABEL, stageBasis, stageDisplay } from "./stage";

function rev(origin: string | null): RevisionCard {
  return {
    revision_id: "r",
    revision_no: 1,
    status: "sent",
    origin,
    sent_at: "2026-05-01T00:00:00Z",
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: null,
    drive: null,
    quote_number: "1300",
  };
}

function card(over: Partial<OpportunityCardData>, origins: (string | null)[] = ["historical_import"]): OpportunityCardData {
  return {
    opportunity_id: "o",
    title: "t",
    stage: "quoting",
    created_at: null,
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: null,
    other_organizations: [],
    contact: null,
    quotes: [{ quote_id: "q", quote_number: "1300", number_origin: null, revisions: origins.map(rev) }],
    quote_numbers: ["1300"],
    revision_count: origins.length,
    latest_revision: null,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "x", source: "suggested", due_at: null },
    ...over,
  };
}

describe("stage basis", () => {
  it("calls an imported, open `quoting` case a historical sent quotation, not a verified status", () => {
    const c = card({});
    expect(stageBasis(c)).toBe("historical_import");
    expect(stageDisplay(c).label).toBe(HISTORICAL_STAGE_LABEL);
    expect(stageDisplay(c).title).toMatch(/No confirma el estado comercial actual/);
  });

  it("treats the stage as a CRM record once anything authored exists, the case closes, or the stage moved", () => {
    expect(stageBasis(card({}, ["historical_import", "authored"]))).toBe("crm_record");
    expect(stageBasis(card({ closed_at: "2026-06-01T00:00:00Z", stage: "lost" }))).toBe("crm_record");
    expect(stageBasis(card({ stage: "negotiating" }))).toBe("crm_record");
    expect(stageBasis(card({}, []))).toBe("crm_record");
    expect(stageDisplay(card({ stage: "negotiating" })).label).toBe("Conversación");
  });
});
