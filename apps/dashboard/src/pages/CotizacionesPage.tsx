import { useMemo, useState } from "react";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { useCustomerQuotesGlobal } from "../components/quotes/useCustomerQuotesGlobal";
import { CustomerQuoteQueueTable } from "../components/quotes/CustomerQuoteQueueTable";
import { filterQuoteQueueItems, type QueueRecencyFilter } from "../components/quotes/customerQuoteQueueFilters";
import { QuoteDetailDrawer } from "../components/quotes/QuoteDetailDrawer";
import { NuevaCotizacionDialog } from "../components/quotes/NuevaCotizacionDialog";
import type { CustomerQuoteGlobalItem } from "../api/customerQuoteTypes";

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

  const visibleItems = useMemo(
    () => filterQuoteQueueItems(queue.items, { searchText, recency }),
    [queue.items, searchText, recency],
  );

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="Cotizaciones"
        subtitle="Cola global de cotizaciones durables y su carpeta en Drive."
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

      {!queue.loading && !queue.error && visibleItems.length === 0 ? (
        <V2EmptyState
          title={queue.items.length === 0 ? "Aún no hay cotizaciones" : "Sin resultados para estos filtros"}
          description={
            queue.items.length === 0
              ? "Crea la primera cotización desde una oportunidad en Ventas, o usa Nueva Cotización aquí."
              : "Ajusta la búsqueda o los filtros."
          }
          action={
            queue.items.length === 0 ? (
              <button
                type="button"
                onClick={() => onOpenVentas()}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
              >
                Ir a Ventas
              </button>
            ) : undefined
          }
        />
      ) : (
        <CustomerQuoteQueueTable items={visibleItems} onOpenQuote={setOpenItem} />
      )}

      <QuoteDetailDrawer
        item={openItem}
        open={openItem !== null}
        onClose={() => setOpenItem(null)}
        onOpenVentas={onOpenVentas}
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
    </div>
  );
}
