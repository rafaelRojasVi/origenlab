import { useCallback, useEffect, useState } from "react";
import { OperatorApiError } from "../../api/operatorClient";
import {
  fetchSalesOpportunities,
  transitionSalesOpportunityStage,
} from "../../api/commercialOperationsClient";
import type { SalesOpportunityListItem, SalesOpportunityStage } from "../../api/commercialOperationsTypes";
import { SALES_OPPORTUNITY_ACTIVE_STAGES } from "../../lib/salesOpportunityFormat";

const BOARD_FETCH_LIMIT = 200;
const CONFLICT_MESSAGE =
  "Esta oportunidad cambió en otra sesión. Actualizamos el estado con la versión más reciente.";

export function useSalesOpportunityBoard() {
  const [items, setItems] = useState<SalesOpportunityListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [enabledToggles, setEnabledToggles] = useState<SalesOpportunityStage[]>([]);
  const [pendingStageChangeId, setPendingStageChangeId] = useState<string | null>(null);
  const [stageError, setStageError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await fetchSalesOpportunities({
        stage: [...SALES_OPPORTUNITY_ACTIVE_STAGES, ...enabledToggles],
        limit: BOARD_FETCH_LIMIT,
      });
      setItems(result.items);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "No pudimos cargar el pipeline.");
    } finally {
      setLoading(false);
    }
  }, [enabledToggles]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleStage = useCallback((stage: SalesOpportunityStage) => {
    setEnabledToggles((current) =>
      current.includes(stage) ? current.filter((value) => value !== stage) : [...current, stage],
    );
  }, []);

  const changeStage = useCallback(
    async (item: SalesOpportunityListItem, nextStage: SalesOpportunityStage) => {
      setStageError(null);
      setPendingStageChangeId(item.sales_opportunity_id);

      const previousItems = items;
      setItems((current) =>
        current.map((row) =>
          row.sales_opportunity_id === item.sales_opportunity_id ? { ...row, stage: nextStage } : row,
        ),
      );

      try {
        const updated = await transitionSalesOpportunityStage(item.sales_opportunity_id, {
          stage: nextStage,
          expected_version: item.version,
        });

        setItems((current) =>
          current.map((row) =>
            row.sales_opportunity_id === item.sales_opportunity_id
              ? {
                  ...row,
                  stage: updated.stage,
                  version: updated.version,
                  updated_at: updated.updated_at,
                  updated_by: updated.updated_by,
                  stage_updated_at: updated.updated_at,
                }
              : row,
          ),
        );
      } catch (reason: unknown) {
        setItems(previousItems);

        if (reason instanceof OperatorApiError && reason.status === 409) {
          setStageError(CONFLICT_MESSAGE);
        } else {
          setStageError(reason instanceof Error ? reason.message : "No pudimos cambiar la etapa. Reintenta.");
        }

        await load();
      } finally {
        setPendingStageChangeId(null);
      }
    },
    [items, load],
  );

  return {
    items,
    loading,
    error,
    enabledToggles,
    toggleStage,
    refetch: load,
    changeStage,
    pendingStageChangeId,
    stageError,
    dismissStageError: () => setStageError(null),
  };
}
