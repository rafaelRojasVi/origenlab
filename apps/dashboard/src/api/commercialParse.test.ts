import { describe, expect, it } from "vitest";
import {
  normalizeWarmCaseItem,
  parseWarmCasesMeta,
  parseWarmCasesResponse,
} from "./commercialParse";

const WARM_INTERNAL_KEYS = [
  "body_snippet",
  "source_file",
  "recipients_preview",
  "sender_preview",
  "raw_body",
  "headers",
] as const;

// Mirrors CANONICAL_WARM_CASE_CATEGORIES in apps/api/src/origenlab_api/schemas/cases.py.
// Kept as an explicit literal list (no codegen, no reading the Python source from this
// test) so a future contract change fails this test loudly instead of silently.
const CANONICAL_WARM_CASE_CATEGORIES = [
  "client_opportunity",
  "client_response",
  "supplier_quote_received",
  "supplier_followup",
  "payment_admin",
  "logistics_admin",
  "internal_admin",
  "system_noise",
  "bounce_problem",
  "deal_evidence_candidate",
  "quote_sent",
  "waiting_supplier",
  "waiting_client",
  "campaign_outreach",
  "waiting_campaign_reply",
  "auto_acknowledgement",
] as const;

// Mirrors LEGACY_WARM_CASE_CATEGORY_ALIASES.keys() in apps/api/src/origenlab_api/schemas/cases.py.
const LEGACY_WARM_CASE_CATEGORY_ALIASES = [
  "client_reply",
  "supplier_reply",
  "bounce",
  "opportunity",
  "auto_reply",
  "vendor_logistics",
] as const;

describe("parseWarmCasesResponse", () => {
  it("accepts meta.data_source postgres_mirror", () => {
    const parsed = parseWarmCasesResponse({
      meta: { data_source: "postgres_mirror", count: 1, reduced_mode: false, note: "" },
      items: [{ case_id: "case:1", last_email_id: 10, category: "client_reply", status: "open" }],
    });
    expect(parsed.meta.data_source).toBe("postgres_mirror");
  });

  it("falls back meta.count to items.length when count is missing or zero", () => {
    const missing = parseWarmCasesResponse({
      meta: { data_source: "postgres_mirror", reduced_mode: false, note: "" },
      items: [
        { case_id: "a", last_email_id: 1, category: "opportunity", status: "open" },
        { case_id: "b", last_email_id: 2, category: "opportunity", status: "open" },
      ],
    });
    expect(missing.meta.count).toBe(2);

    const zero = parseWarmCasesResponse({
      meta: { data_source: "postgres_mirror", count: 0, reduced_mode: false, note: "" },
      items: [{ case_id: "a", last_email_id: 1, category: "opportunity", status: "open" }],
    });
    expect(zero.meta.count).toBe(1);
  });

  it("tolerates empty and sparse payloads", () => {
    const parsed = parseWarmCasesResponse({
      meta: { reduced_mode: true, note: "no enrichment" },
      items: [
        {
          contact_email: "a@b.cl",
          last_seen_at: null,
          subject: null,
          snippet: undefined,
          body_preview: "must not surface",
          sqlite_path: "/hidden/db.sqlite",
        },
        null,
      ],
    });

    expect(parsed.items).toHaveLength(2);
    expect(parsed.items[0].contact_email).toBe("a@b.cl");
    expect(parsed.items[0].last_seen_at).toBeNull();
    expect(parsed.items[0].snippet).toBe("");
    expect(parsed.items[0].gmail_url).toBeNull();
    expect(parsed.meta.reduced_mode).toBe(true);
    expect(parsed.meta.note).toContain("no enrichment");
    expect(parsed.items[1].case_id).toBe("warm-row-2");
    expect(JSON.stringify(parsed)).not.toContain("body_preview");
    expect(JSON.stringify(parsed)).not.toContain("sqlite_path");
  });
});

