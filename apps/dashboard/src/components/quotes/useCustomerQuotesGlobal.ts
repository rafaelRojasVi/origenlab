import { useCallback, useEffect, useState } from "react";
import { fetchCustomerQuotesGlobal, fetchDrivePendingQuotes } from "../../api/customerQuoteClient";
import type {
  CustomerQuoteGlobalItem,
  DrivePendingQuoteItem,
  QuoteProvisioningStatus,
  QuoteQueueRow,
} from "../../api/customerQuoteTypes";
import type { SalesOpportunityStage } from "../../api/commercialOperationsTypes";

const QUEUE_FETCH_LIMIT = 200;

function rowTimestamp(row: QuoteQueueRow): string {
  if (row.kind === "crm") {
    return row.item.quote.updated_at;
  }
  return row.item.modified_time ?? row.item.created_time ?? "";
}

/** Newest-first; rows with no known timestamp sort to the bottom. */
function sortRowsByRecency(rows: QuoteQueueRow[]): QuoteQueueRow[] {
  return [...rows].sort((a, b) => {
    const tsA = rowTimestamp(a);
    const tsB = rowTimestamp(b);
    if (!tsA && !tsB) return 0;
    if (!tsA) return 1;
    if (!tsB) return -1;
    return tsB.localeCompare(tsA);
  });
}

export function useCustomerQuotesGlobal() {
  const [items, setItems] = useState<CustomerQuoteGlobalItem[]>([]);
  const [driveItems, setDriveItems] = useState<DrivePendingQuoteItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stageToggles, setStageToggles] = useState<SalesOpportunityStage[]>([]);
  const [driveStatusToggles, setDriveStatusToggles] = useState<QuoteProvisioningStatus[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    const [crmResult, driveResult] = await Promise.allSettled([
      fetchCustomerQuotesGlobal({
        stage: stageToggles.length ? stageToggles : undefined,
        driveStatus: driveStatusToggles.length ? driveStatusToggles : undefined,
        limit: QUEUE_FETCH_LIMIT,
        offset: 0,
      }),
      fetchDrivePendingQuotes(),
    ]);

    const errors: string[] = [];

    if (crmResult.status === "fulfilled") {
      setItems(crmResult.value.items);
    } else {
      setItems([]);
      errors.push(
        crmResult.reason instanceof Error
          ? crmResult.reason.message
          : "No pudimos cargar las cotizaciones.",
      );
    }

    if (driveResult.status === "fulfilled") {
      setDriveItems(driveResult.value.items);
    } else {
      setDriveItems([]);
      errors.push(
        driveResult.reason instanceof Error
          ? driveResult.reason.message
          : "No pudimos cargar las carpetas pendientes de Drive.",
      );
    }

    setError(errors.length ? errors.join(" ") : null);
    setLoading(false);
  }, [stageToggles, driveStatusToggles]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleStage = useCallback((stage: SalesOpportunityStage) => {
    setStageToggles((current) =>
      current.includes(stage) ? current.filter((value) => value !== stage) : [...current, stage],
    );
  }, []);

  const toggleDriveStatus = useCallback((status: QuoteProvisioningStatus) => {
    setDriveStatusToggles((current) =>
      current.includes(status) ? current.filter((value) => value !== status) : [...current, status],
    );
  }, []);

  const rows: QuoteQueueRow[] = sortRowsByRecency([
    ...items.map((item): QuoteQueueRow => ({ kind: "crm", item })),
    ...driveItems.map((item): QuoteQueueRow => ({ kind: "drive_pending", item })),
  ]);

  return {
    items,
    driveItems,
    rows,
    isEmpty: items.length === 0 && driveItems.length === 0,
    loading,
    error,
    refetch: load,
    stageToggles,
    toggleStage,
    driveStatusToggles,
    toggleDriveStatus,
  };
}
