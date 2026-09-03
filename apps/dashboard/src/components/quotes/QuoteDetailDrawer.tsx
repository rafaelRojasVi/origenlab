import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  fetchCustomerQuote,
  fetchCustomerQuoteEvents,
  retryCustomerQuoteDriveWorkspace,
} from "../../api/customerQuoteClient";
import { OperatorApiError } from "../../api/operatorClient";
import type { CustomerQuote, CustomerQuoteEvent, CustomerQuoteGlobalItem } from "../../api/customerQuoteTypes";
import { salesOpportunityStageLabel } from "../../lib/salesOpportunityFormat";
import { formatCommercialOpportunityDate } from "../../lib/commercialOpportunityFormat";
import { REVISION_STATUS_LABELS } from "../../lib/revisionStatusLabel";
import type { WorkflowCommand } from "../../lib/quoteBoard";
import { QuoteWorkspaceStatus } from "./driveWorkspaceUi";
import { QuoteWorkflowActions } from "./QuoteWorkflowActions";

const RETRY_CONFLICT_MESSAGE =
  "La cotización cambió en otra sesión. Actualizamos el estado con la versión más reciente.";

const EVENT_TYPE_LABELS: Record<string, string> = {
  quote_created: "Cotización creada",
  drive_provision_requested: "Aprovisionamiento de Drive solicitado",
  drive_workspace_ready: "Carpeta de Drive lista",
  drive_provision_failed: "Error al aprovisionar Drive",
  quote_adopted_from_drive: "Incorporada desde Drive",
  quote_submitted_for_review: "Enviada a revisión",
  quote_adjustments_requested: "Ajustes solicitados",
  quote_approved: "Aprobada",
  quote_send_confirmed: "Envío confirmado",
  quote_closed: "Cotización cerrada",
};

function eventTypeLabel(eventType: string): string {
  return EVENT_TYPE_LABELS[eventType] ?? eventType;
}

/** Refreshing on open must never revert a faster local mutation to stale data. */
function mergeIfNewer(current: CustomerQuote | null, incoming: CustomerQuote): CustomerQuote | null {
  if (!current || incoming.version < current.version) return current;
  return { ...current, ...incoming };
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  if (children == null || children === "") return null;
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
      <dt className="shrink-0 text-xs font-medium uppercase tracking-wide text-[var(--color-muted)] sm:w-32">{label}</dt>
      <dd className="min-w-0 break-words text-sm text-slate-800">{children}</dd>
    </div>
  );
}

