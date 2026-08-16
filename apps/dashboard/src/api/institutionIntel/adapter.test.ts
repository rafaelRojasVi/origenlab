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

describe("institutionIntelAdapter.getCurrentOpportunities", () => {
  function stubResponse(body: Record<string, unknown>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => body,
        text: async () => "",
        _url: url,
      })),
    );
  }

  it("fetches exactly the pre-filtered current_opportunity_queue route — never an institution profile", async () => {
    const fetchMock = vi.fn(async (_url: string) => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({ meta: META, limit: 20, offset: 0, total: 0, count: 0, items: [] }),
      text: async () => "",
    }));
    vi.stubGlobal("fetch", fetchMock);

    await institutionIntelAdapter.getCurrentOpportunities();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const calledUrl = String(fetchMock.mock.calls[0]?.[0]);
    expect(calledUrl).toContain("/operator/procurement/queues/current_opportunity");
    expect(calledUrl).not.toContain("/institutions/");
  });

  it("maps a real open/public row (Universidad de Antofagasta style fixture)", async () => {
    stubResponse({
      meta: META,
      limit: 20,
      offset: 0,
      total: 1,
      count: 1,
      items: [
        {
          institution_id: "univ-antofagasta-hash",
          display_name: "UNIVERSIDAD DE ANTOFAGASTA",
          tender_code: "4291-46-le26",
          coalesced_tender_id: "coalesced_tender_abc",
          equipment_category: "centrifuge",
          lifecycle_class: "active_open",
          prospect_strength_band: "high",
          close_timestamp: "2026-08-24T19:00:00",
          queue: "current_opportunity_queue",
          queue_row_id: "row-antofagasta-1",
          contact_authorization: false,
          outreach_authorization: false,
        },
      ],
    });

    const result = await institutionIntelAdapter.getCurrentOpportunities();

    expect(result.availability.status).toBe("available");
    if (result.availability.status !== "available") throw new Error("expected available");
    expect(result.availability.data).toEqual([
      expect.objectContaining({
        institutionDisplayName: "UNIVERSIDAD DE ANTOFAGASTA",
        tenderCode: "4291-46-le26",
        categoryOrTitle: "centrifuge",
        eligibilityStatus: "open_public",
      }),
    ]);
  });

  it("restricted tender (ISP / 1093303-5-CO26) is absent because the backend queue already excludes it — frontend adds no extra filtering", async () => {
    // Real production behavior: the restricted tender simply never appears in
    // this queue's response. This fixture reproduces exactly that shape (only
    // the open tender present) rather than reimplementing an eligibility
    // check client-side.
    stubResponse({
      meta: META,
      limit: 20,
      offset: 0,
      total: 1,
      count: 1,
      items: [
        {
          institution_id: "univ-antofagasta-hash",
          display_name: "UNIVERSIDAD DE ANTOFAGASTA",
          tender_code: "4291-46-le26",
          equipment_category: "centrifuge",
          prospect_strength_band: "high",
          close_timestamp: "2026-08-24T19:00:00",
          queue: "current_opportunity_queue",
          queue_row_id: "row-antofagasta-1",
          contact_authorization: false,
          outreach_authorization: false,
        },
      ],
    });

    const result = await institutionIntelAdapter.getCurrentOpportunities();
    expect(result.availability.status).toBe("available");
    if (result.availability.status !== "available") throw new Error("expected available");
    const tenderCodes = result.availability.data.map((row) => row.tenderCode);
    expect(tenderCodes).not.toContain("1093303-5-co26");
    expect(tenderCodes).not.toContain("1093303-5-CO26");
  });

  it("genuinely empty queue maps to available_empty, not not_available", async () => {
    stubResponse({ meta: META, limit: 20, offset: 0, total: 0, count: 0, items: [] });
    const result = await institutionIntelAdapter.getCurrentOpportunities();
    expect(result.availability.status).toBe("available_empty");
  });

  it("reduced_mode (feed unavailable) maps to not_available, never a fake empty queue", async () => {
    stubResponse({
      meta: { ...META, reduced_mode: true, canonical_reason: "missing_institution_prospect_packet" },
      limit: 20,
      offset: 0,
      total: 0,
      count: 0,
      items: [],
    });
    const result = await institutionIntelAdapter.getCurrentOpportunities();
    expect(result.availability.status).toBe("not_available");
  });
});

function stubTenderDetail(body: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
      text: async () => "",
    })),
  );
}

