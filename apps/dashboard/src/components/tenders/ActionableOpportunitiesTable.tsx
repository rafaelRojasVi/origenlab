import { useEffect, useState } from "react";
import type { Availed, CurrentOpportunityRow } from "../../api/institutionIntel/types";
import { institutionIntelAdapter } from "../../api/institutionIntel/adapter";
import { AXIS_BAND_LABEL, formatEquipmentCategory } from "../../lib/institutionIntelLabels";
import { EligibilityBadge } from "../institutionIntel/IntelBadges";
import { TablePaginationBar } from "../commercial/TablePaginationBar";
import type { ClientPageSizeOption } from "../../lib/clientTablePagination";

const DEFAULT_PAGE_SIZE = 15;

/**
 * Single authoritative "actionable current opportunity" table for the real
 * Licitaciones / equipos page — backed by W1's current_opportunity_queue
 * (`GET /operator/procurement/queues/current_opportunity`), which already
 * applies the fail-closed gates (active/open lifecycle, public procurement
 * eligibility, equipment_purchase_signal, catalog relevance, line evidence).
 *
 * This component renders only fields the backend actually supplies for this
 * queue grain — no region, contact email, or Mercado Público link, because
 * W1's institution-prospect pipeline does not carry those fields. It never
 * offers a contact/outreach action: procurement opportunity never implies
 * contact/outreach authorization.
 */
export function ActionableOpportunitiesTable({
  selectedTenderCode,
  onSelectTender,
}: {
  /** Presentation-only master/detail selection — does not affect the W1 data source or its columns. */
  selectedTenderCode?: string | null;
  onSelectTender?: (tenderCode: string) => void;
} = {}) {
  const [availability, setAvailability] = useState<Availed<readonly CurrentOpportunityRow[]> | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);
  const [totalItems, setTotalItems] = useState(0);

  function handlePageSizeChange(next: ClientPageSizeOption) {
    setPageSize(next === "all" ? 500 : next);
    setPage(1);
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    void institutionIntelAdapter
      .getCurrentOpportunities({ page, pageSize })
      .then((result) => {
        if (cancelled) return;
        setAvailability(result.availability);
        setTotalItems(result.pageInfo.totalItems);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setAvailability({ status: "not_available" });
        setTotalItems(0);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [page, pageSize]);

  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));

  return (
    <section className="space-y-3" aria-labelledby="oportunidades-accionables" data-testid="actionable-opportunities-section">
      <div>
        <h2 id="oportunidades-accionables" className="text-lg font-semibold text-slate-900">
          Oportunidades accionables
        </h2>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          Licitaciones vigentes que ya pasaron elegibilidad de participación pública, señal de compra de
          equipos, relevancia de catálogo y evidencia de línea — fuente única para "cotizar ahora".
        </p>
      </div>

      {loading || availability === null ? (
        <div className="h-32 animate-pulse rounded-lg bg-slate-100" role="status" aria-live="polite" />
      ) : null}

      {!loading && availability?.status === "not_available" ? (
        <div
          className="rounded-xl border border-amber-200 bg-amber-50/90 px-5 py-5 text-sm text-amber-950"
          role="status"
          data-testid="w1-opportunities-unavailable"
        >
          <p className="font-semibold">Cola de oportunidades accionables no disponible</p>
          <p className="mt-2 text-xs text-amber-900">
            No se pudo leer el paquete publicado de W1. No implica que no existan oportunidades — solo que
            no se pueden confirmar en este momento. No hay una fuente alternativa autorizada para
            confirmar oportunidades accionables mientras W1 no esté disponible.
          </p>
        </div>
      ) : null}

      {!loading && availability?.status === "available_empty" ? (
        <p className="text-sm text-[var(--color-muted)]" role="status" data-testid="w1-opportunities-empty">
          Se revisó la cola actual de W1 — no hay oportunidades accionables en este momento.
        </p>
      ) : null}

      {!loading && availability?.status === "available_incomplete" ? (
        <div
          className="rounded-md border border-amber-200 bg-amber-50/80 px-3 py-2 text-xs text-amber-950"
          data-testid="w1-opportunities-stale"
        >
          <p className="font-medium">Datos posiblemente desactualizados (feed marcado como stale).</p>
          {availability.reasonCodes.length > 0 ? (
            <p className="mt-1 text-[11px] text-amber-900">{availability.reasonCodes.join(", ")}</p>
          ) : null}
        </div>
      ) : null}

      {!loading &&
      availability &&
      (availability.status === "available" ||
        (availability.status === "available_incomplete" && availability.partial)) ? (
        <ActionableOpportunitiesTableBody
          rows={availability.status === "available" ? availability.data : (availability.partial ?? [])}
          page={page}
          pageSize={pageSize}
          totalPages={totalPages}
          onPageChange={setPage}
          onPageSizeChange={handlePageSizeChange}
          selectedTenderCode={selectedTenderCode}
          onSelectTender={onSelectTender}
        />
      ) : null}
    </section>
  );
}

