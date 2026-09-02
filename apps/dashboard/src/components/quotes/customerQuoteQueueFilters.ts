import type { QuoteQueueRow } from "../../api/customerQuoteTypes";

export type QueueRecencyFilter = "all" | "7d" | "30d";

const RECENCY_DAYS: Record<Exclude<QueueRecencyFilter, "all">, number> = {
  "7d": 7,
  "30d": 30,
};

function rowSearchHaystack(row: QuoteQueueRow): string {
  const values =
    row.kind === "crm"
      ? [
          row.item.quote.quote_number,
          row.item.quote.document_number,
          row.item.quote.sales_opportunity_title,
          row.item.organization_display_name,
          row.item.contact_display_name,
          row.item.contact_primary_email,
        ]
      : [row.item.folder_name, row.item.document_identifier];

  return values
    .filter((value): value is string => Boolean(value))
    .join(" ")
    .toLowerCase();
}

function rowRecencyTimestamp(row: QuoteQueueRow): string | null {
  if (row.kind === "crm") {
    return row.item.quote.updated_at;
  }
  return row.item.modified_time ?? row.item.created_time;
}

export function filterQuoteQueueRows(
  rows: readonly QuoteQueueRow[],
  filters: { searchText: string; recency: QueueRecencyFilter },
  now: Date = new Date(),
): QuoteQueueRow[] {
  const search = filters.searchText.trim().toLowerCase();

  return rows.filter((row) => {
    if (search && !rowSearchHaystack(row).includes(search)) {
      return false;
    }

    if (filters.recency !== "all") {
      const timestamp = rowRecencyTimestamp(row);
      // Unknown recency never hides a row -- only a known, stale date does.
      if (timestamp) {
        const days = RECENCY_DAYS[filters.recency];
        const ageMs = now.getTime() - new Date(timestamp).getTime();
        if (ageMs > days * 24 * 60 * 60 * 1000) return false;
      }
    }

    return true;
  });
}
