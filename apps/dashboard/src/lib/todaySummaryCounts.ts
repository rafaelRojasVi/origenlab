import type { WarmCaseCategory, WarmCaseItem } from "../api/commercialTypes";
import type { CommercialDealUiRow } from "../api/commercialDealsTypes";
import { matchesWarmCaseViewPreset } from "./warmCaseViewPreset";

const CLIENT_OPPORTUNITY_CATEGORIES: ReadonlySet<WarmCaseCategory> = new Set([
  "client_opportunity",
  "opportunity",
  "client_response",
  "client_reply",
]);

const SUPPLIER_CATEGORIES: ReadonlySet<WarmCaseCategory> = new Set([
  "supplier_quote_received",
  "supplier_followup",
  "supplier_reply",
]);

const PAYMENTS_LOGISTICS_CATEGORIES: ReadonlySet<WarmCaseCategory> = new Set([
  "payment_admin",
  "logistics_admin",
  "vendor_logistics",
]);

export interface TodaySummaryCounts {
  clientOpportunities: number;
  supplierQuotesFollowups: number;
  paymentsLogistics: number;
  dealEvidence: number;
  dealBlockers: number;
  tendersEquipment: number;
  equipmentFeedUnavailable: boolean;
}

export function countClientOpportunities(items: WarmCaseItem[]): number {
  return items.filter((row) => CLIENT_OPPORTUNITY_CATEGORIES.has(row.category)).length;
}

export function countSupplierQuotesFollowups(items: WarmCaseItem[]): number {
  return items.filter((row) => SUPPLIER_CATEGORIES.has(row.category)).length;
}

export function countPaymentsLogistics(items: WarmCaseItem[]): number {
  return items.filter(
    (row) =>
      PAYMENTS_LOGISTICS_CATEGORIES.has(row.category) ||
      matchesWarmCaseViewPreset(row, "pagos_admin") ||
      matchesWarmCaseViewPreset(row, "logistica"),
  ).length;
}

export function countDealEvidence(items: WarmCaseItem[]): number {
  return items.filter((row) => row.category === "deal_evidence_candidate").length;
}

export function countDealBlockers(deals: CommercialDealUiRow[]): number {
  return deals.filter((deal) => (deal.margin_blockers?.length ?? 0) > 0).length;
}

export function computeTodaySummaryCounts(
  warmItems: WarmCaseItem[],
  equipmentCount: number,
  dealItems: CommercialDealUiRow[],
  equipmentFeedUnavailable = false,
): TodaySummaryCounts {
  return {
    clientOpportunities: countClientOpportunities(warmItems),
    supplierQuotesFollowups: countSupplierQuotesFollowups(warmItems),
    paymentsLogistics: countPaymentsLogistics(warmItems),
    dealEvidence: countDealEvidence(warmItems),
    dealBlockers: countDealBlockers(dealItems),
    tendersEquipment: equipmentFeedUnavailable ? 0 : equipmentCount,
    equipmentFeedUnavailable,
  };
}
