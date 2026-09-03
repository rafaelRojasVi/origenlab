/**
 * Cotizaciones board move legality (CRM-Q2 / CRM-Q2B).
 *
 * Board position is never written directly — every legal move here resolves
 * to one of the four server commands (submit_for_review / request_adjustments
 * / approve / confirm_send); every other from/to pair is refused with an
 * operator-readable reason and never reaches the API. Closing a quote is
 * never reachable by drag — see CloseQuoteDialog.
 *
 * Two separate tables, deliberately: `REVISION_TRANSITIONS` (keyed on the
 * real `revision_status`) is the single source of truth for which action
 * buttons are legal in a given state — since CRM-Q2B collapsed
 * draft/adjustments_requested/pending_approval into one visible "review"
 * lane, that lane alone can no longer answer "which actions are legal"
 * (each of the three statuses has a different legal set). `LANE_TRANSITIONS`
 * (keyed on the coarser `BoardStage`) is only for desktop drag/drop, which
 * operates over visible columns — it only contains the two transitions that
 * still cross a lane boundary (approve, confirm_send); submit_for_review and
 * request_adjustments now start and end inside the same "review" lane, so
 * they are never drag-triggerable and must be explicit buttons.
 */

import type { BoardStage, RevisionStatus } from "../api/customerQuoteTypes";

/** The Kanban lane set, including the non-durable Drive intake lane (which
 * never appears as a `board_stage` on a durable quote — it is the label the
 * dashboard applies to items sourced from the separate drive-pending
 * endpoint). */
export type CotizacionesLane = BoardStage | "drive_intake";

export type WorkflowCommand =
  | "submit_for_review"
  | "request_adjustments"
  | "approve"
  | "confirm_send";

export interface RevisionTransition {
  command: WorkflowCommand;
  from: RevisionStatus;
  to: RevisionStatus;
  label: string;
  /** approve dispatches immediately from its explicit button;
   * request_adjustments/confirm_send always open an explicit confirmation
   * dialog first and only dispatch on the operator's explicit confirm.
   * submit_for_review dispatches immediately too — moving a draft/adjusted
   * quote into approval review is not itself a consequential outcome. */
  requiresConfirmation: boolean;
}

/** Single source of truth for which explicit action button is legal from a
 * given revision_status — the drawer, mobile list, and card action rows all
 * derive from this, never from `board_stage` (too coarse post-CRM-Q2B). */
export const REVISION_TRANSITIONS: readonly RevisionTransition[] = [
  {
    command: "submit_for_review",
    from: "draft",
    to: "pending_approval",
    label: "Enviar a aprobación",
    requiresConfirmation: false,
  },
  {
    command: "submit_for_review",
    from: "adjustments_requested",
    to: "pending_approval",
    label: "Enviar a aprobación",
    requiresConfirmation: false,
  },
  {
    command: "approve",
    from: "pending_approval",
    to: "approved",
    label: "Aprobar",
    requiresConfirmation: false,
  },
  {
    command: "request_adjustments",
    from: "pending_approval",
    to: "adjustments_requested",
    label: "Solicitar ajustes",
    requiresConfirmation: true,
  },
  {
    command: "confirm_send",
    from: "approved",
    to: "sent",
    label: "Confirmar envío",
    requiresConfirmation: true,
  },
];

export function legalActionsForRevisionStatus(
  status: RevisionStatus,
): readonly RevisionTransition[] {
  return REVISION_TRANSITIONS.filter((transition) => transition.from === status);
}

interface LaneTransition {
  command: "approve" | "confirm_send";
  from: BoardStage;
  to: BoardStage;
  label: string;
  requiresConfirmation: boolean;
}

/** Drag/drop only ever crosses a lane boundary for these two commands — the
 * other two now start and end inside the single "review" lane. */
const LANE_TRANSITIONS: readonly LaneTransition[] = [
  {
    command: "approve",
    from: "review",
    to: "approved_to_send",
    label: "Aprobar",
    requiresConfirmation: false,
  },
  {
    command: "confirm_send",
    from: "approved_to_send",
    to: "sent_follow_up",
    label: "Confirmar envío",
    requiresConfirmation: true,
  },
];

export type BoardMoveDecision =
  | {
      allowed: true;
      command: "approve" | "confirm_send";
      label: string;
      requiresConfirmation: boolean;
    }
  | { allowed: false; reason: string };

export function resolveBoardMove(
  from: CotizacionesLane,
  to: CotizacionesLane,
): BoardMoveDecision {
  if (from === "drive_intake") {
    return {
      allowed: false,
      reason:
        'Esta carpeta aún no está incorporada al CRM. Usa "Incorporar al CRM" primero.',
    };
  }

  if (to === "drive_intake") {
    return {
      allowed: false,
      reason: "No puedes mover una cotización durable de vuelta a Pendientes Drive.",
    };
  }

  if (from === to) {
    return { allowed: false, reason: "La cotización ya está en esta etapa." };
  }

  if (to === "closed") {
    // Cerrada is visually adjacent to every other lane, so the generic
    // "solo se permiten transiciones adyacentes" refusal would be actively
    // misleading here -- closing is never drag-triggered, always explicit.
    return {
      allowed: false,
      reason: 'Para cerrar una cotización usa "Cerrar cotización" y selecciona Ganada o Nula.',
    };
  }

  const match = LANE_TRANSITIONS.find(
    (transition) => transition.from === from && transition.to === to,
  );

  if (!match) {
    return {
      allowed: false,
      reason: "Ese movimiento no está permitido: solo se permiten transiciones adyacentes.",
    };
  }

  return {
    allowed: true,
    command: match.command,
    label: match.label,
    requiresConfirmation: match.requiresConfirmation,
  };
}
