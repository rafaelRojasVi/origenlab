import type { SalesOpportunityListItem, SalesOpportunityStage } from "../../api/commercialOperationsTypes";
import {
  SALES_OPPORTUNITY_ACTIVE_STAGES,
  SALES_OPPORTUNITY_TOGGLE_STAGES,
  salesOpportunityStageLabel,
} from "../../lib/salesOpportunityFormat";
import { SalesOpportunityCard } from "./SalesOpportunityCard";
import type { useSalesOpportunityBoard } from "./useSalesOpportunityBoard";

const TOGGLE_LABELS: Record<"won" | "lost" | "dormant", string> = {
  won: "Ganadas",
  lost: "Perdidas",
  dormant: "Dormidas",
};

export function SalesOpportunityBoard({
  board,
  onOpenOpportunity,
}: {
  board: ReturnType<typeof useSalesOpportunityBoard>;
  onOpenOpportunity: (item: SalesOpportunityListItem) => void;
}) {
  const columns: SalesOpportunityStage[] = [
    ...SALES_OPPORTUNITY_ACTIVE_STAGES,
    ...board.enabledToggles,
  ];

  const grouped: Record<string, SalesOpportunityListItem[]> = {};
  for (const stage of columns) grouped[stage] = [];
  for (const item of board.items) {
    if (grouped[item.stage]) grouped[item.stage].push(item);
  }

  const isEmpty = !board.loading && !board.error && board.items.length === 0;

  return (
    <div className="hidden space-y-4 md:block" data-testid="sales-opportunity-board-desktop">
      <div className="flex flex-wrap gap-2">
        {SALES_OPPORTUNITY_TOGGLE_STAGES.map((stage) => {
          const key = stage as "won" | "lost" | "dormant";
          const active = board.enabledToggles.includes(stage);
          return (
            <button
              key={stage}
              type="button"
              aria-pressed={active}
              onClick={() => board.toggleStage(stage)}
              className={`motion-safe:transition-colors rounded-full border px-3 py-1 text-xs font-medium ${
                active
                  ? "border-brand-600 bg-brand-50 text-brand-800"
                  : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              {TOGGLE_LABELS[key]}
            </button>
          );
        })}
      </div>

      {board.stageError ? (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          <span>{board.stageError}</span>
          <button type="button" onClick={board.dismissStageError} className="shrink-0 text-amber-700 underline">
            Cerrar
          </button>
        </div>
      ) : null}

      {board.error ? (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          <p className="font-medium">No pudimos cargar el pipeline.</p>
          <button
            type="button"
            onClick={board.refetch}
            className="mt-2 rounded-md border border-red-300 bg-white px-3 py-1 text-sm font-medium text-red-800 hover:bg-red-50"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      {board.loading ? (
        <div
          className="flex gap-4 overflow-x-auto pb-2"
          role="status"
          aria-live="polite"
          aria-label="Cargando pipeline"
        >
          {columns.map((stage) => (
            <div key={stage} className="w-72 shrink-0 space-y-2">
              <div className="h-4 w-24 animate-pulse rounded bg-slate-200" />
              <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
              <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
            </div>
          ))}
        </div>
      ) : null}

      {!board.loading && !board.error && isEmpty ? (
        <div className="rounded-xl border border-dashed border-slate-300 px-6 py-10 text-center">
          <p className="text-sm font-medium text-slate-800">No hay oportunidades activas todavía.</p>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Promueve oportunidades detectadas desde Negocios para comenzar a gestionarlas aquí.
          </p>
        </div>
      ) : null}

      {!board.loading && !board.error && !isEmpty ? (
        <div className="flex gap-4 overflow-x-auto pb-2">
          {columns.map((stage) => (
            <div
              key={stage}
              data-testid={`pipeline-column-drop-${stage}`}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                const droppedId = event.dataTransfer.getData("text/plain");
                const droppedItem = board.items.find((row) => row.sales_opportunity_id === droppedId);
                if (droppedItem && droppedItem.stage !== stage) {
                  void board.changeStage(droppedItem, stage);
                }
              }}
              className="w-72 shrink-0 space-y-2"
            >
              <div className="flex items-center justify-between px-1">
                <h3 className="text-sm font-semibold text-slate-800">{salesOpportunityStageLabel(stage)}</h3>
                <span className="text-xs text-[var(--color-muted)]">{grouped[stage].length}</span>
              </div>
              <div className="motion-safe:transition-all space-y-2">
                {grouped[stage].map((item) => (
                  <SalesOpportunityCard
                    key={item.sales_opportunity_id}
                    item={item}
                    onOpen={() => onOpenOpportunity(item)}
                    onStageChange={(nextStage) => void board.changeStage(item, nextStage)}
                    stagePending={board.pendingStageChangeId === item.sales_opportunity_id}
                  />
                ))}
                {grouped[stage].length === 0 ? (
                  <p className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-[var(--color-muted)]">
                    Sin oportunidades
                  </p>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
