import { useState } from "react";
import { useSalesOpportunityBoard } from "../components/pipeline/useSalesOpportunityBoard";
import { SalesOpportunityBoard } from "../components/pipeline/SalesOpportunityBoard";
import { MobileSalesOpportunityList } from "../components/pipeline/MobileSalesOpportunityList";
import { SalesOpportunityWorkspaceDrawer } from "../components/pipeline/SalesOpportunityWorkspaceDrawer";
import type { SalesOpportunityListItem } from "../api/commercialOperationsTypes";

export function PipelinePage() {
  const board = useSalesOpportunityBoard();
  const [openItem, setOpenItem] = useState<SalesOpportunityListItem | null>(null);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Pipeline</h2>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Oportunidades de venta en gestión activa · CRM durable.
          </p>
        </div>
        <button
          type="button"
          onClick={board.refetch}
          disabled={board.loading}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {board.loading ? "Actualizando…" : "Actualizar datos"}
        </button>
      </div>

      <SalesOpportunityBoard board={board} onOpenOpportunity={setOpenItem} />
      <MobileSalesOpportunityList board={board} onOpenOpportunity={setOpenItem} />

      <SalesOpportunityWorkspaceDrawer
        item={openItem}
        open={openItem !== null}
        onClose={() => setOpenItem(null)}
        onStageChanged={() => board.refetch()}
        onTaskChanged={() => board.refetch()}
      />
    </div>
  );
}