describe("normalizeWarmCaseItem", () => {
  it("defaults invalid category to opportunity and invalid status to open", () => {
    const row = normalizeWarmCaseItem(
      { case_id: "case:1", category: "not_a_real_category", status: "bogus" },
      0,
    );
    expect(row.category).toBe("opportunity");
    expect(row.status).toBe("open");
  });

  it("coerces missing or invalid last_email_id to 0", () => {
    expect(normalizeWarmCaseItem({ case_id: "a" }, 0).last_email_id).toBe(0);
    expect(normalizeWarmCaseItem({ case_id: "a", last_email_id: "x" }, 0).last_email_id).toBe(0);
    expect(normalizeWarmCaseItem({ case_id: "a", last_email_id: 42 }, 0).last_email_id).toBe(42);
  });

  it("keeps grouped_email_count at least 1", () => {
    expect(normalizeWarmCaseItem({ case_id: "a", grouped_email_count: 0 }, 0).grouped_email_count).toBe(
      1,
    );
    expect(
      normalizeWarmCaseItem({ case_id: "a", grouped_email_count: 3 }, 0).grouped_email_count,
    ).toBe(3);
  });

  it("always nulls gmail_url in UI output", () => {
    const row = normalizeWarmCaseItem(
      { case_id: "a", gmail_url: "https://mail.google.com/mail/u/0/#inbox/abc" },
      0,
    );
    expect(row.gmail_url).toBeNull();
  });

  it("ignores raw/internal API fields on items", () => {
    const payload: Record<string, unknown> = {
      case_id: "case:1",
      category: "client_reply",
      status: "open",
    };
    for (const key of WARM_INTERNAL_KEYS) {
      payload[key] = `leak-${key}`;
    }
    const row = normalizeWarmCaseItem(payload, 0);
    for (const key of WARM_INTERNAL_KEYS) {
      expect(key in row).toBe(false);
    }
    expect(JSON.stringify(row)).not.toContain("leak-body_snippet");
  });

  it("redacts path-like preview text without adding source/path fields", () => {
    const row = normalizeWarmCaseItem(
      {
        case_id: "case:1",
        snippet: "see /home/user/data/emails.sqlite for details",
        subject: "/mnt/queue/warm_cases.csv attached",
      },
      0,
    );
    expect(row.snippet).toContain("[path redacted]");
    expect(row.snippet).not.toContain("/home/user");
    expect(row.subject).toContain("[path redacted]");
    expect("source_file" in row).toBe(false);
    expect("source_path" in row).toBe(false);
  });
});

describe("normalizeWarmCaseItem — warm case category contract", () => {
  it("preserves every canonical category from the Python schema unchanged", () => {
    for (const category of CANONICAL_WARM_CASE_CATEGORIES) {
      const row = normalizeWarmCaseItem({ case_id: "a", category }, 0);
      expect(row.category).toBe(category);
    }
  });

  it("preserves every legacy alias category unchanged", () => {
    for (const category of LEGACY_WARM_CASE_CATEGORY_ALIASES) {
      const row = normalizeWarmCaseItem({ case_id: "a", category }, 0);
      expect(row.category).toBe(category);
    }
  });

  it("does not silently relabel campaign_outreach, waiting_campaign_reply, or auto_acknowledgement", () => {
    expect(normalizeWarmCaseItem({ case_id: "a", category: "campaign_outreach" }, 0).category).toBe(
      "campaign_outreach",
    );
    expect(
      normalizeWarmCaseItem({ case_id: "a", category: "waiting_campaign_reply" }, 0).category,
    ).toBe("waiting_campaign_reply");
    expect(
      normalizeWarmCaseItem({ case_id: "a", category: "auto_acknowledgement" }, 0).category,
    ).toBe("auto_acknowledgement");
  });

  it("rejects payment_received, which is not part of the Python contract", () => {
    const row = normalizeWarmCaseItem({ case_id: "a", category: "payment_received" }, 0);
    expect(row.category).toBe("opportunity");
  });

  it("still falls back a genuinely unknown category to opportunity", () => {
    const row = normalizeWarmCaseItem({ case_id: "a", category: "not_a_real_category" }, 0);
    expect(row.category).toBe("opportunity");
  });
});

describe("parseWarmCasesMeta", () => {
  it("maps postgres_mirror and sqlite data sources", () => {
    expect(parseWarmCasesMeta({ data_source: "postgres_mirror" }).data_source).toBe("postgres_mirror");
    expect(parseWarmCasesMeta({ data_source: "sqlite" }).data_source).toBe("sqlite");
    expect(parseWarmCasesMeta({ data_source: "unknown" }).data_source).toBe("sqlite");
  });
});
