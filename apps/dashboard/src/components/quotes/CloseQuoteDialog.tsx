import { useState } from "react";
import { closeCustomerQuote } from "../../api/customerQuoteClient";
import type { CustomerQuote, CustomerQuoteGlobalItem, QuoteOutcome } from "../../api/customerQuoteTypes";
import { describeCustomerQuoteCommandError } from "../../api/customerQuoteErrors";
import { newIdempotencyKey } from "../../lib/idempotencyKey";

const OUTCOME_OPTIONS: readonly { value: QuoteOutcome; label: string; description: string }[] = [
  { value: "won", label: "Ganada", description: "El cliente aceptó la cotización." },
  { value: "null", label: "Nula", description: "Esta cotización quedó sin efecto." },
];

/**
 * Explicit closure dialog: closing a sent quote always requires the
 * operator to pick an outcome first -- there is no default selection and
 * no drag-triggered path (see resolveBoardMove, which refuses any drop
 * onto the closed lane). Ganada/Nula are the only two outcomes; Nula never
 * implies the linked sales opportunity is lost -- this dialog never
 * touches commercial.sales_opportunity.
 */
export function CloseQuoteDialog({
  open,
  item,
  onClose,
  onClosed,
}: {
  open: boolean;
  item: CustomerQuoteGlobalItem | null;
  onClose: () => void;
  onClosed: (quote: CustomerQuote) => void;
}) {
  const [outcome, setOutcome] = useState<QuoteOutcome | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState(() => newIdempotencyKey("close"));

  if (!open || !item) return null;

  function handleClose() {
    setOutcome(null);
    setError(null);
    setIdempotencyKey(newIdempotencyKey("close"));
    onClose();
  }

  async function handleConfirm() {
    if (!item || outcome === null || submitting) return;

    setSubmitting(true);
    setError(null);

    try {
      const updated = await closeCustomerQuote(
        item.quote.quote_id,
        { expected_version: item.quote.version, outcome },
        idempotencyKey,
      );
      setOutcome(null);
      setIdempotencyKey(newIdempotencyKey("close"));
      onClosed(updated);
    } catch (reason: unknown) {
      setError(describeCustomerQuoteCommandError(reason, "close"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="fixed inset-0 z-40 bg-slate-900/30"
        aria-label="Cerrar diálogo Cerrar cotización"
        onClick={handleClose}
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="close-quote-heading"
        data-testid="close-quote-dialog"
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
      >
        <div className="w-full max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-xl">
          <div className="space-y-3 px-4 py-4">
            <h2 id="close-quote-heading" className="text-lg font-semibold text-slate-900">
              Cerrar cotización
            </h2>

            <fieldset className="space-y-2">
              <legend className="text-sm font-medium text-slate-700">Resultado</legend>
              {OUTCOME_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className="flex cursor-pointer items-start gap-2 rounded-md border border-[var(--color-border)] p-2 hover:bg-slate-50"
                >
                  <input
                    type="radio"
                    name="close-quote-outcome"
                    value={option.value}
                    checked={outcome === option.value}
                    onChange={() => setOutcome(option.value)}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="block text-sm font-medium text-slate-900">{option.label}</span>
                    <span className="block text-xs text-slate-500">{option.description}</span>
                  </span>
                </label>
              ))}
            </fieldset>

            {error ? (
              <p role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {error}
              </p>
            ) : null}
          </div>

          <footer className="flex items-center justify-end gap-2 border-t border-[var(--color-border)] px-4 py-3">
            <button
              type="button"
              onClick={handleClose}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Cancelar
            </button>
            <button
              type="button"
              onClick={() => void handleConfirm()}
              disabled={outcome === null || submitting}
              className="rounded-md bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Cerrando…" : "Cerrar cotización"}
            </button>
          </footer>
        </div>
      </div>
    </>
  );
}