describe("institutionIntelAdapter.getLicitacionIntel", () => {
  const baseResponse = {
    tender_code: "745712-19-LP26",
    queue_meta: META,
    found_in_queue: true,
    queue_row: { display_name: "SAG" },
    queue_rows: [],
    t1_meta: {
      data_source: "tender_terms_read_model",
      read_only: true,
      contract_version: "tender_terms_publication_v1",
      supported_contract_version: true,
      terms_version: "anexo_t1_v2",
      as_of_utc: "2026-08-15T00:00:00Z",
      source_path: "tender_terms",
      source_kind: "tender_terms_bundle",
      artifact_basename: "tender_terms",
      canonical_reason: "tender_terms_read_model",
      reduced_mode: false,
      note: "",
      published: true,
      contact_authorization: false,
      outreach_authorization: false,
    },
    t1_published: true,
    tender_facts: [],
    items: [],
    coverage: {
      attachments_discovered: 2,
      attachments_downloaded: 2,
      is_complete: true,
      incomplete_reason_codes: [],
      unread_attachment_ids: [],
    },
  };

  it("maps eligibility to open_public from queue membership, never from a nonexistent queue-row field", async () => {
    stubTenderDetail({
      ...baseResponse,
      queue_row: { display_name: "SAG", procurement_eligibility_status: "restricted_invitation_unconfirmed" },
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.eligibilityStatus).toBe("open_public");
  });

  it("never infers procurementMethodRaw from tender_code suffix patterns", async () => {
    stubTenderDetail(baseResponse);
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.procurementMethodRaw).toBeNull();
  });

  it("maps item_quantity from an explicit item fact, not a top-level field", async () => {
    stubTenderDetail({
      ...baseResponse,
      items: [
        {
          item_id: "item-1",
          item_number: "1",
          item_label: "Balanza analitica",
          facts: [
            {
              fact_id: "f1",
              field_name: "item_quantity",
              state: "explicit",
              coverage_status: "complete",
              value: 3,
              evidence: [],
              candidates: [],
              reason_codes: [],
            },
          ],
        },
      ],
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.itemBudget.status).toBe("available");
    if (result?.itemBudget.status !== "available") throw new Error("expected available");
    expect(result.itemBudget.data[0].quantity).toBe(3);
  });

  it("maps incomplete T1 coverage to available_incomplete, never fully available", async () => {
    stubTenderDetail({
      ...baseResponse,
      tender_facts: [
        {
          fact_id: "f1",
          field_name: "delivery_days",
          state: "unknown",
          coverage_status: "incomplete",
          value: null,
          evidence: [],
          candidates: [],
          reason_codes: [],
        },
      ],
      coverage: {
        attachments_discovered: 3,
        attachments_downloaded: 1,
        is_complete: false,
        incomplete_reason_codes: ["attachment_download_incomplete"],
        unread_attachment_ids: ["att-2"],
      },
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.terms.status).toBe("available_incomplete");
    expect(result?.coverage.status).toBe("available_incomplete");
    if (result?.coverage.status !== "available_incomplete") throw new Error("expected available_incomplete");
    expect(result.coverage.reasonCodes).toContain("attachment_download_incomplete");
  });

  it("no T1 publication maps to not_available for terms/itemBudget/coverage", async () => {
    stubTenderDetail({ ...baseResponse, t1_published: false, coverage: null });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.terms.status).toBe("not_available");
    expect(result?.itemBudget.status).toBe("not_available");
    expect(result?.coverage.status).toBe("not_available");
  });

  it("never surfaces a numeric item_budget amount for a conflicting-state fact, even though the raw payload carries a value", async () => {
    stubTenderDetail({
      ...baseResponse,
      items: [
        {
          item_id: "item-1",
          item_number: "1",
          item_label: "Centrífuga refrigerada",
          facts: [
            {
              fact_id: "f1",
              field_name: "item_budget",
              state: "conflicting",
              coverage_status: "incomplete",
              value: 500000,
              evidence: [],
              candidates: [
                { value: 500000, evidence: [] },
                { value: 620000, evidence: [] },
              ],
              reason_codes: [],
            },
          ],
        },
      ],
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.itemBudget.status).toBe("available");
    if (result?.itemBudget.status !== "available") throw new Error("expected available");
    expect(result.itemBudget.data[0].amount).toBeNull();
  });

  it("never surfaces a numeric item_budget amount for an unknown/not_explicitly_found-state fact", async () => {
    stubTenderDetail({
      ...baseResponse,
      items: [
        {
          item_id: "item-1",
          item_number: "1",
          item_label: "Centrífuga refrigerada",
          facts: [
            {
              fact_id: "f1",
              field_name: "unit_price",
              state: "not_explicitly_found",
              coverage_status: "complete",
              value: null,
              evidence: [],
              candidates: [],
              reason_codes: [],
            },
          ],
        },
      ],
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.itemBudget.status).toBe("available");
    if (result?.itemBudget.status !== "available") throw new Error("expected available");
    expect(result.itemBudget.data[0].amount).toBeNull();
  });

  it("maps all six known T1 term field names to their Spanish operator-facing label, never the raw snake_case token", async () => {
    const knownFields: Record<string, string> = {
      contract_duration_months: "Duración del contrato",
      faithful_performance_guarantee_percent: "Garantía de fiel cumplimiento",
      maximum_delivery_days: "Plazo máximo de entrega",
      offer_validity_days: "Vigencia de la oferta",
      payment_deadline_days: "Plazo de pago",
      total_budget: "Presupuesto total",
    };
    stubTenderDetail({
      ...baseResponse,
      tender_facts: Object.keys(knownFields).map((field_name) => ({
        fact_id: field_name,
        field_name,
        state: "explicit",
        coverage_status: "complete",
        value: 1,
        evidence: [],
        candidates: [],
        reason_codes: [],
      })),
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.terms.status).toBe("available");
    if (result?.terms.status !== "available") throw new Error("expected available");
    for (const fact of result.terms.data) {
      expect(fact.label).toBe(knownFields[fact.fieldName]);
      expect(fact.label).not.toBe(fact.fieldName);
    }
  });

  it("falls back to a humanized (not raw snake_case) label for an unknown field name", async () => {
    stubTenderDetail({
      ...baseResponse,
      tender_facts: [
        {
          fact_id: "f1",
          field_name: "some_future_unmapped_field",
          state: "explicit",
          coverage_status: "complete",
          value: 1,
          evidence: [],
          candidates: [],
          reason_codes: [],
        },
      ],
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.terms.status).toBe("available");
    if (result?.terms.status !== "available") throw new Error("expected available");
    expect(result.terms.data[0].label).toBe("Some Future Unmapped Field");
    expect(result.terms.data[0].label).not.toBe("some_future_unmapped_field");
  });

  it("preserves the structured {days, day_basis} value maximum_delivery_days carries on the wire, rather than coercing it", async () => {
    stubTenderDetail({
      ...baseResponse,
      tender_facts: [
        {
          fact_id: "f1",
          field_name: "maximum_delivery_days",
          state: "explicit",
          coverage_status: "complete",
          value: { days: 30, day_basis: "business" },
          unit: "days",
          evidence: [],
          candidates: [],
          reason_codes: [],
        },
      ],
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.terms.status).toBe("available");
    if (result?.terms.status !== "available") throw new Error("expected available");
    expect(result.terms.data[0].value).toEqual({ days: 30, day_basis: "business" });
  });

  it("fails closed to null (never a raw stringifiable object) for a malformed structured fact value — missing days", async () => {
    stubTenderDetail({
      ...baseResponse,
      tender_facts: [
        {
          fact_id: "f1",
          field_name: "maximum_delivery_days",
          state: "explicit",
          coverage_status: "complete",
          value: { day_basis: "business" },
          unit: "days",
          evidence: [],
          candidates: [],
          reason_codes: [],
        },
      ],
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.terms.status).toBe("available");
    if (result?.terms.status !== "available") throw new Error("expected available");
    expect(result.terms.data[0].value).toBeNull();
  });

  it("fails closed to null for a malformed structured fact value — non-numeric days", async () => {
    stubTenderDetail({
      ...baseResponse,
      tender_facts: [
        {
          fact_id: "f1",
          field_name: "maximum_delivery_days",
          state: "explicit",
          coverage_status: "complete",
          value: { days: "thirty", day_basis: "business" },
          unit: "days",
          evidence: [],
          candidates: [],
          reason_codes: [],
        },
      ],
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.terms.status).toBe("available");
    if (result?.terms.status !== "available") throw new Error("expected available");
    expect(result.terms.data[0].value).toBeNull();
  });

  it("does expose the amount for an explicit/derived item_budget fact", async () => {
    stubTenderDetail({
      ...baseResponse,
      items: [
        {
          item_id: "item-1",
          item_number: "1",
          item_label: "Centrífuga refrigerada",
          facts: [
            {
              fact_id: "f1",
              field_name: "item_budget",
              state: "explicit",
              coverage_status: "complete",
              value: 6200000,
              evidence: [],
              candidates: [],
              reason_codes: [],
            },
          ],
        },
      ],
    });
    const result = await institutionIntelAdapter.getLicitacionIntel("745712-19-LP26");
    expect(result?.itemBudget.status).toBe("available");
    if (result?.itemBudget.status !== "available") throw new Error("expected available");
    expect(result.itemBudget.data[0].amount).toBe(6200000);
  });
});
