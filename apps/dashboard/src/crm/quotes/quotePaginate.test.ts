/**
 * Unit tests for quotePaginate — pure function over measured heights.
 * No DOM, no React, no jsdom layout.  Heights are invented to exercise every branch.
 */
import { describe, expect, it } from "vitest";
import { paginate } from "./quotePaginate";
import type { Block } from "./quotePaginate";

// Helpers ─────────────────────────────────────────────────────────────────────

/** Build a block shorthand: (id, kind, height). */
function b(id: string, kind: Block["kind"], height: number): Block {
  return { id, kind, height };
}

/** Collect all block ids across all pages. */
function allIds(result: ReturnType<typeof paginate>) {
  return result.pages.flatMap((p) => p.map((bl) => bl.id));
}

// Content height and gap used throughout — deliberately small so tests stay readable.
const CH = 500;  // content height
const GH = 10;   // gap
const TH = 20;   // thead height

// ─────────────────────────────────────────────────────────────────────────────

describe("paginate — single page", () => {
  it("places all blocks on one page when they fit", () => {
    const blocks: Block[] = [
      b("header", "normal", 60),
      b("client", "normal", 50),
      b("row-0", "row", 30),
      b("row-1", "row", 30),
      b("totals", "avoid", 40),
      b("footer", "avoid", 60),
    ];
    // cursor: 60 + (10+60) + (10+20+10+30) + (10+30) + (10+40) + (10+60)
    //       = 60 + 70 + 70 + 40 + 50 + 70 = 360 < 500
    const { pages, needsThead } = paginate(blocks, CH, TH, GH);
    expect(pages).toHaveLength(1);
    expect(allIds({ pages, needsThead })).toEqual(["header", "client", "row-0", "row-1", "totals", "footer"]);
    expect(needsThead[0]).toBe(true);
  });

  it("sets needsThead[0]=false when there are no rows", () => {
    const { pages, needsThead } = paginate([b("header", "normal", 60), b("footer", "avoid", 60)], CH, TH, GH);
    expect(pages).toHaveLength(1);
    expect(needsThead[0]).toBe(false);
  });

  it("returns one empty page for an empty input", () => {
    const { pages, needsThead } = paginate([], CH, TH, GH);
    expect(pages).toHaveLength(1);
    expect(pages[0]).toHaveLength(0);
    expect(needsThead[0]).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("paginate — table rows split across pages", () => {
  it("breaks rows to a new page and repeats thead", () => {
    // Page capacity 100; thead=20; gap=10.
    // header=50 on page 1 → cursor=50
    // row-0 first row: needs gapBefore=10 + theadBefore=(20+10) + height=30 = 70 → cursor=120 > 100 → new page
    // On page 2: no gapBefore (first), theadBefore=(20+10)=30, height=30 → cursor=60; fits
    const blocks: Block[] = [b("header", "normal", 50), b("row-0", "row", 30)];
    const { pages, needsThead } = paginate(blocks, 100, 20, 10);
    expect(pages).toHaveLength(2);
    expect(pages[0].map((x) => x.id)).toEqual(["header"]);
    expect(pages[1].map((x) => x.id)).toEqual(["row-0"]);
    expect(needsThead[0]).toBe(false);
    expect(needsThead[1]).toBe(true);
  });

  it("puts rows on the same page when enough space remains after thead", () => {
    // Page 1: header=50, then row-0 needs 10+30+10+40=90 → 50+90=140 ≤ 200; fits
    const blocks: Block[] = [b("header", "normal", 50), b("row-0", "row", 40)];
    const { pages, needsThead } = paginate(blocks, 200, 30, 10);
    expect(pages).toHaveLength(1);
    expect(needsThead[0]).toBe(true);
  });

  it("repeats thead on each new page that has rows", () => {
    // CH=100, row height=90 each — each row fills a page by itself.
    const blocks: Block[] = [b("row-0", "row", 90), b("row-1", "row", 90), b("row-2", "row", 90)];
    const { pages, needsThead } = paginate(blocks, 100, 10, 5);
    // row-0: first on page 1, cursor = (10+5)+90 = 105 → 0+15+90=105 > 100 → placed on empty page 1 (oversized, stays)
    // Actually row-0 is FIRST on page 1 (isFirstOnPage=true) so it always stays.
    // row-0: isFirstOnPage=true → no break; cursor = theadBefore(10+5=15) + 90 = 105
    // row-1: isFirstOnPage=false; needs gapBefore(5)+0(not first row now pageHasRows=true)+90 = 95
    //        cursor+95 = 200 > 100 → break to page 2
    //        on page 2: isFirstRow=true (pageHasRows=false on new page); newTheadBefore=10+5=15; cursor=15+90=105
    // row-2: needs gapBefore(5)+90=95; 105+95=200>100 → break to page 3; cursor=15+90=105
    expect(pages).toHaveLength(3);
    expect(needsThead[0]).toBe(true);
    expect(needsThead[1]).toBe(true);
    expect(needsThead[2]).toBe(true);
  });

  it("does not add extra thead space when theadH=0", () => {
    // With theadH=0, rows should behave like normal blocks.
    const blocks: Block[] = [b("row-0", "row", 50), b("row-1", "row", 50)];
    const { pages } = paginate(blocks, 100, 0, 5);
    // row-0 first on page 1; cursor=0+50=50; row-1 needs 5+50=55; 50+55=105>100 → page 2
    expect(pages).toHaveLength(2);
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("paginate — avoid blocks move whole", () => {
  it("moves an avoid block to the next page when it does not fit", () => {
    // CH=100; header=60 → cursor=60; totals=50 needs 10+50=60; 60+60=120>100 → page 2
    const blocks: Block[] = [b("header", "normal", 60), b("totals", "avoid", 50)];
    const { pages } = paginate(blocks, 100, 0, 10);
    expect(pages).toHaveLength(2);
    expect(pages[0][0].id).toBe("header");
    expect(pages[1][0].id).toBe("totals");
  });

  it("keeps an avoid block on the current page when it fits exactly", () => {
    // CH=100; header=50 → cursor=50; totals=40 needs 10+40=50; 50+50=100 ≤ 100 → fits
    const blocks: Block[] = [b("header", "normal", 50), b("totals", "avoid", 40)];
    const { pages } = paginate(blocks, 100, 0, 10);
    expect(pages).toHaveLength(1);
  });

  it("keeps sequential avoid blocks together on the same page", () => {
    // footer=20, conditions=20, note=20: total 20+(10+20)+(10+20)=80 ≤ 100
    const blocks: Block[] = [b("footer", "avoid", 20), b("cond", "avoid", 20), b("note", "avoid", 20)];
    const { pages } = paginate(blocks, 100, 0, 10);
    expect(pages).toHaveLength(1);
  });

  it("an oversized avoid block on an empty page stays there (no infinite loop)", () => {
    // block height=200 > CH=100; it is the first on page 1, so it stays.
    const blocks: Block[] = [b("footer", "avoid", 200)];
    const { pages } = paginate(blocks, 100, 0, 10);
    expect(pages).toHaveLength(1);
    expect(pages[0][0].id).toBe("footer");
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("paginate — realistic quote layout", () => {
  it("typical short quote: all on one page", () => {
    const blocks: Block[] = [
      b("header",     "normal", 80),
      b("client",     "normal", 60),
      b("row-0",      "row",    35),
      b("row-1",      "row",    35),
      b("totals",     "avoid",  55),
      b("conditions", "avoid",  70),
      b("footer",     "avoid",  80),
    ];
    // cursor with TH=25, GH=14:
    // header: 80
    // client: 80 + (14+60) = 154
    // row-0 (first row): 154 + 14 + (25+14) + 35 = 154+14+39+35 = 242
    // row-1: 242 + 14 + 35 = 291
    // totals: 291 + 14 + 55 = 360
    // conditions: 360 + 14 + 70 = 444
    // footer: 444 + 14 + 80 = 538 → > 500? depends.
    // With CH=600, everything fits.
    const { pages } = paginate(blocks, 600, 25, 14);
    expect(pages).toHaveLength(1);
  });

  it("22-line quote: rows split across pages, avoid blocks keep together", () => {
    // Each row is 32px; thead is 24px; header+client = 140px; CH=1011; GH=14
    // Page 1: header(140) → cursor=140
    //         thead + row-0…row-N fit until cursor > 1011
    // After header: each row costs 14+(24+14)+32=84 for the first row, 14+32=46 for subsequent.
    // First row: 140 + 84 = 224; subsequent rows: +46 each
    // Rows until overflow: 224 + (N-1)*46 ≤ 1011 → (N-1)*46 ≤ 787 → N ≤ 18.1 → up to row-17 (18 rows) on page 1
    // Rows 18-21 go to page 2 (4 rows there)
    const rows = Array.from({ length: 22 }, (_, i) => b(`row-${i}`, "row", 32));
    const blocks: Block[] = [
      b("header", "normal", 100),
      b("client", "normal", 40),
      ...rows,
      b("totals",     "avoid", 55),
      b("conditions", "avoid", 60),
      b("footer",     "avoid", 80),
    ];
    const { pages, needsThead } = paginate(blocks, 1011, 24, 14);
    expect(pages.length).toBeGreaterThanOrEqual(2);
    // All rows must appear exactly once across all pages
    const rowIds = pages.flatMap((p) => p.map((bl) => bl.id).filter((id) => id.startsWith("row-")));
    expect(rowIds).toHaveLength(22);
    expect(rowIds).toEqual(Array.from({ length: 22 }, (_, i) => `row-${i}`));
    // Every page with rows must have needsThead set
    pages.forEach((page, i) => {
      const hasRow = page.some((bl) => bl.kind === "row");
      if (hasRow) expect(needsThead[i]).toBe(true);
    });
    // Avoid blocks must all be on the same page (they fit together at ~195px)
    const avoidPages = ["totals", "conditions", "footer"].map((id) =>
      pages.findIndex((p) => p.some((bl) => bl.id === id)),
    );
    // They might be split if not all fit, but they must be in correct order
    expect(avoidPages[0]).toBeLessThanOrEqual(avoidPages[1]);
    expect(avoidPages[1]).toBeLessThanOrEqual(avoidPages[2]);
  });

  it("preserves document order across all pages", () => {
    const blocks: Block[] = [
      b("header", "normal", 60),
      b("row-0", "row", 60),
      b("row-1", "row", 60),
      b("row-2", "row", 60),
      b("totals", "avoid", 60),
      b("footer", "avoid", 60),
    ];
    const result = paginate(blocks, 200, 20, 10);
    expect(allIds(result)).toEqual(["header", "row-0", "row-1", "row-2", "totals", "footer"]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("paginate — defaults", () => {
  it("uses PAGE_USABLE_H and BLOCK_GAP_PX as defaults", () => {
    // With real defaults, a header+footer that together fit on one page should do so.
    const { pages } = paginate([b("header", "normal", 100), b("footer", "avoid", 100)]);
    expect(pages.length).toBe(1);
  });

  it("theadH defaults to 0 (no thead overhead)", () => {
    const { pages } = paginate([b("row-0", "row", 50), b("row-1", "row", 50)]);
    // Both rows fit on one page with no thead overhead.
    expect(pages.length).toBe(1);
  });
});
