import { useEffect, useMemo, useState } from "react";
import type { ApiBackend } from "../../api/operatorTypes";
import type { EquipmentOpportunityItem } from "../../api/commercialTypes";
import { formatPagedFooterLabel } from "../../lib/clientTablePagination";
import { useClientTablePagination } from "../../lib/useClientTablePagination";
import { TablePaginationBar } from "./TablePaginationBar";
import {
  DEFAULT_EQUIPMENT_FILTERS,
  applyEquipmentTableView,
  equipmentFiltersActive,
  type EquipmentSortKey,
  type EquipmentTableFilters,
} from "../../lib/equipmentTableView";
import { equipmentSourceLabel } from "../../lib/dataSourceLabel";
import {
  EQUIPMENT_FEED_UNAVAILABLE_LINES,
  EQUIPMENT_FEED_UNAVAILABLE_TITLE,
  isEquipmentFeedUnavailable,
} from "../../lib/equipmentFeedStatus";
import { formatEquipmentCloseDate } from "../../lib/dashboardDateFormat";
import { getEquipmentFilterEmptyMessage } from "../../lib/equipmentEmptyState";
import { useEquipmentWatchlist } from "../../lib/equipmentWatchlist";
import { TokenLabel } from "../operator/TokenLabel";
import { ContactEmailButton } from "./ContactEmailButton";
import { EquipmentTriageBadges } from "./EquipmentTriageBadges";
import { EquipmentWatchlistButton } from "./EquipmentWatchlistButton";
import {
  EquipmentOpportunityDetailDrawer,
  MercadoPublicoLink,
  equipmentOpportunityRowKey,
} from "./EquipmentOpportunityDetailDrawer";
import { TableListToolbar, ToolbarField, toolbarInputClass, toolbarSelectClass } from "./TableListToolbar";
import { TableSection } from "./TableSection";

