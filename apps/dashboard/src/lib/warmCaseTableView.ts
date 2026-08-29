import type { WarmCaseCategory, WarmCaseItem, WarmCaseStatus } from "../api/commercialTypes";
import { emailDomain, matchesSearch, normalizeSearchQuery, parseSortableTimestamp } from "./clientTableView";
import { isInternalOperatorContact } from "./internalContactFilter";
import {
  getWarmCaseReviewKey,
  type WarmCaseReviewFilter,
  type WarmCaseReviewLabel,
} from "./warmCaseReviewLabels";
import {
  DEFAULT_WARM_VIEW_PRESET,
  filterWarmCasesByViewPreset,
  type WarmCaseViewPreset,
} from "./warmCaseViewPreset";

export type WarmCaseSortKey = "last_seen_desc" | "last_seen_asc" | "status" | "category" | "contact";

export interface WarmCaseTableFilters {
  search: string;
  status: WarmCaseStatus | "";
  category: WarmCaseCategory | "";
  sort: WarmCaseSortKey;
  /** When true, hide @origenlab.cl and @labdelivery.cl (client-side only). Default on. */
  hideInternalContacts: boolean;
  /** Queue focus preset (client-side). Default: real client threads. */
  preset: WarmCaseViewPreset;
  /** Local review label filter (client-side only). */
  review: WarmCaseReviewFilter;
}

export const DEFAULT_WARM_FILTERS: WarmCaseTableFilters = {
  search: "",
  status: "",
  category: "",
  sort: "last_seen_desc",
  hideInternalContacts: true,
  preset: DEFAULT_WARM_VIEW_PRESET,
  review: "all",
};

export interface WarmCaseTableViewOptions {
  reviewLabels?: Record<string, WarmCaseReviewLabel>;
}

/** Resets search/status/category and view preset to Clientes reales (initial load state). */
export function clearWarmCaseTableFilters(): WarmCaseTableFilters {
  return { ...DEFAULT_WARM_FILTERS };
}

const STATUS_ORDER: Record<WarmCaseStatus, number> = {
  problem: 0,
  new: 1,
  open: 2,
  waiting: 3,
  quoted: 4,
};

/** Lower rank sorts first. Unknown categories fall back to 99 so new API values do not break builds. */
const CATEGORY_SORT_RANK: Partial<Record<WarmCaseCategory, number>> = {
  bounce: 0,
  bounce_problem: 0,
  auto_reply: 1,
  system_noise: 1,
  payment_admin: 2,
  vendor_logistics: 3,
  logistics_admin: 3,
  internal_admin: 4,
  supplier_reply: 5,
  supplier_quote_received: 5,
  supplier_followup: 5,
  client_reply: 6,
  client_response: 6,
  quote_sent: 7,
  waiting_supplier: 8,
  waiting_client: 9,
  campaign_outreach: 20,
  waiting_campaign_reply: 21,
  auto_acknowledgement: 22,
  deal_evidence_candidate: 10,
  opportunity: 11,
  client_opportunity: 11,
};

export function categoryRank(category: WarmCaseCategory): number {
  return CATEGORY_SORT_RANK[category] ?? 99;
}

export function warmCaseSearchHaystack(row: WarmCaseItem): string {
  return [
    row.contact_email,
    emailDomain(row.contact_email),
    row.account_name,
    row.subject,
    row.snippet,
    row.equipment_signal,
    row.next_action,
  ]
    .join(" ")
    .toLowerCase();
}

function matchesReviewFilter(
  row: WarmCaseItem,
  review: WarmCaseReviewFilter | undefined,
  reviewLabels?: Record<string, WarmCaseReviewLabel>,
): boolean {
  if (!review || review === "all") {
    return true;
  }
  const label = reviewLabels?.[getWarmCaseReviewKey(row)] ?? "";
  if (review === "unreviewed") {
    return !label;
  }
  return label === review;
}

export function filterWarmCases(
  items: WarmCaseItem[],
  filters: WarmCaseTableFilters,
  options?: WarmCaseTableViewOptions,
): WarmCaseItem[] {
  const q = normalizeSearchQuery(filters.search);
  return items.filter((row) => {
    if (filters.hideInternalContacts && isInternalOperatorContact(row.contact_email)) {
      return false;
    }
    if (filters.status && row.status !== filters.status) {
      return false;
    }
    if (filters.category && row.category !== filters.category) {
      return false;
    }
    if (!matchesReviewFilter(row, filters.review, options?.reviewLabels)) {
      return false;
    }
    if (q && !matchesSearch(warmCaseSearchHaystack(row), q)) {
      return false;
    }
    return true;
  });
}

function compareWarmCases(a: WarmCaseItem, b: WarmCaseItem, sort: WarmCaseSortKey): number {
  switch (sort) {
    case "last_seen_asc":
      return parseSortableTimestamp(a.last_seen_at) - parseSortableTimestamp(b.last_seen_at);
    case "last_seen_desc":
      return parseSortableTimestamp(b.last_seen_at) - parseSortableTimestamp(a.last_seen_at);
    case "status":
      return (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99);
    case "category":
      return categoryRank(a.category) - categoryRank(b.category);
    case "contact":
      return a.contact_email.localeCompare(b.contact_email);
    default:
      return 0;
  }
}

export function sortWarmCases(items: WarmCaseItem[], sort: WarmCaseSortKey): WarmCaseItem[] {
  return [...items].sort((a, b) => compareWarmCases(a, b, sort));
}

export function applyWarmCaseTableView(
  items: WarmCaseItem[],
  filters: WarmCaseTableFilters,
  options?: WarmCaseTableViewOptions,
): WarmCaseItem[] {
  const byPreset = filterWarmCasesByViewPreset(items, filters.preset);
  return sortWarmCases(filterWarmCases(byPreset, filters, options), filters.sort);
}

export function warmFiltersActive(filters: WarmCaseTableFilters): boolean {
  return Boolean(
    filters.search.trim() ||
      filters.status ||
      filters.category ||
      (filters.review && filters.review !== "all") ||
      filters.preset !== DEFAULT_WARM_VIEW_PRESET ||
      !filters.hideInternalContacts,
  );
}

export type { WarmCaseViewPreset } from "./warmCaseViewPreset";
export {
  DEFAULT_WARM_VIEW_PRESET,
  WARM_VIEW_PRESET_LABELS,
  WARM_VIEW_PRESET_ORDER,
  matchesWarmCaseViewPreset,
} from "./warmCaseViewPreset";

export function uniqueWarmStatuses(items: WarmCaseItem[]): WarmCaseStatus[] {
  const set = new Set<WarmCaseStatus>();
  for (const row of items) {
    if (row.status) {
      set.add(row.status);
    }
  }
  return [...set].sort();
}

export function uniqueWarmCategories(items: WarmCaseItem[]): WarmCaseCategory[] {
  const set = new Set<WarmCaseCategory>();
  for (const row of items) {
    if (row.category) {
      set.add(row.category);
    }
  }
  return [...set].sort();
}
