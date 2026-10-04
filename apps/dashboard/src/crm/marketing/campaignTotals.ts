/**
 * The recipient totals a campaign card and its detail show. Each key is one predicate on the API
 * (`campaign_history.TOTALS`), and the recipient list opened from a total filters by that same
 * key — so a number and the rows behind it always agree.
 */

import type { CampaignSummary, CampaignTotals, RepliesState, TotalKey } from "../crmTypes";

export const TOTAL_LABEL: Record<TotalKey, string> = {
  audience: "Audiencia",
  included: "Incluidos",
  sent: "Enviados",
  excluded: "Excluidos",
  blocked: "Bloqueados",
  unsent: "No enviados",
  rejected: "Rechazados",
  bounced: "Rebotados",
  responses: "Respuestas",
};

export const TOTAL_HINT: Record<TotalKey, string> = {
  audience: "Todos los destinatarios registrados de la campaña",
  included: "Destinatarios no excluidos de la audiencia",
  sent: "Destinatarios con al menos un envío aceptado por Gmail (aceptado no es entregado)",
  excluded: "Destinatarios excluidos de la audiencia",
  blocked: "Excluidos por un bloqueo de dirección o dominio",
  unsent: "Incluidos en la audiencia sin ningún envío aceptado",
  rejected: "Destinatarios con al menos un intento rechazado",
  bounced: "Destinatarios con rebote registrado",
  responses: "Destinatarios con una respuesta guardada en el CRM",
};

/** The six totals every campaign card shows, in order. */
export const CARD_TOTALS: TotalKey[] = ["audience", "sent", "excluded", "rejected", "bounced", "responses"];

/** Every total the recipient list can filter by, in order. */
export const FILTER_TOTALS: TotalKey[] = ["audience", "included", "sent", "unsent", "excluded", "blocked", "rejected", "bounced", "responses"];

/** Whether a response count means anything: never synchronized is not zero. */
export function repliesUnknown(replies: RepliesState | undefined, totals: CampaignTotals | null | undefined): boolean {
  if (replies) return replies.state === "not_synced";
  return !totals || totals.responses === 0;
}

export function cardTotals(c: CampaignSummary): { key: TotalKey; value: number | null }[] {
  const t = c.totals;
  if (!t) return [];
  return CARD_TOTALS.map((key) => ({
    key,
    value: key === "responses" && repliesUnknown(c.replies, t) ? null : t[key],
  }));
}

/** The literal label for an archived campaign with no send attempt at all. */
export const NEVER_SENT_LABEL = "Nunca enviada";

/**
 * Whether a campaign is historical and was never sent: archived, and not one send attempt
 * (accepted, rejected or otherwise) recorded. A draft with no attempts is not "never sent" — it
 * simply has not been sent yet.
 */
export function neverSent(c: Pick<CampaignSummary, "status" | "send_attempts" | "attempt_totals">): boolean {
  if (c.status !== "archived") return false;
  const attempts = c.attempt_totals ? c.attempt_totals.attempts : c.send_attempts.reduce((n, a) => n + a.count, 0);
  return attempts === 0;
}

/**
 * A campaign imported from V1 that never went out: no attempt, no send. The Marketing list hides
 * these by default (the record stays in the CRM; history is never deleted), behind a link.
 */
export function isNeverSentHistorical(c: CampaignSummary): boolean {
  return c.origin === "imported_v1" && !c.first_sent_at && neverSent(c);
}
