import { useMemo, useState } from "react";
import type { ApiBackend } from "../../api/operatorTypes";
import type { WarmCaseCategory, WarmCaseItem, WarmCaseStatus } from "../../api/commercialTypes";
import { formatWarmCasesTableFooter } from "../../lib/clientTablePagination";
import { useClientTablePagination } from "../../lib/useClientTablePagination";
import { warmCasesSourceLabel } from "../../lib/dataSourceLabel";
import {
  clearWarmCaseTableFilters,
  DEFAULT_WARM_FILTERS,
  WARM_VIEW_PRESET_LABELS,
  WARM_VIEW_PRESET_ORDER,
  applyWarmCaseTableView,
  uniqueWarmCategories,
  uniqueWarmStatuses,
  warmFiltersActive,
  type WarmCaseSortKey,
  type WarmCaseTableFilters,
} from "../../lib/warmCaseTableView";
import {
  getWarmCaseReviewKey,
  useWarmCaseReviewLabels,
  WARM_CASE_REVIEW_FILTER_OPTIONS,
  WARM_CASE_REVIEW_LABEL_OPTIONS,
  type WarmCaseReviewFilter,
  type WarmCaseReviewLabel,
} from "../../lib/warmCaseReviewLabels";
import { formatOperatorToken, warmStatusBadgeClass } from "../../lib/operatorLabels";
import { formatWarmCaseNextAction } from "../../lib/warmCaseDetailStrategy";
import { formatDashboardDateTime } from "../../lib/dashboardDateFormat";
import { truncate } from "../../lib/safeText";
import {
  formatWarmCaseSubjectLine,
  warmCaseSubjectShowsInlineGroupCount,
} from "../../lib/warmCaseDisplay";
import { TokenLabel } from "../operator/TokenLabel";
import { ContactEmailButton } from "./ContactEmailButton";
import { CopyTextButton } from "./CopyTextButton";
import { TableListToolbar, ToolbarField, toolbarInputClass, toolbarSelectClass } from "./TableListToolbar";
import { TableSection } from "./TableSection";
import { CaseDetailDrawer } from "./CaseDetailDrawer";
import { TablePaginationBar } from "./TablePaginationBar";

