import { describe, expect, it } from "vitest";
import {
  normalizeEquipmentItem,
  normalizeWarmCaseItem,
  parseEquipmentMeta,
  parseEquipmentOpportunitiesResponse,
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

const EQUIPMENT_INTERNAL_KEYS = [
  "source_file",
  "csv_path",
  "sqlite_path",
  "body",
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

describe("parseEquipmentOpportunitiesResponse", () => {
  it("accepts meta.data_source postgres_mirror", () => {
    const parsed = parseEquipmentOpportunitiesResponse({
      meta: { data_source: "postgres_mirror", count: 0, reduced_mode: true, note: "" },
      items: [],
    });
    expect(parsed.meta.data_source).toBe("postgres_mirror");
  });

  it("strips source_path and ignores source_path_info from UI meta", () => {
    const parsed = parseEquipmentOpportunitiesResponse({
      meta: {
        data_source: "postgres_mirror",
        source_path: "/var/data/queue/secret.csv",
        source_path_info: {
          redacted: true,
          basename: "secret.csv",
          kind: "file",
        },
        count: 0,
        reduced_mode: true,
        note: "mirror lag",
      },
      items: [],
    });

    expect(parsed.items).toEqual([]);
    expect(parsed.meta.reduced_mode).toBe(true);
    expect("source_path" in parsed.meta).toBe(false);
    expect("source_path_info" in parsed.meta).toBe(false);
    expect(JSON.stringify(parsed.meta)).not.toContain("secret.csv");
  });

  it("keeps ChileCompra detail fields and preserves public equipment fields", () => {
    const parsed = parseEquipmentOpportunitiesResponse({
      meta: { data_source: "postgres_mirror", count: 1, reduced_mode: false, note: "" },
      items: [
        {
          priority_rank: 1,
          opportunity_key: "equipment:equipment_queue:lp-26",
          codigo_licitacion: "1051-1-LP26",
          buyer: "Hospital",
          equipment_category: "centrifuge",
          item_description: "Centrifuge unit",
          next_action: "quote_now",
          contact_email: "procurement@hospital.cl",
          close_date: "2026-06-17T19:00:00",
          close_at: "2026-06-17T19:00:00-04:00",
          fecha_publicacion: "10/06/2026",
          mercado_publico_url:
            "https://www.mercadopublico.cl/BuscarLicitacion?codigoLicitacion=1051-1-LP26",
          unspsc_code: "41100000",
          cantidad: "2",
          producto: "Centrifuga",
          validity_status: "open",
          chilecompra_status: "Publicada",
          region: null,
          safe_channel: undefined,
        },
      ],
    });

    const item = parsed.items[0];
    expect(item.opportunity_key).toBe("equipment:equipment_queue:lp-26");
    expect(item.codigo_licitacion).toBe("1051-1-LP26");
    expect(item.buyer).toBe("Hospital");
    expect(item.equipment_category).toBe("centrifuge");
    expect(item.item_description).toBe("Centrifuge unit");
    expect(item.next_action).toBe("quote_now");
    expect(item.contact_email).toBe("procurement@hospital.cl");
    expect(item.fecha_publicacion).toBe("10/06/2026");
    expect(item.mercado_publico_url).toContain("mercadopublico.cl");
    expect(item.unspsc_code).toBe("41100000");
    expect(item.region).toBe("");
    expect(item.safe_channel).toBe("");
    expect(JSON.stringify(parsed)).not.toMatch(/CHILECOMPRA_API_TICKET/i);
  });

  it("sanitizes anexos and drops ChileCompra ticket/api URLs", () => {
    const parsed = parseEquipmentOpportunitiesResponse({
      meta: { data_source: "postgres_mirror", count: 1, reduced_mode: false, note: "" },
      items: [
        {
          priority_rank: 1,
          codigo_licitacion: "LP-1",
          anexos: [
            {
              nombre: "Bases.pdf",
              url: "https://www.mercadopublico.cl/archivos/bases.pdf",
            },
            {
              nombre: "Ticket doc",
              url: "https://api.mercadopublico.cl/v1/doc?ticket=SECRET",
            },
          ],
        },
      ],
    });

    expect(parsed.items[0].anexos).toHaveLength(2);
    expect(parsed.items[0].anexos?.[0]?.url).toContain("mercadopublico.cl");
    expect(parsed.items[0].anexos?.[1]?.url).toBeUndefined();
  });
});

describe("normalizeEquipmentItem", () => {
  it("ignores raw/internal API fields on items", () => {
    const payload: Record<string, unknown> = {
      priority_rank: 1,
      codigo_licitacion: "LP-1",
    };
    for (const key of EQUIPMENT_INTERNAL_KEYS) {
      payload[key] = `leak-${key}`;
    }
    const row = normalizeEquipmentItem(payload, 0);
    for (const key of EQUIPMENT_INTERNAL_KEYS) {
      expect(key in row).toBe(false);
    }
    expect(JSON.stringify(row)).not.toContain("leak-raw_body");
  });
});

describe("parseEquipmentMeta", () => {
  it("never exposes source_path or source_path_info", () => {
    const meta = parseEquipmentMeta({
      data_source: "postgres_mirror",
      source_path: "/home/ops/queue.csv",
      source_path_info: { redacted: true, basename: "queue.csv", kind: "file" },
      count: 2,
      reduced_mode: false,
      note: "",
    });
    expect(meta.data_source).toBe("postgres_mirror");
    expect(meta.count).toBe(2);
    expect("source_path" in meta).toBe(false);
    expect("source_path_info" in meta).toBe(false);
  });
});
