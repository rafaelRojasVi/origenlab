import { useState } from "react";
import type { CustomerQuoteGlobalItem } from "../../api/customerQuoteTypes";

/**
 * Shared explicit-confirmation dialog for request_adjustments and
 * confirm_send: a board/mobile/drawer gesture never dispatches either
 * command directly (see resolveBoardMove) -- this is the only path that
 * actually calls onConfirm, and only after the operator clicks the
 * explicit confirm button.
 */
export function WorkflowConfirmDialog({
  open,
  item,
  title,
  message,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  open: boolean;
  item: CustomerQuoteGlobalItem | null;
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: (item: CustomerQuoteGlobalItem) => Promise<void>;
  onClose: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open || !item) return null;

  async function handleConfirm() {
    if (submitting || !item) return;

    setSubmitting(true);
    setError(null);

    try {
      await onConfirm(item);
      setSubmitting(false);
      onClose();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "No pudimos completar la acción. Reintenta.");
      setSubmitting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="fixed inset-0 z-40 bg-slate-900/30"
        aria-label={`Cerrar ${title}`}
        onClick={onClose}
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="workflow-confirm-heading"
        data-testid="workflow-confirm-dialog"
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
      >
        <div className="w-full max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-xl">
          <div className="space-y-3 px-4 py-4">
            <h2 id="workflow-confirm-heading" className="text-lg font-semibold text-slate-900">
              {title}
            </h2>
            <p className="text-sm text-slate-700">{message}</p>
            {error ? (
              <p role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {error}
              </p>
            ) : null}
          </div>

          <footer className="flex items-center justify-end gap-2 border-t border-[var(--color-border)] px-4 py-3">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Cancelar
            </button>
            <button
              type="button"
              onClick={() => void handleConfirm()}
              disabled={submitting}
              className="rounded-md bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Confirmando…" : confirmLabel}
            </button>
          </footer>
        </div>
      </div>
    </>
  );
}