export function WarmCasesTable({
  backend,
  items,
  meta,
  loading,
  error,
  onRetry,
  onContactSelect,
  title = "Casos tibios",
  subtitle = "Cola de solo lectura · solo asunto y vista previa (sin cuerpo del correo).",
  initialFilters,
  showViewPresets = true,
  sectionName,
  globalQueueTotal,
}: {
  backend: ApiBackend;
  items: WarmCaseItem[];
  onContactSelect: (email: string) => void;
  meta: {
    data_source: "sqlite" | "postgres_mirror";
    reduced_mode: boolean;
    note: string;
    count: number;
  } | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  title?: string;
  subtitle?: string;
  initialFilters?: Partial<WarmCaseTableFilters>;
  showViewPresets?: boolean;
  /** Subsection label (e.g. Pagos) when table shows a filtered slice of the warm queue. */
  sectionName?: string;
  /** Full warm queue size from parent fetch; used with sectionName for footer clarity. */
  globalQueueTotal?: number;
}) {
  const [filters, setFilters] = useState<WarmCaseTableFilters>({
    ...DEFAULT_WARM_FILTERS,
    ...initialFilters,
  });
  const [selectedCase, setSelectedCase] = useState<WarmCaseItem | null>(null);
  const { labels, reviewedCount, getLabel, setLabel } = useWarmCaseReviewLabels();

  const sourceLabel = meta
    ? warmCasesSourceLabel(backend, meta.data_source)
    : warmCasesSourceLabel(backend, "sqlite");

  const visibleRows = useMemo(
    () => applyWarmCaseTableView(items, filters, { reviewLabels: labels }),
    [items, filters, labels],
  );
  const statusOptions = useMemo(() => uniqueWarmStatuses(items), [items]);
  const categoryOptions = useMemo(() => uniqueWarmCategories(items), [items]);
  const filtersActive = warmFiltersActive(filters);
  const loadedCount = items.length;
  const presetLabel = WARM_VIEW_PRESET_LABELS[filters.preset];

  const { pageSize, setPage, setPageSize, pagination } = useClientTablePagination(visibleRows, [
    filters.search,
    filters.status,
    filters.category,
    filters.sort,
    filters.preset,
    filters.review,
    filters.hideInternalContacts,
    reviewedCount,
    loadedCount,
  ]);

  const pagedRows = pagination.slice;

  const footerLabel = formatWarmCasesTableFooter({
    from: pagination.from,
    to: pagination.to,
    visibleTotal: pagination.visibleTotal,
    loadedTotal: loadedCount,
    page: pagination.page,
    totalPages: pagination.totalPages,
    sectionName,
    globalQueueTotal,
    presetLabel: showViewPresets ? presetLabel : undefined,
    filtered: filtersActive,
  });

  const presetChips = (
    <div
      className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--color-border)] bg-slate-50/80 px-3 py-3"
      role="group"
      aria-label="Vista de la cola tibia"
    >
      <span className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
        Vista
      </span>
      {WARM_VIEW_PRESET_ORDER.map((preset) => {
        const active = filters.preset === preset;
        return (
          <button
            key={preset}
            type="button"
            aria-pressed={active}
            className={`rounded-full px-3 py-1 text-sm font-medium transition-colors ${
              active
                ? "bg-brand-600 text-white shadow-sm"
                : "border border-[var(--color-border)] bg-white text-slate-700 hover:bg-slate-100"
            }`}
            onClick={() => setFilters((f) => ({ ...f, preset }))}
          >
            {WARM_VIEW_PRESET_LABELS[preset]}
          </button>
        );
      })}
    </div>
  );

  const toolbar = (
    <TableListToolbar>
      <ToolbarField label="Buscar" className="min-w-[12rem] flex-1">
        <input
          type="search"
          className={toolbarInputClass()}
          placeholder="Contacto, dominio, organización, asunto…"
          value={filters.search}
          onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          aria-label="Buscar casos tibios"
        />
      </ToolbarField>
      <ToolbarField label="Estado">
        <select
          className={toolbarSelectClass()}
          value={filters.status}
          onChange={(e) =>
            setFilters((f) => ({ ...f, status: e.target.value as WarmCaseStatus | "" }))
          }
          aria-label="Filtrar por estado"
        >
          <option value="">Todos</option>
          {statusOptions.map((s) => (
            <option key={s} value={s} title={s}>
              {formatOperatorToken(s, "warm_status").label}
            </option>
          ))}
        </select>
      </ToolbarField>
      <ToolbarField label="Categoría">
        <select
          className={toolbarSelectClass()}
          value={filters.category}
          onChange={(e) =>
            setFilters((f) => ({ ...f, category: e.target.value as WarmCaseCategory | "" }))
          }
          aria-label="Filtrar por categoría"
        >
          <option value="">Todas</option>
          {categoryOptions.map((c) => (
            <option key={c} value={c} title={c}>
              {formatOperatorToken(c, "warm_category").label}
            </option>
          ))}
        </select>
      </ToolbarField>
      <ToolbarField label="Orden">
        <select
          className={toolbarSelectClass()}
          value={filters.sort}
          onChange={(e) =>
            setFilters((f) => ({ ...f, sort: e.target.value as WarmCaseSortKey }))
          }
          aria-label="Ordenar casos tibios"
        >
          <option value="last_seen_desc">Última actividad (más reciente)</option>
          <option value="last_seen_asc">Última actividad (más antigua)</option>
          <option value="status">Estado</option>
          <option value="category">Categoría</option>
          <option value="contact">Correo de contacto</option>
        </select>
      </ToolbarField>
      <ToolbarField label="Revisión">
        <select
          className={toolbarSelectClass()}
          value={filters.review}
          onChange={(e) =>
            setFilters((f) => ({
              ...f,
              review: e.target.value as WarmCaseReviewFilter,
            }))
          }
          aria-label="Filtrar por etiqueta de revisión local"
        >
          {WARM_CASE_REVIEW_FILTER_OPTIONS.map((option) => (
            <option key={option.value || "all"} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </ToolbarField>
      {reviewedCount > 0 ? (
        <p
          className="self-center text-xs text-[var(--color-muted)]"
          data-testid="warm-case-review-count"
        >
          {reviewedCount} revisado{reviewedCount === 1 ? "" : "s"} en este navegador
        </p>
      ) : null}
      <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
        <input
          type="checkbox"
          checked={filters.hideInternalContacts}
          onChange={(e) =>
            setFilters((f) => ({ ...f, hideInternalContacts: e.target.checked }))
          }
          aria-label="Ocultar contactos internos de OrigenLab"
        />
        <span title="Oculta @origenlab.cl y @labdelivery.cl solo en esta tabla (filtro local).">
          Ocultar contactos internos
        </span>
      </label>
      {filtersActive ? (
        <button
          type="button"
          className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
          onClick={() => setFilters(clearWarmCaseTableFilters())}
          aria-label="Limpiar filtros y volver a Clientes reales"
          title="Limpia búsqueda, estado y categoría; restablece la vista Clientes reales."
        >
          Limpiar filtros
        </button>
      ) : null}
    </TableListToolbar>
  );

  return (
    <TableSection
      title={title}
      subtitle={subtitle}
      dataSourceLabel={sourceLabel}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={!loading && !error && loadedCount === 0}
      emptyMessage="No hay casos tibios desde la API."
      filterEmpty={!loading && !error && loadedCount > 0 && visibleRows.length === 0}
      filterEmptyMessage="Ningún caso coincide con la búsqueda o los filtros."
      reducedNote={
        meta?.reduced_mode && meta.note
            ? `Modo reducido: ${meta.note}`
            : meta?.reduced_mode
            ? "Modo reducido: enriquecimiento o datos no disponibles."
            : undefined
      }
      toolbar={
        loadedCount > 0 ? (
          <div className="space-y-3">
            {showViewPresets ? presetChips : null}
            {toolbar}
          </div>
        ) : undefined
      }
    >
      <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-[var(--color-muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">Contacto</th>
              <th className="px-3 py-2 font-medium">Organización</th>
              <th className="px-3 py-2 font-medium">Estado</th>
              <th className="px-3 py-2 font-medium">Categoría</th>
              <th className="px-3 py-2 font-medium">Última actividad</th>
              <th className="px-3 py-2 font-medium">Equipo</th>
              <th className="px-3 py-2 font-medium">Asunto / vista previa</th>
              <th className="px-3 py-2 font-medium">Próxima acción</th>
              <th className="px-3 py-2 font-medium">Revisión</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)]">
            {pagedRows.map((row) => (
              <tr
                key={getWarmCaseReviewKey(row)}
                className="align-top cursor-pointer transition-colors hover:bg-slate-50 focus-within:bg-slate-50"
                onClick={() => setSelectedCase(row)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelectedCase(row);
                  }
                }}
                tabIndex={0}
                role="button"
                aria-label={`Abrir resumen del caso ${row.contact_email}`}
              >
                <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                  <ContactEmailButton email={row.contact_email} onSelect={onContactSelect} />
                  <div className="mt-1">
                    <CopyTextButton label="Copiar correo" value={row.contact_email} />
                  </div>
                </td>
                <td className="px-3 py-2 text-slate-800">{row.account_name || "—"}</td>
                <td className="px-3 py-2">
                  <TokenLabel
                    token={row.status}
                    kind="warm_status"
                    className={warmStatusBadgeClass(row.status)}
                  />
                </td>
                <td className="px-3 py-2">
                  <TokenLabel
                    token={row.category}
                    kind="warm_category"
                    className="text-xs text-slate-800"
                  />
                </td>
                <td className="px-3 py-2 whitespace-nowrap text-xs text-slate-600">
                  {formatDashboardDateTime(row.last_seen_at)}
                </td>
                <td className="px-3 py-2 text-xs text-slate-700">
                  {row.equipment_signal || "—"}
                </td>
                <td className="px-3 py-2 max-w-xs">
                  <div className="font-medium text-slate-800">
                    {formatWarmCaseSubjectLine(row)}
                    {(row.grouped_email_count ?? 1) > 1 &&
                    !warmCaseSubjectShowsInlineGroupCount(row) ? (
                      <span
                        className="ml-2 inline-flex rounded-md border border-sky-200 bg-sky-50 px-2 py-0.5 text-xs font-semibold text-sky-900"
                        title="Correos agrupados en este caso tibio (no es todo el historial Gmail)"
                        data-testid="warm-case-grouped-email-count"
                      >
                        {row.grouped_email_count} correos agrupados
                      </span>
                    ) : null}
                  </div>
                  {row.snippet ? (
                    <p className="mt-1 text-xs text-[var(--color-muted)]">
                      <span className="font-medium text-slate-600">Vista previa:</span>{" "}
                      {truncate(row.snippet, 120)}
                    </p>
                  ) : null}
                </td>
                <td className="px-3 py-2">
                  <span className="text-xs text-slate-800">{formatWarmCaseNextAction(row)}</span>
                </td>
                <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                  <select
                    className="w-full min-w-[9rem] rounded-md border border-[var(--color-border)] bg-white px-2 py-1 text-xs text-slate-800"
                    value={getLabel(row)}
                    onChange={(e) => setLabel(row, e.target.value as WarmCaseReviewLabel)}
                    onClick={(e) => e.stopPropagation()}
                    aria-label={`Etiqueta de revisión para ${row.contact_email}`}
                    data-testid="warm-case-review-select"
                  >
                    <option value="">Sin revisar</option>
                    {WARM_CASE_REVIEW_LABEL_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {pagination.visibleTotal > 0 ? (
          <TablePaginationBar
            page={pagination.page}
            totalPages={pagination.totalPages}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
          />
        ) : null}
        <p className="border-t border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-muted)]">
          {footerLabel}
        </p>
      </div>

      <CaseDetailDrawer
        item={selectedCase}
        open={selectedCase !== null}
        onClose={() => setSelectedCase(null)}
        onContactSelect={onContactSelect}
      />
    </TableSection>
  );
}
