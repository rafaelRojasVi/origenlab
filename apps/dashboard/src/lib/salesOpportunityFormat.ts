import type { SalesOpportunityStage } from "../api/commercialOperationsTypes";

export const SALES_OPPORTUNITY_ACTIVE_STAGES: readonly SalesOpportunityStage[] = [
  "new",
  "qualifying",
  "qualified",
  "quoting",
  "negotiating",
];

export const SALES_OPPORTUNITY_TOGGLE_STAGES: readonly SalesOpportunityStage[] = [
  "won",
  "lost",
  "dormant",
];

const TERMINAL_STAGES: ReadonlySet<SalesOpportunityStage> = new Set(["won", "lost"]);

export const SALES_OPPORTUNITY_STAGE_LABELS: Record<SalesOpportunityStage, string> = {
  new: "Nueva",
  qualifying: "Calificando",
  qualified: "Calificada",
  quoting: "Cotizando",
  negotiating: "Negociando",
  won: "Ganada",
  lost: "Perdida",
  dormant: "Dormida",
};

export function salesOpportunityStageLabel(stage: SalesOpportunityStage): string {
  return SALES_OPPORTUNITY_STAGE_LABELS[stage];
}

export function isSalesOpportunityStageTerminal(stage: SalesOpportunityStage): boolean {
  return TERMINAL_STAGES.has(stage);
}

export function formatSalesOpportunityAge(value: string): string {
  const parsed = new Date(value);

  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }

  const diffDays = Math.floor((Date.now() - parsed.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays <= 0) {
    return "Hoy";
  }

  if (diffDays === 1) {
    return "Hace 1 día";
  }

  return `Hace ${diffDays} días`;
}
