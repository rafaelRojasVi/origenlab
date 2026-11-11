/**
 * JS pagination for the quote print preview.
 *
 * This module is a pure function over measured block heights — no DOM access, fully
 * testable with jsdom.  The React component measures actual heights after render (and
 * again after fonts load) and passes them here; the result drives the multi-page layout.
 */

/** mm → px at 96 dpi (the standard CSS pixel density). */
const MM = 96 / 25.4;

/** A4 page height in px at 96 dpi. */
export const A4_HEIGHT_PX = Math.round(297 * MM); // 1123

/** A4 page width in px at 96 dpi (also the .qp-page width). */
export const A4_WIDTH_PX = Math.round(210 * MM); // 794

/** Border-top drawn on every page (see .qp-page in quotePrint.css). */
export const PAGE_BORDER_PX = 6;

/** Padding on all four sides of each page (14mm). */
export const PAGE_PADDING_PX = Math.round(14 * MM); // 53

/**
 * Content height available inside one A4 page.
 *   A4_HEIGHT − border_top − padding_top − padding_bottom
 */
export const PAGE_CONTENT_H =
  A4_HEIGHT_PX - PAGE_BORDER_PX - PAGE_PADDING_PX - PAGE_PADDING_PX; // ≈ 1011

/** Gap between consecutive blocks inside a page (print: .qp-page > * + * { margin-top }). */
export const BLOCK_GAP_PX = 14;

/** Extra bottom padding reserved for the «Página N de M» label. */
export const PAGE_NUM_RESERVE_PX = Math.round(8 * MM); // ≈ 30

/** Usable content height after reserving space for the page number. */
export const PAGE_USABLE_H = PAGE_CONTENT_H - PAGE_NUM_RESERVE_PX; // ≈ 981

// ─────────────────────────────────────────────────────────────────────────────

/** How each block participates in page-break decisions. */
export type BlockKind =
  /** Fill greedily; moves to the next page if it doesn't fit. */
  | 'normal'
  /** Table row: same as 'normal' but triggers thead re-insertion when first on a page. */
  | 'row'
  /** Never split — moves to the next page as a whole when it doesn't fit. */
  | 'avoid';

export interface Block {
  id: string;
  kind: BlockKind;
  height: number;
}

export interface PagedBlock {
  id: string;
  kind: BlockKind;
}

export interface PagedResult {
  /**
   * Each element is one A4 page; each page holds its blocks in document order.
   * The thead is NOT listed here — render it when needsThead[i] is true.
   */
  pages: PagedBlock[][];
  /** needsThead[i] is true when page i has at least one table row. */
  needsThead: boolean[];
}

/**
 * Assign blocks to A4 pages given measured block heights.
 *
 * Rules
 * ─────
 * - 'row' and 'avoid' blocks never split: when a block doesn't fit in the remaining
 *   space of the current page, the entire block is moved to the next page.
 * - 'row' blocks additionally trigger thead re-insertion before the first row on each page
 *   they appear on.
 * - An oversized block (height ≥ contentH) is placed alone on its own page; it may
 *   overflow visually but never causes an infinite loop.
 * - 'normal' blocks behave identically to 'avoid' in this implementation (always move
 *   whole); the distinction exists for semantic clarity and future extensibility.
 *
 * @param blocks    Measured blocks in document order. Do NOT include the thead.
 * @param contentH  Usable content height per page in px (default: PAGE_USABLE_H).
 * @param theadH    Height of the table's <thead> (for repeat calculation).
 * @param gapH      Gap between consecutive blocks inside a page.
 * @param tailId    The closing block (signature): never left alone on a page. When it would
 *                  be, the block before it (conditions, note) goes with it, so the last page
 *                  never holds just a signature.
 */
export function paginate(
  blocks: Block[],
  contentH: number = PAGE_USABLE_H,
  theadH: number = 0,
  gapH: number = BLOCK_GAP_PX,
  tailId?: string,
): PagedResult {
  if (blocks.length === 0) return { pages: [[]], needsThead: [false] };

  const pages: PagedBlock[][] = [[]];
  const needsThead: boolean[] = [false];
  let cursor = 0;      // height consumed on the current page
  let pageHasRows = false;

  for (const block of blocks) {
    const page = pages[pages.length - 1];
    const isFirstOnPage = page.length === 0;
    const isFirstRow = block.kind === 'row' && !pageHasRows;

    // Space needed to place this block on the current page:
    //   gap before it  +  thead+gap if it is the first row  +  block height itself
    const gapBefore = isFirstOnPage ? 0 : gapH;
    const theadBefore = isFirstRow && theadH > 0 ? theadH + gapH : 0;
    const neededH = gapBefore + theadBefore + block.height;

    // Break to a new page when the block does not fit AND the page is not empty
    // (an oversized block on an empty page stays there to avoid an infinite loop).
    if (cursor + neededH > contentH && !isFirstOnPage) {
      pages.push([]);
      needsThead.push(false);
      cursor = 0;
      pageHasRows = false;

      // On the new page this block is first, so no leading gap.
      const newIsFirstRow = block.kind === 'row';
      const newTheadBefore = newIsFirstRow && theadH > 0 ? theadH + gapH : 0;
      cursor = newTheadBefore + block.height;

      if (newIsFirstRow) {
        needsThead[needsThead.length - 1] = true;
        pageHasRows = true;
      }
    } else {
      cursor += neededH;
      if (isFirstRow) {
        needsThead[needsThead.length - 1] = true;
        pageHasRows = true;
      }
    }

    pages[pages.length - 1].push({ id: block.id, kind: block.kind });
  }

  const last = pages[pages.length - 1];
  const before = pages[pages.length - 2];
  if (tailId && before && last.length === 1 && last[0].id === tailId && before.length > 1
      && before[before.length - 1].kind !== 'row') {
    last.unshift(before.pop()!);
  }

  return { pages, needsThead };
}
