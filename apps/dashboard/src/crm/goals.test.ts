import { describe, expect, it } from "vitest";
import type { CallDay, OpportunityCardData, RevisionCard } from "./crmTypes";
import {
  boardViewFromHash,
  businessDaysLeft,
  cotizarBacklog,
  dateSantiago,
  firstSentAt,
  formatMillions,
  formatWait,
  quoteSpeed,
  reach,
  requiredPace,
  soldThisMonth,
} from "./goals";
import type { Invoice } from "./pagos/invoiceApi";
import { buildParaTiQueue } from "./today";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-08T15:00:00Z");
const DAY = 86_400_000;
const HOUR = 3_600_000;
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

function invoice(doc_type: number, issued_on: string, net_amount: number, exempt_amount = 0): Invoice {
  return {
    invoice_id: `i-${doc_type}-${issued_on}`, version: 1, doc_type, folio: 100, issued_on, days_open: 0, age_bucket: null,
    client_rut: "11.111.111-1", client_name: "Instituto Ficticio", organization: null, avg_days_client: 30, expected_pay_date: null,
    net_amount, vat_amount: Math.round(net_amount * 0.19), exempt_amount, total_amount: net_amount + exempt_amount,
    balance: null, reference: null, notes: [], status: "unpaid", paid_on: null, case_link: null, opportunity: null,
  };
}

function rev(sentAt: string | null, over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: `r-${sentAt}`, revision_no: 1, status: sentAt ? "sent" : "draft", origin: "authored", sent_at: sentAt,
    superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null, quote_number: "01239-26",
    ...over,
  };
}

let n = 0;
function card(over: Partial<OpportunityCardData> = {}, revisions: RevisionCard[] = []): OpportunityCardData {
  n += 1;
  const latest = revisions.length ? revisions[revisions.length - 1] : null;
  return {
    opportunity_id: `00000000-0000-4000-8000-0000000001${String(n).padStart(2, "0")}`,
    title: `Caso ${n}`, stage: "quoting", version: 1, created_at: null, updated_at: null, closed_at: null, close_reason: null,
    organization: { organization_id: `org-${n}`, name: `Instituto Ficticio ${n}`, confirmation: "confirmed", version: 1 },
    other_organizations: [], contact: null,
    quotes: revisions.length ? [{ quote_id: `q-${n}`, quote_number: "01239-26", number_origin: "allocated", revisions }] : [],
    quote_numbers: revisions.length ? ["01239-26"] : [], revision_count: revisions.length, latest_revision: latest,
    drive_folder: null, attention: [], status: "ok", next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  };
}

describe("dateSantiago", () => {
  it("is the Santiago day, not the UTC one", () => {
    expect(dateSantiago(new Date("2026-11-08T15:00:00Z"))).toBe("2026-11-08");
    // 01:30 UTC on the 9th is still the 8th in Santiago (UTC−3).
    expect(dateSantiago(new Date("2026-11-09T01:30:00Z"))).toBe("2026-11-08");
  });
});

describe("soldThisMonth", () => {
  it("adds 33, 34 and 56 and takes off 61, net + exempt, by each document's month", () => {
    const items = [
      invoice(33, "2026-11-04", 1_050_000),
      invoice(34, "2026-11-05", 0, 200_000),
      invoice(61, "2026-11-06", 100_000),
      invoice(33, "2026-10-31", 500_000),
      invoice(56, "2026-11-07", 30_000),
    ];
    // Owner ruling 2026-11-08: a nota de débito (56) counts.
    expect(soldThisMonth(items, "2026-11-08")).toEqual({ net: 1_180_000, docs: 4 });
  });

  it("is zero with no documents this month", () => {
    expect(soldThisMonth([invoice(33, "2026-10-31", 500_000)], "2026-11-08")).toEqual({ net: 0, docs: 0 });
  });
});

describe("businessDaysLeft", () => {
  it("counts Monday to Friday from today to the end of the month", () => {
    expect(businessDaysLeft("2026-11-08")).toBe(16);
    expect(businessDaysLeft("2026-11-02")).toBe(21);
    expect(businessDaysLeft("2026-11-28")).toBe(1);
    expect(businessDaysLeft("2026-11-30")).toBe(1);
    expect(businessDaysLeft("2026-10-31")).toBe(0);
  });
});

describe("requiredPace", () => {
  it("splits what is left over the business days left", () => {
    expect(requiredPace(1_150_000, 6_000_000, "2026-11-08")).toEqual({ remaining: 4_850_000, days: 16, perDay: 303_125 });
  });
  it("asks nothing once the target is met", () => {
    expect(requiredPace(6_500_000, 6_000_000, "2026-11-08")).toEqual({ remaining: 0, days: 16, perDay: 0 });
  });
  it("has no pace with no business day left", () => {
    expect(requiredPace(1_000_000, 6_000_000, "2026-10-31").perDay).toBeNull();
  });
});

describe("cotizarBacklog", () => {
  it("counts the Cotizar rows of the queue and the longest wait", () => {
    const a = card({ stage: "lead", created_at: ago(2 * DAY) });
    const b = card({ stage: "qualifying", created_at: ago(5 * HOUR) });
    const sent = card({}, [rev(ago(8 * DAY))]);
    const paused = card({ stage: "lead", created_at: ago(9 * DAY),
      paused_until: new Date(NOW.getTime() + 3 * DAY).toISOString().slice(0, 10), pause_reason: "Esperar" });
    expect(cotizarBacklog(buildParaTiQueue([a, b, sent, paused], NOW, null))).toEqual({ count: 2, oldestMs: 2 * DAY });
    expect(cotizarBacklog([])).toEqual({ count: 0, oldestMs: null });
  });
});

