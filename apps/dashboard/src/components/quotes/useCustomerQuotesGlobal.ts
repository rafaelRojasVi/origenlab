import { useCallback, useEffect, useState } from "react";
import { fetchCustomerQuotesGlobal } from "../../api/customerQuoteClient";
import type { CustomerQuoteGlobalItem, QuoteProvisioningStatus } from "../../api/customerQuoteTypes";
import type { SalesOpportunityStage } from "../../api/commercialOperationsTypes";

const QUEUE_FETCH_LIMIT = 200;

export function useCustomerQuotesGlobal() {
  const [items, setItems] = useState<CustomerQuoteGlobalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stageToggles, setStageToggles] = useState<SalesOpportunityStage[]>([]);
  const [driveStatusToggles, setDriveStatusToggles] = useState<QuoteProvisioningStatus[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await fetchCustomerQuotesGlobal({
        stage: stageToggles.length ? stageToggles : undefined,
        driveStatus: driveStatusToggles.length ? driveStatusToggles : undefined,
        limit: QUEUE_FETCH_LIMIT,
        offset: 0,
      });
      setItems(result.items);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "No pudimos cargar las cotizaciones.");
      setItems([]);
    } finally {
      setLoading(false);
    }
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

  return { items, loading, error, refetch: load, stageToggles, toggleStage, driveStatusToggles, toggleDriveStatus };
}
