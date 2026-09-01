import { useState } from "react";
import { useSalesOpportunityBoard } from "../components/pipeline/useSalesOpportunityBoard";
import { SalesOpportunityBoard } from "../components/pipeline/SalesOpportunityBoard";
import { MobileSalesOpportunityList } from "../components/pipeline/MobileSalesOpportunityList";
import { SalesOpportunityWorkspaceDrawer } from "../components/pipeline/SalesOpportunityWorkspaceDrawer";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import type { SalesOpportunityListItem } from "../api/commercialOperationsTypes";

export function VentasPage() {
  const board = useSalesOpportunityBoard();
  const [openItem, setOpenItem] = useState<SalesOpportunityListItem | null>(null);

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="Oportunidades activas"
        subtitle="CRM durable · arrastra una tarjeta o cambia la etapa para mover una oportunidad."
        actions={
          <button
            type="button"
            onClick={board.refetch}
            disabled={board.loading}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {board.loading ? "Actualizando…" : "Actualizar datos"}
          </button>
        }
      />

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