describe("firstSentAt and quoteSpeed", () => {
  const asked = (msAgo: number, tookMs: number) =>
    card({ requested_at: ago(msAgo + tookMs), created_at: ago(msAgo + tookMs) }, [rev(ago(msAgo))]);

  it("firstSentAt is the earliest sent revision that is not void, whatever its origin", () => {
    const c = card({}, [rev(ago(12 * DAY), { status: "void" }), rev(ago(10 * DAY), { origin: "historical_import" }), rev(ago(5 * DAY)), rev(ago(2 * DAY), { revision_no: 2 })]);
    expect(firstSentAt(c)).toBe(ago(10 * DAY));
    expect(firstSentAt(card({}, [rev(null)]))).toBeNull();
  });

  it("counts a Gmail-synced (historical_import) quote sent after the case opened", () => {
    const c = card({ created_at: ago(DAY + 8 * HOUR), requested_at: ago(DAY + 8 * HOUR) }, [rev(ago(DAY), { origin: "historical_import" })]);
    expect(quoteSpeed([c], NOW).current).toEqual({ medianMs: 8 * HOUR, n: 1 });
  });

  it("skips a case whose first quote went out no later than the case was created", () => {
    // Built by the September import from an already-sent quote: requested_at is older, created_at is not.
    const imported = card({ requested_at: ago(10 * DAY), created_at: ago(DAY) }, [rev(ago(2 * DAY), { origin: "historical_import" })]);
    const same = card({ created_at: ago(DAY) }, [rev(ago(DAY))]);
    expect(quoteSpeed([imported, same], NOW).current).toEqual({ medianMs: null, n: 0 });
  });

  it("takes the median of the last 30 days and of the 30 before", () => {
    const cards = [
      asked(1 * DAY, 5 * HOUR),
      asked(3 * DAY, 24 * HOUR),
      asked(10 * DAY, 36 * HOUR),
      asked(35 * DAY, 48 * HOUR),
      asked(40 * DAY, 72 * HOUR),
      // Ignored: sent before it was asked, sent before the case was created (the import), never sent.
      card({ requested_at: ago(2 * DAY) }, [rev(ago(3 * DAY))]),
      card({ requested_at: ago(4 * DAY), created_at: ago(DAY) }, [rev(ago(2 * DAY), { origin: "historical_import" })]),
      card({ requested_at: ago(4 * DAY) }, [rev(null)]),
      // A later rev 2 does not move the first quote: 6 h, inside the current window.
      card({ requested_at: ago(20 * DAY + 6 * HOUR) }, [rev(ago(20 * DAY)), rev(ago(DAY), { revision_no: 2 })]),
    ];
    // Without the rev-2 case: 5 h, 24 h, 36 h → 24 h. With it (6 h): 5, 6, 24, 36 → (6 + 24) / 2 = 15 h.
    const speed = quoteSpeed(cards.slice(0, 8), NOW);
    expect(speed.current).toEqual({ medianMs: 24 * HOUR, n: 3 });
    expect(speed.previous).toEqual({ medianMs: 60 * HOUR, n: 2 });
    expect(quoteSpeed(cards, NOW).current).toEqual({ medianMs: 15 * HOUR, n: 4 });
  });

  it("falls back to created_at when the API sends no requested_at", () => {
    const c = card({ created_at: ago(DAY + 5 * HOUR) }, [rev(ago(DAY))]);
    expect(quoteSpeed([c], NOW).current).toEqual({ medianMs: 5 * HOUR, n: 1 });
  });
});

describe("reach", () => {
  const calls: CallDay[] = [
    { day: "2026-11-02", outcome: "contesto", count: 4 },
    { day: "2026-11-06", outcome: "no_contesto", count: 10 },
    { day: "2026-11-06", outcome: "reunion", count: 1 },
    { day: "2026-10-01", outcome: "contesto", count: 9 },
  ];
  it("counts today, the week so far against 25 a business day, and 30 days of conversations", () => {
    expect(reach(calls, "2026-11-06")).toEqual({ today: 11, week: 15, weekTarget: 125, conversations30d: 5 });
  });
  it("the week's target grows by business day; a weekend keeps five", () => {
    expect(reach(calls, "2026-11-04").weekTarget).toBe(75);
    expect(reach(calls, "2026-11-08").weekTarget).toBe(125);
    expect(reach([], "2026-11-08")).toEqual({ today: 0, week: 0, weekTarget: 125, conversations30d: 0 });
  });
});

describe("boardViewFromHash", () => {
  it("reads ?vista=seguimiento|cotizar and nothing else", () => {
    expect(boardViewFromHash("#/ventas/pipeline?vista=seguimiento")).toBe("seguimiento");
    expect(boardViewFromHash("#/ventas/pipeline/00000000-0000-4000-8000-000000000001?vista=cotizar")).toBe("cotizar");
    expect(boardViewFromHash("#/ventas/pipeline?vista=otra")).toBeNull();
    expect(boardViewFromHash("#/ventas/pipeline")).toBeNull();
  });
});

describe("format", () => {
  it("formatMillions", () => {
    expect(formatMillions(1_050_000)).toBe("$1,05 M");
    expect(formatMillions(6_000_000)).toBe("$6 M");
    expect(formatMillions(4_950_000)).toBe("$4,95 M");
  });
  it("formatWait", () => {
    expect(formatWait(30 * 60_000)).toBe("< 1 h");
    expect(formatWait(5 * HOUR)).toBe("5 h");
    expect(formatWait(24 * HOUR)).toBe("1 día");
    expect(formatWait(36 * HOUR)).toBe("1,5 días");
  });
});
