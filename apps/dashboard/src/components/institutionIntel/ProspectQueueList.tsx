import { useEffect, useState } from "react";
import type { ProspectQueueRow, QueueName } from "../../api/institutionIntel/types";
import { institutionIntelAdapter } from "../../api/institutionIntel/adapter";
import {
  AXIS_BAND_LABEL,
  formatEquipmentCategory,
  formatQueueName,
  formatReviewToken,
  humanizeToken,
} from "../../lib/institutionIntelLabels";
import { TablePaginationBar } from "../commercial/TablePaginationBar";
import { EligibilityBadge, QueueBadge } from "./IntelBadges";

const ALL_QUEUES: readonly QueueName[] = [
  "current_opportunity_queue",
  "historical_prospect_queue",
  "contact_gap_queue",
  "institution_match_review_queue",
  "line_evidence_review_queue",
  "retender_review_queue",
];

/** Money-driving queues lead; data-quality/housekeeping queues (review, evidence, retender) trail — a frontend grouping decision, not something the backend pre-ranks. */
const DE_EMPHASIZED_QUEUES = new Set<QueueName>([
  "institution_match_review_queue",
  "line_evidence_review_queue",
  "retender_review_queue",
]);

function formatCommercialSignalType(raw: string): string {
  switch (raw) {
    case "equipment_purchase_signal":
      return "señal de compra";
    case "rental_or_comodato_signal":
      return "señal de arriendo / comodato";
    case "installed_base_signal":
      return "señal de base instalada";
    case "consumable_or_reagent_signal":
      return "señal de consumibles / reactivos";
    case "review_required_signal":
      return "señal pendiente de revisión";
    default:
      return raw ? humanizeToken(raw) : "señal comercial";
  }
}

function formatBuyerKey(raw?: string): string {
  if (!raw) return "Familia de licitaciones";
  return raw.replace(/^buyer_name:/, "").trim() || "Familia de licitaciones";
}

