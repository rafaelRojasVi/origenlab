import type { BoardStage } from "../../api/customerQuoteTypes";
import { legalActionsForStage, type WorkflowCommand } from "../../lib/quoteBoard";

/**
 * Explicit legal next actions for a durable quote's current board stage --
 * shared by the mobile list card and the detail drawer, both derived from
 * the same BOARD_TRANSITIONS table the desktop drag/drop uses. Never
 * offers a command outside the current stage's legal set.
 */
export function QuoteWorkflowActions({
  boardStage,
  disabled,
  onDispatch,
  onRequestConfirmation,
}: {
  boardStage: BoardStage;
  disabled: boolean;
  onDispatch: (command: WorkflowCommand) => void;
  onRequestConfirmation: (command: WorkflowCommand) => void;
}) {
  const actions = legalActionsForStage(boardStage);

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
