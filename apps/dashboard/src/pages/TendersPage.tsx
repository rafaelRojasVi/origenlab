import { useState } from "react";
import { ActionableOpportunitiesTable } from "../components/tenders/ActionableOpportunitiesTable";
import { TenderDetailDrawer } from "../components/tenders/TenderDetailDrawer";

/**
 * Public procurement / tender master/detail workspace.
 *
 * The W1-backed "Oportunidades accionables" table (left column, ~480px in
 * the visual reference) is the ONLY procurement-opportunity content on this
 * page — the single authoritative source for confirmed-actionable current
 * opportunities, already gated on fail-closed eligibility/lifecycle/catalog/
 * line-evidence rules. Selecting a row opens the ANEXO-T1 term-intelligence
 * drawer (right column, ~620px) for that tender_code — additive detail on
 * top of the same W1 actionability gate, never a second opportunity source.
 *
 * The legacy equipment_first feed (EquipmentOpportunitiesTable, GET
 * /opportunities/equipment) predates procurement-eligibility work and can
 * still label a restricted-eligibility tender `next_action=quote_now`. A
 * surrounding warning does not neutralize that row-level signal, so it is
 * deliberately NOT rendered here at all (not even demoted) — the legacy
 * pipeline, API, and client remain intact for possible future discovery/
 * review use, just not on this page.
 */
export function TendersPage() {
  const [selectedTenderCode, setSelectedTenderCode] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-start" data-testid="tenders-master-detail">
      <div className="min-w-0 flex-1 lg:basis-[480px] lg:grow-[3]">
        <ActionableOpportunitiesTable
          selectedTenderCode={selectedTenderCode}
          onSelectTender={setSelectedTenderCode}
        />
      </div>
      <div className="min-w-0 flex-1 lg:basis-[620px] lg:grow-[4] lg:max-w-[640px] lg:sticky lg:top-4">
        <TenderDetailDrawer tenderCode={selectedTenderCode} />
      </div>
    </div>
  );
}
