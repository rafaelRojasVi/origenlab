import { describe, expect, it } from "vitest";
import {
  parseCustomerQuote,
  parseCustomerQuoteDrivePendingListResponse,
  parseCustomerQuoteEventListResponse,
  parseCustomerQuoteGlobalListResponse,
  parseCustomerQuoteListResponse,
  parseCustomerQuoteReadResponse,
} from "./customerQuoteParse";

const QUOTE_ID = `quote_${"a".repeat(32)}`;
const SALES_ID = `sales_${"b".repeat(32)}`;

function rawWorkspace(overrides: Record<string, unknown> = {}) {
  return {
    provider: "google_drive",
    provisioning_status: "ready",
    folder_id: "folder-1",
    folder_web_url: "https://drive.google.com/drive/folders/folder-1",
    sheet_file_id: "sheet-1",
    sheet_web_url: "https://docs.google.com/spreadsheets/d/sheet-1/edit",
    failure_category: null,
    attempt_count: 1,
    version: 3,
    retryable: true,
    lease_expires_at: null,
    requested_at: "2026-08-30T14:00:00+00:00",
    completed_at: "2026-08-30T14:00:05+00:00",
    ...overrides,
  };
}

function rawQuote(overrides: Record<string, unknown> = {}) {
  return {
    quote_id: QUOTE_ID,
    sales_opportunity_id: SALES_ID,
    quote_number: "CN011729",
    document_number: "CN00011729",
    quote_origin: "generated",
    sales_opportunity_title: "Centrífuga CEAF",
    status: "draft",
    version: 1,
    latest_revision_number: 1,
    revision_status: "draft",
    revision_updated_by: "tatiana@origenlab.cl",
    revision_updated_at: "2026-08-30T14:00:00+00:00",
    board_stage: "preparation",
    created_by: "tatiana@origenlab.cl",
    updated_by: "tatiana@origenlab.cl",
    created_at: "2026-08-30T14:00:00+00:00",
    updated_at: "2026-08-30T14:00:00+00:00",
    drive_workspace: rawWorkspace(),
    ...overrides,
  };
}

describe("parseCustomerQuote", () => {
  it("parses a complete quote with a ready workspace", () => {
    const quote = parseCustomerQuote(rawQuote());

    expect(quote.quote_number).toBe("CN011729");
    expect(quote.document_number).toBe("CN00011729");
    expect(quote.sales_opportunity_title).toBe("Centrífuga CEAF");
    expect(quote.status).toBe("draft");
    expect(quote.latest_revision_number).toBe(1);
    expect(quote.drive_workspace.provisioning_status).toBe("ready");
    expect(quote.drive_workspace.folder_web_url).toBe(
      "https://drive.google.com/drive/folders/folder-1",
    );
    expect(quote.drive_workspace.sheet_web_url).toBe(
      "https://docs.google.com/spreadsheets/d/sheet-1/edit",
    );
  });

  it("parses pending and failed workspace states", () => {
    const pending = parseCustomerQuote(
      rawQuote({
        drive_workspace: rawWorkspace({
          provisioning_status: "pending",
          folder_id: null,
          folder_web_url: null,
          sheet_file_id: null,
          sheet_web_url: null,
          completed_at: null,
        }),
      }),
    );

    expect(pending.drive_workspace.provisioning_status).toBe("pending");
    expect(pending.drive_workspace.folder_web_url).toBeNull();

    const failed = parseCustomerQuote(
      rawQuote({
        drive_workspace: rawWorkspace({
          provisioning_status: "failed",
          failure_category: "drive_unavailable",
          sheet_file_id: null,
          sheet_web_url: null,
          completed_at: null,
        }),
      }),
    );

    expect(failed.drive_workspace.provisioning_status).toBe("failed");
    expect(failed.drive_workspace.failure_category).toBe("drive_unavailable");
  });

  it("accepts folder_ready as a valid drive_workspace.provisioning_status", () => {
    const folderReady = parseCustomerQuote(
      rawQuote({
        drive_workspace: rawWorkspace({
          provisioning_status: "folder_ready",
          sheet_file_id: null,
          sheet_web_url: null,
        }),
      }),
    );

    expect(folderReady.drive_workspace.provisioning_status).toBe("folder_ready");
    expect(folderReady.drive_workspace.sheet_web_url).toBeNull();
  });

  it("nulls Drive links that are not validated https Google URLs", () => {
    const quote = parseCustomerQuote(
      rawQuote({
        drive_workspace: rawWorkspace({
          folder_web_url: "http://drive.google.com/insecure",
          sheet_web_url: "https://evil.example.com/spreadsheets/d/x",
        }),
      }),
    );

    expect(quote.drive_workspace.folder_web_url).toBeNull();
    expect(quote.drive_workspace.sheet_web_url).toBeNull();
  });

  it("nulls javascript and lookalike-host URLs", () => {
    const quote = parseCustomerQuote(
      rawQuote({
        drive_workspace: rawWorkspace({
          // eslint-disable-next-line no-script-url
          folder_web_url: "javascript:alert(1)",
          sheet_web_url: "https://docs.google.com.evil.example/spreadsheets",
        }),
      }),
    );

    expect(quote.drive_workspace.folder_web_url).toBeNull();
    expect(quote.drive_workspace.sheet_web_url).toBeNull();
  });

  it("parses retryable and lease_expires_at", () => {
    const leased = parseCustomerQuote(
      rawQuote({
        drive_workspace: rawWorkspace({
          provisioning_status: "pending",
          retryable: false,
          lease_expires_at: "2026-08-30T14:05:00+00:00",
        }),
      }),
    );

    expect(leased.drive_workspace.retryable).toBe(false);
    expect(leased.drive_workspace.lease_expires_at).toBe(
      "2026-08-30T14:05:00+00:00",
    );
  });

  it("rejects a missing retryable flag", () => {
    const raw = rawWorkspace();
    delete (raw as Record<string, unknown>).retryable;

    expect(() =>
      parseCustomerQuote(rawQuote({ drive_workspace: raw })),
    ).toThrow();
  });

  it("rejects an unknown provisioning status", () => {
    expect(() =>
      parseCustomerQuote(
        rawQuote({
          drive_workspace: rawWorkspace({
            provisioning_status: "exploded",
          }),
        }),
      ),
    ).toThrow();
  });

  it("rejects a malformed quote id", () => {
    expect(() => parseCustomerQuote(rawQuote({ quote_id: "not-an-id" }))).toThrow();
  });
});

