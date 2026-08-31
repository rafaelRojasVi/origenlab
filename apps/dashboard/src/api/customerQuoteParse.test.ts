import { describe, expect, it } from "vitest";
import {
  parseCustomerQuote,
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
    status: "draft",
    version: 1,
    latest_revision_number: 1,
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
