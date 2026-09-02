import { useState } from "react";
import type { CustomerQuoteGlobalItem } from "../../api/customerQuoteTypes";
import { resolveBoardMove, type CotizacionesLane } from "../../lib/quoteBoard";
import { CotizacionesCard } from "./CotizacionesCard";
import { DriveIntakeCard } from "./DriveIntakeCard";
import type { useCustomerQuotesGlobal } from "./useCustomerQuotesGlobal";

const COLUMN_ORDER: readonly CotizacionesLane[] = [
  "drive_intake",
  "preparation",
  "review",
  "approved_to_send",
  "sent_follow_up",
];

const COLUMN_LABELS: Record<CotizacionesLane, string> = {
  drive_intake: "Pendientes Drive",
  preparation: "Preparación",
  review: "Revisión",
  approved_to_send: "Aprobada / por enviar",
  sent_follow_up: "Enviada / seguimiento",
};

/**
 * Desktop Kanban. A board gesture never writes a lane directly: every drop
 * resolves through resolveBoardMove to either an immediate command
 * dispatch, an explicit confirmation callback (request_adjustments /
 * confirm_send), or a refusal that never reaches the API.
 */
export function CotizacionesBoard({
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
  const [moveError, setMoveError] = useState<string | null>(null);

  const grouped: Record<CotizacionesLane, CustomerQuoteGlobalItem[]> = {
    drive_intake: [],
    preparation: [],
    review: [],
    approved_to_send: [],
    sent_follow_up: [],
  };
  for (const item of queue.items) {
    grouped[item.quote.board_stage].push(item);
  }

  function columnCount(lane: CotizacionesLane): number {
    return lane === "drive_intake" ? queue.driveItems.length : grouped[lane].length;
  }

  function handleDrop(targetLane: CotizacionesLane, quoteId: string) {
    if (queue.pendingQuoteId !== null) return;

    const item = queue.items.find((row) => row.quote.quote_id === quoteId);
    if (!item) return;

    const decision = resolveBoardMove(item.quote.board_stage, targetLane);

    if (!decision.allowed) {
      setMoveError(decision.reason);
      return;
    }

    setMoveError(null);

    if (decision.requiresConfirmation) {
      if (decision.command === "request_adjustments") {
        onRequestAdjustments(item);
      } else {
        onRequestConfirmSend(item);
      }
      return;
    }

    void queue.dispatchWorkflowCommand(item, decision.command);
  }

  return (
    <div className="hidden space-y-4 md:block" data-testid="cotizaciones-board-desktop">
      {moveError ? (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          <span>{moveError}</span>
          <button type="button" onClick={() => setMoveError(null)} className="shrink-0 text-amber-700 underline">
            Cerrar
          </button>
        </div>
      ) : null}

      {queue.actionError ? (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          <span>{queue.actionError}</span>
          <button type="button" onClick={queue.dismissActionError} className="shrink-0 text-amber-700 underline">
            Cerrar
          </button>
        </div>
      ) : null}

      {queue.error ? (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          <p className="font-medium">No pudimos cargar las cotizaciones.</p>
          <button
            type="button"
            onClick={queue.refetch}
            className="mt-2 rounded-md border border-red-300 bg-white px-3 py-1 text-sm font-medium text-red-800 hover:bg-red-50"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      <div className="flex gap-4 overflow-x-auto pb-2">
        {COLUMN_ORDER.map((lane) => (
          <div
            key={lane}
            data-testid={`cotizaciones-column-drop-${lane}`}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              const quoteId = event.dataTransfer.getData("text/plain");
              if (quoteId) handleDrop(lane, quoteId);
            }}
            className="w-72 shrink-0 space-y-2"
          >
            <div className="flex items-center justify-between px-1">
              <h3 className="text-sm font-semibold text-slate-800">{COLUMN_LABELS[lane]}</h3>
              <span className="text-xs text-[var(--color-muted)]">{columnCount(lane)}</span>
            </div>

            <div className="motion-safe:transition-all space-y-2">
              {lane === "drive_intake"
                ? queue.driveItems.map((item) => (
                    <DriveIntakeCard key={item.folder_id} item={item} onAdopt={() => onAdoptDriveFolder(item)} />
                  ))
                : grouped[lane].map((item) => (
                    <CotizacionesCard
                      key={item.quote.quote_id}
                      item={item}
                      onOpen={() => onOpenQuote(item)}
                      dragDisabled={queue.pendingQuoteId !== null}
                    />
                  ))}

              {columnCount(lane) === 0 ? (
                <p className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-[var(--color-muted)]">
                  Sin cotizaciones
                </p>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
