import { afterEach, describe, expect, it, vi } from "vitest";
import { institutionIntelAdapter } from "./adapter";

const META = {
  data_source: "institution_prospect_read_model",
  read_only: true,
  contract_version: "institution_prospect_contract_v4",
  supported_contract_version: true,
  reduced_mode: false,
  stale: false,
  canonical_reason: "institution_prospect_read_model",
  note: "",
  as_of_utc: "2026-08-15T12:12:01+00:00",
  not_persisted: true,
  contact_authorization: false,
  outreach_authorization: false,
};

function stubQueueRow(row: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        meta: META,
        limit: 20,
        offset: 0,
        total: 1,
        count: 1,
        items: [row],
      }),
      text: async () => "",
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("institutionIntelAdapter live W1 queue shapes", () => {
  it("preserves historical rental/comodato semantics without inventing a category", async () => {
    stubQueueRow({
      institution_id: "002c706c",
      display_name: "CORPORACION MUNICIPAL DE FOMENTO PRODUCTIVO DE ARICA",
      equipment_category: "",
      commercial_signal_type: "rental_or_comodato_signal",
      tender_count: "1",
      most_recent_observed_date: "2026-01-19",
      queue: "historical_prospect_queue",
      queue_row_id: "hist-live-1",
      contact_authorization: false,
      outreach_authorization: false,
    });

    const result = await institutionIntelAdapter.listQueueRows({
      queue: "historical_prospect_queue",
    });

    expect(result.items[0]).toMatchObject({
      queue: "historical_prospect_queue",
      institutionDisplayName:
        "CORPORACION MUNICIPAL DE FOMENTO PRODUCTIVO DE ARICA",
      category: "",
      commercialSignalType: "rental_or_comodato_signal",
      tenderCount: 1,
    });
  });

  it("maps institution review-cluster fields from W1", async () => {
    stubQueueRow({
      institution_id: "",
      display_name: "",
      institution_review_cluster_id: "008550af43420e2d",
      cluster_resolution_status: "review_only_fragmented_identity",
      member_profile_ids: ["profile-a", "profile-b"],
      cluster_reason_codes: [
        "normalized_name_multi_profile_fragmentation",
        "review_only_cluster_no_auto_merge",
      ],
      confirmed_account: false,
      queue: "institution_match_review_queue",
      queue_row_id: "identity-live-1",
      contact_authorization: false,
      outreach_authorization: false,
    });

    const result = await institutionIntelAdapter.listQueueRows({
      queue: "institution_match_review_queue",
    });

    expect(result.items[0]).toMatchObject({
      queue: "institution_match_review_queue",
      institutionId: "",
      institutionDisplayName: "",
      reviewClusterId: "008550af43420e2d",
      resolutionStatus: "review_only_fragmented_identity",
      memberProfileIds: ["profile-a", "profile-b"],
      confirmedAccount: false,
    });
  });

  it("maps retender family fields and member tender codes from W1", async () => {
    stubQueueRow({
      family_id: "0009a395",
      buyer_key: "buyer_name:i municipalidad de coquimbo",
      member_tender_codes: ["2446-4-le26", "2446-16-le26"],
      unresolved_relationship_count: "1",
      recurrence_status: "recurrence_not_established",
      family_resolution_status: "retender_review_required",
      family_reason_codes: [
        "independence_not_established",
        "retender_review_required",
      ],
      queue: "retender_review_queue",
      queue_row_id: "retender-live-1",
      contact_authorization: false,
      outreach_authorization: false,
    });

    const result = await institutionIntelAdapter.listQueueRows({
      queue: "retender_review_queue",
    });

    expect(result.items[0]).toMatchObject({
      queue: "retender_review_queue",
      institutionId: "",
      institutionDisplayName: "",
      buyerKey: "buyer_name:i municipalidad de coquimbo",
      tenderCodes: ["2446-4-le26", "2446-16-le26"],
      recurrenceStatus: "recurrence_not_established",
      resolutionStatus: "retender_review_required",
      unresolvedRelationshipCount: "1",
    });
  });
});
