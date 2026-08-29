import { useState } from "react";
import type { SalesOpportunityListItem, SalesOpportunityStage } from "../../api/commercialOperationsTypes";
import {
  SALES_OPPORTUNITY_ACTIVE_STAGES,
  SALES_OPPORTUNITY_TOGGLE_STAGES,
  salesOpportunityStageLabel,
} from "../../lib/salesOpportunityFormat";
import { SalesOpportunityCard } from "./SalesOpportunityCard";
import type { useSalesOpportunityBoard } from "./useSalesOpportunityBoard";

const ALL_STAGES: readonly SalesOpportunityStage[] = [
  ...SALES_OPPORTUNITY_ACTIVE_STAGES,
  ...SALES_OPPORTUNITY_TOGGLE_STAGES,
];

const TOGGLE_LABELS: Record<"won" | "lost" | "dormant", string> = {
  won: "Ganadas",
  lost: "Perdidas",
  dormant: "Dormidas",
};

export function MobileSalesOpportunityList({
  board,
  onOpenOpportunity,
}: {
  board: ReturnType<typeof useSalesOpportunityBoard>;
  onOpenOpportunity: (item: SalesOpportunityListItem) => void;
}) {
  const [selectedStage, setSelectedStage] = useState<SalesOpportunityStage>("new");

  function selectStage(stage: SalesOpportunityStage) {
    const isToggleStage = SALES_OPPORTUNITY_TOGGLE_STAGES.includes(stage);
    if (isToggleStage && !board.enabledToggles.includes(stage)) {
      board.toggleStage(stage);
    }
    setSelectedStage(stage);
  }

  const visibleItems = board.items.filter((item) => item.stage === selectedStage);
  const isEmptyOverall = !board.loading && !board.error && board.items.length === 0;

  return (
    <div className="space-y-3 md:hidden" data-testid="sales-opportunity-board-mobile">
      <div role="tablist" aria-label="Etapa del pipeline" className="flex gap-2 overflow-x-auto pb-1">
        {ALL_STAGES.map((stage) => {
          const isToggleStage = SALES_OPPORTUNITY_TOGGLE_STAGES.includes(stage);
          const label = isToggleStage ? TOGGLE_LABELS[stage as "won" | "lost" | "dormant"] : salesOpportunityStageLabel(stage);
          return (
            <button
              key={stage}
              type="button"
              role="tab"
              aria-selected={selectedStage === stage}
              onClick={() => selectStage(stage)}
              className={`motion-safe:transition-colors shrink-0 rounded-full border px-3 py-1.5 text-xs font-medium ${
                selectedStage === stage
                  ? "border-brand-600 bg-brand-600 text-white"
                  : "border-slate-300 bg-white text-slate-600"
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {board.stageError ? (
        <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {board.stageError}
        </div>
      ) : null}

      {board.error ? (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          <p className="font-medium">No pudimos cargar el pipeline.</p>
          <button
            type="button"
            onClick={board.refetch}
            className="mt-2 rounded-md border border-red-300 bg-white px-3 py-1 text-sm font-medium text-red-800"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      {board.loading ? (
        <div className="space-y-2" role="status" aria-live="polite" aria-label="Cargando pipeline">
          <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
          <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
        </div>
      ) : null}

      {!board.loading && !board.error && isEmptyOverall ? (
        <div className="rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center">
          <p className="text-sm font-medium text-slate-800">No hay oportunidades activas todavía.</p>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Promueve oportunidades detectadas desde Negocios para comenzar a gestionarlas aquí.
          </p>
        </div>
      ) : null}

      {!board.loading && !board.error && !isEmptyOverall ? (
        <div className="space-y-2">
          {visibleItems.map((item) => (
            <SalesOpportunityCard
              key={item.sales_opportunity_id}
              item={item}
              onOpen={() => onOpenOpportunity(item)}
              onStageChange={(nextStage) => void board.changeStage(item, nextStage)}
              stagePending={board.pendingStageChangeId !== null}
            />
          ))}
          {visibleItems.length === 0 ? (
            <p className="rounded-lg border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-[var(--color-muted)]">
              Sin oportunidades en esta etapa.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