/**
 * Presentation-only merge: the W1 queue can carry more than one
 * (institution, tender_code, equipment_category) row for the same
 * tender_code (e.g. balance + centrifuge on one tender). The underlying
 * `CurrentOpportunityRow[]` data stays exactly as returned by the adapter —
 * this only groups rows by tenderCode for the tender-level master list, and
 * joins the distinct categories for display (e.g. "Balanza + Centrífuga").
 */
function groupByTender(rows: readonly CurrentOpportunityRow[]): (CurrentOpportunityRow & { categories: string[] })[] {
  const order: string[] = [];
  const byTender = new Map<string, CurrentOpportunityRow & { categories: string[] }>();
  for (const row of rows) {
    const existing = byTender.get(row.tenderCode);
    if (existing) {
      if (!existing.categories.includes(row.categoryOrTitle)) {
        existing.categories.push(row.categoryOrTitle);
      }
      continue;
    }
    order.push(row.tenderCode);
    byTender.set(row.tenderCode, { ...row, categories: [row.categoryOrTitle] });
  }
  return order.map((code) => byTender.get(code)!);
}

function ActionableOpportunitiesTableBody({
  rows,
  page,
  pageSize,
  totalPages,
  onPageChange,
  onPageSizeChange,
  selectedTenderCode,
  onSelectTender,
}: {
  rows: readonly CurrentOpportunityRow[];
  page: number;
  pageSize: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: ClientPageSizeOption) => void;
  selectedTenderCode?: string | null;
  onSelectTender?: (tenderCode: string) => void;
}) {
  const groupedRows = groupByTender(rows);
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm">
      <table className="min-w-full text-left text-sm" data-testid="actionable-opportunities-table">
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-[var(--color-muted)]">
          <tr>
            <th className="px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">Comprador / institución</th>
            <th className="px-3 py-2 font-medium">Categoría</th>
            <th className="px-3 py-2 font-medium">Fuerza de prospecto</th>
            <th className="px-3 py-2 font-medium">Cierra</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border)]">
          {groupedRows.map((row, index) => {
            const selected = selectedTenderCode != null && selectedTenderCode === row.tenderCode;
            const selectable = typeof onSelectTender === "function";
            return (
              <tr
                key={row.tenderCode}
                data-testid="actionable-opportunity-row"
                aria-selected={selectable ? selected : undefined}
                onClick={selectable ? () => onSelectTender(row.tenderCode) : undefined}
                className={
                  selectable
                    ? `cursor-pointer transition-colors ${selected ? "bg-[var(--accent-50,#f0fdfa)]" : "hover:bg-slate-50"}`
                    : undefined
                }
              >
                <td className="px-3 py-2 font-semibold text-slate-900">{index + 1}</td>
                <td className="px-3 py-2 font-medium text-slate-900">
                  {row.institutionDisplayName || row.institutionId || "—"}
                  <div className="mt-0.5 font-mono text-xs font-normal text-slate-500">{row.tenderCode}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <EligibilityBadge status={row.eligibilityStatus} />
                  </div>
                </td>
                <td className="px-3 py-2 text-slate-700">
                  {row.categories.map(formatEquipmentCategory).join(" + ")}
                </td>
                <td className="px-3 py-2 text-slate-700">{AXIS_BAND_LABEL[row.prospectStrengthBand]}</td>
                <td className="px-3 py-2 whitespace-nowrap text-xs text-slate-600">
                  {row.closeTimestamp ? new Date(row.closeTimestamp).toLocaleDateString("es-CL") : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {totalPages > 1 ? (
        <TablePaginationBar
          page={page}
          totalPages={totalPages}
          pageSize={pageSize === 500 ? "all" : (pageSize as 15 | 30 | 50)}
          onPageChange={onPageChange}
          onPageSizeChange={onPageSizeChange}
        />
      ) : null}
    </div>
  );
}