describe("parseCustomerQuoteListResponse", () => {
  it("parses meta and items", () => {
    const response = parseCustomerQuoteListResponse({
      meta: { count: 1 },
      items: [rawQuote()],
    });

    expect(response.meta.count).toBe(1);
    expect(response.items).toHaveLength(1);
    expect(response.items[0].quote_number).toBe("CN011729");
  });
});

describe("parseCustomerQuoteReadResponse", () => {
  it("parses the item envelope", () => {
    const response = parseCustomerQuoteReadResponse({
      meta: { data_source: "postgres", read_only: true },
      item: rawQuote(),
    });

    expect(response.item.quote_id).toBe(QUOTE_ID);
  });
});

describe("parseCustomerQuoteGlobalListResponse", () => {
  it("parses items with sales-opportunity context fields", () => {
    const raw = {
      meta: { count: 1, total_count: 1, limit: 100, offset: 0 },
      items: [
        {
          quote: rawQuote(),
          sales_opportunity_stage: "quoting",
          sales_opportunity_owner_key: "tatiana@origenlab.cl",
          organization_display_name: "Hospital Regional de Rancagua",
          contact_display_name: "Marcela Soto",
          contact_primary_email: "marcela.soto@hospitalrancagua.cl",
          next_task_title: null,
          next_task_due_at: null,
        },
      ],
    };

    const parsed = parseCustomerQuoteGlobalListResponse(raw);

    expect(parsed.meta.total_count).toBe(1);
    expect(parsed.items[0].quote.quote_number).toBe("CN011729");
    expect(parsed.items[0].sales_opportunity_stage).toBe("quoting");
    expect(parsed.items[0].organization_display_name).toBe(
      "Hospital Regional de Rancagua",
    );
  });

  it("rejects an unknown sales opportunity stage", () => {
    expect(() =>
      parseCustomerQuoteGlobalListResponse({
        meta: { count: 1, total_count: 1, limit: 100, offset: 0 },
        items: [
          {
            quote: rawQuote(),
            sales_opportunity_stage: "invented",
            sales_opportunity_owner_key: "tatiana@origenlab.cl",
            organization_display_name: null,
            contact_display_name: null,
            contact_primary_email: null,
            next_task_title: null,
            next_task_due_at: null,
          },
        ],
      }),
    ).toThrow(/stage/);
  });
});

