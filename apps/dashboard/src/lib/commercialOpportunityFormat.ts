import type { CommercialOpportunityDataSource } from "../api/commercialOpportunitiesTypes";

export const COMMERCIAL_OPPORTUNITY_STAGE_OPTIONS = [
  "qualifying",
  "quote_requested",
  "quote_preparing",
  "quote_sent",
  "technical_review",
  "purchase_pending",
  "won",
  "fulfillment",
  "post_sale",
  "lost",
  "commercial_history",
  "unknown",
] as const;

export const COMMERCIAL_OPPORTUNITY_REVIEW_OPTIONS = [
  "ok",
  "needs_review",
] as const;

const STAGE_LABELS: Record<string, string> = {
  qualifying: "Calificación",
  quote_requested: "Cotización solicitada",
  quote_preparing: "Cotización en preparación",
  quote_sent: "Cotización enviada",
  technical_review: "Revisión técnica",
  purchase_pending: "Compra pendiente",
  won: "Ganado",
  fulfillment: "Ejecución / despacho",
  post_sale: "Postventa",
  lost: "Perdido",
  commercial_history: "Historial comercial",
  unknown: "Desconocido",
};

const REVIEW_LABELS: Record<string, string> = {
  ok: "Sin revisión pendiente",
  needs_review: "Requiere revisión",
};

function humanizeToken(token: string): string {
  const normalized = token.trim();
  if (!normalized) return "—";
  return normalized
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function commercialOpportunityStageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? humanizeToken(stage);
}

export function commercialOpportunityReviewLabel(status: string): string {
  return REVIEW_LABELS[status] ?? humanizeToken(status);
}

export function commercialOpportunityTokenLabel(token: string): string {
  return humanizeToken(token);
}

export function commercialOpportunitySourceLabel(
  source: CommercialOpportunityDataSource,
): string {
  return source === "postgres_mirror"
    ? "Espejo Postgres · solo lectura"
    : "PR3 SQLite · solo lectura";
}

export function formatCommercialOpportunityDate(
  value: string | null,
): string {
  if (!value) return "—";

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("es-CL", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}
