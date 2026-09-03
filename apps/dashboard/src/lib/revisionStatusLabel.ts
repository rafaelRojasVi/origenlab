import type { RevisionStatus } from "../api/customerQuoteTypes";

/** Single source of truth for revision_status labels (CRM-Q2B) -- the card
 * and drawer both derive from this instead of duplicating the mapping.
 * Load-bearing for the Revisión lane in particular: CRM-Q2B collapsed
 * draft/adjustments_requested/pending_approval into one visible column, so
 * this is the only remaining place their difference is visible without
 * opening the drawer. */
export const REVISION_STATUS_LABELS: Record<RevisionStatus, string> = {
  draft: "Borrador",
  pending_approval: "Lista para aprobación",
  adjustments_requested: "Ajustes solicitados",
  approved: "Aprobada",
  sent: "Enviada",
  superseded: "Reemplazada",
  closed_won: "Cerrada · Ganada",
  closed_null: "Cerrada · Nula",
};

export function revisionStatusLabel(status: RevisionStatus): string {
  return REVISION_STATUS_LABELS[status];
}
