import type { CustomerQuoteGlobalItem } from "../../api/customerQuoteTypes";
import type { CotizacionesLane } from "../../lib/quoteBoard";
import { CotizacionesCard } from "./CotizacionesCard";
import { DriveIntakeCard } from "./DriveIntakeCard";
import { QuoteWorkflowActions } from "./QuoteWorkflowActions";
import type { useCustomerQuotesGlobal } from "./useCustomerQuotesGlobal";

const SECTION_ORDER: readonly CotizacionesLane[] = [
  "drive_intake",
  "review",
  "approved_to_send",
  "sent_follow_up",
];

const SECTION_LABELS: Record<CotizacionesLane, string> = {
  drive_intake: "Pendientes Drive",
  review: "Revisión",
  approved_to_send: "Aprobada / por enviar",
  sent_follow_up: "Enviada / seguimiento",
};

/**
 * Mobile Cotizaciones view: a grouped vertical list, one section per lane,
 * with explicit stage-action buttons instead of drag/drop -- no
 * horizontally-scrolling desktop Kanban. Every action funnels through the
 * same dispatch/confirmation callbacks the desktop board uses.
 */
export function CotizacionesMobileList({
  queue,
  onOpenQuote,
  onAdoptDriveFolder,
  onRequestAdjustments,
  onRequestConfirmSend,
}: {
  queue: ReturnType<typeof useCustomerQuotesGlobal>;
  onOpenQuote: (item: CustomerQuoteGlobalItem) => void;
  onAdoptDriveFolder: (item: (typeof queue.driveItems)[number]) => void;
  onRequestAdjustments: (item: CustomerQuoteGlobalItem) => void;
  onRequestConfirmSend: (item: CustomerQuoteGlobalItem) => void;
}) {
  const grouped: Record<CotizacionesLane, CustomerQuoteGlobalItem[]> = {
    drive_intake: [],
    review: [],
    approved_to_send: [],
    sent_follow_up: [],
  };
  for (const item of queue.items) {
    grouped[item.quote.board_stage].push(item);
  }

  return (
    <div className="space-y-4 md:hidden" data-testid="cotizaciones-board-mobile">
      {queue.actionError ? (
        <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {queue.actionError}
        </div>
      ) : null}

      {queue.error ? (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          <p className="font-medium">No pudimos cargar las cotizaciones.</p>
          <button
            type="button"
            onClick={queue.refetch}
            className="mt-2 rounded-md border border-red-300 bg-white px-3 py-1 text-sm font-medium text-red-800"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      {queue.loading ? (
        <div className="space-y-2" role="status" aria-live="polite" aria-label="Cargando cotizaciones">
          <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
          <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
        </div>
      ) : null}

      {!queue.loading && !queue.error
        ? SECTION_ORDER.map((lane) => {
            const count = lane === "drive_intake" ? queue.driveItems.length : grouped[lane].length;

            return (
              <section key={lane} data-testid={`cotizaciones-mobile-section-${lane}`} className="space-y-2">
                <div className="flex items-center justify-between px-1">
                  <h3 className="text-sm font-semibold text-slate-800">{SECTION_LABELS[lane]}</h3>
                  <span className="text-xs text-[var(--color-muted)]">{count}</span>
                </div>

                <div className="space-y-2">
                  {lane === "drive_intake"
                    ? queue.driveItems.map((item) => (
                        <DriveIntakeCard key={item.folder_id} item={item} onAdopt={() => onAdoptDriveFolder(item)} />
                      ))
                    : grouped[lane].map((item) => (
                        <CotizacionesCard
                          key={item.quote.quote_id}
                          item={item}
                          onOpen={() => onOpenQuote(item)}
                          dragDisabled
                          actions={
                            <QuoteWorkflowActions
                              revisionStatus={item.quote.revision_status}
                              disabled={queue.pendingQuoteId !== null}
                              onDispatch={(command) => void queue.dispatchWorkflowCommand(item, command)}
                              onRequestConfirmation={(command) =>
                                command === "request_adjustments"
                                  ? onRequestAdjustments(item)
                                  : onRequestConfirmSend(item)
                              }
                            />
                          }
                        />
                      ))}

                  {count === 0 ? (
                    <p className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-[var(--color-muted)]">
                      Sin cotizaciones
                    </p>
                  ) : null}
                </div>
              </section>
            );
          })
        : null}
    </div>
  );
}