export function EquipmentOpportunitiesTable({
  backend,
  items,
  meta,
  loading,
  error,
  onRetry,
  onContactSelect,
}: {
  backend: ApiBackend;
  items: EquipmentOpportunityItem[];
  onContactSelect: (email: string) => void;
  meta: {
    data_source: "active_current_csv" | "postgres_mirror";
    reduced_mode: boolean;
    note: string;
    count: number;
    campaign_mode: string | null;
  } | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const [filters, setFilters] = useState<EquipmentTableFilters>(DEFAULT_EQUIPMENT_FILTERS);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const { savedKeys, isSaved, toggleSaved } = useEquipmentWatchlist();

  const sourceLabel = meta
    ? equipmentSourceLabel(backend, meta.data_source)
    : equipmentSourceLabel(backend, "active_current_csv");

  const visibleRows = useMemo(
    () => applyEquipmentTableView(items, filters, { savedKeys }),
    [items, filters, savedKeys],
  );
  const { pageSize, setPage, setPageSize, pagination } = useClientTablePagination(visibleRows, [
    filters.search,
    filters.sort,
    filters.triage,
    filters.watchlist,
    savedKeys.size,
    items.length,
  ]);
  const pagedRows = pagination.slice;
  const filtersActive = equipmentFiltersActive(filters);
  const loadedCount = items.length;
  const apiCount = meta?.count ?? loadedCount;
  const campaignExtra = meta?.campaign_mode ? `campaign ${meta.campaign_mode}` : undefined;
  const feedUnavailable = isEquipmentFeedUnavailable(meta);
  const showUnavailableEmpty = !loading && !error && feedUnavailable;
  const showZeroEmpty = !loading && !error && !feedUnavailable && loadedCount === 0;

  const selectedRow = useMemo(() => {
    if (!selectedKey) return null;
    return visibleRows.find((row) => equipmentOpportunityRowKey(row) === selectedKey) ?? null;
  }, [selectedKey, visibleRows]);

  useEffect(() => {
    if (!selectedKey) return;
    const stillVisible = visibleRows.some((row) => equipmentOpportunityRowKey(row) === selectedKey);
    if (!stillVisible) setSelectedKey(null);
  }, [selectedKey, visibleRows]);

  const openRow = (row: EquipmentOpportunityItem) => {
    setSelectedKey(equipmentOpportunityRowKey(row));
  };

  const toolbar = (
    <TableListToolbar>
      <ToolbarField label="Buscar" className="min-w-[12rem] flex-1">
        <input
          type="search"
          className={toolbarInputClass()}
          placeholder="Comprador, región, categoría, ítem, nota…"
          value={filters.search}
          onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          aria-label="Buscar oportunidades de equipos"
        />
      </ToolbarField>
      <ToolbarField label="Ordenar">
        <select
          className={toolbarSelectClass()}
          value={filters.sort}
          onChange={(e) =>
            setFilters((f) => ({ ...f, sort: e.target.value as EquipmentSortKey }))
          }
          aria-label="Ordenar oportunidades de equipos"
        >
          <option value="rank_asc">Prioridad (menor primero)</option>
          <option value="rank_desc">Prioridad (mayor primero)</option>
          <option value="close_date_asc">Fecha de cierre (más próxima)</option>
          <option value="close_date_desc">Fecha de cierre (más lejana)</option>
          <option value="category">Categoría de equipo</option>
          <option value="buyer">Comprador</option>
        </select>
      </ToolbarField>
      <ToolbarField label="Triaje">
        <select
          className={toolbarSelectClass()}
          value={filters.triage}
          onChange={(e) =>
            setFilters((f) => ({
              ...f,
              triage: e.target.value as EquipmentTableFilters["triage"],
            }))
          }
          aria-label="Filtrar oportunidades de equipos por triaje"
        >
          <option value="all">Todas</option>
          <option value="quote_now">Cotizar ahora</option>
          <option value="closing_soon">Cierre pronto</option>
          <option value="missing_contact">Sin contacto</option>
          <option value="supplier_needed">Requiere proveedor</option>
          <option value="mercado_publico_only">Solo Mercado Público</option>
        </select>
      </ToolbarField>
      <ToolbarField label="Guardadas">
        <select
          className={toolbarSelectClass()}
          value={filters.watchlist}
          onChange={(e) =>
            setFilters((f) => ({
              ...f,
              watchlist: e.target.value as EquipmentTableFilters["watchlist"],
            }))
          }
          aria-label="Filtrar oportunidades de equipos por lista de seguimiento"
        >
          <option value="all">Todas</option>
          <option value="saved">Solo guardadas</option>
        </select>
      </ToolbarField>
      {savedKeys.size > 0 ? (
        <p className="self-center text-xs text-[var(--color-muted)]" data-testid="equipment-watchlist-count">
          {savedKeys.size} guardada{savedKeys.size === 1 ? "" : "s"} en este navegador
        </p>
      ) : null}
      {filtersActive ? (
        <button
          type="button"
          className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
          onClick={() => setFilters(DEFAULT_EQUIPMENT_FILTERS)}
        >
          Limpiar filtros
        </button>
      ) : null}
    </TableListToolbar>
  );

  return (
    <TableSection
      title="Oportunidades de equipos"
      subtitle="Cola de licitaciones y equipos · solo lectura desde manifiesto."
      dataSourceLabel={sourceLabel}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={showZeroEmpty}
      emptyMessage="No hay oportunidades de equipos en la cola actual."
      filterEmpty={!loading && !error && loadedCount > 0 && visibleRows.length === 0}
      filterEmptyMessage={getEquipmentFilterEmptyMessage(filters)}
      reducedNote={!feedUnavailable && meta?.note ? meta.note : undefined}
      toolbar={loadedCount > 0 && !feedUnavailable ? toolbar : undefined}
    >
      {showUnavailableEmpty ? (
        <div
          className="rounded-xl border border-amber-200 bg-amber-50/90 px-5 py-5 text-sm text-amber-950"
          role="status"
          data-testid="equipment-feed-unavailable"
        >
          <p className="font-semibold">{EQUIPMENT_FEED_UNAVAILABLE_TITLE}</p>
          <ul className="mt-3 list-disc space-y-1 pl-5">
            {EQUIPMENT_FEED_UNAVAILABLE_LINES.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {!showUnavailableEmpty ? (
      <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-[var(--color-muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">Prioridad</th>
              <th className="px-3 py-2 font-medium">Comprador / institución</th>
              <th className="px-3 py-2 font-medium">Contacto</th>
              <th className="px-3 py-2 font-medium">Región</th>
              <th className="px-3 py-2 font-medium">Categoría</th>
              <th className="px-3 py-2 font-medium">Estado de contacto</th>
              <th className="px-3 py-2 font-medium">Fecha de cierre</th>
              <th className="px-3 py-2 font-medium">Canal</th>
              <th className="px-3 py-2 font-medium">Ítem / evidencia</th>
              <th className="px-3 py-2 font-medium">Próxima acción</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)]">
            {pagedRows.map((row, index) => {
              const rowKey = equipmentOpportunityRowKey(row);
              const isSelected = selectedKey === rowKey;
              const licitationLabel = row.codigo_licitacion
                ? `Ver detalle de licitación ${row.codigo_licitacion}`
                : `Ver detalle de oportunidad ${row.buyer || row.priority_rank}`;

              return (
              <tr
                key={rowKey}
                className={`align-top cursor-pointer transition-colors hover:bg-slate-50/80 ${
                  isSelected ? "bg-sky-50/90 ring-1 ring-inset ring-sky-200" : ""
                }`}
                onClick={() => openRow(row)}
                aria-selected={isSelected}
              >
                <td className="px-3 py-2 font-semibold text-slate-900">{row.priority_rank ?? index + 1}</td>
                <td className="px-3 py-2">
                  <button
                    type="button"
                    className="w-full text-left"
                    aria-expanded={isSelected}
                    aria-controls="equipment-opportunity-detail-panel"
                    aria-label={licitationLabel}
                    onClick={(event) => {
                      event.stopPropagation();
                      openRow(row);
                    }}
                  >
                    <div className="font-medium text-slate-900 hover:text-brand-800">
                      {row.buyer || "—"}
                    </div>
                    <div className="text-xs text-[var(--color-muted)]">{row.codigo_licitacion}</div>
                    <EquipmentTriageBadges item={row} />
                  </button>
                  <EquipmentWatchlistButton
                    item={row}
                    saved={isSaved(row)}
                    onToggle={() => toggleSaved(row)}
                  />
                  {row.mercado_publico_url ? (
                    <div onClick={(event) => event.stopPropagation()}>
                      <MercadoPublicoLink
                        url={row.mercado_publico_url}
                        className="mt-1 inline-block text-xs text-sky-700 hover:underline"
                      />
                    </div>
                  ) : null}
                </td>
                <td className="px-3 py-2" onClick={(event) => event.stopPropagation()}>
                  <ContactEmailButton email={row.contact_email} onSelect={onContactSelect} />
                </td>
                <td className="px-3 py-2 text-slate-700">{row.region || "—"}</td>
                <td className="px-3 py-2">
                  <TokenLabel
                    token={row.equipment_category}
                    kind="equipment_category"
                    className="text-slate-800"
                  />
                </td>
                <td className="px-3 py-2">
                  <TokenLabel
                    token={row.contact_status}
                    kind="equipment_contact_status"
                    className="text-xs text-slate-800"
                  />
                </td>
                <td className="px-3 py-2 whitespace-nowrap text-xs text-slate-600">
                  {formatEquipmentCloseDate(row.close_date, row.close_at)}
                </td>
                <td className="px-3 py-2">
                  <TokenLabel
                    token={row.safe_channel}
                    kind="equipment_safe_channel"
                    className="text-xs text-slate-800"
                  />
                </td>
                <td className="px-3 py-2 max-w-sm">
                  <p
                    className="line-clamp-2 text-slate-800"
                    title={row.item_description || undefined}
                  >
                    {row.item_description || "—"}
                  </p>
                </td>
                <td className="px-3 py-2">
                  <TokenLabel
                    token={row.next_action}
                    kind="equipment_next_action"
                    className="text-xs text-slate-800"
                  />
                </td>
              </tr>
            );
            })}
          </tbody>
        </table>
        {visibleRows.length > 0 ? (
          <TablePaginationBar
            page={pagination.page}
            totalPages={pagination.totalPages}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
          />
        ) : null}
        <p
          className="border-t border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-muted)]"
          data-testid="equipment-table-footer"
        >
          {formatPagedFooterLabel({
            from: pagination.from,
            to: pagination.to,
            visibleTotal: pagination.visibleTotal,
            page: pagination.page,
            totalPages: pagination.totalPages,
            extraParts: [
              ...(filtersActive && visibleRows.length < loadedCount ? ["filtros activos"] : []),
              ...(apiCount !== loadedCount ? [`API reportó ${apiCount}`] : []),
              ...(campaignExtra ? [campaignExtra] : []),
            ],
          })}
        </p>
      </div>
      ) : null}
      {selectedRow ? (
        <EquipmentOpportunityDetailDrawer
          item={selectedRow}
          open
          onClose={() => setSelectedKey(null)}
          watchlistSaved={isSaved(selectedRow)}
          onToggleWatchlist={() => toggleSaved(selectedRow)}
        />
      ) : null}
    </TableSection>
  );
}
