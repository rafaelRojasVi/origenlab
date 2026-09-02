/**
 * Cotizaciones board move legality (CRM-Q2).
 *
 * The single source of truth for which drag/drop or explicit-action gesture
 * maps to which durable revision-workflow command. Board position is never
 * written directly — every legal move here resolves to one of the four
 * server commands (submit_for_review / request_adjustments / approve /
 * confirm_send); every other from/to pair is refused with an
 * operator-readable reason and never reaches the API.
 */

import type { BoardStage } from "../api/customerQuoteTypes";

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

export interface BoardTransition {
  command: WorkflowCommand;
  from: BoardStage;
  to: BoardStage;
  label: string;
  /** submit_for_review/approve dispatch immediately on drag;
   * request_adjustments/confirm_send always open an explicit confirmation
   * dialog first and only dispatch on the operator's explicit confirm. */
  requiresConfirmation: boolean;
}

export const BOARD_TRANSITIONS: readonly BoardTransition[] = [
  {
    command: "submit_for_review",
    from: "preparation",
    to: "review",
    label: "Enviar a revisión",
    requiresConfirmation: false,
  },
  {
    command: "approve",
    from: "review",
    to: "approved_to_send",
    label: "Aprobar",
    requiresConfirmation: false,
  },
  {
    command: "request_adjustments",
    from: "review",
    to: "preparation",
    label: "Solicitar ajustes",
    requiresConfirmation: true,
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
      command: WorkflowCommand;
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

  const match = BOARD_TRANSITIONS.find(
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

/** Every legal next action from a given durable board stage — the exact
 * table the drawer and the mobile list render as explicit action buttons. */
export function legalActionsForStage(
  stage: BoardStage,
): readonly BoardTransition[] {
  return BOARD_TRANSITIONS.filter((transition) => transition.from === stage);
}