function QueueRowCard({ row, onOpenInstitution }: { row: ProspectQueueRow; onOpenInstitution: (id: string) => void }) {
  const priority = row.queue === "current_opportunity_queue";
  return (
    <div
      className={`rounded-xl border bg-[var(--color-card)] px-4 py-3 shadow-sm ${
        priority ? "border-red-200 border-l-4" : "border-[var(--color-border)]"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <QueueBadge queue={row.queue} />
            {row.institutionId ? (
              <button
                type="button"
                className="text-sm font-bold text-brand-700 hover:underline"
                onClick={() => onOpenInstitution(row.institutionId)}
              >
                {row.institutionDisplayName || "Institución"}
              </button>
            ) : row.institutionDisplayName ? (
              <span className="text-sm font-bold text-slate-700">
                {row.institutionDisplayName}
              </span>
            ) : null}
          </div>

          {row.queue === "current_opportunity_queue" ? (
            <>
              <p className="text-sm text-slate-800">
                {formatEquipmentCategory(row.categoryOrTitle)} — licitación {row.tenderCode}
              </p>
              <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                <EligibilityBadge status={row.eligibilityStatus} /> · fuerza de prospecto{" "}
                <b className="text-slate-700">{AXIS_BAND_LABEL[row.prospectStrengthBand]}</b>
              </p>
            </>
          ) : null}

          {row.queue === "historical_prospect_queue" ? (
            <>
              <p className="text-sm text-slate-800">
                {row.category ? `${formatEquipmentCategory(row.category)} — ` : ""}
                {formatCommercialSignalType(row.commercialSignalType)}, sin licitación abierta actualmente
              </p>
              <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                Última vez vista: {row.mostRecentObservedDate ?? "—"} · {row.tenderCount} licitación(es)
              </p>
            </>
          ) : null}

          {row.queue === "contact_gap_queue" ? (
            <p className="text-sm text-slate-800">
              Fuerza <b>{AXIS_BAND_LABEL[row.prospectStrengthBand]}</b>, urgencia{" "}
              <b>{AXIS_BAND_LABEL[row.opportunityUrgencyBand]}</b> — sin ruta de contacto clara
            </p>
          ) : null}

          {row.queue === "institution_match_review_queue" ? (
            <>
              <p className="text-sm text-slate-800">
                {row.conflictReason ||
                  (row.resolutionStatus === "review_only_fragmented_identity"
                    ? "Identidad fragmentada — revisión manual"
                    : row.resolutionStatus
                      ? humanizeToken(row.resolutionStatus)
                      : "Identidad requiere revisión manual")}
              </p>
              <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                {row.candidateDisplayNames?.length
                  ? row.candidateDisplayNames.join(" / ")
                  : row.memberProfileIds?.length
                    ? `${row.memberProfileIds.length} perfiles forman este cluster de revisión`
                    : row.reviewClusterId
                      ? `Cluster ${row.reviewClusterId.slice(0, 12)}…`
                      : "Sin identidad institucional confirmada"}
              </p>
            </>
          ) : null}

          {row.queue === "line_evidence_review_queue" ? (
            <>
              <p className="text-sm text-slate-800">Licitación {row.tenderCode}</p>
              {row.clauseText ? (
                <p className="mt-0.5 border-l-2 border-[var(--color-border)] pl-2 text-xs italic text-[var(--color-muted)]">
                  &ldquo;{row.clauseText}&rdquo;
                </p>
              ) : (
                <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                  {row.canonicalEquipmentClasses?.length
                    ? `Clases detectadas: ${row.canonicalEquipmentClasses
                        .map(formatEquipmentCategory)
                        .join(", ")}`
                    : row.relevanceClass
                      ? `Relevancia: ${formatReviewToken(row.relevanceClass)}`
                      : "Evidencia estructurada pendiente de revisión"}
                  {row.lineDisposition ? ` · ${formatReviewToken(row.lineDisposition)}` : ""}
                </p>
              )}
            </>
          ) : null}

          {row.queue === "retender_review_queue" ? (
            <>
              <p className="text-sm text-slate-800">{formatBuyerKey(row.buyerKey)}</p>
              <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                {row.tenderCodes.length ? row.tenderCodes.join(" ↔ ") : "Sin códigos de licitación"}
              </p>
              <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                {row.resolutionReason ||
                  (row.resolutionStatus
                    ? formatReviewToken(row.resolutionStatus)
                    : "Relación entre licitaciones pendiente de revisión")}
              </p>
            </>
          ) : null}
        </div>

        {row.queue === "current_opportunity_queue" && row.closeTimestamp ? (
          <span className="shrink-0 rounded-md bg-red-50 px-2 py-1 text-[11px] font-bold tabular-nums text-red-700">
            cierra {new Date(row.closeTimestamp).toLocaleDateString("es-CL")}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The real API serves exactly one queue per request (no combined "all
 * queues" endpoint), and individual queues can be huge in production
 * (line_evidence_review_queue: ~14,758 rows). "Todo" therefore fetches a
 * small preview page per queue rather than trying to paginate a merged feed
 * the backend doesn't offer — a specific queue selection uses real
 * server-side pagination instead.
 */
const TODO_PREVIEW_PAGE_SIZE = 5;

export function ProspectQueueList({ onOpenInstitution }: { onOpenInstitution: (institutionId: string) => void }) {
  const [rows, setRows] = useState<ProspectQueueRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeQueue, setActiveQueue] = useState<QueueName | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(15);
  const [totalItems, setTotalItems] = useState(0);

  /** The API caps `limit` at 500 (contract: 1..500, else 422) — "Todos" maps to that bound rather than an unbounded fetch. */
  function handlePageSizeChange(next: 15 | 30 | 50 | "all") {
    setPageSize(next === "all" ? 500 : next);
    setPage(1);
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    if (activeQueue === null) {
      void Promise.all(
        ALL_QUEUES.map((queue) =>
          institutionIntelAdapter.listQueueRows({ queue, page: 1, pageSize: TODO_PREVIEW_PAGE_SIZE }),
        ),
      ).then((pages) => {
        if (cancelled) return;
        const merged = pages.flatMap((p) => p.items);
        setRows(merged);
        setTotalItems(merged.length);
        setLoading(false);
      });
    } else {
      void institutionIntelAdapter.listQueueRows({ queue: activeQueue, page, pageSize }).then((result) => {
        if (cancelled) return;
        setRows([...result.items]);
        setTotalItems(result.pageInfo.totalItems);
        setLoading(false);
      });
    }

    return () => {
      cancelled = true;
    };
  }, [activeQueue, page, pageSize]);

  useEffect(() => {
    setPage(1);
  }, [activeQueue]);

  const sorted = [...rows].sort((a, b) => {
    const aDe = DE_EMPHASIZED_QUEUES.has(a.queue) ? 1 : 0;
    const bDe = DE_EMPHASIZED_QUEUES.has(b.queue) ? 1 : 0;
    return aDe - bDe;
  });

  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));

  return (
    <div className="space-y-4" data-testid="prospect-queue-list">
      <p className="rounded-md border border-dashed border-[var(--color-border)] bg-white px-3 py-2 text-xs text-[var(--color-muted)]">
        Vista conceptual — sintetiza las seis colas del backend en una sola lista priorizada; esa
        combinación es una decisión de esta interfaz, no algo que el backend ya entregue así.
      </p>

      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={() => setActiveQueue(null)}
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            activeQueue === null ? "bg-brand-600 text-white" : "border border-[var(--color-border)] bg-white text-slate-700"
          }`}
        >
          Todo
        </button>
        {ALL_QUEUES.map((queue) => (
          <button
            key={queue}
            type="button"
            onClick={() => setActiveQueue(queue)}
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              activeQueue === queue
                ? "bg-brand-600 text-white"
                : "border border-[var(--color-border)] bg-white text-slate-700"
            }`}
          >
            {formatQueueName(queue)}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-2" role="status" aria-live="polite">
          <div className="h-20 animate-pulse rounded-xl bg-slate-100" />
          <div className="h-20 animate-pulse rounded-xl bg-slate-100" />
        </div>
      ) : (
        <>
          {activeQueue === null ? (
            <p className="text-[11px] text-[var(--color-muted)]">
              Vista previa — hasta {TODO_PREVIEW_PAGE_SIZE} por cola. Elige una cola específica para ver todo con
              paginación real.
            </p>
          ) : null}
          <div className="space-y-2">
            {sorted.map((row) => (
              <QueueRowCard key={row.rowId} row={row} onOpenInstitution={onOpenInstitution} />
            ))}
            {sorted.length === 0 ? (
              <p className="text-sm text-[var(--color-muted)]">Sin elementos en esta cola.</p>
            ) : null}
          </div>
          {activeQueue !== null && totalItems > 0 ? (
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]">
              <TablePaginationBar
                page={page}
                totalPages={totalPages}
                pageSize={pageSize === 500 ? "all" : (pageSize as 15 | 30 | 50)}
                onPageChange={setPage}
                onPageSizeChange={handlePageSizeChange}
              />
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