describe("parseCustomerQuoteDrivePendingListResponse", () => {
  it("parses a Drive-only pending workspace item", () => {
    const raw = {
      meta: { count: 1 },
      items: [
        {
          folder_id: "folder-1",
          folder_name: "CN01191-ICN Chile",
          folder_web_url: "https://drive.google.com/drive/folders/folder-1",
          document_identifier: "CN01191",
          created_time: "2026-08-01T12:00:00+00:00",
          modified_time: "2026-08-15T09:30:00+00:00",
        },
      ],
    };

    const parsed = parseCustomerQuoteDrivePendingListResponse(raw);

    expect(parsed.meta.count).toBe(1);
    expect(parsed.items[0].folder_id).toBe("folder-1");
    expect(parsed.items[0].folder_name).toBe("CN01191-ICN Chile");
    expect(parsed.items[0].document_identifier).toBe("CN01191");
  });

  it("nulls out a non-Drive-hosted folder_web_url instead of trusting it", () => {
    const raw = {
      meta: { count: 1 },
      items: [
        {
          folder_id: "folder-1",
          folder_name: "CN01191-ICN Chile",
          folder_web_url: "https://evil.example.com/phish",
          document_identifier: "CN01191",
          created_time: null,
          modified_time: null,
        },
      ],
    };

    const parsed = parseCustomerQuoteDrivePendingListResponse(raw);

    expect(parsed.items[0].folder_web_url).toBeNull();
  });

  it("accepts a null document_identifier for an ambiguous folder name", () => {
    const raw = {
      meta: { count: 1 },
      items: [
        {
          folder_id: "folder-1",
          folder_name: "Sin prefijo reconocible",
          folder_web_url: "https://drive.google.com/drive/folders/folder-1",
          document_identifier: null,
          created_time: null,
          modified_time: null,
        },
      ],
    };

    const parsed = parseCustomerQuoteDrivePendingListResponse(raw);

    expect(parsed.items[0].document_identifier).toBeNull();
  });
});

describe("parseCustomerQuote workflow fields", () => {
  it("parses quote_origin, revision_status, and board_stage", () => {
    const quote = parseCustomerQuote(
      rawQuote({
        quote_origin: "adopted",
        revision_status: "approved",
        board_stage: "approved_to_send",
      }),
    );

    expect(quote.quote_origin).toBe("adopted");
    expect(quote.revision_status).toBe("approved");
    expect(quote.board_stage).toBe("approved_to_send");
  });

  it("rejects an unknown quote_origin", () => {
    expect(() => parseCustomerQuote(rawQuote({ quote_origin: "invented" }))).toThrow();
  });

  it("rejects an unknown revision_status", () => {
    expect(() => parseCustomerQuote(rawQuote({ revision_status: "invented" }))).toThrow();
  });

  it("rejects an unknown board_stage", () => {
    expect(() => parseCustomerQuote(rawQuote({ board_stage: "drive_intake" }))).toThrow();
  });

  it("accepts every board_stage the API can return", () => {
    for (const stage of [
      "preparation",
      "review",
      "approved_to_send",
      "sent_follow_up",
    ]) {
      expect(() => parseCustomerQuote(rawQuote({ board_stage: stage }))).not.toThrow();
    }
  });
});

describe("parseCustomerQuoteEventListResponse", () => {
  it("parses an event history list", () => {
    const raw = {
      meta: { count: 1 },
      items: [
        {
          event_id: "event_1",
          event_type: "quote_submitted_for_review",
          actor_key: "tatiana@origenlab.cl",
          payload: { revision_number: 1, from_status: "draft", to_status: "pending_approval" },
          created_at: "2026-09-02T12:00:00+00:00",
        },
      ],
    };

    const parsed = parseCustomerQuoteEventListResponse(raw);

    expect(parsed.meta.count).toBe(1);
    expect(parsed.items[0].event_type).toBe("quote_submitted_for_review");
    expect(parsed.items[0].payload.from_status).toBe("draft");
  });

  it("rejects a non-array items field", () => {
    expect(() =>
      parseCustomerQuoteEventListResponse({ meta: { count: 0 }, items: {} }),
    ).toThrow();
  });
});
