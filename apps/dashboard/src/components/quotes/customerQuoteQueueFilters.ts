import type { CustomerQuoteGlobalItem, QuoteProvisioningStatus } from "../../api/customerQuoteTypes";

export type QueueRecencyFilter = "all" | "7d" | "30d";

const RECENCY_DAYS: Record<Exclude<QueueRecencyFilter, "all">, number> = {
  "7d": 7,
  "30d": 30,
};

export function filterQuoteQueueItems(
  items: readonly CustomerQuoteGlobalItem[],
  filters: { searchText: string; recency: QueueRecencyFilter },
  now: Date = new Date(),
): CustomerQuoteGlobalItem[] {
  const search = filters.searchText.trim().toLowerCase();

  return items.filter((row) => {
    if (search) {
      const haystack = [
        row.quote.quote_number,
        row.quote.document_number,
        row.quote.sales_opportunity_title,
        row.organization_display_name,
        row.contact_display_name,
        row.contact_primary_email,
      ]
        .filter((value): value is string => Boolean(value))
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(search)) return false;
    }

    if (filters.recency !== "all") {
      const days = RECENCY_DAYS[filters.recency];
      const updated = new Date(row.quote.updated_at);
      const ageMs = now.getTime() - updated.getTime();
      if (ageMs > days * 24 * 60 * 60 * 1000) return false;
    }

    return true;
  });
}

const DRIVE_STATE_LABELS: Record<QuoteProvisioningStatus, "Drive listo" | "Aprovisionando" | "Error de Drive"> = {
  ready: "Drive listo",
  pending: "Aprovisionando",
  failed: "Error de Drive",
};

export function quoteQueueStateLabel(
  item: CustomerQuoteGlobalItem,
): { status: "Borrador"; drive: "Drive listo" | "Aprovisionando" | "Error de Drive" } {
  return {
    status: "Borrador",
    drive: DRIVE_STATE_LABELS[item.quote.drive_workspace.provisioning_status],
  };
}