export function QuoteDetailDrawer({
  item,
  open,
  onClose,
  onOpenVentas,
  onDispatchWorkflowCommand,
  onRequestAdjustments,
  onRequestConfirmSend,
  onRequestClose,
  dispatchPending,
}: {
  item: CustomerQuoteGlobalItem | null;
  open: boolean;
  onClose: () => void;
  onOpenVentas: (opportunityId: string) => void;
  onDispatchWorkflowCommand: (
    item: CustomerQuoteGlobalItem,
    command: WorkflowCommand,
  ) => Promise<void>;
  onRequestAdjustments: (item: CustomerQuoteGlobalItem) => void;
  onRequestConfirmSend: (item: CustomerQuoteGlobalItem) => void;
  onRequestClose: (item: CustomerQuoteGlobalItem) => void;
  dispatchPending: boolean;
}) {
  const [quote, setQuote] = useState<CustomerQuote | null>(item?.quote ?? null);
  const [retryPending, setRetryPending] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [events, setEvents] = useState<CustomerQuoteEvent[]>([]);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (open && item) {
      setQuote(item.quote);
      setRetryError(null);
      setEvents([]);

      void fetchCustomerQuote(item.quote.quote_id)
        .then((result) => {
          setQuote((current) => mergeIfNewer(current, result.item));
        })
        .catch(() => undefined);

      void fetchCustomerQuoteEvents(item.quote.quote_id)
        .then((result) => setEvents(result.items))
        .catch(() => undefined);
    }
  }, [open, item]);

  useEffect(() => {
    if (!open || !quote) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onCloseRef.current();
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused.current?.focus();
    };
  }, [open, quote?.quote_id]);

  if (!open || !item || !quote) return null;

  async function handleRetry() {
    if (retryPending || !quote) return;

    setRetryPending(true);
    setRetryError(null);

    try {
      const updated = await retryCustomerQuoteDriveWorkspace(quote.quote_id, {
        expected_version: quote.drive_workspace.version,
      });
      setQuote((current) => mergeIfNewer(current, updated));
    } catch (reason: unknown) {
      if (reason instanceof OperatorApiError && reason.status === 409) {
        setRetryError(RETRY_CONFLICT_MESSAGE);
        try {
          const refreshed = await fetchCustomerQuote(quote.quote_id);
          setQuote((current) => mergeIfNewer(current, refreshed.item));
        } catch {
          // Keep the current state; the conflict message stands.
        }
      } else {
        setRetryError("No pudimos reintentar la creación en Drive. Vuelve a intentarlo.");
      }
    } finally {
      setRetryPending(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="fixed inset-0 z-40 hidden bg-slate-900/30 md:block"
        aria-label="Cerrar detalle de cotización"
        onClick={onClose}
      />

      <aside
        role="dialog"
        aria-modal="false"
        aria-labelledby="quote-detail-heading"
        data-testid="quote-detail-drawer"
        className="fixed inset-0 z-50 flex flex-col bg-[var(--color-card)] md:inset-y-0 md:left-auto md:right-0 md:max-w-xl md:border-l md:border-[var(--color-border)] md:shadow-xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">Cotización</p>
            <h2 id="quote-detail-heading" className="mt-1 text-lg font-semibold text-slate-900">
              {quote.document_number}
            </h2>
            <p className="mt-1 text-sm text-slate-500">{quote.quote_number}</p>
          </div>
          <button
            type="button"
            ref={closeButtonRef}
            onClick={onClose}
            className="shrink-0 rounded-md border border-[var(--color-border)] px-2 py-1 text-sm text-slate-700 hover:bg-slate-50"
          >
            Cerrar
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
          <section className="space-y-2" aria-label="Identidad">
            <h3 className="text-sm font-semibold text-slate-800">Identidad</h3>
            <dl className="space-y-2">
              <DetailRow label="Organización">{item.organization_display_name ?? "—"}</DetailRow>
              <DetailRow label="Contacto">{item.contact_display_name ?? "—"}</DetailRow>
              <DetailRow label="Correo">{item.contact_primary_email ?? "—"}</DetailRow>
            </dl>
          </section>

          <section className="space-y-2" aria-label="Oportunidad">
            <h3 className="text-sm font-semibold text-slate-800">Oportunidad</h3>
            <dl className="space-y-2">
              <DetailRow label="Título">{quote.sales_opportunity_title}</DetailRow>
              <DetailRow label="Etapa">{salesOpportunityStageLabel(item.sales_opportunity_stage)}</DetailRow>
              <DetailRow label="Responsable">{item.sales_opportunity_owner_key}</DetailRow>
            </dl>
            <button
              type="button"
              onClick={() => onOpenVentas(quote.sales_opportunity_id)}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Ver en Ventas
            </button>
          </section>

          {item.next_task_title ? (
            <section
              className="rounded-lg border border-brand-200 bg-brand-50 px-3 py-2"
              aria-label="Próxima acción"
            >
              <p className="text-xs font-semibold uppercase tracking-wide text-brand-700">Próxima acción</p>
              <p className="text-sm text-slate-800">{item.next_task_title}</p>
              {item.next_task_due_at ? (
                <p className="text-xs text-slate-600">Vence: {formatCommercialOpportunityDate(item.next_task_due_at)}</p>
              ) : null}
            </section>
          ) : null}

          <section className="space-y-2" aria-label="Cotización">
            <h3 className="text-sm font-semibold text-slate-800">Cotización</h3>
            <dl className="space-y-2">
              <DetailRow label="Estado">
                {REVISION_STATUS_LABELS[quote.revision_status]} · Rev. {quote.latest_revision_number}
              </DetailRow>
              <DetailRow label="Creada">{formatCommercialOpportunityDate(quote.created_at)}</DetailRow>
              <DetailRow label="Actualizada">{formatCommercialOpportunityDate(quote.revision_updated_at)}</DetailRow>
            </dl>
            <div className="flex flex-wrap gap-2">
              <QuoteWorkflowActions
                revisionStatus={quote.revision_status}
                disabled={dispatchPending}
                onDispatch={(command) => void onDispatchWorkflowCommand({ ...item, quote }, command)}
                onRequestConfirmation={(command) =>
                  command === "request_adjustments"
                    ? onRequestAdjustments({ ...item, quote })
                    : onRequestConfirmSend({ ...item, quote })
                }
              />
              {quote.revision_status === "sent" ? (
                <button
                  type="button"
                  disabled={dispatchPending}
                  onClick={() => onRequestClose({ ...item, quote })}
                  className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50 disabled:opacity-50"
                >
                  Cerrar cotización
                </button>
              ) : null}
            </div>
          </section>

          <section className="space-y-2" aria-label="Carpeta en Drive">
            <h3 className="text-sm font-semibold text-slate-800">Carpeta en Drive</h3>
            <QuoteWorkspaceStatus
              quote={quote}
              retryPending={retryPending}
              retryError={retryError}
              onRetry={() => void handleRetry()}
            />
          </section>

          <section className="space-y-2" aria-label="Historial">
            <h3 className="text-sm font-semibold text-slate-800">Historial</h3>
            {events.length === 0 ? (
              <p className="text-sm text-[var(--color-muted)]">Sin eventos registrados.</p>
            ) : (
              <ul className="space-y-1.5">
                {events.map((event) => (
                  <li key={event.event_id} className="text-sm text-slate-700">
                    <span className="font-medium text-slate-800">{eventTypeLabel(event.event_type)}</span>
                    <span className="ml-2 text-xs text-[var(--color-muted)]">
                      {formatCommercialOpportunityDate(event.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </aside>
    </>
  );
}
