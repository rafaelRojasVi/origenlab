import {
  SALES_OPPORTUNITY_ACTIVE_STAGES,
  SALES_OPPORTUNITY_TOGGLE_STAGES,
  isSalesOpportunityStageTerminal,
  salesOpportunityStageLabel,
} from "../../lib/salesOpportunityFormat";
import type { SalesOpportunityStage } from "../../api/commercialOperationsTypes";

const ALL_STAGES: readonly SalesOpportunityStage[] = [
  ...SALES_OPPORTUNITY_ACTIVE_STAGES,
  ...SALES_OPPORTUNITY_TOGGLE_STAGES,
];

export function StageChangeMenu({
  stage,
  disabled = false,
  onChange,
}: {
  stage: SalesOpportunityStage;
  disabled?: boolean;
  onChange: (stage: SalesOpportunityStage) => void;
}) {
  if (isSalesOpportunityStageTerminal(stage)) {
    return (
      <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
        {salesOpportunityStageLabel(stage)} · cerrada
      </span>
    );
  }

  return (
    <select
      aria-label="Cambiar etapa"
      value={stage}
      disabled={disabled}
      onClick={(event) => event.stopPropagation()}
      onChange={(event) => onChange(event.target.value as SalesOpportunityStage)}
      className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition-colors hover:border-slate-400 disabled:opacity-50"
    >
      {ALL_STAGES.map((value) => (
        <option key={value} value={value}>
          {salesOpportunityStageLabel(value)}
        </option>
      ))}
    </select>
  );
}
