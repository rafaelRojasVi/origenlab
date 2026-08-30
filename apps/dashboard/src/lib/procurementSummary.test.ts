import { describe, expect, it } from "vitest";
import type { ProcurementStatus } from "../api/institutionIntel/types";
import {
  ACTIONABLE_OPPORTUNITIES_LABEL,
  summarizeProcurementStatus,
} from "./procurementSummary";

/** Pass `queueValue: null` to build a status with no current_opportunity_queue field at all. */
function status(overrides: Partial<ProcurementStatus["meta"]> = {}, queueValue: number | null = 4): ProcurementStatus {
  return {
    meta: {
      data_source: "institution_prospect_bundle",
      read_only: true,
      contract_version: "institution_prospect_contract_v4",
      supported_contract_version: true,
      reduced_mode: false,
      stale: false,
      canonical_reason: "institution_prospect_read_model",
      note: "",
      as_of_utc: "2026-08-30T00:12:01+00:00",
      not_persisted: true,
      contact_authorization: false,
      outreach_authorization: false,
      ...overrides,
    },
    operatorQueueSizes:
      queueValue === null ? {} : { current_opportunity_queue: queueValue },
    summaryOk: true,
  };
}

describe("summarizeProcurementStatus", () => {
  it("is available with the real queue value when the status is healthy", () => {
    const summary = summarizeProcurementStatus(status({}, 4));
    expect(summary.available).toBe(true);
    expect(summary.stale).toBe(false);
    expect(summary.value).toBe(4);
  });

  it("keeps a healthy zero as a real zero, not N/D", () => {
    const summary = summarizeProcurementStatus(status({}, 0));
    expect(summary.available).toBe(true);
    expect(summary.value).toBe(0);
  });

  it("shows the numeric value AND stale when meta.stale is true but otherwise usable", () => {
    const summary = summarizeProcurementStatus(status({ stale: true }, 4));
    expect(summary.available).toBe(true);
    expect(summary.stale).toBe(true);
    expect(summary.value).toBe(4);
  });

  it("is unavailable when reduced_mode is true, regardless of a present queue value", () => {
    const summary = summarizeProcurementStatus(status({ reduced_mode: true }, 4));
    expect(summary.available).toBe(false);
    expect(summary.value).toBe(0);
  });

  it("is unavailable when summaryOk is false", () => {
    const base = status({}, 4);
    const summary = summarizeProcurementStatus({ ...base, summaryOk: false });
    expect(summary.available).toBe(false);
  });

  it("is unavailable when the queue value is missing", () => {
    const summary = summarizeProcurementStatus(status({}, null));
    expect(summary.available).toBe(false);
  });

  it("is unavailable when status is null (request failed or not yet loaded)", () => {
    const summary = summarizeProcurementStatus(null);
    expect(summary.available).toBe(false);
    expect(summary.stale).toBe(false);
    expect(summary.value).toBe(0);
  });

  it("does not report a value as an available current_opportunity_queue count equal to any other feed's count by construction", () => {
    // current_opportunity_queue is a count of W1 actionable queue rows, not a
    // unique-tender count and not expected to equal any legacy feed's count.
    const summary = summarizeProcurementStatus(status({}, 4));
    expect(summary.value).toBe(4);
    expect(ACTIONABLE_OPPORTUNITIES_LABEL).toBe("Oportunidades accionables");
  });
});
