/** Actionable-opportunity summary (W1 procurement status), read-only dashboard. */

import type { ProcurementStatus } from "../api/institutionIntel/types";

export const ACTIONABLE_OPPORTUNITIES_LABEL = "Oportunidades accionables";

export const ACTIONABLE_OPPORTUNITIES_NORMAL_HINT =
  "Licitaciones públicas con señales de compra actualmente accionables";

export const ACTIONABLE_OPPORTUNITIES_STALE_HINT =
  "Datos accionables desactualizados · revisar actualización W1";

export const ACTIONABLE_OPPORTUNITIES_UNAVAILABLE_HINT =
  "Fuente de oportunidades accionables no disponible";

export interface ActionableOpportunitySummary {
  /** True when `value` is real and safe to display numerically (healthy zero included). */
  available: boolean;
  /** True when available but the underlying W1 bundle is past its own staleness threshold. */
  stale: boolean;
  /** W1 actionable-queue row count. Only meaningful when `available` is true. */
  value: number;
}

/**
 * current_opportunity_queue counts W1 actionable QUEUE ROWS, not unique
 * tenders — it is not expected to match any other feed's count.
 */
export function summarizeProcurementStatus(
  status: ProcurementStatus | null,
): ActionableOpportunitySummary {
  const queueValue = status?.operatorQueueSizes.current_opportunity_queue;
  const available =
    status != null &&
    status.meta.reduced_mode === false &&
    status.summaryOk !== false &&
    typeof queueValue === "number" &&
    Number.isFinite(queueValue) &&
    queueValue >= 0;

  return {
    available,
    stale: available && status!.meta.stale === true,
    value: available ? (queueValue as number) : 0,
  };
}
