import { useMemo, useState } from "react";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { useCustomerQuotesGlobal } from "../components/quotes/useCustomerQuotesGlobal";
import { CotizacionesBoard } from "../components/quotes/CotizacionesBoard";
import { CotizacionesMobileList } from "../components/quotes/CotizacionesMobileList";
import { filterQuoteQueueRows, type QueueRecencyFilter } from "../components/quotes/customerQuoteQueueFilters";
import { QuoteDetailDrawer } from "../components/quotes/QuoteDetailDrawer";
import { NuevaCotizacionDialog } from "../components/quotes/NuevaCotizacionDialog";
import { AdoptDriveFolderModal } from "../components/quotes/AdoptDriveFolderModal";
import { WorkflowConfirmDialog } from "../components/quotes/WorkflowConfirmDialog";
import type { CustomerQuoteGlobalItem, DrivePendingQuoteItem, QuoteQueueRow } from "../api/customerQuoteTypes";

export function CotizacionesPage({
  onOpenVentas,
}: {
  onOpenVentas: (opportunityId?: string) => void;
}) {
  const queue = useCustomerQuotesGlobal();
  const [searchText, setSearchText] = useState("");
  const [recency, setRecency] = useState<QueueRecencyFilter>("all");
  const [openItem, setOpenItem] = useState<CustomerQuoteGlobalItem | null>(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [adoptItem, setAdoptItem] = useState<DrivePendingQuoteItem | null>(null);
  const [adjustmentsTarget, setAdjustmentsTarget] = useState<CustomerQuoteGlobalItem | null>(null);
  const [sendTarget, setSendTarget] = useState<CustomerQuoteGlobalItem | null>(null);

  const visibleRows = useMemo(
    () => filterQuoteQueueRows(queue.rows, { searchText, recency }),
    [queue.rows, searchText, recency],
  );

  const visibleItems = useMemo(
    () =>
      visibleRows
        .filter((row): row is Extract<QuoteQueueRow, { kind: "crm" }> => row.kind === "crm")
        .map((row) => row.item),
    [visibleRows],
  );
  const visibleDriveItems = useMemo(
    () =>
      visibleRows
        .filter((row): row is Extract<QuoteQueueRow, { kind: "drive_pending" }> => row.kind === "drive_pending")
        .map((row) => row.item),
    [visibleRows],
  );

  const filteredQueue = { ...queue, items: visibleItems, driveItems: visibleDriveItems };
  const dispatchPending = openItem !== null && queue.pendingQuoteId === openItem.quote.quote_id;

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="Cotizaciones"
        subtitle="Tablero durable de cotizaciones y su carpeta en Drive."
        actions={
          <>
            <button
              type="button"
              onClick={() => setCreateDialogOpen(true)}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
            >
              Nueva Cotización
            </button>
            <button
              type="button"
              onClick={queue.refetch}
              disabled={queue.loading}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              {queue.loading ? "Recargando…" : "Recargar cotizaciones"}
            </button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          aria-label="Buscar cotizaciones"
          placeholder="Buscar por número, cliente u oportunidad…"
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
          className="w-full max-w-xs rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600"
        />
        <select
          aria-label="Filtrar por recencia"
          value={recency}
          onChange={(event) => setRecency(event.target.value as QueueRecencyFilter)}
          className="rounded-md border border-[var(--color-border)] bg-white px-2 py-1.5 text-sm text-slate-700"
        >
          <option value="all">Toda fecha</option>
          <option value="7d">Últimos 7 días</option>
          <option value="30d">Últimos 30 días</option>
        </select>
      </div>

      {queue.error ? (
        <p role="alert" className="text-sm text-amber-900">{queue.error}</p>
      ) : null}

      {!queue.loading && !queue.error && queue.isEmpty ? (
        <V2EmptyState
          title="Aún no hay cotizaciones"
          description="Crea la primera cotización desde una oportunidad en Ventas, o usa Nueva Cotización aquí."
          action={
            <button
              type="button"
              onClick={() => onOpenVentas()}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
            >
              Ir a Ventas
            </button>
          }
        />
      ) : !queue.loading && !queue.error && visibleRows.length === 0 ? (
        <V2EmptyState title="Sin resultados para estos filtros" description="Ajusta la búsqueda o los filtros." />
      ) : (
        <>
          <CotizacionesBoard
            queue={filteredQueue}
            onOpenQuote={setOpenItem}
            onAdoptDriveFolder={setAdoptItem}
            onRequestAdjustments={setAdjustmentsTarget}
            onRequestConfirmSend={setSendTarget}
          />
          <CotizacionesMobileList
            queue={filteredQueue}
            onOpenQuote={setOpenItem}
            onAdoptDriveFolder={setAdoptItem}
            onRequestAdjustments={setAdjustmentsTarget}
            onRequestConfirmSend={setSendTarget}
          />
        </>
      )}

      <QuoteDetailDrawer
        item={openItem}
        open={openItem !== null}
        onClose={() => setOpenItem(null)}
        onOpenVentas={onOpenVentas}
        onDispatchWorkflowCommand={queue.dispatchWorkflowCommand}
        onRequestAdjustments={setAdjustmentsTarget}
        onRequestConfirmSend={setSendTarget}
        dispatchPending={dispatchPending}
      />

      <NuevaCotizacionDialog
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        onCreated={(item) => {
          setCreateDialogOpen(false);
          setOpenItem(item);
          void queue.refetch();
        }}
      />

      <AdoptDriveFolderModal
        item={adoptItem}
        open={adoptItem !== null}
        onClose={() => setAdoptItem(null)}
        onAdopted={() => {
          setAdoptItem(null);
          void queue.refetch();
        }}
      />

      <WorkflowConfirmDialog
        open={adjustmentsTarget !== null}
        item={adjustmentsTarget}
        title="Solicitar ajustes"
        message="La cotización volverá a Preparación para que el equipo la ajuste antes de reenviarla a revisión."
        confirmLabel="Solicitar ajustes"
        onConfirm={(item) => queue.dispatchWorkflowCommand(item, "request_adjustments")}
        onClose={() => setAdjustmentsTarget(null)}
      />

      <WorkflowConfirmDialog
        open={sendTarget !== null}
        item={sendTarget}
        title="Confirmar envío"
        message="Esta acción confirma que la cotización ya fue enviada al cliente por otro medio (correo, etc.). No se enviará ningún correo automáticamente."
        confirmLabel="Confirmar envío"
        onConfirm={(item) => queue.dispatchWorkflowCommand(item, "confirm_send")}
        onClose={() => setSendTarget(null)}
      />
    </div>
  );
}
