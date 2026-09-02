import { useCallback, useEffect, useRef, useState } from "react";
import {
  approveCustomerQuote,
  confirmCustomerQuoteSend,
  fetchCustomerQuotesGlobal,
  fetchDrivePendingQuotes,
  requestCustomerQuoteAdjustments,
  submitCustomerQuoteForReview,
} from "../../api/customerQuoteClient";
import { OperatorApiError } from "../../api/operatorClient";
import type {
  CustomerQuote,
  CustomerQuoteGlobalItem,
  DrivePendingQuoteItem,
  QuoteProvisioningStatus,
  QuoteQueueRow,
} from "../../api/customerQuoteTypes";
import type { SalesOpportunityStage } from "../../api/commercialOperationsTypes";
import type { WorkflowCommand } from "../../lib/quoteBoard";

const QUEUE_FETCH_LIMIT = 200;

const WORKFLOW_CONFLICT_MESSAGE =
  "Esta cotización cambió en otra sesión. Actualizamos el estado con la versión más reciente.";

const WORKFLOW_COMMAND_CLIENTS: Record<
  WorkflowCommand,
  (quoteId: string, command: { expected_version: number }) => Promise<CustomerQuote>
> = {
  submit_for_review: submitCustomerQuoteForReview,
  request_adjustments: requestCustomerQuoteAdjustments,
  approve: approveCustomerQuote,
  confirm_send: confirmCustomerQuoteSend,
};

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
  const [pendingQuoteId, setPendingQuoteId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Synchronous mutex, not state: rejects a second overlapping dispatch
  // immediately, before React has a chance to re-render with pendingQuoteId
  // set (mirrors useSalesOpportunityBoard's stageMutationInFlightRef).
  const mutationInFlightRef = useRef(false);

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

  const dispatchWorkflowCommand = useCallback(
    async (item: CustomerQuoteGlobalItem, command: WorkflowCommand) => {
      if (mutationInFlightRef.current) return;
      mutationInFlightRef.current = true;

      setActionError(null);
      setPendingQuoteId(item.quote.quote_id);

      try {
        const updated = await WORKFLOW_COMMAND_CLIENTS[command](item.quote.quote_id, {
          expected_version: item.quote.version,
        });

        setItems((current) =>
          current.map((row) =>
            row.quote.quote_id === item.quote.quote_id ? { ...row, quote: updated } : row,
          ),
        );
      } catch (reason: unknown) {
        if (reason instanceof OperatorApiError && reason.status === 409) {
          setActionError(WORKFLOW_CONFLICT_MESSAGE);
          await load();
        } else {
          setActionError(
            reason instanceof Error ? reason.message : "No pudimos actualizar la cotización. Reintenta.",
          );
        }
      } finally {
        setPendingQuoteId(null);
        mutationInFlightRef.current = false;
      }
    },
    [load],
  );

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
    dispatchWorkflowCommand,
    pendingQuoteId,
    actionError,
    dismissActionError: () => setActionError(null),
  };
}
