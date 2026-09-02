import type { RevisionStatus } from "../../api/customerQuoteTypes";
import { legalActionsForRevisionStatus, type WorkflowCommand } from "../../lib/quoteBoard";

/**
 * Explicit legal next actions for a durable quote's current revision_status
 * -- shared by the mobile list card and the detail drawer, both derived
 * from the same REVISION_TRANSITIONS table. Keyed on revision_status, not
 * board_stage: since CRM-Q2B collapsed draft/adjustments_requested/
 * pending_approval into one "review" lane, board_stage alone can no longer
 * distinguish which actions are legal. Never offers a command outside the
 * current status's legal set.
 */
export function QuoteWorkflowActions({
  revisionStatus,
  disabled,
  onDispatch,
  onRequestConfirmation,
}: {
  revisionStatus: RevisionStatus;
  disabled: boolean;
  onDispatch: (command: WorkflowCommand) => void;
  onRequestConfirmation: (command: WorkflowCommand) => void;
}) {
  const actions = legalActionsForRevisionStatus(revisionStatus);

  if (actions.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-2">
      {actions.map((action) => (
        <button
          key={action.command}
          type="button"
          disabled={disabled}
          onClick={(event) => {
            event.stopPropagation();
            if (action.requiresConfirmation) {
              onRequestConfirmation(action.command);
            } else {
              onDispatch(action.command);
            }
          }}
          className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50 disabled:opacity-50"
        >
          {action.label}
        </button>
      ))}
    </div>
  );
}
