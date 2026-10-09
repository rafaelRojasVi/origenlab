import { describe, expect, it } from "vitest";
import type { OpportunityCardData } from "./crmTypes";
import { applyCaseReceipt, type CaseCommandReceipt } from "./caseCommands";
import { boardColumnOf, byLatestActivity } from "./stage";

function card(over: Partial<OpportunityCardData>): OpportunityCardData {
  return {
    opportunity_id: "o",
    title: "t",
    stage: "quoting",
    version: 3,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    closed_at: null,
    close_reason: null,
    organization: null,
    other_organizations: [],
    contact: null,
    quotes: [],
    quote_numbers: [],
    revision_count: 0,
    latest_revision: null,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "x", source: "suggested", due_at: null },
    ...over,
  };
}

function receipt(over: Partial<CaseCommandReceipt>): CaseCommandReceipt {
  return {
    command: "advance-case-stage",
    opportunity_id: "o",
    opportunity_version: 4,
    idempotency_key: "k",
    command_receipt_id: "r",
    replayed: false,
    ...over,
  };
}

const NOW = new Date("2026-10-09T15:00:00Z");

describe("applyCaseReceipt", () => {
  it("moves the card to the receipt's stage and version at once, and to the top of «Más recientes»", () => {
    const other = card({ opportunity_id: "p", updated_at: "2026-10-01T00:00:00Z" });
    const [moved, untouched] = applyCaseReceipt([card({}), other], receipt({ stage: "negotiating" }), NOW);
    expect(moved.stage).toBe("negotiating");
    expect(moved.version).toBe(4);
    expect(boardColumnOf(moved, NOW)).toBe("conversacion");
    expect(untouched).toBe(other);
    expect([other, moved].sort(byLatestActivity)[0]).toBe(moved);
  });

  it("closes the card on a terminal stage", () => {
    const [won] = applyCaseReceipt([card({ stage: "negotiating" })], receipt({ command: "record-case-won", stage: "won" }), NOW);
    expect(won.stage).toBe("won");
    expect(won.closed_at).toBe(NOW.toISOString());
  });

  it("keeps the stage when the command does not change it, and ignores a receipt not newer than the card", () => {
    const [noted] = applyCaseReceipt([card({})], receipt({ command: "create-task" }), NOW);
    expect(noted.stage).toBe("quoting");
    expect(noted.version).toBe(4);
    const c = card({ version: 5 });
    expect(applyCaseReceipt([c], receipt({ stage: "lost" }), NOW)[0]).toBe(c);
  });
});
